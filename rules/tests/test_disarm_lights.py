"""Lights off on passphrase disarm: only the lights Faraday turned on, through the pipeline."""
import tempfile
import unittest
from pathlib import Path

from shared import ActionType, ChangedBy, Mode, Source, make_action, validate_action

from brain import Brain
from brain.pipeline import Pipeline
from log import EventLog
from modes import ModeState
from rules import lights_off_on_disarm
from sim import events

SECRET = "silver otter lantern"


def on(outlet, source=Source.RULES):
    return make_action(ActionType.OUTLET_ON, {"outlet": outlet}, source).to_dict()


def off(outlet, source=Source.RULES):
    return make_action(ActionType.OUTLET_OFF, {"outlet": outlet}, source).to_dict()


class Rule(unittest.TestCase):
    def disarm(self):
        return ModeState(SECRET, mode=Mode.NIGHT).disarm_with_passphrase(SECRET)[0]

    def test_only_lights_faraday_turned_on(self):
        log = [on("porch_light"), on("living_room_lamp", Source.BRAIN), on("spare", Source.DASHBOARD)]
        actions = lights_off_on_disarm(self.disarm(), log)
        self.assertEqual([(a.type, a.args["outlet"]) for a in actions],
                         [("outlet_off", "living_room_lamp"), ("outlet_off", "porch_light")])
        for a in actions:
            self.assertEqual((a.source, validate_action(a.to_dict())), (Source.RULES, []))

    def test_lights_already_off_are_not_sent_again(self):
        self.assertEqual(lights_off_on_disarm(self.disarm(), [on("porch_light"), off("porch_light")]), [])

    def test_only_the_passphrase_disarm(self):
        night = ModeState(SECRET).arm(Mode.NIGHT, ChangedBy.DASHBOARD)[0]
        for event in (night, events.motion(), events.speech("lights off")):
            self.assertEqual(lights_off_on_disarm(event, [on("porch_light")]), [])


class ThroughThePipeline(unittest.TestCase):
    def test_alarm_then_passphrase_turns_both_lights_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            emitted = []
            p = Pipeline(Brain(None, SECRET, log=lambda _m: None), ModeState(SECRET, mode=Mode.NIGHT),
                         EventLog(Path(tmp) / "e.jsonl", SECRET), on_action=emitted.append,
                         say=lambda _m: None)
            p.handle(events.motion())
            p.handle(events.motion())                   # alarm: porch light + lamp on
            emitted.clear()
            result = p.owner_passphrase(SECRET)
            offs = [a.args["outlet"] for a in result.actions if a.type == ActionType.OUTLET_OFF]
            self.assertEqual(sorted(offs), ["living_room_lamp", "porch_light"])
            self.assertEqual([a.type for a in emitted if a.type == ActionType.OUTLET_OFF],
                             ["outlet_off", "outlet_off"])   # handed to integration like any action
            self.assertIn('"outlet_off"', p.log.path.read_text(encoding="utf-8"))
            self.assertEqual(p.modes.mode, Mode.HOME)


if __name__ == "__main__":
    unittest.main()
