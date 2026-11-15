"""Owner instructions on the dashboard: listed in the Rules card, removable."""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from brain import Brain
from brain.pipeline import Pipeline
from dashboard import Dashboard
from dashboard.server import STATIC_DIR
from log import EventLog
from modes import ModeState
from rules import InstructionStore
from sim import events

SECRET = "silver otter lantern"


class Card(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.pipeline = Pipeline(Brain(None, SECRET, log=lambda _m: None), ModeState(SECRET),
                                 EventLog(d / "e.jsonl", SECRET), say=lambda _m: None,
                                 instructions=InstructionStore(d / "i.json"))
        self.board = Dashboard(self.pipeline, "127.0.0.1", 0).start()
        self.base = self.board.url.rstrip("/")

    def tearDown(self):
        self.board.stop()
        self.tmp.cleanup()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Faraday": "1"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_spoken_instruction_shows_and_can_be_deleted(self):
        self.assertEqual(self.board.state()["instructions"], [])
        self.pipeline.handle(events.speech("Hey Faraday, if a delivery comes, ask them to leave it by the side door."))
        [shown] = self.board.state()["instructions"]
        self.assertEqual((shown["who"], shown["text"]), ("Delivery", "ask them to leave it by the side door"))
        self.assertEqual(self.post("/api/instructions/delete", {"id": shown["id"]}), (200, {"ok": True}))
        self.assertEqual(self.board.state()["instructions"], [])
        self.assertEqual(self.post("/api/instructions/delete", {"id": shown["id"]})[0], 404)

    def test_page_renders_them_in_the_rules_card(self):
        js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn('"/api/instructions/delete"', js)
        self.assertIn("Rules & instructions", js)


if __name__ == "__main__":
    unittest.main()
