"""Baseline responses (PLAN.md mode table) and merging the brain's actions on top."""
import unittest

from shared import ActionType, EventType, Mode, Source, make_action, validate_action

from rules import TABLE, baseline_actions, merge
from sim import events


def kinds(actions):
    return [(a.type, a.args.get("outlet")) for a in actions]


class Baseline(unittest.TestCase):
    def test_table_covers_doorbell_and_motion_in_every_mode(self):
        for event_type in (EventType.DOORBELL, EventType.MOTION):
            for mode in Mode.ALL:
                self.assertIn((event_type, mode), TABLE)

    def test_actions_are_valid_and_from_rules(self):
        for make in (events.doorbell, events.motion):
            for mode in Mode.ALL:
                event = make()
                for action in baseline_actions(event, mode):
                    self.assertEqual(validate_action(action.to_dict()), [])
                    self.assertEqual(action.source, Source.RULES)
                    self.assertEqual(action.caused_by, event.id)

    def test_mode_table(self):
        photo, porch = (ActionType.TAKE_PHOTO, None), (ActionType.OUTLET_ON, "porch_light")
        expected = {
            (EventType.DOORBELL, Mode.HOME): [photo],
            (EventType.DOORBELL, Mode.AWAY): [photo],
            (EventType.DOORBELL, Mode.NIGHT): [porch, photo],
            (EventType.MOTION, Mode.HOME): [],
            (EventType.MOTION, Mode.AWAY): [photo],
            (EventType.MOTION, Mode.NIGHT): [porch, photo],
        }
        for (event_type, mode), want in expected.items():
            event = events.doorbell() if event_type == EventType.DOORBELL else events.motion()
            self.assertEqual(kinds(baseline_actions(event, mode)), want, (event_type, mode))

    def test_other_events_have_no_baseline(self):
        for event in (events.speech("hi"), events.photo_described(None)):
            for mode in Mode.ALL:
                self.assertEqual(baseline_actions(event, mode), [])


class Merge(unittest.TestCase):
    def setUp(self):
        self.motion = events.motion()
        self.base = baseline_actions(self.motion, Mode.NIGHT)

    def brain(self, kind, args):
        return make_action(kind, args, Source.BRAIN, caused_by=self.motion.id)

    def test_repeats_and_conflicts_are_dropped(self):
        extra = [self.brain(ActionType.OUTLET_ON, {"outlet": "porch_light"}),
                 self.brain(ActionType.OUTLET_OFF, {"outlet": "porch_light"}),
                 self.brain(ActionType.TAKE_PHOTO, {"reason": "another reason"})]
        self.assertEqual(merge(self.base, extra), [])

    def test_new_things_are_kept_once(self):
        lamp = self.brain(ActionType.OUTLET_ON, {"outlet": "living_room_lamp"})
        hello = self.brain(ActionType.SPEAK, {"text": "You are on camera."})
        lamp_again = self.brain(ActionType.OUTLET_ON, {"outlet": "living_room_lamp"})
        self.assertEqual(merge(self.base, [lamp, hello, lamp_again]), [lamp, hello])

    def test_different_speech_is_not_a_duplicate(self):
        a = self.brain(ActionType.SPEAK, {"text": "One moment, please."})
        b = self.brain(ActionType.SPEAK, {"text": "Hello."})
        self.assertEqual(merge([a], [a, b]), [b])


if __name__ == "__main__":
    unittest.main()
