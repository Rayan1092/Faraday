"""Baseline responses (PLAN.md mode table) and merging the brain's actions on top."""
import unittest

from shared import AlertLevel, ActionType, EventType, Mode, Source, make_action, validate_action

from rules import baseline_actions, merge
from rules.baseline import ALARM_LINE, ANNOUNCE_DOORBELL, WARNING_LINE
from sim import events

N, W, A = AlertLevel.NORMAL, AlertLevel.WARNING, AlertLevel.ALARM
PHOTO = ("take_photo", None)
PORCH = ("outlet_on", "porch_light")
LAMP = ("outlet_on", "living_room_lamp")


def steps(actions):
    """take_photo / outlet_on porch / speak <text> -> comparable tuples."""
    out = []
    for a in actions:
        if a.type == ActionType.SPEAK:
            out.append(("speak", a.args["text"]))
        else:
            out.append((a.type, a.args.get("outlet")))
    return out


class ModeTable(unittest.TestCase):
    """The mode table in PLAN.md, row by row. Lights escalate with the alert level."""

    def check(self, make, mode, alert, want):
        self.assertEqual(steps(baseline_actions(make(), mode, alert)), want, (make, mode, alert))

    def test_home(self):
        self.check(events.doorbell, Mode.HOME, N, [("speak", ANNOUNCE_DOORBELL)])  # announce only
        for alert in (N, W, A):
            self.check(events.motion, Mode.HOME, alert, [])                     # nothing

    def test_away(self):
        self.check(events.doorbell, Mode.AWAY, N, [PHOTO])  # AI answers after the photo
        self.check(events.motion, Mode.AWAY, W, [PORCH, PHOTO, ("speak", WARNING_LINE)])
        self.check(events.motion, Mode.AWAY, A, [PORCH, LAMP, PHOTO, ("speak", ALARM_LINE)])

    def test_night_is_quiet(self):
        self.check(events.doorbell, Mode.NIGHT, N, [PORCH, PHOTO])
        self.check(events.motion, Mode.NIGHT, W, [PORCH, PHOTO])
        self.check(events.motion, Mode.NIGHT, A, [PORCH, LAMP, PHOTO])

    def test_doorbell_ignores_alert_level(self):
        for alert in (N, W, A):
            self.check(events.doorbell, Mode.NIGHT, alert, [PORCH, PHOTO])

    def test_actions_are_valid_and_from_rules(self):
        for make in (events.doorbell, events.motion):
            for mode in Mode.ALL:
                for alert in (N, W, A):
                    event = make()
                    for action in baseline_actions(event, mode, alert):
                        self.assertEqual(validate_action(action.to_dict()), [])
                        self.assertEqual(action.source, Source.RULES)
                        self.assertEqual(action.caused_by, event.id)

    def test_other_events_have_no_baseline(self):
        for event in (events.speech("hi"), events.photo_described(None)):
            for mode in Mode.ALL:
                self.assertEqual(baseline_actions(event, mode, A), [])


class Merge(unittest.TestCase):
    def setUp(self):
        self.motion = events.motion()
        self.base = baseline_actions(self.motion, Mode.NIGHT, W)

    def extra(self, kind, args):
        return make_action(kind, args, Source.BRAIN, caused_by=self.motion.id)

    def test_repeats_and_conflicts_are_dropped(self):
        extra = [self.extra(ActionType.OUTLET_ON, {"outlet": "porch_light"}),
                 self.extra(ActionType.OUTLET_OFF, {"outlet": "porch_light"}),
                 self.extra(ActionType.TAKE_PHOTO, {"reason": "another reason"})]
        self.assertEqual(merge(self.base, extra), [])

    def test_new_things_are_kept_once(self):
        lamp = self.extra(ActionType.OUTLET_ON, {"outlet": "living_room_lamp"})
        hello = self.extra(ActionType.SPEAK, {"text": "You are on camera."})
        lamp_again = self.extra(ActionType.OUTLET_ON, {"outlet": "living_room_lamp"})
        self.assertEqual(merge(self.base, [lamp, hello, lamp_again]), [lamp, hello])

    def test_alarm_lamp_already_on_means_rule_lamp_is_dropped(self):
        alarm = baseline_actions(self.motion, Mode.NIGHT, A)
        self.assertEqual(merge(alarm, [self.extra(ActionType.OUTLET_ON,
                                                  {"outlet": "living_room_lamp"})]), [])

    def test_different_speech_is_not_a_duplicate(self):
        a = self.extra(ActionType.SPEAK, {"text": "One moment, please."})
        b = self.extra(ActionType.SPEAK, {"text": "Hello."})
        self.assertEqual(merge([a], [a, b]), [b])


if __name__ == "__main__":
    unittest.main()
