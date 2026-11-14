"""One event in, everything Faraday does about it out.

For each incoming event, in this order:
1. modes/ screens speech for the passphrase (redacts it, maybe disarms).
2. log/ writes the event, the redacted version.
3. modes/ updates the alert level (motion while armed).
4. Follow-up events from steps 1 and 3 (mode_changed, alert_changed) are
   handled the same way, so the LED changes before anything slow happens.
5. rules/baseline.py actions are emitted right away (lights, photos, fixed
   warning lines), escalating with the alert level.
6. Stored user rules (add_rule) fire, minus anything the baseline already did.
7. If Bonsai is about to be asked about a visitor, a short "One moment" is
   emitted first so there's no silence.
8. The brain's actions are added on top, minus anything already done.
9. Software actions run: set_mode goes back through modes/ (which refuses unsafe
   changes), add_rule is stored, summarize_log is spoken.
10. At alarm in Away, once the photo description arrives, Bonsai is asked for one
    confrontation line in a background thread. Plain code has already spoken.

Actions and events are passed to on_action / on_event as soon as they exist,
so integration/ can run hardware without waiting for Bonsai. One lock keeps
events from the app, the dashboard and background threads from interleaving.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from shared import (Action, ActionType, AlertLevel, ChangedBy, Event, EventType, Mode,
                    Source, make_action)

from log import EventLog
from modes import ModeState
from rules import RuleStore, baseline_actions, merge

from . import summary
from .core import Brain
from .settings import load_settings, passphrase

FILLER = "One moment, please."
CHECKING = "Let me check."
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
                 say: Callable[[str], None] = print,
                 rules: Optional[RuleStore] = None):
        self.brain = brain
        self.modes = modes
        self.log = log
        self.rules = rules
        self.on_event = on_event
        self.on_action = on_action
        self.say = say
        self.lock = threading.RLock()
        # take_photo action id -> (event type, mode, alert level) when the photo was asked for
        self._photo_cause: Dict[str, Tuple[str, str, str]] = {}
        self._background: List[threading.Thread] = []

    @classmethod
    def from_settings(cls, settings: Optional[Dict[str, Any]] = None, use_llm: bool = True,
                      log: Optional[EventLog] = None, mode: str = Mode.HOME,
                      **callbacks: Any) -> "Pipeline":
        settings = settings or load_settings()
        say = callbacks.get("say", print)
        brain = Brain.from_settings(settings, use_llm=use_llm, log=say)
        window = settings.get("alerts", {}).get("alarm_window_s", 60)
        modes = ModeState(passphrase(settings), window, mode)
        rules = callbacks.pop("rules", None) or RuleStore.from_settings(settings)
        for problem in rules.skipped:
            say(f"[rules] {problem}")
        return cls(brain, modes, log or EventLog.from_settings(settings), rules=rules,
                   **callbacks)

    # ---- entry points ------------------------------------------------------

    def start(self) -> Result:
        """Announce the starting mode (mode_changed, changed_by startup)."""
        return self.handle(self.modes.startup())

    def handle(self, event: Event) -> Result:
        with self.lock:
            result = Result()
            self._process(event, result, 0)
            return result

    def owner_set_mode(self, mode: str) -> Optional[Result]:
        """Dashboard (or sim key) arming. Going home needs owner_passphrase()."""
        with self.lock:
            return self._follow(self.modes.arm(mode, ChangedBy.DASHBOARD, "Set by the owner."))

    def owner_passphrase(self, attempt: str) -> Optional[Result]:
        """A typed passphrase. The attempt itself is never logged. None if wrong."""
        with self.lock:
            return self._follow(self.modes.disarm_with_passphrase(attempt))

    def wait_background(self, timeout: float = 30) -> None:
        """Wait for background Bonsai calls (the alarm confrontation). For tests and shutdown."""
        for t in list(self._background):
            t.join(timeout)
        self._background = [t for t in self._background if t.is_alive()]

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

        mode, alert = self.modes.mode, self.modes.alert
        done: List[Action] = []
        for action in baseline_actions(event, mode, alert):
            self._emit(action, result, done)
        if self.rules is not None:
            for action in merge(done, self.rules.fire(event, mode)):
                self._emit(action, result, done)
        if event.type == EventType.PHOTO_DESCRIBED:
            cause = self._photo_cause.get(event.caused_by or "")
            if cause == (EventType.MOTION, Mode.AWAY, AlertLevel.ALARM):
                self._confront_later(event)

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

        for action in list(done):
            if action.type == ActionType.SET_MODE:
                by = ChangedBy.RULE if action.source == Source.RULES else ChangedBy.BRAIN
                for e in self.modes.arm(action.args["mode"], by,
                                        f"{by.capitalize()} chose set_mode.", caused_by=action.id):
                    self._process(e, result, depth + 1)
            elif action.type == ActionType.ADD_RULE and self.rules is not None:
                heard = next((str(e.data.get("text", "")) for e in result.events
                              if e.id == action.caused_by and e.type == EventType.SPEECH_HEARD), "")
                rule = self.rules.add(action.args, description=heard, caused_by=action.id)
                self.say(f"  [rules] {'stored ' + rule['id'] if rule else 'refused'}: "
                         f"{(rule or action.args).get('description', '')}")
            elif action.type == ActionType.SUMMARIZE_LOG:
                self._summarize(action, result, done)

    def _emit(self, action: Action, result: Result, done: List[Action]) -> None:
        stored = self.log.write(action)
        # Defense in depth: hand on exactly what was logged (redacted).
        action = Action(type=action.type, source=action.source, args=stored["args"],
                        timestamp=action.timestamp, id=action.id, caused_by=action.caused_by)
        done.append(action)
        result.actions.append(action)
        if action.type == ActionType.TAKE_PHOTO:
            cause = next((e.type for e in result.events if e.id == action.caused_by), "")
            self._photo_cause[action.id] = (cause, self.modes.mode, self.modes.alert)
        if self.on_action:
            self.on_action(action)

    def _summarize(self, action: Action, result: Result, done: List[Action]) -> None:
        """Spoken summary of the log: Bonsai from plain-code facts, else a simple list."""
        happened = summary.collect(summary.window(self.log.recent(500),
                                                  int(action.args.get("minutes", 60))))
        text = None
        if self.brain.client is not None and not happened.empty:
            self._emit(make_action(ActionType.SPEAK, {"text": CHECKING}, Source.BRAIN,
                                   caused_by=action.id), result, done)
            text = self.brain.summarize(summary.facts(happened))
        text = text or summary.spoken_list(happened)
        self._emit(make_action(ActionType.SPEAK, {"text": text}, Source.BRAIN,
                               caused_by=action.id), result, done)

    def _confront_later(self, event: Event) -> None:
        """Ask Bonsai for one confrontation line without blocking the pipeline."""
        description = str(event.data.get("description", ""))

        def run() -> None:
            line = self.brain.confront(description)
            if not line:
                return
            try:
                action = make_action(ActionType.SPEAK, {"text": line}, Source.BRAIN,
                                     caused_by=event.id)
            except ValueError:
                return
            with self.lock:
                # Bonsai can take a while. If the owner disarmed (or the alert was
                # cleared) meanwhile, the line is stale: never confront after that.
                if self.modes.mode != Mode.AWAY or self.modes.alert != AlertLevel.ALARM:
                    self.say(f"  [brain] confrontation dropped, no longer in alarm: {line}")
                    return
                self._emit(action, Result(), [])

        thread = threading.Thread(target=run, name="faraday-confront", daemon=True)
        self._background.append(thread)
        thread.start()

    def _is_visitor_conversation(self, event: Event, mode: str) -> bool:
        """Speech while armed (owner is out or asleep), or the camera looking at
        someone who rang the doorbell."""
        if event.type == EventType.SPEECH_HEARD:
            return mode in Mode.ARMED
        if event.type == EventType.PHOTO_DESCRIBED:
            cause = self._photo_cause.get(event.caused_by or "")
            return bool(cause) and cause[0] == EventType.DOORBELL
        return False
