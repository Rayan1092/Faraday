"""Camera: OpenCV snapshot -> captures/photos/<time>.jpg, then Bonsai vision.

    feed = CameraFeed.from_settings(settings)          # the one owner of the webcam
    cam = PhotoCamera(feed, Vision.from_settings(settings))
    event = cam.take(take_photo_action)                 # photo_described event
    feed.jpeg()                                         # latest frame for the live stream

The webcam can only be opened once, so CameraFeed owns it: photos and the
Controls page's live stream both read from the same capture. It opens on
first use and closes again after `idle_s` with no viewer and no photo, so the
camera light isn't on all day.

Object detection (hardware/detect.py, YOLOX-tiny + YuNet faces, no LLM):
the live stream gets labelled boxes (about 5 detections a second while
someone watches), and every photo is analysed: people / faces / detections go
on photo_described, and the counts are a hint in the vision prompt.

If vision fails or times out, the description comes from the detector alone
("One person is at the door. ...", "Nobody is there.", described_by
"detector"). Without a detector the photo is kept with "Photo saved." (source
"camera"). If the camera itself fails, the event still goes out with an empty
photo_path and "Camera unavailable." so the pipeline's follow-up (greeting a
visitor after a doorbell photo) still happens.
"""
from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

from shared import Action, Event, EventType, PhotoDescribedData, Source, make_event

from hardware.settings import REPO_ROOT
from hardware.vision import Vision

log = logging.getLogger("hardware.camera")
NO_VISION = "Photo saved."
NO_CAMERA = "Camera unavailable."
WARMUP_FRAMES = 5          # first frames are dark while auto-exposure settles


class Camera:
    def __init__(self, index: int = 0, width: int = 1280, height: int = 720,
                 photos_dir: Path = REPO_ROOT / "captures" / "photos"):
        self.index, self.width, self.height = index, width, height
        self.photos_dir = photos_dir

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "Camera":
        cfg = settings.get("camera", {})
        captures = settings.get("paths", {}).get("captures", "captures")
        return cls(int(cfg.get("index", 0)), int(cfg.get("width", 1280)),
                   int(cfg.get("height", 720)), REPO_ROOT / captures / "photos")

    def snapshot(self) -> Optional[Path]:
        """Grab one frame and save it. Returns the file path, or None."""
        import cv2
        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else (
            cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY)
        cap = cv2.VideoCapture(self.index, backend)
        try:
            if not cap.isOpened():
                log.error("camera %s did not open", self.index)
                return None
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            frame = None
            for _ in range(WARMUP_FRAMES):
                ok, f = cap.read()
                if ok:
                    frame = f
            if frame is None:
                log.error("camera %s gave no frame", self.index)
                return None
            self.photos_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]   # milliseconds
            path = self.photos_dir / f"{stamp}.jpg"
            if not cv2.imwrite(str(path), frame):
                log.error("could not write %s", path)
                return None
            return path
        finally:
            cap.release()


def _open_capture(index: int, width: int, height: int) -> Any:
    import cv2
    backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else (
        cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY)
    cap = cv2.VideoCapture(index, backend)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


