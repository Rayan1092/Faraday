"""Camera: OpenCV snapshot -> captures/photos/<time>.jpg, then Bonsai vision.

    cam = PhotoCamera.from_settings(settings)
    event = cam.take(take_photo_action)      # photo_described event, or None

If vision fails or times out the photo is kept and the description is
"Photo saved." (source "camera"), so the dashboard still shows it. If the
camera itself fails there is no photo and take() returns None.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from shared import Action, Event, EventType, PhotoDescribedData, Source, make_event

from hardware.settings import REPO_ROOT
from hardware.vision import Vision

log = logging.getLogger("hardware.camera")
NO_VISION = "Photo saved."
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


def relative(path: Path) -> str:
    """Repo-relative, forward slashes (FORMATS.md), e.g. captures/photos/x.jpg."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


class PhotoCamera:
    """take_photo -> snapshot -> Bonsai description -> photo_described event."""

    def __init__(self, camera: Any, vision: Optional[Vision]):
        self.camera = camera        # anything with snapshot() -> Optional[Path]
        self.vision = vision

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "PhotoCamera":
        return cls(Camera.from_settings(settings), Vision.from_settings(settings))

    def take(self, action: Optional[Action] = None) -> Optional[Event]:
        t0 = time.monotonic()
        path = self.camera.snapshot()
        if path is None:
            return None
        text = self.vision.describe(path) if self.vision is not None else None
        log.info("photo %s in %.1fs: %s", path.name, time.monotonic() - t0, text or NO_VISION)
        data = PhotoDescribedData(photo_path=relative(path), description=text or NO_VISION,
                                  model=self.vision.model if text and self.vision else None)
        return make_event(EventType.PHOTO_DESCRIBED, data,
                          Source.VISION if text else Source.CAMERA,
                          caused_by=action.id if action is not None else None)
