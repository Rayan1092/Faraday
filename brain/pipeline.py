"""One event in, everything Faraday does about it out.

For each incoming event, in this order:
1. modes/ screens speech for the passphrase (redacts it, maybe disarms).
2. log/ writes the event, the redacted version.
3. modes/ updates the alert level (motion while armed).
4. Follow-up events from steps 1 and 3 (mode_changed, alert_changed) are
   handled the same way, so the LED changes before anything slow happens.
5. rules/baseline.py actions are emitted right away (lights, photos).
6. If Bonsai is about to be asked about a visitor, a short "One moment" is
   emitted first so there's no silence.
7. The brain's actions are added on top, minus anything already done.
8. set_mode actions go back through modes/ (which refuses unsafe changes).

Actions and events are passed to on_action / on_event as soon as they exist,
so integration/ can run hardware without waiting for Bonsai.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from shared import (Action, ActionType, ChangedBy, Event, EventType, Mode, Source,
                    make_action)

from log import EventLog
from modes import ModeState
from rules import baseline_actions, merge

from .core import Brain
from .settings import load_settings, passphrase

FILLER = "One moment, please."
MAX_DEPTH = 4  # event -> action -> follow-up event chains, so nothing can loop
TRACE_WINDOW = 50  # log entries searched when tracing caused_by


@dataclass
class Result:
    events: List[Event] = field(default_factory=list)    # handled, already redacted
    actions: List[Action] = field(default_factory=list)  # emitted, in order


class Pipeline:
    def __init__(self, brain: Brain, modes: ModeState, log: EventLog,
                 on_event: Optional[Callable[[Event], None]] = None,
                 on_action: Optional[Callable[[Action], None]] = None,
                 say: Callable[[str], None] = print):
        self.brain = brain
        self.modes = modes
        self.log = log
        self.on_event = on_event
        self.on_action = on_action
        self.say = say
        self._photo_cause: Dict[str, str] = {}  # take_photo action id -> event type

    @classmethod
    def from_settings(cls, settings: Optional[Dict[str, Any]] = None, use_llm: bool = True,
                      log: Optional[EventLog] = None, mode: str = Mode.HOME,
                      **callbacks: Any) -> "Pipeline":
        settings = settings or load_settings()
        say = callbacks.get("say", print)
        brain = Brain.from_settings(settings, use_llm=use_llm, log=say)
        window = settings.get("alerts", {}).get("alarm_window_s", 60)
        modes = ModeState(passphrase(settings), window, mode)
        return cls(brain, modes, log or EventLog.from_settings(settings), **callbacks)

    # ---- entry points ------------------------------------------------------

    def start(self) -> Result:
        """Announce the starting mode (mode_changed, changed_by startup)."""
        return self.handle(self.modes.startup())

    def handle(self, event: Event) -> Result:
        result = Result()
        self._process(event, result, 0)
        return result

    def owner_set_mode(self, mode: str) -> Optional[Result]:
        """Dashboard (or sim key) arming. Going home needs owner_passphrase()."""
        return self._follow(self.modes.arm(mode, ChangedBy.DASHBOARD, "Set by the owner."))

    def owner_passphrase(self, attempt: str) -> Optional[Result]:
        """A typed passphrase. The attempt itself is never logged. None if wrong."""
        return self._follow(self.modes.disarm_with_passphrase(attempt))

    # ---- internals ---------------------------------------------------------

    def _follow(self, events: List[Event]) -> Optional[Result]:
        if not events:
            return None
        result = Result()
        for e in events:
            self._process(e, result, 0)
        return result

    def _process(self, event: Event, result: Result, depth: int) -> None:
        if depth > MAX_DEPTH:
            return
        event, follow_ups = self.modes.screen_speech(event)
        self.log.write(event)
        result.events.append(event)
        if self.on_event:
            self.on_event(event)
        follow_ups += self.modes.on_event(event)
        for e in follow_ups:
            self._process(e, result, depth + 1)

        mode = self.modes.mode
        done: List[Action] = []
        for action in baseline_actions(event, mode):
            self._emit(action, result, done)

        def filler(ev: Event, md: str) -> None:
            if self._is_visitor_conversation(ev, md):
                self._emit(make_action(ActionType.SPEAK, {"text": FILLER}, Source.BRAIN,
                                       caused_by=ev.id), result, done)

        # A wide window so caused_by chains (photo -> take_photo -> doorbell) can be
        # traced; the prompt itself only shows the last brain.recent_lines entries.
        recent = [e for e in self.log.recent(TRACE_WINDOW) if e.get("id") != event.id]
        try:
            decision = self.brain.decide(event, mode, recent, before_model=filler)
        except Exception as e:  # the baseline already ran; never stall on a brain bug
            self.say(f"[pipeline] brain failed on {event.type}: {e!r}")
            return
        added = merge(done, decision.actions)
        if decision.actions or decision.elapsed_s is not None:
            took = f", Bonsai {decision.elapsed_s:.2f}s" if decision.elapsed_s is not None else ""
            skipped = len(decision.actions) - len(added)
            self.say(f"  brain -> {len(decision.actions)} action(s) via {decision.via}{took}"
                     + (f", {skipped} already done" if skipped else ""))
        for action in added:
            self._emit(action, result, done)

        for action in done:
            if action.type == ActionType.SET_MODE:
                by = ChangedBy.RULE if action.source == Source.RULES else ChangedBy.BRAIN
                for e in self.modes.arm(action.args["mode"], by,
                                        f"{by.capitalize()} chose set_mode.", caused_by=action.id):
                    self._process(e, result, depth + 1)

    def _emit(self, action: Action, result: Result, done: List[Action]) -> None:
        stored = self.log.write(action)
        # Defense in depth: hand on exactly what was logged (redacted).
        action = Action(type=action.type, source=action.source, args=stored["args"],
                        timestamp=action.timestamp, id=action.id, caused_by=action.caused_by)
        done.append(action)
        result.actions.append(action)
        if action.type == ActionType.TAKE_PHOTO:
            cause = next((e.type for e in result.events if e.id == action.caused_by), "")
            self._photo_cause[action.id] = cause
        if self.on_action:
            self.on_action(action)

    def _is_visitor_conversation(self, event: Event, mode: str) -> bool:
        """Speech while armed (owner is out or asleep), or the camera looking at
        someone who rang the doorbell."""
        if event.type == EventType.SPEECH_HEARD:
            return mode in Mode.ARMED
        if event.type == EventType.PHOTO_DESCRIBED:
            return self._photo_cause.get(event.caused_by or "") == EventType.DOORBELL
        return False
