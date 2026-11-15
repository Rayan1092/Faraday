"""Heard & said shows both sides as live captions (owner, Mac: only Faraday's lines were
left). The owner after "Hey Faraday", the visitor, and Faraday's replies; the passphrase
stays redacted, because the log already redacted it."""
import tempfile
import unittest
from pathlib import Path

from shared import ActionType, Source, make_action

from dashboard.server import STATIC_DIR
from dashboard.state import build_state
from log import EventLog
from sim import events

SECRET = "silver otter lantern"


class BothSides(unittest.TestCase):
    def test_owner_visitor_and_faraday(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp) / "e.jsonl", SECRET)
            log.write(events.speech("Who's at the door?", "owner"))
            log.write(make_action(ActionType.SPEAK, {"text": "Checking the camera."}, Source.BRAIN))
            log.write(events.speech("I have a package.", "visitor"))
            log.write(events.speech(f"Hey Faraday, {SECRET}", "owner"))
            talk = build_state(log.recent(50), "home", "normal")["talk"]
        self.assertEqual([(t["who"], t.get("speaker", "")) for t in talk],
                         [("heard", "owner"), ("said", ""), ("heard", "visitor"), ("heard", "owner")])
        self.assertNotIn(SECRET, " ".join(t["text"] for t in talk))

    def test_page_keeps_every_line(self):
        js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn('owner: "You"', js)
        self.assertIn('visitor: "Visitor"', js)
        self.assertNotIn("firstElementChild.remove()", js)     # never drops the oldest lines


if __name__ == "__main__":
    unittest.main()
