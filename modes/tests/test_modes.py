"""Mode changes and alert rules (FORMATS.md alert_changed)."""
import unittest
from datetime import datetime, timedelta, timezone

from shared import AlertLevel, ChangedBy, EventType, Mode, Source, validate_event

from modes import ModeState, MotionOutcome
from sim import events

SECRET = "silver otter lantern"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


EST = timezone(timedelta(hours=-5))


class FakeWall:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


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
        clock.t += 15                                              # the 15 s grace period
        out = s.end_grace()
        self.assertEqual(levels(out), [("normal", "warning")])
        self.assertEqual(s.grace_ended, motion.id)
        return out

    def test_walk_up_then_ring_is_never_warned(self):
        s, clock = state(Mode.AWAY)
        self.assertEqual(s.on_event(events.motion()), [])          # porch light only (baseline)
        clock.t += 6
        self.assertEqual(s.on_event(events.doorbell()), [])        # a visitor: pending warning cancelled
        clock.t += 20
        self.assertEqual(s.end_grace(), [])
        self.assertEqual(s.alert, AlertLevel.NORMAL)
        for wait in (10, 15, 15):                                  # they shuffle about while talking
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
        clock.t += 9.5
        self.assertFalse(s.grace_due())
        self.assertEqual(s.end_grace(), [])                        # 14.5 s: not yet
        self.assertIsNone(s.grace_ended)
        clock.t += 0.5                                             # 15 s: no extra margin
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

    def test_late_motion_is_a_new_walk_up(self):
        """Motion long after a warning: a grace period again, then the warning steps again
        (no event, the level stays warning), and only then can the alarm come."""
        s, clock = state()
        self.warn(s, clock)
        clock.t += 61
        late = events.motion()
        self.assertEqual(s.on_event(late), [])
        self.assertEqual((s.last_outcome, s.alert), (MotionOutcome.GRACE, AlertLevel.WARNING))
        clock.t += 9
        self.assertEqual(s.on_event(events.motion()), [])          # no alarm while we wait
        self.assertEqual(s.last_outcome, MotionOutcome.PENDING)
        clock.t += 9
        self.assertEqual(s.end_grace(), [])                        # warned again: no level change
        self.assertEqual(s.grace_ended, late.id)
        clock.t += 9
        self.assertEqual(levels(s.on_event(events.motion())), [("warning", "alarm")])

    def test_old_warning_then_a_courier_who_rings_is_never_alarmed(self):
        """Review: warning at 10:00; at 14:00 a courier trips the sensor twice, 9 s apart,
        then rings. Before, that was an alarm."""
        s, clock = state(Mode.AWAY)
        self.warn(s, clock)
        clock.t += 240
        s.on_event(events.motion())
        clock.t += 9
        self.assertEqual(s.on_event(events.motion()), [])
        clock.t += 3
        s.on_event(events.doorbell())
        clock.t += 30
        self.assertEqual(s.end_grace(), [])
        self.assertIsNone(s.grace_ended)
        self.assertEqual(s.alert, AlertLevel.WARNING)              # never alarm

    def test_the_grace_counts_from_the_sensors_first_packet(self):
        """Owner, Mac test: motion at 03:48:52 (the first packet) was reported at 03:48:55,
        and the warning came at 03:49:13. It must come at 03:49:07: 15 s after the wave."""
        clock = Clock()
        wall = FakeWall(datetime(2026, 11, 15, 3, 48, 55, tzinfo=EST))
        s = ModeState(SECRET, 60, Mode.AWAY, clock, wall=wall)
        motion = events.motion()
        motion.timestamp = "2026-11-15T03:48:52.000-05:00"
        s.on_event(motion)
        self.assertEqual(s.last_outcome, MotionOutcome.GRACE)
        self.assertAlmostEqual(s.grace_remaining(), 12.0, places=3)
        clock.t += 11.9
        self.assertFalse(s.grace_due())
        clock.t += 0.2                                             # 03:49:07.1
        self.assertEqual(levels(s.end_grace()), [("normal", "warning")])

    def test_odd_timestamps_count_as_now(self):
        clock = Clock()
        wall = FakeWall(datetime(2026, 11, 15, 3, 48, 55, tzinfo=EST))
        for ts in ("2026-11-15T03:49:30.000-05:00",               # in the future
                   "2026-11-15T03:40:00.000-05:00",               # 9 minutes old
                   "not a time", ""):
            s = ModeState(SECRET, 60, Mode.AWAY, clock, wall=wall)
            motion = events.motion()
            motion.timestamp = ts
            s.on_event(motion)
            self.assertAlmostEqual(s.grace_remaining(), 15.0, places=3, msg=ts)

    def test_a_press_that_arrives_in_time_counts(self):
        s, clock = state(Mode.AWAY)
        s.on_event(events.motion())
        clock.t += 14.9
        self.assertFalse(s.grace_due())
        s.on_event(events.doorbell())
        clock.t += 5
        self.assertEqual(s.end_grace(), [])
        self.assertEqual(s.alert, AlertLevel.NORMAL)

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
        clock.t += 61
        self.assertEqual(s.on_event(events.motion()), [])
        self.assertEqual(s.last_outcome, MotionOutcome.GRACE)      # a new visit: grace again

    def test_visitor_quiet_is_60_s(self):
        """Owner, Mac test: waves 80 and 117 s after a doorbell were swallowed."""
        for after, outcome in ((50, MotionOutcome.QUIET), (80, MotionOutcome.GRACE),
                               (117, MotionOutcome.GRACE)):
            s, clock = state(Mode.AWAY)
            s.on_event(events.doorbell())
            clock.t += after
            s.on_event(events.motion())
            self.assertEqual(s.last_outcome, outcome, after)

    def test_every_motion_says_why(self):
        """Owner, Mac test: a console line whenever motion is ignored, and why."""
        s, clock = state(Mode.HOME)
        s.on_event(events.motion())
        self.assertIn("Home mode", s.last_reason)
        s, clock = state(Mode.AWAY)
        s.on_event(events.motion())
        self.assertIn("warning in 15 s unless the doorbell rings", s.last_reason)
        clock.t += 5
        s.on_event(events.motion())
        self.assertIn("ignored: already waiting for a doorbell (warning in 10 s)", s.last_reason)
        clock.t += 10
        s.end_grace()
        clock.t += 3
        s.on_event(events.motion())
        self.assertIn("ignored: the same wave, 3 s after the warning", s.last_reason)
        clock.t += 6
        s.on_event(events.motion())
        self.assertEqual(s.last_reason, "ALARM: motion again 9 s after the warning")
        s.on_event(events.motion())
        self.assertEqual(s.last_reason, "ignored: the alarm is already on")
        s, clock = state(Mode.AWAY)
        s.on_event(events.doorbell())
        clock.t += 20
        s.on_event(events.motion())
        self.assertIn("ignored: a visitor rang 20 s ago (motion stays quiet for 60 s", s.last_reason)

    def test_a_mode_change_is_a_new_visit(self):
        s, clock = state(Mode.AWAY)
        s.on_event(events.doorbell())                              # a visitor while away...
        s.arm(Mode.NIGHT, ChangedBy.DASHBOARD)                     # ...then the owner goes to bed
        clock.t += 30
        s.on_event(events.motion())
        self.assertEqual(s.last_outcome, MotionOutcome.GRACE)      # not quiet any more

    def test_settings_values(self):
        s, clock = state(window=10)
        s.motion_grace_s, s.doorbell_settle_s = 5, 0
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
