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

    def test_outlet_states_from_the_archived_log(self):
        from shared import Action
        from integration.fresh import outlet_states_in
        (self.root / "logs").mkdir()
        lines = [Action("outlet_on", "rules", {"outlet": "porch_light"}).to_json(),
                 Action("outlet_on", "rules", {"outlet": "living_room_lamp"}).to_json(),
                 Action("outlet_off", "passphrase" if False else "rules", {"outlet": "living_room_lamp"}).to_json(),
                 "not json"]
        (self.root / "logs" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        dest = archive_run({}, root=self.root)
        self.assertEqual(outlet_states_in(dest / "logs" / "events.jsonl"),
                         {"porch_light": "outlet_on", "living_room_lamp": "outlet_off"})
        self.assertEqual(outlet_states_in(self.root / "nope.jsonl"), {})

    def test_same_second_twice_does_not_collide(self):
        for _ in range(2):
            (self.root / "logs").mkdir(exist_ok=True)
            (self.root / "logs" / "events.jsonl").write_text("{}\n", encoding="utf-8")
            archive_run({}, root=self.root, now=datetime(2026, 11, 15, 1, 2, 3))
        self.assertEqual(sorted(p.name for p in (self.root / "old_runs").iterdir()),
                         ["20261115-010203", "20261115-010203-2"])


class Base(unittest.TestCase):
    lights_off = False
    prior: list = []                    # log entries from an earlier run

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="faraday-start-"))
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0)
        s["camera"]["alarm_record_s"] = 0.5
        s["paths"] = {"captures": str(self.tmp / "captures"), "logs": str(self.tmp / "logs")}
        self.settings = s
        self.said = []
        self.fl = FakeFlipper(s, charge_poll_s=0)
        if self.prior:
            (self.tmp / "events.jsonl").write_text(
                "".join(a.to_json() + "\n" for a in self.prior), encoding="utf-8")
        self.app = App(s, self.fl, camera=StandInCamera(), use_llm=False, mode=Mode.HOME,
                       event_log=EventLog(self.tmp / "events.jsonl", s["security"]["passphrase"]),
                       rules=RuleStore(self.tmp / "rules.json"), dashboard=True, port=0,
                       out=self.said.append, lights_off_at_start=self.lights_off)

    def tearDown(self):
        self.app.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


def acts(*pairs):
    from shared import Action
    return [Action(kind, "rules", {"outlet": outlet}) for kind, outlet in pairs]


class LightsOffAtStart(Base):
    lights_off = True

    def test_every_outlet_off_logged_and_dashboard_shows_off(self):
        """First run ever (nothing logged): off to every outlet, as the owner asked."""
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


class LightsAlreadyOff(Base):
    """Review of #48 (HIGH): the outlets toggle on a repeated code, so an off sent to a
    lamp that was already off (the last run ended with lights off) turned it ON."""
    lights_off = True
    prior = acts(("outlet_on", "porch_light"), ("outlet_off", "porch_light"),
                 ("outlet_off", "living_room_lamp"), ("outlet_off", "spare"))

    def test_nothing_sent_and_the_lamps_stay_off(self):
        self.app.start()
        self.assertTrue(self.fl.connected.wait(5))
        time.sleep(0.5)
        self.assertEqual(self.fl.device.tx, [])
        self.assertEqual(self.fl.device.outlet_lit, {1: False, 2: False, 3: False})
        self.assertTrue(any("already off" in s for s in self.said))


class LampLeftOn(Base):
    lights_off = True
    prior = acts(("outlet_on", "porch_light"), ("outlet_off", "living_room_lamp"),
                 ("outlet_off", "spare"))

    def test_only_the_lamp_that_was_on_gets_off(self):
        self.fl.device.outlet_lit[1] = True                           # left on by the last run
        self.app.start()
        self.assertTrue(self.fl.connected.wait(5))
        self.assertTrue(wait_until(lambda: self.fl.device.tx == ["outlet1_off"]))
        time.sleep(0.3)
        self.assertEqual(self.fl.device.tx, ["outlet1_off"])
        self.assertEqual(self.fl.device.outlet_lit, {1: False, 2: False, 3: False})


class RestartRemembers(Base):
    """Without lights-off: a restart still knows the porch light is on, so the
    alarm's outlet_on isn't sent again (it would toggle the light off)."""
    prior = acts(("outlet_on", "porch_light"))

    def test_seeded_from_the_log(self):
        self.assertEqual(self.app.outlet_states(), {"porch_light": "outlet_on"})
        self.app.start()
        self.assertFalse(self.app.control("porch_light", True))
        self.assertTrue(any("already on" in s for s in self.said))

    def test_failed_old_send_does_not_wipe_a_newer_state(self):
        from concurrent.futures import Future
        from shared import Action
        fut = Future()
        fut.set_result((False, ["error"]))
        self.app._check_send(fut, Action("outlet_off", "system", {"outlet": "porch_light"}))
        self.assertEqual(self.app.outlet_states(), {"porch_light": "outlet_on"})


class NotForTests(Base):
    def test_off_by_default_for_app_level_tests(self):
        self.app.start()
        self.assertTrue(self.fl.connected.wait(5))
        time.sleep(0.5)
        self.assertEqual(self.fl.device.tx, [])


class CameraOff(Base):
    """Review of #56: camera.enabled false gives no camera at all (no stand-in). A
    take_photo is still answered at once, so nothing waits for a photo forever."""

    def test_take_photo_answered_with_camera_unavailable(self):
        from shared import Action
        self.app.camera = None
        self.app.start()
        self.app._on_action(Action("take_photo", "dashboard", {"reason": "who's at the door"}, id="act_look0001"))
        self.assertTrue(wait_until(lambda: self.events("photo_described")))
        ev = self.events("photo_described")[-1]
        self.assertEqual((ev["data"]["photo_path"], ev["data"]["description"], ev["source"], ev["caused_by"]),
                         ("", "Camera unavailable.", "camera", "act_look0001"))
        self.assertTrue(wait_until(lambda: not self.app.speech_busy()))

    def events(self, typ):
        return [e for e in self.app.pipeline.log.recent(200) if e["type"] == typ]


class FakeVideo:
    """Stands in for CameraFeed.record(): writes a few frames."""

    def __init__(self, delay=0.3):
        self.calls, self.delay = 0, delay

    def record(self, seconds, out_dir, stop=None):
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

    def feed(self, tmp, **kw):
        from hardware.camera import CameraFeed
        from hardware.tests.test_media import FakeCapture
        return CameraFeed(photos_dir=tmp, fps=20, idle_s=5, capture_factory=lambda *a: FakeCapture(), **kw)

    def test_stop_ends_early_no_detector_and_old_failure_ignored(self):
        tmp = Path(tempfile.mkdtemp(prefix="faraday-rec2-"))

        class Det:
            calls = 0

            def detect(self, frame):
                Det.calls += 1
                return []
        feed = self.feed(tmp, detector=Det(), detect_fps=20)
        feed.failed = True                                       # an earlier open failed
        stop = threading.Event()
        threading.Timer(0.6, stop.set).start()
        try:
            t0 = time.monotonic()
            info = feed.record(5.0, tmp / "alarm-y", stop=stop)
            self.assertLess(time.monotonic() - t0, 2.0)            # stopped early
            self.assertGreaterEqual(info["frames"], 3)              # the old failure didn't stop it
            self.assertEqual(Det.calls, 0)                          # recording isn't watching
        finally:
            feed.stop()
            shutil.rmtree(tmp, ignore_errors=True)

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
