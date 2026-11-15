"""The photo bench (brain/bench_photos.py) with stand-ins for the camera side."""
import tempfile
import unittest
from pathlib import Path

from shared import ThreatCategory as C

from brain import bench_photos, threat

PERSON = [{"label": "person", "count": 1, "confidence": 0.9}]
SCENES = {   # file -> (detector summary, the vision model's sentence)
    "nobody-motion-1.jpg": ({"people": 0, "detections": [], "faces": 0}, "Nobody is there."),
    "delivery-doorbell-1.jpg": ({"people": 1, "detections": PERSON, "faces": 1},
                                "One person holding a cardboard box at the door, face visible."),
    "visitor-doorbell-1.jpg": ({"people": 1, "detections": PERSON, "faces": 1},
                               "A woman in a red coat waving at the camera."),
    "suspicious-motion-1.jpg": ({"people": 1, "detections": PERSON, "faces": 0},
                                "A person with the hood up and face covered at the door."),
    "visitor-doorbell-2.jpg": ({"people": 1, "detections": PERSON, "faces": 1}, None),   # vision failed
    "notes.txt": ({}, ""),
}


class PhotoBench(unittest.TestCase):
    def test_names(self):
        self.assertEqual(bench_photos.parse_name(Path("nobody-motion-1.jpg")), ("nobody", "motion"))
        self.assertEqual(bench_photos.parse_name(Path("Delivery-Doorbell-3.PNG")), ("delivery", "doorbell"))
        self.assertIsNone(bench_photos.parse_name(Path("unknown-motion-1.jpg")))
        self.assertIsNone(bench_photos.parse_name(Path("door.jpg")))

    def test_runs_the_app_path_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            photos = []
            for name in SCENES:
                (Path(tmp) / name).write_bytes(b"x")
                photos.append(Path(tmp) / name)
            rows = bench_photos.run(sorted(photos), lambda p: SCENES[p.name][0], lambda p: SCENES[p.name][1],
                                    threat.ThreatAssessor(None), lambda info: "One person is at the door.")
        got = {r["photo"]: r["label"] for r in rows}
        self.assertEqual(got, {"delivery-doorbell-1.jpg": C.DELIVERY, "nobody-motion-1.jpg": C.NOBODY,
                               "suspicious-motion-1.jpg": C.SUSPICIOUS, "visitor-doorbell-1.jpg": C.VISITOR,
                               "visitor-doorbell-2.jpg": C.VISITOR})          # vision failed: the detector's facts
        md = bench_photos.to_markdown(rows, {"model": "fake"})
        self.assertIn("right: **5/5** (100%)", md)
        self.assertIn("face visible, rang the doorbell", md)

    def test_needs_named_photos(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(bench_photos.main([tmp]), 2)


if __name__ == "__main__":
    unittest.main()
