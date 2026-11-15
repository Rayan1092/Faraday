"""Live object detection on the camera (non-LLM): YOLOX through OpenCV DNN.

Model: YOLOX-tiny (Megvii, Apache-2.0), COCO's 80 classes, 416x416 input,
ONNX from the official release (tag 0.1.1rc0). The file is in the repo at
hardware/models/ (see hardware/models/README.md), so nothing is downloaded at
run time and it works with Wi-Fi off. OpenCV is already a requirement; no new
package. If the model can't load, the fallback is OpenCV's built-in HOG people
detector (people count only; OpenCV 4 only, OpenCV 5 dropped it).

Faces: OpenCV's YuNet face detector (FaceDetectorYN, model in the repo, MIT)
counts faces visible to the camera ("face visible or covered" for the
description); on OpenCV 4 without the model, the built-in Haar cascade.

    det = Detector.from_settings(settings)       # None if disabled
    found = det.detect(frame_bgr)                # [Detection(label, confidence, box)]
    summary(found)  -> {"people": 1, "detections": [{"label": "person", "count": 1, "confidence": 0.91}]}
    draw(frame_bgr, found)                       # copy with labelled boxes (live stream)

False positives (owner 2026-11-15: random things showed as "cell phone", and a bag
that wasn't there was reported). People stay sensitive: any person box at SCORE
(0.35) counts at once. Every other object must be steady and confident:
- steady: seen in at least 3 of the last 5 frames (the same place, any label, so
  a phone flickering between "cell phone" and "remote" is one thing), else it
  isn't shown at all;
- named only if one label reached NAMED (0.6; setting camera.detector.object_score)
  in at least 3 of those frames, else the live stream shows it as "unknown object".
A photo is checked over itself plus the next 4 frames (about 0.4 s), and only
people and named objects go on photo_described, into the vision hint and into
the detector's own description. StreamFilter does the same for the live boxes.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

import numpy as np

from hardware.settings import REPO_ROOT

log = logging.getLogger("hardware.detect")

MODEL_DIR = REPO_ROOT / "hardware" / "models"
DEFAULT_MODEL = MODEL_DIR / "yolox_tiny.onnx"
FACE_MODEL = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
INPUT = 416
SCORE = 0.35          # lowest confidence kept
NMS = 0.45

COCO = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
)


PLURAL = {"person": "people", "knife": "knives", "mouse": "mice", "sheep": "sheep",
          "skis": "skis", "scissors": "scissors", "bus": "buses", "couch": "couches",
          "wine glass": "wine glasses", "sandwich": "sandwiches", "toothbrush": "toothbrushes"}
CLOSE = 0.3           # a person box this tall (share of the frame) is close to the camera
NAMED = 0.6           # an object (not a person) is named only at this confidence or more
                      # (camera.detector.object_score; below `score` (0.35) has no effect)
STEADY = (3, 5)       # ... and only if seen in at least 3 of the last 5 frames
MATCH_IOU = 0.3       # the same thing in two frames: boxes overlapping at least this much
UNKNOWN = "unknown object"


def plural(label: str, n: int) -> str:
    return label if n == 1 else PLURAL.get(label, label + "s")


def _quiet_opencv() -> None:
    """OpenCV 5 prints a WARN line per model load ("Targets are not supported")."""
    try:
        import cv2
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass


class Detection(NamedTuple):
    label: str
    confidence: float
    box: Tuple[int, int, int, int]      # x1, y1, x2, y2 in frame pixels


def _grids(size: int) -> Tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for stride in (8, 16, 32):
        n = size // stride
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), stride))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


class Yolox:
    """YOLOX ONNX through cv2.dnn (onnxruntime if OpenCV can't load it)."""

    def __init__(self, model: Path = DEFAULT_MODEL, size: int = INPUT,
                 score: float = SCORE, nms: float = NMS):
        import cv2
        _quiet_opencv()
        self.size, self.score, self.nms = size, score, nms
        self.grid, self.stride = _grids(size)
        self._net = None
        self._ort = None
        try:
            self._net = cv2.dnn.readNetFromONNX(str(model))
            self.engine = "opencv-dnn"
        except Exception as e:
            try:
                import onnxruntime as ort
                self._ort = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
                self._ort_input = self._ort.get_inputs()[0].name
                self.engine = "onnxruntime"
            except Exception:
                raise RuntimeError(f"cannot load {model}: {e!r}") from e
        self._lock = threading.Lock()       # cv2.dnn nets aren't thread-safe

    def _preprocess(self, frame: np.ndarray) -> Tuple[np.ndarray, float]:
        import cv2
        h, w = frame.shape[:2]
        r = min(self.size / h, self.size / w)
        resized = cv2.resize(frame, (int(w * r), int(h * r)), interpolation=cv2.INTER_LINEAR)
        padded = np.full((self.size, self.size, 3), 114, dtype=np.uint8)
        padded[:resized.shape[0], :resized.shape[1]] = resized
        blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)
        return blob, r

    def detect(self, frame: np.ndarray) -> List[Detection]:
        import cv2
        blob, r = self._preprocess(frame)
        with self._lock:
            if self._net is not None:
                self._net.setInput(blob)
                out = self._net.forward()
            else:
                out = self._ort.run(None, {self._ort_input: blob})[0]
        pred = np.asarray(out, dtype=np.float32).reshape(-1, 85)
        xy = (pred[:, :2] + self.grid) * self.stride
        wh = np.exp(pred[:, 2:4]) * self.stride
        cls = pred[:, 5:] * pred[:, 4:5]
        ids = cls.argmax(1)
        conf = cls[np.arange(len(cls)), ids]
        keep = conf >= self.score
        if not keep.any():
            return []
        xy, wh, ids, conf = xy[keep], wh[keep], ids[keep], conf[keep]
        boxes = np.concatenate([xy - wh / 2, wh], 1) / r            # x, y, w, h in frame pixels
        idx = cv2.dnn.NMSBoxesBatched(boxes.tolist(), conf.tolist(), ids.tolist(),
                                      self.score, self.nms)
        H, W = frame.shape[:2]
        found = []
        for i in np.array(idx).reshape(-1):
            x, y, bw, bh = boxes[i]
            x1, y1 = min(max(0, int(round(x))), W - 1), min(max(0, int(round(y))), H - 1)
            x2, y2 = min(max(0, int(round(x + bw))), W - 1), min(max(0, int(round(y + bh))), H - 1)
            if x2 <= x1 or y2 <= y1:
                continue                      # wholly in the padding or off the frame
            found.append(Detection(COCO[int(ids[i])], round(float(conf[i]), 2), (x1, y1, x2, y2)))
        found.sort(key=lambda d: -d.confidence)
        return _agnostic(found)


