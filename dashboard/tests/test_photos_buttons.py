"""Real photos appear as soon as they're saved; no demo-only buttons on the page."""
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
from dashboard.server import STATIC_DIR
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


class NoDemoButtons(unittest.TestCase):
    """Owner 2026-11-15: the real product, not a demo build. No "Ring doorbell", "Motion"
    or typed-speech buttons, no menu holding them, and no routes behind them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        brain = Brain(None, SECRET, log=lambda _m: None)
        self.pipeline = Pipeline(brain, ModeState(SECRET), EventLog(d / "e.jsonl", SECRET),
                                 say=lambda _m: None)
        self.board = Dashboard(self.pipeline, "127.0.0.1", 0).start()
        self.base = self.board.url.rstrip("/")

    def tearDown(self):
        self.board.stop()
        self.tmp.cleanup()

    def test_routes_are_gone(self):
        for path, body in (("/api/sensor", {"type": "doorbell"}), ("/api/sensor", {"type": "motion"}),
                           ("/api/say", {"text": "Faraday, disarm"})):
            req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Faraday": "1"})
            with self.assertRaises(urllib.error.HTTPError, msg=path) as ctx:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(ctx.exception.code, 404, path)
        self.assertEqual(self.pipeline.log.recent(10), [])    # nothing reached the pipeline

    def test_page_has_no_demo_controls(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
        for gone in ("btn-ring", "btn-motion", "say-form", "btn-backup", "Stage backup", "/api/sensor",
                     "/api/say", 'id="net"', "ONLINE", "OFFLINE"):
            self.assertNotIn(gone, html + js, gone)


if __name__ == "__main__":
    unittest.main()
