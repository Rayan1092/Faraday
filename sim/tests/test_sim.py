"""The sim runs the demo script offline through modes/ and log/."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from shared import AlertLevel, Mode, validate_action, validate_event

from brain import Brain
from brain.pipeline import Pipeline
from log import EventLog
from modes import ModeState
from sim import events
from sim.__main__ import Sim

SECRET = "silver otter lantern"


class SimDemo(unittest.TestCase):
    def test_demo_script_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            brain = Brain(None, SECRET, log=lambda _m: None)
            pipeline = Pipeline(brain, ModeState(SECRET, min_gap_s=0), EventLog(path, SECRET),
                                say=lambda _m: None)   # min_gap 0: the script's motions come fast
            sim = Sim(pipeline)
            script = ["t Faraday, I'm heading out.", "d", "t Package for Sam, by the side door.",
                      "3", "m", "w", "m", "t Faraday, disarm", "1 wrong words",
                      "t Silver, otter lantern!", "t What happened while I was out?", "s", "x"]
            with redirect_stdout(io.StringIO()) as out:
                pipeline.start()
                for line in script:
                    self.assertTrue(sim.command(line), line)
                self.assertFalse(sim.command("q"))
            text = out.getvalue()
            self.assertEqual((sim.modes.mode, sim.modes.alert), (Mode.HOME, AlertLevel.NORMAL))
            self.assertIn('"level": "alarm"', text)
            self.assertIn("I can't disarm", text)
            self.assertIn("wrong passphrase", text)
            self.assertNotIn("otter", text.replace("t Silver, otter lantern!", ""))
            rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
            self.assertNotIn("otter", path.read_text(encoding="utf-8"))
            for row in rows:
                check = validate_event if row["kind"] == "event" else validate_action
                self.assertEqual(check(row), [], row)
            # The fake camera answered every take_photo.
            photos = [r for r in rows if r["type"] == "take_photo"]
            described = [r for r in rows if r["type"] == "photo_described"]
            self.assertEqual(len(photos), len(described))

    def test_sim_events_have_source_sim(self):
        for event in (events.doorbell(), events.motion(), events.speech("hi"),
                      events.photo_described(None)):
            self.assertEqual(event.source, "sim")
            self.assertEqual(validate_event(event.to_dict()), [])


if __name__ == "__main__":
    unittest.main()