def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _agnostic(found: List[Detection], iou: float = 0.7) -> List[Detection]:
    """Drop a box that mostly overlaps a more confident box of another class
    (one dog seen as both "dog" and "cat"). Input sorted by confidence."""
    kept: List[Detection] = []
    for d in found:
        if all(k.label == d.label or _iou(k.box, d.box) < iou for k in kept):
            kept.append(d)
    return kept


class Steady(NamedTuple):
    detection: Detection         # its latest box
    name: Optional[str]          # the confident label, or None ("unknown object")
    confidence: float            # the named label's best confidence (else the latest box's)
    frame: int                   # index of the frame its latest box came from
    first: Optional[Detection]   # its box in frames[0] (the photo), or None if not seen there


SAME_OBJECT_IOU = 0.5   # a second box this much on top of a matched one, same frame: same object
WEAPONS = {"knife", "scissors", "baseball bat"}   # never hidden behind another label (brain/threat.py)


def steady_objects(frames: Sequence[Sequence[Detection]], need: int = STEADY[0],
                   named: float = NAMED, iou: float = MATCH_IOU) -> List[Steady]:
    """Objects other than people seen in at least `need` of `frames` (oldest
    first). The same object in two frames = boxes overlapping at least `iou`,
    whatever the label; the best overlaps are matched first, so a steady object
    keeps its own box when something new appears next to it. A second box on an
    object in the same frame (one phone as "cell phone" and "remote" at once)
    only votes for a label. Named only if one label reached `named` confidence in
    at least `need` frames. People are never in the result (callers keep them)."""
    tracks: List[Dict[str, Any]] = []
    for fi, found in enumerate(frames):
        objs = [d for d in found if d.label != "person"]
        pairs = sorted(((max(_iou(b, d.box) for b in t["recent"]), ti, di)
                        for ti, t in enumerate(tracks) for di, d in enumerate(objs)), reverse=True)
        primary: Dict[int, int] = {}                 # detection index -> track, one each
        taken = set()
        for overlap, ti, di in pairs:
            if overlap < iou:
                break
            if ti not in taken and di not in primary:
                primary[di] = ti
                taken.add(ti)
        votes: Dict[int, int] = {}                   # extra boxes on an object seen this frame
        for di, d in enumerate(objs):                # most confident first (detect() sorts)
            if di in primary:
                continue
            on = [(_iou(objs[dj].box, d.box), tj) for dj, tj in primary.items()]
            best = max(on, default=(0.0, None))
            if best[0] >= SAME_OBJECT_IOU:
                votes[di] = best[1]
                continue
            tracks.append({"latest": d, "frame": -1, "frames": set(), "named": {}, "boxes": {},
                           "recent": [], "first": {}})
            primary[di] = len(tracks) - 1
        for di, ti in list(primary.items()) + list(votes.items()):
            d, t = objs[di], tracks[ti]
            if di in primary:
                t["latest"], t["frame"] = d, fi
                t["frames"].add(fi)
                t["boxes"][fi] = d
                t["recent"] = [d.box]                # its boxes in its latest frame ...
            else:
                t["recent"].append(d.box)            # ... votes too, for the next frame's match
            if fi == 0:
                t["first"].setdefault(d.label, d)    # its photo box for each label
            if d.confidence >= named:
                hits, top = t["named"].get(d.label, (set(), 0.0))
                hits.add(fi)
                t["named"][d.label] = (hits, max(top, d.confidence))
    out: List[Steady] = []
    for t in tracks:
        if len(t["frames"]) < need:
            continue                                  # a flicker: not shown at all
        # a weapon label that qualifies wins (a knife must never hide behind "cell phone");
        # then the label seen most often, then the most confident
        names = sorted(((label in WEAPONS, len(h), top, label) for label, (h, top) in t["named"].items()
                        if len(h) >= need), reverse=True)
        first = t["boxes"].get(0)
        if names:
            first = t["first"].get(names[0][3], first) if first is not None else None
            out.append(Steady(t["latest"], names[0][3], round(names[0][2], 2), t["frame"], first))
        else:
            out.append(Steady(t["latest"], None, t["latest"].confidence, t["frame"], first))
    return out


