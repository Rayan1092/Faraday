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
            x1, y1 = max(0, int(round(x))), max(0, int(round(y)))
            x2, y2 = min(W - 1, int(round(x + bw))), min(H - 1, int(round(y + bh)))
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

    def detect(self, frame: np.ndarray) -> List[Detection]:
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

    def __init__(self, objects: Any, faces: Optional[Faces] = None):
        self.objects, self.faces = objects, faces
        self.engine = getattr(objects, "engine", type(objects).__name__)
        self.last_ms = 0.0

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
        return cls(objects, faces)

    def detect(self, frame: np.ndarray) -> List[Detection]:
        t0 = time.perf_counter()
        try:
            found = self.objects.detect(frame)
        except Exception as e:
            log.warning("detection failed: %r", e)
            found = []
        self.last_ms = (time.perf_counter() - t0) * 1000
        return found

    def analyse(self, frame: np.ndarray) -> Dict[str, Any]:
        """For a photo: summary of objects + people + visible faces."""
        found = self.detect(frame)
        out = summary(found)
        h = frame.shape[0]
        out["close_people"] = sum(1 for d in found if d.label == "person"
                                  and (d.box[3] - d.box[1]) >= CLOSE * h)
        if self.faces is not None:
            try:
                out["faces"] = self.faces.count(frame)
            except Exception as e:
                log.warning("face count failed: %r", e)
        out["boxes"] = found
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


COLORS = {"person": (60, 200, 255)}


def draw(frame: np.ndarray, found: Sequence[Detection]) -> np.ndarray:
    """A copy of the frame with labelled boxes (for the live stream only)."""
    import cv2
    img = frame.copy()
    for d in found:
        x1, y1, x2, y2 = d.box
        color = COLORS.get(d.label, (80, 230, 120))
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        text = f"{d.label} {int(d.confidence * 100)}%"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        ty = max(th + 4, y1)
        cv2.rectangle(img, (x1, ty - th - 4), (x1 + tw + 6, ty), color, -1)
        cv2.putText(img, text, (x1 + 3, ty - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1,
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
        det.detect(frame)                       # warm-up
        info = det.analyse(frame)
        print(f"{path}: {det.last_ms:.0f} ms  people={info['people']}  faces={info.get('faces')}")
        for d in info["detections"]:
            print(f"   {d['label']:<14} x{d['count']}  {d['confidence']:.2f}")
        print(f"   says: {describe_from(info)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
