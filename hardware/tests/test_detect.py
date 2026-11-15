"""Object detection (YOLOX via OpenCV DNN) and the live-stream overlay. No camera.

    python -m unittest hardware.tests.test_detect
"""
from __future__ import annotations

import time
import unittest

import cv2
import numpy as np

from hardware.detect import (COCO, DEFAULT_MODEL, FACE_MODEL, Detection, Detector, Faces, Yolox,
                             _agnostic, describe_from, draw, hint, plural, summary)


class Decode(unittest.TestCase):
    """The YOLOX output decoding (grid + stride, objectness x class score, NMS),
    with a stub network so the maths is checked without the model."""

    def make(self, rows):
        y = Yolox.__new__(Yolox)
        from hardware.detect import _grids
        import threading
        y.size, y.score, y.nms = 416, 0.35, 0.45
        y.grid, y.stride = _grids(416)
        y._lock = threading.Lock()
        out = np.zeros((1, len(y.grid), 85), dtype=np.float32)
        for index, cls, obj, score, (dx, dy, lw, lh) in rows:
            out[0, index, :4] = (dx, dy, lw, lh)
            out[0, index, 4] = obj
            out[0, index, 5 + cls] = score

        class Net:
            def setInput(self, blob):
                assert blob.shape == (1, 3, 416, 416)

            def forward(self):
                return out
        y._net, y._ort = Net(), None
        return y

    def test_one_person_box_in_frame_pixels(self):
        # stride-8 grid cell (x=26, y=20) -> index 20*52+26; centre (26.5*8, 20.5*8) = (212, 164)
        index = 20 * 52 + 26
        y = self.make([(index, 0, 0.9, 0.9, (0.5, 0.5, np.log(10.0), np.log(20.0)))])
        frame = np.zeros((832, 832, 3), np.uint8)          # scale r = 0.5
        found = y.detect(frame)
        self.assertEqual(len(found), 1)
        d = found[0]
        self.assertEqual(d.label, "person")
        self.assertAlmostEqual(d.confidence, 0.81, places=2)
        # centre (212,164)/0.5 = (424,328); size (80,160)/0.5 = (160,320)
        self.assertEqual(d.box, (344, 168, 504, 488))

    def test_box_wholly_in_the_padding_is_dropped(self):
        # frame 416 x 208: rows 208..415 of the input are letterbox padding
        index = 40 * 52 + 26                                     # centre y = 40.5*8 = 324 > 208
        y = self.make([(index, 0, 0.9, 0.9, (0.5, 0.5, 1.0, 1.0))])
        self.assertEqual(y.detect(np.zeros((208, 416, 3), np.uint8)), [])

    def test_a_failing_detector_returns_none_not_nothing(self):
        class Raises:
            def detect(self, frame):
                raise cv2.error("no NMSBoxesBatched in this OpenCV")
        det = Detector(Raises())
        with self.assertLogs("hardware.detect", "WARNING"):
            self.assertIsNone(det.detect(np.zeros((10, 10, 3), np.uint8)))
        self.assertIsNone(det.analyse(np.zeros((10, 10, 3), np.uint8)))   # warned once a minute

    def test_low_scores_are_dropped_and_duplicates_merged(self):
        index = 20 * 52 + 26
        y = self.make([(index, 16, 0.9, 0.9, (0.5, 0.5, 2.0, 2.0)),       # dog
                       (index + 1, 15, 0.9, 0.8, (0.4, 0.5, 2.0, 2.0)),   # same animal as "cat"
                       (5, 0, 0.3, 0.5, (0.5, 0.5, 1.0, 1.0))])           # 0.15 < 0.35
        found = y.detect(np.zeros((416, 416, 3), np.uint8))
        self.assertEqual([d.label for d in found], ["dog"])


class Helpers(unittest.TestCase):
    def found(self):
        return [Detection("person", 0.91, (10, 10, 100, 400)), Detection("person", 0.62, (200, 10, 260, 120)),
                Detection("backpack", 0.55, (20, 100, 60, 160))]

    def test_summary_counts_and_best_confidence(self):
        s = summary(self.found())
        self.assertEqual(s["people"], 2)
        self.assertEqual(s["detections"], [{"label": "person", "count": 2, "confidence": 0.91},
                                           {"label": "backpack", "count": 1, "confidence": 0.55}])

    def test_hint_and_fallback_description(self):
        info = dict(summary(self.found()), faces=1, close_people=1)
        self.assertEqual(hint(info), "The object detector found: 2 people, 1 backpack. Visible faces: 1.")
        self.assertEqual(describe_from(info), "2 people are at the door, with a backpack in view.")
        self.assertEqual(describe_from({"people": 0, "detections": []}), "Nobody is there.")
        self.assertEqual(hint({"people": 0, "detections": []}),
                         "The object detector found no people and no objects.")
        self.assertEqual(describe_from({"people": 1, "detections": [{"label": "person", "count": 1,
                                                                    "confidence": 0.8}],
                                        "faces": 0, "close_people": 1}),
                         "One person is at the door. No face is visible: covered or turned away.")
        # far away: faces can't be judged, so nothing is said about them
        self.assertEqual(describe_from({"people": 1, "detections": [], "faces": 0, "close_people": 0}),
                         "One person is at the door.")
        self.assertEqual((plural("person", 3), plural("knife", 2), plural("cup", 2)),
                         ("people", "knives", "cups"))

    def test_agnostic_keeps_separate_objects(self):
        a = Detection("person", 0.9, (0, 0, 100, 200))
        b = Detection("backpack", 0.6, (10, 50, 50, 120))      # inside, but small: kept
        c = Detection("bear", 0.5, (0, 0, 100, 195))           # same box: dropped
        self.assertEqual(_agnostic([a, b, c]), [a, b])

    def test_draw_returns_a_copy_with_boxes(self):
        frame = np.zeros((240, 320, 3), np.uint8)
        out = draw(frame, [Detection("person", 0.9, (20, 30, 120, 200))])
        self.assertEqual(frame.sum(), 0)                       # original untouched
        self.assertGreater(out[30:200, 20].sum(), 0)           # box edge drawn


