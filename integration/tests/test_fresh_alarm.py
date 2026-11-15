"""--fresh archiving, lights off at every start, alarm recording. No hardware.

    python -m unittest integration.tests.test_fresh_alarm
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from shared import Mode, make_event, validate_event

from hardware.flipper.fake import FakeFlipper
from hardware.settings import CONFIG_DIR
from integration.app import App
from integration.fresh import archive_run
from integration.standins import StandInCamera
from integration.tests.test_app import wait_until
from log import EventLog
from rules import RuleStore


class Fresh(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="faraday-fresh-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_moves_logs_and_captures_to_old_runs(self):
        (self.root / "logs").mkdir()
        (self.root / "logs" / "events.jsonl").write_text("{}\n", encoding="utf-8")
        (self.root / "logs" / "instructions.json").write_text("[]", encoding="utf-8")
        (self.root / "captures" / "photos").mkdir(parents=True)
        (self.root / "captures" / "photos" / "a.jpg").write_bytes(b"jpg")
        (self.root / "cache").mkdir()                                   # TTS cache stays
        (self.root / "cache" / "x.wav").write_bytes(b"wav")
        dest = archive_run({}, root=self.root, now=datetime(2026, 11, 15, 1, 2, 3))
        self.assertEqual(dest, self.root / "old_runs" / "20261115-010203")
        self.assertTrue((dest / "logs" / "events.jsonl").exists())
        self.assertTrue((dest / "logs" / "instructions.json").exists())
        self.assertTrue((dest / "captures" / "photos" / "a.jpg").exists())
        self.assertEqual(list((self.root / "logs").iterdir()), [])      # empty, still there
        self.assertEqual(list((self.root / "captures").iterdir()), [])
        self.assertTrue((self.root / "cache" / "x.wav").exists())

    def test_nothing_to_move(self):
        self.assertIsNone(archive_run({}, root=self.root))
        (self.root / "logs").mkdir()
        self.assertIsNone(archive_run({}, root=self.root))              # empty folder only

    def test_same_second_twice_does_not_collide(self):
        for _ in range(2):
            (self.root / "logs").mkdir(exist_ok=True)
            (self.root / "logs" / "events.jsonl").write_text("{}\n", encoding="utf-8")
            archive_run({}, root=self.root, now=datetime(2026, 11, 15, 1, 2, 3))
        self.assertEqual(sorted(p.name for p in (self.root / "old_runs").iterdir()),
                         ["20261115-010203", "20261115-010203-2"])


class Base(unittest.TestCase):
    lights_off = False

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="faraday-start-"))
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0)
        s["camera"]["alarm_record_s"] = 0.5
        s["paths"] = {"captures": str(self.tmp / "captures"), "logs": str(self.tmp / "logs")}
        self.said = []
        self.fl = FakeFlipper(s, charge_poll_s=0)
        self.app = App(s, self.fl, camera=StandInCamera(), use_llm=False, mode=Mode.HOME,
                       event_log=EventLog(self.tmp / "events.jsonl", s["security"]["passphrase"]),
                       rules=RuleStore(self.tmp / "rules.json"), dashboard=True, port=0,
                       out=self.said.append, lights_off_at_start=self.lights_off)

    def tearDown(self):
        self.app.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class LightsOffAtStart(Base):
    lights_off = True

    def test_every_outlet_off_logged_and_dashboard_shows_off(self):
        import urllib.request
        self.app.start()
        self.assertTrue(self.fl.connected.wait(5))
        self.assertTrue(wait_until(lambda: len(self.fl.device.tx) == 3))
        self.assertEqual(self.fl.device.tx, ["outlet1_off", "outlet2_off", "outlet3_off"])
        acts = [e for e in self.app.pipeline.log.recent(50) if e["kind"] == "action"]
        self.assertEqual({(a["type"], a["source"]) for a in acts}, {("outlet_off", "system")})
        with urllib.request.urlopen(self.app.board.url + "api/state", timeout=5) as r:
            lights = {l["id"]: l["on"] for l in json.loads(r.read())["lights"]}
        self.assertFalse(any(lights.values()))
        self.assertFalse(self.app.control("porch_light", False))      # already off: not resent
        self.assertTrue(self.app.control("porch_light", True))


class NotForTests(Base):
    def test_off_by_default_for_app_level_tests(self):
        self.app.start()
        self.assertTrue(self.fl.connected.wait(5))
        time.sleep(0.5)
        self.assertEqual(self.fl.device.tx, [])


class FakeVideo:
    """Stands in for CameraFeed.record(): writes a few frames."""

    def __init__(self, delay=0.3):
        self.calls, self.delay = 0, delay

    def record(self, seconds, out_dir):
        self.calls += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 4):
            (out_dir / f"frame-{i:04d}.jpg").write_bytes(b"jpg")
        time.sleep(self.delay)
        return {"path": out_dir, "frames": 3, "duration_s": 0.2, "video_path": None}


class AlarmRecording(Base):
    def alarm(self):
        return make_event("alert_changed", {"level": "alarm", "previous": "warning", "reason": "test"},
                          "modes")

    def test_alarm_saves_frames_and_logs_recording_saved(self):
        self.app.video = FakeVideo()
        self.app.start()
        alert = self.alarm()
        self.app._on_pipeline_event(alert)
        self.app._on_pipeline_event(self.alarm())          # again while recording: ignored
        self.assertTrue(wait_until(lambda: any(e["type"] == "recording_saved"
                                               for e in self.app.pipeline.log.recent(50))))
        rec = next(e for e in self.app.pipeline.log.recent(50) if e["type"] == "recording_saved")
        self.assertEqual(validate_event(rec), [])
        self.assertEqual((rec["source"], rec["caused_by"], rec["data"]["frames"], rec["data"]["reason"]),
                         ("camera", alert.id, 3, "alarm"))
        self.assertTrue(rec["data"]["path"].endswith("/"))
        self.assertIn("alarm-", rec["data"]["path"])
        self.assertEqual(self.app.video.calls, 1)
        self.assertTrue(any("Recording saved" in s for s in self.said))

    def test_no_camera_no_recording(self):
        self.app.start()
        self.app._on_pipeline_event(self.alarm())
        time.sleep(0.3)
        self.assertFalse(any(e["type"] == "recording_saved" for e in self.app.pipeline.log.recent(50)))

    def test_warning_does_not_record(self):
        self.app.video = FakeVideo()
        self.app.start()
        self.app._on_pipeline_event(make_event("alert_changed", {"level": "warning", "previous": "normal"},
                                               "modes"))
        time.sleep(0.3)
        self.assertEqual(self.app.video.calls, 0)


class FeedRecord(unittest.TestCase):
    """CameraFeed.record on a fake capture: JPEG frames (+ clip.avi if OpenCV can)."""

    def test_records_frames_for_the_time_given(self):
        from hardware.camera import CameraFeed
        from hardware.tests.test_media import FakeCapture
        tmp = Path(tempfile.mkdtemp(prefix="faraday-rec-"))
        feed = CameraFeed(photos_dir=tmp, fps=20, idle_s=5, capture_factory=lambda *a: FakeCapture())
        try:
            info = feed.record(0.6, tmp / "alarm-x")
        finally:
            feed.stop()
        try:
            self.assertGreaterEqual(info["frames"], 5)
            self.assertEqual(len(list((tmp / "alarm-x").glob("frame-*.jpg"))), info["frames"])
            self.assertLessEqual(info["duration_s"], 0.8)
            if info["video_path"] is not None:
                self.assertGreater(info["video_path"].stat().st_size, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
