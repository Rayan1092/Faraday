"""Mode changes and alert rules (FORMATS.md alert_changed)."""
import unittest

from shared import AlertLevel, ChangedBy, EventType, Mode, Source, validate_event

from modes import ModeState
from sim import events

SECRET = "silver otter lantern"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def state(mode=Mode.AWAY, window=60):
    clock = Clock()
    return ModeState(SECRET, window, mode, clock), clock


def levels(evts):
    return [(e.data["previous"], e.data["level"]) for e in evts if e.type == EventType.ALERT_CHANGED]


class AlertRules(unittest.TestCase):
    def test_warning_then_alarm_within_window(self):
        s, clock = state(Mode.NIGHT)
        motion = events.motion()
        first = s.on_event(motion)
        self.assertEqual(levels(first), [("normal", "warning")])
        self.assertEqual(first[0].caused_by, motion.id)
        self.assertEqual(first[0].source, Source.MODES)
        self.assertEqual(validate_event(first[0].to_dict()), [])
        clock.t += 30
        second = s.on_event(events.motion())
        self.assertEqual(levels(second), [("warning", "alarm")])
        self.assertEqual(validate_event(second[0].to_dict()), [])
        clock.t += 5
        self.assertEqual(s.on_event(events.motion()), [])  # already alarm
        self.assertEqual(s.alert, AlertLevel.ALARM)

    def test_window_boundary_is_inclusive(self):
        s, clock = state()
        s.on_event(events.motion())
        clock.t += 60
        self.assertEqual(levels(s.on_event(events.motion())), [("warning", "alarm")])

    def test_late_motion_restarts_the_window(self):
        s, clock = state()
        s.on_event(events.motion())
        clock.t += 61
        self.assertEqual(s.on_event(events.motion()), [])   # stays warning, no event
        self.assertEqual(s.alert, AlertLevel.WARNING)
        clock.t += 59                                       # within 60 s of the late one
        self.assertEqual(levels(s.on_event(events.motion())), [("warning", "alarm")])

    def test_window_comes_from_settings_value(self):
        s, clock = state(window=10)
        s.on_event(events.motion())
        clock.t += 11
        self.assertEqual(s.on_event(events.motion()), [])

    def test_home_motion_and_doorbell_never_change_alert(self):
        s, _ = state(Mode.HOME)
        for _ in range(3):
            self.assertEqual(s.on_event(events.motion()), [])
        s2, _ = state(Mode.AWAY)
        for _ in range(3):
            self.assertEqual(s2.on_event(events.doorbell()), [])
            self.assertEqual(s2.on_event(events.speech("hello")), [])
        self.assertEqual(s2.alert, AlertLevel.NORMAL)

    def test_switching_armed_modes_keeps_alert(self):
        s, clock = state(Mode.AWAY)
        s.on_event(events.motion())
        s.arm(Mode.NIGHT, ChangedBy.DASHBOARD)
        self.assertEqual(s.alert, AlertLevel.WARNING)
        clock.t += 10
        self.assertEqual(levels(s.on_event(events.motion())), [("warning", "alarm")])

    def test_only_passphrase_returns_to_normal(self):
        s, clock = state(Mode.NIGHT)
        s.on_event(events.motion())
        clock.t += 1
        s.on_event(events.motion())
        for who in ChangedBy.ALL:
            s.arm(Mode.AWAY, who)
        self.assertEqual(s.alert, AlertLevel.ALARM)
        self.assertEqual(s.disarm_with_passphrase("wrong words"), [])
        out = s.disarm_with_passphrase("Silver otter lantern.", caused_by="evt_12345678")
        self.assertEqual([e.type for e in out], [EventType.MODE_CHANGED, EventType.ALERT_CHANGED])
        self.assertEqual(out[0].data["changed_by"], ChangedBy.PASSPHRASE)
        self.assertEqual(levels(out), [("alarm", "normal")])
        self.assertEqual({e.caused_by for e in out}, {"evt_12345678"})
        for e in out:
            self.assertEqual(validate_event(e.to_dict()), [])
        self.assertEqual((s.mode, s.alert), (Mode.HOME, AlertLevel.NORMAL))

    def test_disarm_without_alert_sends_only_mode_changed(self):
        s, _ = state(Mode.AWAY)
        out = s.disarm_with_passphrase(SECRET)
        self.assertEqual([e.type for e in out], [EventType.MODE_CHANGED])

    def test_rearm_starts_fresh(self):
        s, clock = state(Mode.AWAY)
        s.on_event(events.motion())
        s.disarm_with_passphrase(SECRET)
        s.arm(Mode.AWAY, ChangedBy.BRAIN)
        clock.t += 5
        self.assertEqual(levels(s.on_event(events.motion())), [("normal", "warning")])


class ModeChanges(unittest.TestCase):
    def test_nobody_reaches_home_without_passphrase(self):
        for start in (Mode.AWAY, Mode.NIGHT):
            s, _ = state(start)
            for who in ChangedBy.ALL:
                self.assertEqual(s.arm(Mode.HOME, who), [])
            self.assertEqual(s.mode, start)

    def test_brain_and_rules_cannot_switch_armed_modes(self):
        s, _ = state(Mode.AWAY)
        self.assertEqual(s.arm(Mode.NIGHT, ChangedBy.BRAIN), [])
        self.assertEqual(s.arm(Mode.NIGHT, ChangedBy.RULE), [])
        self.assertEqual(len(s.arm(Mode.NIGHT, ChangedBy.DASHBOARD)), 1)

    def test_brain_can_arm_from_home(self):
        s, _ = state(Mode.HOME)
        out = s.arm(Mode.AWAY, ChangedBy.BRAIN, caused_by="act_12345678")
        self.assertEqual(out[0].data, {"mode": "away", "previous": "home",
                                       "changed_by": "brain", "reason": ""})
        self.assertEqual(validate_event(out[0].to_dict()), [])

    def test_startup_event(self):
        s, _ = state(Mode.HOME)
        e = s.startup()
        self.assertEqual(e.data["changed_by"], ChangedBy.STARTUP)
        self.assertEqual(validate_event(e.to_dict()), [])


class SpeechScreening(unittest.TestCase):
    def test_passphrase_speech_is_redacted_and_disarms(self):
        s, _ = state(Mode.NIGHT)
        heard = events.speech("OK, silver otter lantern!")
        screened, out = s.screen_speech(heard)
        self.assertEqual(screened.data["text"], "OK, [passphrase]!")
        self.assertEqual(screened.id, heard.id)
        self.assertEqual(out[0].caused_by, heard.id)
        self.assertEqual(s.mode, Mode.HOME)

    def test_redacted_at_home_but_no_events(self):
        s, _ = state(Mode.HOME)
        screened, out = s.screen_speech(events.speech("silver otter lantern"))
        self.assertEqual((screened.data["text"], out), ("[passphrase]", []))

    def test_other_speech_and_events_untouched(self):
        s, _ = state(Mode.AWAY)
        heard = events.speech("Package for Sam.")
        self.assertIs(s.screen_speech(heard)[0], heard)
        bell = events.doorbell()
        self.assertEqual(s.screen_speech(bell), (bell, []))
        self.assertEqual(s.mode, Mode.AWAY)


if __name__ == "__main__":
    unittest.main()
