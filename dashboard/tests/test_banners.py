"""The big banner on the dashboard: the one message for the owner, most important first."""
import unittest
from datetime import datetime, timedelta

from shared import ActionType, ChangedBy, Mode, Source, make_action

from brain.fallback import CANT_DISARM
from dashboard.state import build_state
from modes import ModeState
from sim import events


class Banners(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now().astimezone()

    def at(self, item, seconds_ago):
        d = item.to_dict()
        d["timestamp"] = (self.now - timedelta(seconds=seconds_ago)).isoformat(timespec="milliseconds")
        return d

    def state(self, entries, mode, alert="normal"):
        return build_state(entries, mode, alert, now=self.now)["banner"]

    def test_alarm_beats_everything(self):
        refusal = make_action(ActionType.SPEAK, {"text": CANT_DISARM}, Source.BRAIN)
        b = self.state([self.at(refusal, 2)], Mode.AWAY, "alarm")
        self.assertEqual((b["kind"], b["title"]), ("alarm", "ALARM"))

    def test_refusal_then_passphrase(self):
        refusal = self.at(make_action(ActionType.SPEAK, {"text": CANT_DISARM}, Source.BRAIN), 5)
        b = self.state([refusal], Mode.NIGHT)
        self.assertEqual(b["title"], "DISARM REFUSED")
        disarm = ModeState("x y", mode=Mode.NIGHT).disarm_with_passphrase("x y")[0]
        b = self.state([refusal, self.at(disarm, 1)], Mode.HOME)
        self.assertEqual(b["title"], "DISARMED")
        self.assertIsNone(self.state([refusal, self.at(disarm, 30)], Mode.HOME))  # fades after 20 s

    def test_refusal_banner_fades(self):
        refusal = self.at(make_action(ActionType.SPEAK, {"text": CANT_DISARM}, Source.BRAIN), 90)
        self.assertIsNone(self.state([refusal], Mode.AWAY))

    def test_warning_and_night_doorbell(self):
        self.assertEqual(self.state([], Mode.NIGHT, "warning")["title"], "WARNING")
        night = ModeState("x").arm(Mode.NIGHT, ChangedBy.DASHBOARD)[0]
        b = self.state([self.at(night, 60), self.at(events.doorbell(), 30)], Mode.NIGHT)
        self.assertEqual(b["title"], "DOORBELL AT NIGHT")
        self.assertIsNone(self.state([self.at(night, 400), self.at(events.doorbell(), 300)], Mode.NIGHT))

    def test_doorbell_pressed_banner_per_mode(self):
        for mode, words in ((Mode.HOME, "Someone is at the front door"),
                            (Mode.AWAY, "Greeting the visitor"),
                            (Mode.NIGHT, "no sound")):
            entries = []
            if mode != Mode.HOME:
                entries.append(self.at(ModeState("x").arm(mode, ChangedBy.DASHBOARD)[0], 30))
            entries.append(self.at(events.doorbell(), 3))
            b = self.state(entries, mode)
            self.assertEqual(b["title"], "DOORBELL", mode)
            self.assertIn(words, b["text"])
            self.assertIsNone(self.state([self.at(events.doorbell(), 20)], Mode.HOME))

    def test_alarm_beats_doorbell(self):
        b = self.state([self.at(events.doorbell(), 2)], Mode.AWAY, "alarm")
        self.assertEqual(b["title"], "ALARM")

    def test_quiet_home(self):
        self.assertIsNone(self.state([], Mode.HOME))


if __name__ == "__main__":
    unittest.main()
