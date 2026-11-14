"""Home / Away / Night and the alert level. Plain code, no AI.

Mode rules:
- arm() goes to away or night. The brain and rules may only arm from home;
  they can never switch away<->night or leave an armed mode (PLAN.md Q19).
- Only disarm_with_passphrase() goes back to home.

Alert rules (FORMATS.md alert_changed):
- First motion in away/night: normal -> warning.
- Motion again within alarm_window_s of the last motion: warning -> alarm.
- Later than that: stays warning, the window restarts, no event.
- Only the passphrase disarm returns to normal. It sends mode_changed, then
  alert_changed, both with the same caused_by.
- Motion at home and doorbell presses never change the alert.
  Switching away<->night doesn't reset it.

Every method returns the events it produced (possibly none). The caller puts
them on the bus; this class never does I/O.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional, Tuple

from shared import (AlertLevel, ChangedBy, Event, EventType, Mode, Source,
                    make_event)

from .passphrase import find_passphrase, redact

Clock = Callable[[], float]


class ModeState:
    def __init__(self, passphrase: str, alarm_window_s: float = 60,
                 mode: str = Mode.HOME, clock: Clock = time.monotonic):
        if mode not in Mode.ALL:
            raise ValueError(f"unknown mode {mode!r}")
        self._passphrase = passphrase
        self.alarm_window_s = float(alarm_window_s)
        self._mode = mode
        self._alert = AlertLevel.NORMAL
        self._last_motion: Optional[float] = None
        self._clock = clock

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def alert(self) -> str:
        return self._alert

    @property
    def armed(self) -> bool:
        return self._mode in Mode.ARMED

    # ---- incoming events ---------------------------------------------------

    def on_event(self, event: Event) -> List[Event]:
        """React to an incoming event. Today only motion matters (alerts)."""
        if event.type != EventType.MOTION or not self.armed:
            return []
        now = self._clock()
        recent = (self._last_motion is not None
                  and now - self._last_motion <= self.alarm_window_s)
        self._last_motion = now
        if self._alert == AlertLevel.NORMAL:
            return [self._alert_to(AlertLevel.WARNING,
                                   f"Motion in {self._mode} mode.", event.id)]
        if self._alert == AlertLevel.WARNING and recent:
            return [self._alert_to(AlertLevel.ALARM,
                                   f"Motion again within {self.alarm_window_s:.0f} s.", event.id)]
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
        return [self._mode_to(mode, changed_by, reason, caused_by)]

    def disarm_with_passphrase(self, attempt: str,
                               caused_by: Optional[str] = None) -> List[Event]:
        """Go home if attempt contains the passphrase. The only way back to home."""
        if not self.armed or find_passphrase(attempt, self._passphrase) is None:
            return []
        events = [self._mode_to(Mode.HOME, ChangedBy.PASSPHRASE, "Passphrase accepted.", caused_by)]
        if self._alert != AlertLevel.NORMAL:
            events.append(self._alert_to(AlertLevel.NORMAL, "Passphrase accepted.", caused_by))
        self._last_motion = None
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