class StreamFilter:
    """The live stream's boxes: people at once (sensitive); other objects only once
    steady over the last 5 detection frames, named only when confident, else
    "unknown object"."""

    def __init__(self, window: int = STEADY[1], need: int = STEADY[0], named: float = NAMED):
        from collections import deque
        self.need, self.named = need, named
        self.frames: Any = deque(maxlen=window)

    def reset(self) -> None:
        self.frames.clear()

    def update(self, found: Sequence[Detection]) -> List[Detection]:
        self.frames.append(list(found))
        latest = len(self.frames) - 1
        shown = [d for d in found if d.label == "person"]
        now = [s for s in steady_objects(list(self.frames), self.need, self.named)
               if s.frame == latest]                  # not in this frame: no box now
        named_boxes = [s.detection.box for s in now if s.name]
        for s in now:
            if s.name is not None:
                shown.append(s.detection._replace(label=s.name, confidence=s.confidence))
            elif not any(_iou(s.detection.box, b) >= MATCH_IOU for b in named_boxes):
                shown.append(s.detection._replace(label=UNKNOWN))   # (not a 2nd box on a named thing)
        return shown


class HogPeople:
    """Fallback: OpenCV's built-in HOG people detector (no model file)."""

    engine = "opencv-hog"

    def __init__(self) -> None:
        import cv2
        if not hasattr(cv2, "HOGDescriptor"):
            raise RuntimeError("no HOG in this OpenCV build (OpenCV 5 moved it out)")
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self._lock = threading.Lock()

    def detect(self, frame: np.ndarray) -> List[Detection]:  # HogPeople
        import cv2
        h, w = frame.shape[:2]
        scale = min(1.0, 640.0 / max(h, w))
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
        with self._lock:
            rects, weights = self._hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8),
                                                        scale=1.05)
        found = []
        for (x, y, bw, bh), wt in zip(rects, np.array(weights).reshape(-1)):
            conf = float(min(0.99, max(0.0, wt / 2.0)))
            if conf < 0.3:
                continue
            found.append(Detection("person", round(conf, 2),
                                   (int(x / scale), int(y / scale), int((x + bw) / scale), int((y + bh) / scale))))
        return found


