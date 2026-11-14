"""The backup demo plays PLAN.md's script by itself and ends in the right state."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from shared import AlertLevel, Mode

from brain import Brain
from brain.pipeline import Pipeline
from log import EventLog
from modes import ModeState
from rules import RuleStore
from sim.__main__ import Sim
from sim.demo import DemoRunner, play, steps

SECRET = "silver otter lantern"


class BackupDemo(unittest.TestCase):
    def run_demo(self, secret=SECRET, manual=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        brain = Brain(None, SECRET, log=lambda _m: None)
        pipeline = Pipeline(brain, ModeState(SECRET), EventLog(d / "e.jsonl", SECRET),
                            say=lambda _m: None, rules=RuleStore(d / "r.json"))
        sim = Sim(pipeline)
        slept, prompts = [], []
        runner = DemoRunner(sim, secret, speed=2, manual=manual, sleep=slept.append,
                            wait_enter=lambda p: prompts.append(p) or "", say=lambda _m: None)
        with redirect_stdout(io.StringIO()) as out:
            pipeline.start()
            notes = play(runner)
        return pipeline, notes, slept, prompts, out.getvalue()

    def test_full_script_offline(self):
        pipeline, notes, slept, _, out = self.run_demo()
        self.assertEqual(notes, [])
        self.assertEqual((pipeline.modes.mode, pipeline.modes.alert), (Mode.HOME, AlertLevel.NORMAL))
        log = pipeline.log.recent(500)
        types = [e["type"] for e in log]
        self.assertIn("save_message", types)
        self.assertIn("add_rule", types)
        self.assertIn("summarize_log", types)
        self.assertEqual([e["data"]["level"] for e in log if e["type"] == "alert_changed"],
                         ["warning", "alarm", "normal"])
        lamp = [e for e in log if e["type"] == "outlet_on" and e["args"]["outlet"] == "living_room_lamp"]
        self.assertTrue(lamp)
        self.assertNotIn("otter", out.lower())               # passphrase never printed
        self.assertTrue(all(s > 0 for s in slept))            # paused between moves
        self.assertAlmostEqual(slept[0], 1.5 / 2)             # speed scales the pauses

    def test_manual_waits_before_each_step(self):
        _, _, _, prompts, _ = self.run_demo(manual=True)
        self.assertEqual(len(prompts), len(steps()))

    def test_wrong_passphrase_is_reported_not_fatal(self):
        pipeline, notes, _, _, _ = self.run_demo(secret="wrong words")
        self.assertIn("passphrase from settings was refused", notes)
        self.assertEqual(pipeline.modes.mode, Mode.NIGHT)


if __name__ == "__main__":
    unittest.main()