@unittest.skipUnless(DEFAULT_MODEL.exists(), "model file not in hardware/models")
class RealModel(unittest.TestCase):
    """The committed YOLOX-tiny + YuNet files load and run on this OpenCV."""

    def test_loads_runs_fast_and_sees_nothing_in_a_blank_frame(self):
        det = Detector.from_settings({})
        self.assertIsNotNone(det)
        self.assertIn(det.engine, ("opencv-dnn", "onnxruntime"))
        frame = np.full((720, 1280, 3), 127, np.uint8)
        self.assertEqual(det.objects.detect(frame), [])          # raises if the model is broken
        t0 = time.perf_counter()
        info = det.analyse(frame)
        self.assertIsNotNone(info)
        self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertEqual((info["people"], info["detections"]), (0, []))
        self.assertEqual(len(COCO), 80)

    @unittest.skipUnless(FACE_MODEL.exists(), "face model not in hardware/models")
    def test_face_counter_on_a_drawn_face_free_image(self):
        self.assertEqual(Faces().count(np.zeros((480, 640, 3), np.uint8)), 0)


class StreamOverlay(unittest.TestCase):
    """CameraFeed draws the detector's boxes on the live stream only while
    someone watches; snapshots (photos) stay clean."""

    def test_boxes_on_stream_not_on_photo(self):
        import shutil
        import tempfile
        from pathlib import Path
        from hardware.camera import CameraFeed
        from hardware.tests.test_media import FakeCapture, wait_until

        class Det:
            calls = 0

            def detect(self, frame):
                Det.calls += 1
                return [Detection("person", 0.9, (10, 8, 60, 64))]

        tmp = Path(tempfile.mkdtemp(prefix="faraday-det-"))
        feed = CameraFeed(photos_dir=tmp, fps=30, idle_s=5, capture_factory=lambda *a: FakeCapture(),
                          detector=Det(), detect_fps=10)
        try:
            path = feed.snapshot()                             # nobody watching: no detection
            self.assertIsNotNone(path)
            self.assertEqual(Det.calls, 0)
            photo = cv2.imread(str(path))
            with feed.viewer():
                self.assertTrue(wait_until(lambda: Det.calls > 0))
                time.sleep(0.15)
                jpg = feed.jpeg()
            live = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            # the box's left edge (x=10) is coloured on the stream, not on the photo
            diff = np.abs(live[30:60, 10].astype(int) - photo[30:60, 10].astype(int)).mean()
            self.assertGreater(diff, 30)
        finally:
            feed.stop()
            shutil.rmtree(tmp, ignore_errors=True)


class EventFields(unittest.TestCase):
    """photo_described: described_by / people / faces / detections (PLAN Q30)."""

    def test_valid_and_invalid(self):
        from shared import Source, make_event
        good = {"photo_path": "captures/photos/a.jpg", "description": "One person is at the door.",
                "described_by": "detector", "people": 1, "faces": 0,
                "detections": [{"label": "person", "count": 1, "confidence": 0.91}]}
        make_event("photo_described", good, Source.VISION)
        for key, bad in [("described_by", "llm"), ("people", -1), ("people", True), ("faces", 1.5),
                         ("detections", {"label": "person"}),
                         ("detections", [{"label": "person", "count": 0, "confidence": 0.5}]),
                         ("detections", [{"label": "", "count": 1, "confidence": 0.5}]),
                         ("detections", [{"label": "person", "count": 1, "confidence": 1.5}]),
                         ("detections", [{"label": "person", "count": 1}]),
                         ("detections", [{"label": "person", "count": 1, "confidence": 0.5, "box": [1]}])]:
            with self.subTest(key=key, bad=bad):
                with self.assertRaises(ValueError):
                    make_event("photo_described", dict(good, **{key: bad}), Source.VISION)


if __name__ == "__main__":
    unittest.main()