class Faces:
    """How many faces are visible: YuNet (OpenCV's FaceDetectorYN, model in
    hardware/models, MIT), else the built-in Haar cascade on OpenCV 4 (OpenCV 5
    dropped Haar and HOG from the main package)."""

    def __init__(self, model: Path = FACE_MODEL, score: float = 0.8) -> None:
        import cv2
        _quiet_opencv()
        self._yn = None
        self._cascade = None
        if hasattr(cv2, "FaceDetectorYN") and model.exists():
            self._yn = cv2.FaceDetectorYN.create(str(model), "", (320, 320), score, 0.3, 5000)
            self.engine = "yunet"
        elif hasattr(cv2, "CascadeClassifier"):
            path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            self._cascade = cv2.CascadeClassifier(str(path))
            if self._cascade.empty():
                raise RuntimeError(f"cannot load {path}")
            self.engine = "haar"
        else:
            raise RuntimeError("no face detector in this OpenCV build")
        self._lock = threading.Lock()

    def count(self, frame: np.ndarray) -> int:
        import cv2
        h, w = frame.shape[:2]
        scale = min(1.0, 640.0 / max(h, w))
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
        with self._lock:
            if self._yn is not None:
                self._yn.setInputSize((small.shape[1], small.shape[0]))
                _, faces = self._yn.detect(small)
                return 0 if faces is None else int(len(faces))
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                                   minSize=(36, 36))
        return int(len(faces))


class Detector:
    """Objects (YOLOX, or HOG people as the fallback) plus visible faces."""

    def __init__(self, objects: Any, faces: Optional[Faces] = None, named: float = NAMED):
        self.objects, self.faces = objects, faces
        self.named = named                     # objects are named only at this confidence or more
        self.engine = getattr(objects, "engine", type(objects).__name__)
        self.last_ms = 0.0
        self._warned_at = 0.0

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> Optional["Detector"]:
        cfg = settings.get("camera", {}).get("detector", {}) or {}
        if cfg.get("enabled", True) is False:
            return None
        model = Path(cfg.get("model") or DEFAULT_MODEL)
        if not model.is_absolute():
            model = REPO_ROOT / model
        objects: Any
        try:
            objects = Yolox(model, int(cfg.get("input", INPUT)), float(cfg.get("score", SCORE)),
                            float(cfg.get("nms", NMS)))
        except Exception as e:
            log.warning("object detector unavailable (%s); using HOG people detection", e)
            try:
                objects = HogPeople()
            except Exception:
                log.exception("no people detector either")
                return None
        try:
            faces: Optional[Faces] = Faces()
        except Exception as e:
            log.warning("face counter unavailable: %r", e)
            faces = None
        raw = cfg.get("object_score", NAMED)
        try:
            named = float(raw)
            if isinstance(raw, bool) or not 0.0 <= named <= 1.0:      # NaN fails this too
                raise ValueError(raw)
        except (TypeError, ValueError):
            log.warning("camera.detector.object_score must be a number from 0 to 1, not %r; "
                        "using %.1f", raw, NAMED)
            named = NAMED
        return cls(objects, faces, named)

    def detect(self, frame: np.ndarray) -> Optional[List[Detection]]:
        """Detections, or None if the detector failed (never "nobody" by mistake)."""
        t0 = time.perf_counter()
        try:
            found = self.objects.detect(frame)
        except Exception as e:
            now = time.monotonic()
            if now - self._warned_at > 60:          # once a minute, not 5 times a second
                self._warned_at = now
                log.warning("detection failed: %r", e)
            return None
        finally:
            self.last_ms = (time.perf_counter() - t0) * 1000
        return found

    def analyse(self, frame: np.ndarray) -> Optional[Dict[str, Any]]:
        """For one image: see analyse_frames."""
        return self.analyse_frames([frame])

    def analyse_frames(self, frames: Sequence[np.ndarray],
                       need: int = STEADY[0]) -> Optional[Dict[str, Any]]:
        """For a photo: frames[0] is the photo, the rest the frames right after it.
        People, close people and faces come from the photo (sensitive, as before).
        Other objects count only if they are in the photo, steady (in at least
        `need` of the frames, or all of them when fewer came) and confident
        (self.named). Unknown objects are never reported. None if the detector
        failed on the photo."""
        if not len(frames):
            return None
        first = self.detect(frames[0])
        if first is None:
            return None
        per_frame = [first]
        for frame in frames[1:]:
            found = self.detect(frame)
            if found is not None:
                per_frame.append(found)
        people = [d for d in first if d.label == "person"]
        # only what is in the photo itself (and steady across the frames), at its photo box
        objects = [s.first._replace(label=s.name, confidence=s.confidence)
                   for s in steady_objects(per_frame, min(need, len(per_frame)), self.named)
                   if s.name and s.first is not None]
        out = summary(people + objects)
        h = frames[0].shape[0]
        out["close_people"] = sum(1 for d in people if (d.box[3] - d.box[1]) >= CLOSE * h)
        if self.faces is not None:
            try:
                out["faces"] = self.faces.count(frames[0])
            except Exception as e:
                log.warning("face count failed: %r", e)
        out["boxes"] = people + objects
        out["frames"] = len(per_frame)
        return out


