"""Real photos appear as soon as they're saved; stage-backup sensor buttons."""
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from shared import ActionType, Source, make_action

from brain import Brain
from brain.pipeline import Pipeline
from dashboard import Dashboard
from dashboard.photos import PhotoWatcher
from dashboard.state import build_state
from log import EventLog
from modes import ModeState
from rules import RuleStore
from sim import events

SECRET = "silver otter lantern"


class PhotoView(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now().astimezone()

    def described(self, path, seconds_ago, text="A courier with a box."):
        e = events.photo_described(None, text).to_dict()
        e["data"]["photo_path"] = path
        e["timestamp"] = (self.now - timedelta(seconds=seconds_ago)).isoformat(timespec="milliseconds")
        return e

    def test_new_file_shows_before_its_description(self):
        newest = {"path": "captures/photos/new.jpg", "taken": self.now}
        p = build_state([self.described("captures/photos/old.jpg", 60)], "away", "normal",
                        newest_photo=newest)["photo"]
        self.assertEqual(p["url"], "/media/captures/photos/new.jpg")
        self.assertEqual(p["description"], "Looking at the photo...")

    def test_description_attaches_to_its_file(self):
        newest = {"path": "captures/photos/new.jpg", "taken": self.now - timedelta(seconds=5)}
        p = build_state([self.described("captures/photos/new.jpg", 1)], "away", "normal",
                        newest_photo=newest)["photo"]
        self.assertEqual(p["description"], "A courier with a box.")
        self.assertNotIn("taken", p)
        json.dumps(p)

    def test_older_file_doesnt_hide_a_newer_description(self):
        newest = {"path": "captures/photos/old.jpg", "taken": self.now - timedelta(hours=1)}
        p = build_state([self.described("captures/photos/sim.jpg", 1)], "away", "normal",
                        newest_photo=newest)["photo"]
        self.assertEqual(p["url"], "/media/captures/photos/sim.jpg")

    def test_no_files_no_events(self):
        self.assertIsNone(build_state([], "home", "normal", newest_photo=None)["photo"])


class Watcher(unittest.TestCase):
    def test_picks_the_newest_photo_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photos = root / "captures" / "photos"
            photos.mkdir(parents=True)
            w = PhotoWatcher(photos, root, ttl_s=0)
            self.assertIsNone(w.newest())
            (photos / "a.jpg").write_bytes(b"x")
            (photos / "notes.txt").write_text("x")
            b = photos / "b.png"
            b.write_bytes(b"x")
            os.utime(photos / "a.jpg", (time.time() - 100, time.time() - 100))
            self.assertEqual(w.newest()["path"], "captures/photos/b.png")
        missing = PhotoWatcher(Path(tmp) / "nope", Path(tmp), ttl_s=0)
        self.assertIsNone(missing.newest())


class SensorButtons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        brain = Brain(None, SECRET, log=lambda _m: None)
        self.pipeline = Pipeline(brain, ModeState(SECRET), EventLog(d / "e.jsonl", SECRET),
                                 say=lambda _m: None, rules=RuleStore(d / "r.json"))
        self.board = Dashboard(self.pipeline, "127.0.0.1", 0).start()

    def tearDown(self):
        self.board.stop()
        self.tmp.cleanup()

    def post(self, body):
        req = urllib.request.Request(self.board.url + "api/sensor", data=json.dumps(body).encode(),
                                     method="POST", headers={"X-Faraday": "1"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def wait_for(self, kind):
        for _ in range(60):
            found = [e for e in self.pipeline.log.recent(50) if e["type"] == kind]
            if found:
                return found
            time.sleep(0.05)
        return []

    def test_doorbell_and_motion_buttons(self):
        self.pipeline.owner_set_mode("away")
        self.assertEqual(self.post({"type": "motion"}), 202)
        self.assertTrue(self.wait_for("motion"))                     # same path as the radio
        self.pipeline.tick(force=True)                               # no doorbell: the warning
        self.assertTrue(self.wait_for("alert_changed"))
        self.assertEqual(self.post({"type": "doorbell"}), 202)
        bell = self.wait_for("doorbell")
        self.assertEqual(bell[0]["source"], Source.DASHBOARD)        # honest in the log
        self.assertEqual(bell[0]["data"]["key"], "0x00DEC608")
        self.assertEqual(self.post({"type": "disarm"}), 400)

if __name__ == "__main__":
    unittest.main()
