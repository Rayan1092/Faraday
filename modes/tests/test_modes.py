"""Mode changes and alert rules (FORMATS.md alert_changed)."""
import unittest

from shared import AlertLevel, ChangedBy, EventType, Mode, Source, validate_event

from modes import ModeState, MotionOutcome
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
    """Owner 2026-11-15: a visitor who walks up (motion) and then rings is never warned
    or alarmed. Motion starts a 15 s grace period; no doorbell -> warning; alarm only
    from motion 8 to 60 s after the warning with no doorbell in between."""

    def warn(self, s, clock, motion=None):
        """Motion, no doorbell for the grace period: the warning goes up."""
        motion = motion or events.motion()
        self.assertEqual(s.on_event(motion), [])
        self.assertEqual(s.last_outcome, MotionOutcome.GRACE)
        clock.t += 15
        out = s.end_grace()
        self.assertEqual(levels(out), [("normal", "warning")])
        return out

    def test_walk_up_then_ring_is_never_warned(self):
        s, clock = state(Mode.AWAY)
        self.assertEqual(s.on_event(events.motion()), [])          # porch light only (baseline)
        clock.t += 6
        self.assertEqual(s.on_event(events.doorbell()), [])        # a visitor: pending warning cancelled
        clock.t += 20
        self.assertEqual(s.end_grace(), [])
        self.assertEqual(s.alert, AlertLevel.NORMAL)
        for wait in (10, 30, 30):                                  # they shuffle about while talking
            clock.t += wait
            self.assertEqual(s.on_event(events.motion()), [])
            self.assertEqual(s.last_outcome, MotionOutcome.QUIET)
        self.assertEqual(s.alert, AlertLevel.NORMAL)

    def test_walk_up_no_ring_is_warned_after_the_grace_period(self):
        s, clock = state(Mode.NIGHT)
        motion = events.motion()
        self.assertEqual(s.on_event(motion), [])
        clock.t += 5
        self.assertEqual(s.on_event(events.motion()), [])          # still pending, not extended
        self.assertEqual(s.last_outcome, MotionOutcome.PENDING)
        clock.t += 9
        self.assertFalse(s.grace_due())
        self.assertEqual(s.end_grace(), [])                        # 14 s: not yet
        clock.t += 1
        out = s.end_grace()
        self.assertEqual(levels(out), [("normal", "warning")])
        self.assertEqual(out[0].caused_by, motion.id)
        self.assertEqual((out[0].source, validate_event(out[0].to_dict())), (Source.MODES, []))

    def test_lingering_after_the_warning_raises_the_alarm(self):
        s, clock = state(Mode.AWAY)
        self.warn(s, clock)
        clock.t += 3
        self.assertEqual(s.on_event(events.motion()), [])          # same wave: under 8 s
        self.assertEqual(s.last_outcome, MotionOutcome.IGNORED)
        clock.t += 6                                               # 9 s after the warning
        second = s.on_event(events.motion())
        self.assertEqual(levels(second), [("warning", "alarm")])
        self.assertEqual(validate_event(second[0].to_dict()), [])
        clock.t += 5
        self.assertEqual(s.on_event(events.motion()), [])          # already alarm
        self.assertEqual(s.last_outcome, MotionOutcome.NONE)
        self.assertEqual(s.alert, AlertLevel.ALARM)

    def test_window_boundaries(self):
        s, clock = state()
        self.warn(s, clock)
        clock.t += 8
        self.assertEqual(levels(s.on_event(events.motion())), [("warning", "alarm")])   # 8 s counts
        s, clock = state()
        self.warn(s, clock)
        clock.t += 60
        self.assertEqual(levels(s.on_event(events.motion())), [("warning", "alarm")])   # 60 s counts

    def test_late_motion_restarts_the_window(self):
        s, clock = state()
        self.warn(s, clock)
        clock.t += 61
        self.assertEqual(s.on_event(events.motion()), [])          # stays warning, no event
        self.assertEqual(s.alert, AlertLevel.WARNING)
        clock.t += 30                                              # within 60 s of the late one
        self.assertEqual(levels(s.on_event(events.motion())), [("warning", "alarm")])

    def test_a_doorbell_after_the_warning_means_no_alarm(self):
        s, clock = state()
        self.warn(s, clock)
        clock.t += 5
        s.on_event(events.doorbell())
        clock.t += 10
        self.assertEqual(s.on_event(events.motion()), [])
        self.assertEqual((s.last_outcome, s.alert), (MotionOutcome.QUIET, AlertLevel.WARNING))

    def test_visitor_quiet_ends(self):
        s, clock = state(Mode.AWAY)
        s.on_event(events.doorbell())
        clock.t += 121
        self.assertEqual(s.on_event(events.motion()), [])
        self.assertEqual(s.last_outcome, MotionOutcome.GRACE)      # a new visit: grace again

    def test_a_mode_change_is_a_new_visit(self):
        s, clock = state(Mode.AWAY)
        s.on_event(events.doorbell())                              # a visitor while away...
        s.arm(Mode.NIGHT, ChangedBy.DASHBOARD)                     # ...then the owner goes to bed
        clock.t += 30
        s.on_event(events.motion())
        self.assertEqual(s.last_outcome, MotionOutcome.GRACE)      # not quiet any more

    def test_settings_values(self):
        s, clock = state(window=10)
        s.motion_grace_s = 5
        s.on_event(events.motion())
        clock.t += 5
        self.assertTrue(s.end_grace())
        clock.t += 11
        self.assertEqual(s.on_event(events.motion()), [])          # outside the 10 s window

    def test_force_ends_the_grace_now(self):
        s, _ = state(Mode.AWAY)
        s.on_event(events.motion())
        self.assertEqual(levels(s.end_grace(force=True)), [("normal", "warning")])
        self.assertEqual(s.end_grace(force=True), [])              # nothing pending any more

    def test_home_motion_and_doorbell_never_change_alert(self):
        s, _ = state(Mode.HOME)
        for _ in range(3):
            self.assertEqual(s.on_event(events.motion()), [])
        self.assertIsNone(s.grace_pending)
        s2, _ = state(Mode.AWAY)
        for _ in range(3):
            self.assertEqual(s2.on_event(events.doorbell()), [])
            self.assertEqual(s2.on_event(events.speech("hello")), [])
        self.assertEqual(s2.alert, AlertLevel.NORMAL)

    def test_switching_armed_modes_keeps_alert(self):
        s, clock = state(Mode.AWAY)
        self.warn(s, clock)
        s.arm(Mode.NIGHT, ChangedBy.DASHBOARD)
        self.assertEqual(s.alert, AlertLevel.WARNING)
        clock.t += 10
        self.assertEqual(levels(s.on_event(events.motion())), [("warning", "alarm")])

    def test_only_passphrase_returns_to_normal(self):
        s, clock = state(Mode.NIGHT)
        self.warn(s, clock)
        clock.t += 9
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
        s.on_event(events.motion())                                # a pending warning...
        out = s.disarm_with_passphrase(SECRET)
        self.assertEqual([e.type for e in out], [EventType.MODE_CHANGED])
        self.assertIsNone(s.grace_pending)                         # ...is gone with the disarm

    def test_rearm_starts_fresh(self):
        s, clock = state(Mode.AWAY)
        self.warn(s, clock)
        s.disarm_with_passphrase(SECRET)
        s.arm(Mode.AWAY, ChangedBy.BRAIN)
        clock.t += 5
        self.warn(s, clock)


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