def summary(found: Sequence[Detection]) -> Dict[str, Any]:
    """{"people": n, "detections": [{"label", "count", "confidence" (highest)}]},
    most confident class first."""
    groups: Dict[str, List[float]] = {}
    for d in found:
        groups.setdefault(d.label, []).append(d.confidence)
    dets = [{"label": k, "count": len(v), "confidence": round(max(v), 2)} for k, v in groups.items()]
    dets.sort(key=lambda d: (-d["confidence"], d["label"]))
    return {"people": len(groups.get("person", [])), "detections": dets}


def hint(info: Dict[str, Any]) -> str:
    """One line for the vision prompt: what the detector saw (grounding)."""
    dets = info.get("detections") or []
    if not dets:
        return "The object detector found no people and no objects."
    parts = [f"{d['count']} {plural(d['label'], d['count'])}" for d in dets[:6]]
    line = "The object detector found: " + ", ".join(parts) + "."
    if info.get("close_people") and "faces" in info:
        line += f" Visible faces: {info['faces']}."
    return line


def describe_from(info: Dict[str, Any]) -> str:
    """A factual description from detections alone (when vision fails)."""
    people = int(info.get("people") or 0)
    things = [d for d in (info.get("detections") or []) if d["label"] != "person"]
    names = ", ".join(f"a {d['label']}" if d["count"] == 1 else f"{d['count']} {plural(d['label'], d['count'])}"
                      for d in things[:3])
    if people == 0:
        return "Nobody is there." + (f" I can see {names}." if names else "")
    who = "One person is" if people == 1 else f"{people} people are"
    line = f"{who} at the door"
    if names:
        line += f", with {names} in view"
    line += "."
    faces = info.get("faces")
    if faces is not None and info.get("close_people"):     # far away: faces too small to tell
        if faces == 0:
            line += " No face is visible: covered or turned away."
        elif faces >= people:
            line += " Their face is visible." if people == 1 else " Their faces are visible."
    return line


COLORS = {"person": (60, 200, 255), UNKNOWN: (170, 170, 170)}


def draw(frame: np.ndarray, found: Sequence[Detection]) -> np.ndarray:
    """A copy of the frame with labelled boxes (for the live stream only)."""
    import cv2
    img = frame.copy()
    width = img.shape[1]
    for d in found:
        x1, y1, x2, y2 = d.box
        color = COLORS.get(d.label, (80, 230, 120))
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        text = d.label if d.label == UNKNOWN else f"{d.label} {int(d.confidence * 100)}%"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        ty = max(th + 4, y1)
        tx = max(0, min(x1, width - tw - 6))             # a box at the right edge: label stays in
        cv2.rectangle(img, (tx, ty - th - 4), (tx + tw + 6, ty), color, -1)
        cv2.putText(img, text, (tx + 3, ty - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1,
                    cv2.LINE_AA)
    return img


def main(argv: Optional[List[str]] = None) -> int:
    """python -m hardware.detect [image ...]: detections + timing (no camera)."""
    import argparse
    import cv2
    from hardware.settings import load_settings
    ap = argparse.ArgumentParser(prog="python -m hardware.detect")
    ap.add_argument("images", nargs="*")
    a = ap.parse_args(argv)
    det = Detector.from_settings(load_settings())
    if det is None:
        print("detector disabled (camera.detector.enabled is false)")
        return 1
    print(f"engine: {det.engine}")
    images = a.images or [str(p) for p in sorted((REPO_ROOT / "captures" / "photos").glob("*.jpg"))[-1:]]
    if not images:
        print("no image given and no photo in captures/photos")
        return 1
    for path in images:
        frame = cv2.imread(path)
        if frame is None:
            print(f"{path}: cannot read")
            continue
        raw = det.detect(frame) or []           # also the warm-up
        info = det.analyse(frame)
        print(f"{path}: {det.last_ms:.0f} ms  people={info['people']}  faces={info.get('faces')}")
        for d in info["detections"]:
            print(f"   {d['label']:<14} x{d['count']}  {d['confidence']:.2f}")
        dropped = [d for d in raw if d.label != "person" and d.confidence < det.named]
        if dropped:
            print("   not reported (under %.2f, camera.detector.object_score): %s" % (det.named, ", ".join(
                f"{d.label} {d.confidence:.2f}" for d in dropped)))
        print(f"   says: {describe_from(info)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