class CameraFeed:
    """Single owner of the webcam, shared by photos (snapshot) and the live
    stream (jpeg / viewer). One grabber thread reads frames at `fps` while the
    camera is in use; snapshot() waits for a frame newer than the call."""

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720,
                 photos_dir: Path = REPO_ROOT / "captures" / "photos",
                 fps: float = 10.0, idle_s: float = 15.0, jpeg_quality: int = 75,
                 capture_factory: Callable[[int, int, int], Any] = _open_capture,
                 detector: Any = None, detect_fps: float = 5.0):
        self.index, self.width, self.height = index, width, height
        self.detector = detector               # hardware.detect.Detector: boxes on the stream
        self.detect_fps = detect_fps
        self._boxes: list = []
        self._boxes_at = 0.0                   # frame time the boxes belong to
        self._detect_at = 0.0
        self.photos_dir = photos_dir
        self.fps, self.idle_s, self.jpeg_quality = fps, idle_s, jpeg_quality
        self.capture_factory = capture_factory
        self.opens = 0                         # how many times the device was opened
        self._cap: Any = None
        self._frame: Any = None                # latest BGR frame
        self._frame_at = 0.0
        self._jpeg: Optional[bytes] = None
        self._jpeg_at = 0.0
        self._viewers = 0
        self._holders = 0                      # keep_open() users (alarm recording): no detection
        self._used_at = 0.0
        self._lock = threading.Lock()
        self._new_frame = threading.Condition(self._lock)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.failed = False                    # last open attempt failed

    @classmethod
    def from_settings(cls, settings: Dict[str, Any], detector: Any = None) -> "CameraFeed":
        cfg = settings.get("camera", {})
        captures = settings.get("paths", {}).get("captures", "captures")
        det_cfg = cfg.get("detector", {}) or {}
        return cls(int(cfg.get("index", 0)), int(cfg.get("width", 1280)),
                   int(cfg.get("height", 720)), REPO_ROOT / captures / "photos",
                   float(cfg.get("stream_fps", 10)), float(cfg.get("idle_s", 15)),
                   detector=detector, detect_fps=float(det_cfg.get("fps", 5)))

    # ---- lifecycle ----------------------------------------------------------------
    def _touch(self) -> None:
        with self._lock:
            self._used_at = time.monotonic()
            running = self._thread is not None and self._thread.is_alive()
            if not running:
                self._stop.clear()
                self.failed = False            # a new attempt: the last failure is old news
                self._thread = threading.Thread(target=self._loop, name="faraday-camera",
                                                daemon=True)
                self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(3)
            self._thread = None

    @contextlib.contextmanager
    def viewer(self) -> Iterator["CameraFeed"]:
        """Keeps the camera open while a live stream is being watched."""
        with self._lock:
            self._viewers += 1
        self._touch()
        try:
            yield self
        finally:
            with self._lock:
                self._viewers -= 1
                self._used_at = time.monotonic()

    @property
    def running(self) -> bool:
        """The capture thread is on (the camera is open or being opened)."""
        return self._thread is not None and self._thread.is_alive()

    def frame_age(self) -> Optional[float]:
        """Seconds since the last frame while the camera runs, else None (health)."""
        with self._lock:
            if self._frame is None or not self.running:
                return None
            return time.monotonic() - self._frame_at

    @contextlib.contextmanager
    def keep_open(self) -> Iterator["CameraFeed"]:
        """Keeps the camera open (alarm recording) without counting as a viewer,
        so the object detector doesn't run for it."""
        with self._lock:
            self._holders += 1
        self._touch()
        try:
            yield self
        finally:
            with self._lock:
                self._holders -= 1
                self._used_at = time.monotonic()

    def _release(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None

    def _loop(self) -> None:
        period = 1.0 / max(1.0, self.fps)
        try:
            while not self._stop.is_set():
                with self._lock:
                    idle = (self._viewers == 0 and self._holders == 0
                            and time.monotonic() - self._used_at > self.idle_s)
                if idle:
                    break
                if self._cap is None:
                    self._cap = self.capture_factory(self.index, self.width, self.height)
                    self.opens += 1
                    if not self._cap.isOpened():
                        log.error("camera %s did not open", self.index)
                        self._release()
                        self.failed = True
                        with self._lock:
                            self._new_frame.notify_all()
                        self._stop.wait(2.0)
                        continue
                    self.failed = False
                    for _ in range(WARMUP_FRAMES):   # dark frames while exposure settles
                        self._cap.read()
                t0 = time.monotonic()
                ok, frame = self._cap.read()
                if ok and frame is not None:
                    with self._lock:
                        self._frame, self._frame_at = frame, time.monotonic()
                        frame_at, watched = self._frame_at, self._viewers > 0
                        self._new_frame.notify_all()
                    if (self.detector is not None and watched
                            and t0 - self._detect_at >= 1.0 / max(0.5, self.detect_fps)):
                        self._detect_at = t0
                        found = self.detector.detect(frame) or []    # None = failed: no boxes
                        with self._lock:
                            self._boxes, self._boxes_at = found, frame_at
                else:
                    log.warning("camera %s gave no frame; reopening", self.index)
                    self._release()
                self._stop.wait(max(0.0, period - (time.monotonic() - t0)))
        finally:
            self._release()
            with self._lock:
                self._frame, self._jpeg = None, None
                self._new_frame.notify_all()

    # ---- reading -----------------------------------------------------------------
    def _wait_frame(self, after: float, timeout: float) -> Any:
        return self._wait_frame_at(after, timeout)[0]

    def _wait_frame_at(self, after: float, timeout: float) -> "tuple[Any, float]":
        """(frame newer than `after`, its time), read together; (None, after) if none."""
        self._touch()
        end = time.monotonic() + timeout
        with self._lock:
            while self._frame is None or self._frame_at <= after:
                left = end - time.monotonic()
                if left <= 0 or self.failed:
                    return None, after
                self._new_frame.wait(left)
            return self._frame, self._frame_at

    def jpeg(self, timeout: float = 3.0) -> Optional[bytes]:
        """Latest frame as JPEG (re-encoded at most once per new frame)."""
        frame = self._wait_frame(after=0.0, timeout=timeout)
        if frame is None:
            return None
        with self._lock:
            if self._jpeg is not None and self._jpeg_at == self._frame_at:
                return self._jpeg
            frame, at = self._frame, self._frame_at
            boxes = self._boxes if at - self._boxes_at <= 1.0 else []
        import cv2
        if boxes:                               # live view only; saved photos stay clean
            from hardware.detect import draw
            frame = draw(frame, boxes)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            return None
        data = buf.tobytes()
        with self._lock:
            self._jpeg, self._jpeg_at = data, at
        return data

    def next_jpeg(self, after: float, timeout: float = 3.0) -> "tuple[Optional[bytes], float]":
        """Wait for a frame newer than `after`; returns (jpeg, its time)."""
        frame = self._wait_frame(after=after, timeout=timeout)
        if frame is None:
            return None, after
        with self._lock:
            at = self._frame_at
        return self.jpeg(timeout=0.1), at

    def record(self, seconds: float, out_dir: Path,
               stop: Optional[threading.Event] = None) -> Dict[str, Any]:
        """Save every new frame for `seconds` (alarm recording): JPEGs
        frame-0001.jpg ... in out_dir, then clip.avi (MJPG) at the frame rate that
        was really reached. Only frames newer than the call count; a camera that
        fails meanwhile is retried until the time is up; `stop` ends it early.
        Blocks, so run it on its own thread. Returns {"path", "frames",
        "duration_s", "video_path"}."""
        import cv2
        out_dir.mkdir(parents=True, exist_ok=True)
        frames, first, last, size = 0, None, None, None
        end = time.monotonic() + seconds
        after = time.monotonic()                  # nothing from before the alarm
        with self.keep_open():
            while time.monotonic() < end and not (stop is not None and stop.is_set()):
                frame, at = self._wait_frame_at(after=after, timeout=min(1.0, max(0.05, end - time.monotonic())))
                if frame is None:
                    time.sleep(0.1)               # camera reopening: keep trying until the end
                    continue
                after = at
                if size is None:
                    size = (frame.shape[1], frame.shape[0])
                elif (frame.shape[1], frame.shape[0]) != size:
                    frame = cv2.resize(frame, size)   # resolution changed after a reopen
                frames += 1
                first = at if first is None else first
                last = at
                cv2.imwrite(str(out_dir / f"frame-{frames:04d}.jpg"), frame,
                            [cv2.IMWRITE_JPEG_QUALITY, 85])
        duration = round((last - first) if frames > 1 else 0.0, 2)
        video = self._write_clip(out_dir, frames, duration, size) if frames > 1 else None
        return {"path": out_dir, "frames": frames, "duration_s": duration, "video_path": video}

    @staticmethod
    def _write_clip(out_dir: Path, frames: int, duration: float, size: Any) -> Optional[Path]:
        """clip.avi from the saved JPEGs, at the frame rate really reached (so it
        plays in real time even if the machine was busy)."""
        import cv2
        video = out_dir / "clip.avi"
        fps = max(1.0, min(30.0, (frames - 1) / duration)) if duration > 0 else 10.0
        try:
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
            if not writer.isOpened():
                return None
            for i in range(1, frames + 1):
                img = cv2.imread(str(out_dir / f"frame-{i:04d}.jpg"))
                if img is not None:
                    writer.write(img)
            writer.release()
        except Exception as e:
            log.warning("no alarm video (%r); frames only", e)
            return None
        return video if video.exists() and video.stat().st_size > 0 else None

    def snapshot(self) -> Optional[Path]:
        """Same interface as Camera.snapshot(), but through the shared feed."""
        frame = self._wait_frame(after=time.monotonic(), timeout=6.0)
        if frame is None:
            log.error("camera %s gave no frame for the photo", self.index)
            return None
        import cv2
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        path = self.photos_dir / f"{stamp}.jpg"
        if not cv2.imwrite(str(path), frame):
            log.error("could not write %s", path)
            return None
        return path


def relative(path: Path) -> str:
    """Repo-relative, forward slashes (FORMATS.md), e.g. captures/photos/x.jpg."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


class PhotoCamera:
    """take_photo -> snapshot -> Bonsai description -> photo_described event."""

    def __init__(self, camera: Any, vision: Optional[Vision], detector: Any = None):
        self.camera = camera        # anything with snapshot() -> Optional[Path]
        self.vision = vision
        self.detector = detector    # hardware.detect.Detector, or None
        self.last_info: Optional[Dict[str, Any]] = None

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "PhotoCamera":
        from hardware.detect import Detector
        return cls(Camera.from_settings(settings), Vision.from_settings(settings),
                   Detector.from_settings(settings))

    def _analyse(self, path: Path) -> Optional[Dict[str, Any]]:
        if self.detector is None:
            return None
        try:
            import cv2
            frame = cv2.imread(str(path))
            return self.detector.analyse(frame) if frame is not None else None
        except Exception as e:
            log.warning("detection on %s failed: %r", path.name, e)
            return None

    def take(self, action: Optional[Action] = None) -> Optional[Event]:
        t0 = time.monotonic()
        caused_by = action.id if action is not None else None
        try:
            path = self.camera.snapshot()
        except Exception as e:          # driver errors: treat like no frame
            log.error("snapshot failed: %r", e)
            path = None
        if path is None:
            return make_event(EventType.PHOTO_DESCRIBED,
                              PhotoDescribedData(photo_path="", description=NO_CAMERA),
                              Source.CAMERA, caused_by=caused_by)
        from hardware.detect import describe_from, hint
        info = self._analyse(path)
        self.last_info = info
        text = None
        if self.vision is not None:
            # the hint only when the detector saw something: a missed person must not
            # turn into Bonsai's "Nobody is there."
            line = hint(info) if info is not None and info["detections"] else None
            text = self.vision.describe(path, hint=line) if line else self.vision.describe(path)
        by = "bonsai" if text else None
        if text is None and info is not None:
            text, by = describe_from(info), "detector"     # vision failed: say what we know
        log.info("photo %s in %.1fs (%s): %s", path.name, time.monotonic() - t0,
                 by or "no description", text or NO_VISION)
        extra: Dict[str, Any] = {}
        if info is not None:
            extra = {"people": int(info["people"]), "detections": info["detections"]}
            if "faces" in info:
                extra["faces"] = int(info["faces"])
        data = PhotoDescribedData(photo_path=relative(path), description=text or NO_VISION,
                                  model=self.vision.model if by == "bonsai" and self.vision else None,
                                  described_by=by, **extra)
        # Source "vision" whenever there is a real description (Bonsai or the
        # detector); "camera" only for "Photo saved." (brain/look.py treats that
        # as "I can't see the door right now").
        return make_event(EventType.PHOTO_DESCRIBED, data,
                          Source.VISION if by else Source.CAMERA,
                          caused_by=action.id if action is not None else None)
