"""The warning fires by itself, 15 s after the wave (owner, Mac test).

On the Mac the warning only showed up together with the next motion, because the
grace counted from when the report arrived (3 s after the wave) plus a 2.5 s margin.
These tests use a real timer and a real clock, with a short grace period."""
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from shared import ActionType, AlertLevel, EventType, Mode

from brain.pipeline import Pipeline
from log import EventLog
from modes import ModeState
from rules.baseline import WARNING_LINE
from sim import events

from .helpers import PASSPHRASE, make_brain


class GraceTimer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.said, self.lines = [], []
        self.warned = threading.Event()

    def tearDown(self):
        self.tmp.cleanup()

    def pipeline(self, grace=0.3):
        modes = ModeState(PASSPHRASE, 60, Mode.AWAY, motion_grace_s=grace)

        def action(a):
            if a.type == ActionType.SPEAK:
                self.said.append(a.args["text"])
                if a.args["text"] == WARNING_LINE:
                    self.warned.set()

        return Pipeline(make_brain(), modes, EventLog(Path(self.tmp.name) / "events.jsonl", PASSPHRASE),
                        on_action=action, say=self.lines.append)

    def test_the_warning_fires_by_itself(self):
        p = self.pipeline()
        p.handle(events.motion())
        self.assertTrue(self.warned.wait(3), "no warning without another event")
        self.assertEqual(p.modes.alert, AlertLevel.WARNING)
        self.assertIn("[motion] no doorbell within 0 s: warning", self.lines)

    def test_it_counts_from_the_first_packet(self):
        """A motion reported 0.8 s after its first packet: the warning comes 0.8 s sooner."""
        p = self.pipeline(grace=1.5)
        motion = events.motion()
        motion.timestamp = (datetime.now().astimezone() - timedelta(seconds=0.8)).isoformat(
            timespec="milliseconds")
        start = time.monotonic()
        p.handle(motion)
        self.assertTrue(self.warned.wait(5))
        self.assertLess(time.monotonic() - start, 1.3)            # 1.5 s grace - 0.8 s delay (+ margin)

    def test_a_doorbell_in_time_stops_it(self):
        p = self.pipeline(grace=0.5)
        p.handle(events.motion())
        p.handle(events.doorbell())
        self.assertFalse(self.warned.wait(1.2))
        self.assertEqual(p.modes.alert, AlertLevel.NORMAL)
        self.assertTrue(any(l.startswith("[doorbell] a visitor: the pending warning is cancelled")
                            for l in self.lines))

    def test_ignored_motion_is_printed_with_the_reason(self):
        p = self.pipeline(grace=30)
        p.handle(events.motion())
        p.handle(events.motion())
        self.assertTrue(any(l.startswith("[motion] porch light on; warning in 30 s") for l in self.lines))
        self.assertTrue(any(l.startswith("[motion] ignored: already waiting for a doorbell") for l in self.lines))
        logged = [e["type"] for e in p.log.recent(10) if e.get("kind") == "event"]
        self.assertNotIn(EventType.ALERT_CHANGED, logged)
        self.assertEqual(p.modes.alert, AlertLevel.NORMAL)


if __name__ == "__main__":
    unittest.main()
