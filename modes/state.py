"""Home / Away / Night and the alert level. Plain code, no AI.

Mode rules:
- arm() goes to away or night. The brain and rules may only arm from home;
  they can never switch away<->night or leave an armed mode (PLAN.md Q19).
- Only disarm_with_passphrase() goes back to home.

Alert rules (FORMATS.md alert_changed), the same in away and night. A visitor
who walks up (motion) and then rings is never warned or alarmed (owner, 2026-11-15):
- Motion while the alert is normal starts a grace period (motion_grace_s, 15 s).
  The porch light goes on at once (rules/baseline.py), nothing else yet.
- A doorbell within the grace period: a visitor. The pending warning is
  cancelled and the alert stays normal. The doorbell radio only reports a press
  after the burst ends (about 1.5 s), so a press counts until doorbell_settle_s
  (2.5 s) after the grace period.
- No doorbell by the end of it (end_grace(), called by the pipeline's timer):
  normal -> warning, and `grace_ended` names the motion. The pipeline then speaks
  the warning, takes the photo and runs the threat label.
- After a doorbell, motion doesn't escalate for visitor_quiet_s (120 s).
- Alarm only from motion after a warning: at least min_gap_s (8 s) and at most
  alarm_window_s (60 s) after the warning, with no doorbell in between. Sooner is
  the same wave (ignored). Later is a new walk-up: a new grace period, and if
  nobody rings the warning steps run again (no event: the level stays warning).
- A 'suspicious' threat label while armed raises normal to warning at once.
- Only the passphrase disarm returns to normal. It sends mode_changed, then
  alert_changed, both with the same caused_by.
- Motion at home and doorbell presses at home never change the alert.
  Switching away<->night doesn't reset it.

Every method returns the events it produced (possibly none). The caller puts
them on the bus; this class never does I/O. `last_outcome` says what the last
motion did (MotionOutcome), so the baseline knows which steps to run.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional, Tuple

from shared import (AlertLevel, ChangedBy, Event, EventType, Mode, Source,
                    ThreatCategory, make_event)

from .passphrase import find_passphrase, redact

Clock = Callable[[], float]


class MotionOutcome:
    """What one motion did. rules/baseline.py picks the steps from it."""
    GRACE = "grace"        # first motion: porch light, warning pending
    PENDING = "pending"    # more motion while the warning is pending
    QUIET = "quiet"        # a visitor rang a moment ago: no escalation
    IGNORED = "ignored"    # warning already on: same wave, or a new window starts
    ALARM = "alarm"        # warning -> alarm
    NONE = "none"          # home, or the alarm is already on


class ModeState:
    def __init__(self, passphrase: str, alarm_window_s: float = 60,
                 mode: str = Mode.HOME, clock: Clock = time.monotonic,
                 min_gap_s: float = 8, motion_grace_s: float = 15,
                 visitor_quiet_s: float = 120, doorbell_settle_s: float = 2.5):
        if mode not in Mode.ALL:
            raise ValueError(f"unknown mode {mode!r}")
        self._passphrase = passphrase
        self.alarm_window_s = float(alarm_window_s)
        self.min_gap_s = float(min_gap_s)
        self.motion_grace_s = float(motion_grace_s)
        self.visitor_quiet_s = float(visitor_quiet_s)
        self.doorbell_settle_s = float(doorbell_settle_s)
        self._mode = mode
        self._alert = AlertLevel.NORMAL
        self._clock = clock
        self._grace_since: Optional[float] = None    # a warning is pending since then
        self._grace_cause: Optional[str] = None      # the motion event that started it
        self._warned_at: Optional[float] = None      # when the alarm window started
        self._last_doorbell: Optional[float] = None
        self.last_outcome: Optional[str] = None
        self.grace_ended: Optional[str] = None       # set by end_grace: the motion to warn about

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def alert(self) -> str:
        return self._alert

    @property
    def armed(self) -> bool:
        return self._mode in Mode.ARMED

    @property
    def grace_pending(self) -> Optional[str]:
        """The motion event id whose warning is pending, or None."""
        return self._grace_cause if self._grace_since is not None else None

    def seconds_since_warning(self) -> Optional[float]:
        return None if self._warned_at is None else self._clock() - self._warned_at

    # ---- incoming events ---------------------------------------------------

    def on_event(self, event: Event) -> List[Event]:
        """React to an incoming event: motion, doorbell (alerts) and the threat label."""
        if event.type == EventType.THREAT_ASSESSED:
            return self._on_threat(event)
        if event.type == EventType.DOORBELL and self.armed:
            self._on_doorbell()
            return []
        if event.type == EventType.MOTION:
            self.last_outcome = self._on_motion(event) if self.armed else MotionOutcome.NONE
            if self.last_outcome == MotionOutcome.ALARM:
                since = self._clock() - (self._warned_at or 0)
                return [self._alert_to(AlertLevel.ALARM,
                                       f"Motion again {since:.0f} s after the warning.", event.id)]
        return []

    def _on_doorbell(self) -> None:
        """A visitor: cancel a pending warning, and motion is quiet for a while."""
        self._last_doorbell = self._clock()
        self._grace_since = self._grace_cause = None

    def _on_motion(self, event: Event) -> str:
        now = self._clock()
        if self._alert == AlertLevel.ALARM:
            return MotionOutcome.NONE
        if self._last_doorbell is not None and now - self._last_doorbell <= self.visitor_quiet_s:
            return MotionOutcome.QUIET
        if self._alert == AlertLevel.NORMAL:
            if self._grace_since is None:
                self._grace_since, self._grace_cause = now, event.id
                return MotionOutcome.GRACE
            return MotionOutcome.PENDING
        # warning
        if self._warned_at is None or now - self._warned_at > self.alarm_window_s:
            # A warning from long ago: this is a new walk-up. Grace again, so a visitor
            # who then rings is never alarmed; nobody rings -> the warning steps again.
            if self._grace_since is None:
                self._grace_since, self._grace_cause = now, event.id
                return MotionOutcome.GRACE
            return MotionOutcome.PENDING
        if (self._last_doorbell is not None and self._last_doorbell >= self._warned_at):
            return MotionOutcome.QUIET       # a doorbell in between: never an alarm
        if now - self._warned_at < self.min_gap_s:
            return MotionOutcome.IGNORED     # the same wave as before
        return MotionOutcome.ALARM

    def grace_remaining(self) -> Optional[float]:
        """Seconds until the pending grace period ends (radio margin included), or None."""
        if self._grace_since is None:
            return None
        return self.motion_grace_s + self.doorbell_settle_s - (self._clock() - self._grace_since)

    def grace_due(self) -> bool:
        left = self.grace_remaining()
        return left is not None and left <= 0

    def end_grace(self, force: bool = False) -> List[Event]:
        """The grace period is over with no doorbell. Sets `grace_ended` to the motion
        that started it when the warning steps should run (else None), and returns
        [alert_changed normal -> warning] (caused_by that motion), or [] if the
        alert was already warning (a new walk-up long after an old warning: warned
        again, no level change) or nothing was pending or due.
        force=True ends it early (the simulator's "w" key and tests)."""
        self.grace_ended = None
        if self._grace_since is None or not (force or self.grace_due()):
            return []
        cause = self._grace_cause
        self._grace_since = self._grace_cause = None
        if not self.armed or self._alert == AlertLevel.ALARM:
            return []
        self._warned_at = self._clock()
        self.grace_ended = cause
        if self._alert == AlertLevel.WARNING:
            return []
        return [self._alert_to(AlertLevel.WARNING,
                               f"Motion with no doorbell for {self.motion_grace_s:.0f} s.", cause)]

    def _on_threat(self, event: Event) -> List[Event]:
        """A 'suspicious' label while armed raises normal to warning. That's all it can
        do: a label never lowers an alert, and every other category changes nothing."""
        if (event.data.get("category") == ThreatCategory.SUSPICIOUS and self.armed
                and self._alert == AlertLevel.NORMAL):
            self._grace_since = self._grace_cause = None
            self._warned_at = self._clock()
            return [self._alert_to(AlertLevel.WARNING, "Suspicious person at the door.", event.id)]
        return []

    def screen_speech(self, event: Event) -> Tuple[Event, List[Event]]:
        """Check speech for the passphrase before anyone logs or sees it.

        Returns the event with the passphrase replaced by [passphrase], plus the
        disarm events if it matched while armed.
        """
        if event.type != EventType.SPEECH_HEARD:
            return event, []
        text = str(event.data.get("text", ""))
        clean, matched = redact(text, self._passphrase)
        if not matched:
            return event, []
        screened = Event(type=event.type, source=event.source,
                         data={**event.data, "text": clean}, timestamp=event.timestamp,
                         id=event.id, caused_by=event.caused_by)
        return screened, self.disarm_with_passphrase(text, caused_by=event.id)

    # ---- mode changes ------------------------------------------------------

    def startup(self) -> Event:
        """The first mode_changed after launch, so the LED and dashboard know the mode."""
        return make_event(EventType.MODE_CHANGED,
                          {"mode": self._mode, "previous": self._mode,
                           "changed_by": ChangedBy.STARTUP, "reason": "Faraday started."},
                          Source.MODES)

    def arm(self, mode: str, changed_by: str, reason: str = "",
            caused_by: Optional[str] = None) -> List[Event]:
        """Switch to away or night. Returns [mode_changed], or [] if refused."""
        if mode not in Mode.ARMED or mode == self._mode:
            return []
        if changed_by in (ChangedBy.BRAIN, ChangedBy.RULE) and self.armed:
            return []
        # A new mode is a new situation: a visitor's quiet and a pending warning end here.
        # (The alert itself stays: only the passphrase lowers it.)
        self._grace_since = self._grace_cause = self._last_doorbell = None
        return [self._mode_to(mode, changed_by, reason, caused_by)]

    def disarm_with_passphrase(self, attempt: str,
                               caused_by: Optional[str] = None) -> List[Event]:
        """Go home if attempt contains the passphrase. The only way back to home."""
        if not self.armed or find_passphrase(attempt, self._passphrase) is None:
            return []
        events = [self._mode_to(Mode.HOME, ChangedBy.PASSPHRASE, "Passphrase accepted.", caused_by)]
        if self._alert != AlertLevel.NORMAL:
            events.append(self._alert_to(AlertLevel.NORMAL, "Passphrase accepted.", caused_by))
        self._grace_since = self._grace_cause = self._warned_at = self._last_doorbell = None
        return events

    # ---- helpers -----------------------------------------------------------

    def _mode_to(self, mode: str, changed_by: str, reason: str,
                 caused_by: Optional[str]) -> Event:
        previous, self._mode = self._mode, mode
        return make_event(EventType.MODE_CHANGED,
                          {"mode": mode, "previous": previous,
                           "changed_by": changed_by, "reason": reason},
                          Source.MODES, caused_by=caused_by)

    def _alert_to(self, level: str, reason: str, caused_by: Optional[str]) -> Event:
        previous, self._alert = self._alert, level
        return make_event(EventType.ALERT_CHANGED,
                          {"level": level, "previous": previous, "reason": reason},
                          Source.MODES, caused_by=caused_by)
