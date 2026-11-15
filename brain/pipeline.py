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
7. Doorbell while away: the fixed greeting (step 5) is followed by the owner's
   words for visitors, if any. The doorbell photo gets its threat label but no
   reply: Bonsai only answers what a visitor says. Nobody answers: nothing more.
8. The brain's actions are added on top, minus anything already done.
9. Software actions run: set_mode goes back through modes/ (which refuses unsafe
   changes), add_rule is stored, summarize_log is spoken.
10. At alarm (Away or Night), once the photo description arrives, Bonsai is asked for one
    confrontation line in a background thread. Plain code has already spoken.

Photos the owner asks for (owner_snapshot: the dashboard Snapshot button) get their
threat label like any photo, but nobody at the door is greeted for them.
"Who's at the door?" (Home or Night) is answered by plain code: "Let me look.", a
fresh photo, then its description and threat label spoken (brain/look.py), or
"I can't see the door right now." if the camera or vision fails or is too slow.

Actions and events are passed to on_action / on_event as soon as they exist,
so integration/ can run hardware without waiting for Bonsai. One lock keeps
events from the app, the dashboard and background threads from interleaving.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from shared import (Action, ActionType, AlertLevel, ChangedBy, Event, EventType, Mode,
                    Source, ThreatCategory, make_action)

from log import EventLog
from modes import ModeState
from rules import InstructionStore, RuleStore, baseline_actions, lights_off_on_disarm, merge
from rules.baseline import DOORBELL_GREETING, warning_actions
from modes import MotionOutcome
from rules import instructions as owner_instructions

from . import lines, look, owner, summary, threat
from .threat import ThreatAssessor
from .core import Brain, _age_s
from .settings import load_settings, passphrase
from modes.passphrase import REDACTED

SNAPSHOT_REASON = "Snapshot from the dashboard"
INSTRUCTION_REFUSED = ("I can only pass on words to visitors. "
                       "Lights, modes and the alarm stay with you.")
CHECKING = "Let me check."
GREET_AGAIN_S = 60  # a second doorbell press within this gets no second greeting
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
                 rules: Optional[RuleStore] = None,
                 assessor: Optional[Any] = None,
                 instructions: Optional[InstructionStore] = None,
                 router: Optional[owner.OwnerRouter] = None):
        self.brain = brain
        self.modes = modes
        self.log = log
        self.rules = rules
        # Owner instructions for visitors ("if a delivery comes, ask them to..."). The
        # brain reads them for the visitor prompt; they only ever shape speech.
        self.instructions = instructions
        brain.instructions = instructions
        # Threat label after every photo. Rules-only by default; from_settings gives it Bonsai.
        self.assessor = assessor if assessor is not None else ThreatAssessor(None)
        # The owner's requests ("Hey Faraday, ..."). Keywords only by default; from_settings gives it the 2B.
        self.router = router if router is not None else owner.OwnerRouter(None)
        self.on_event = on_event
        self.on_action = on_action
        self.say = say
        self.lock = threading.RLock()
        # take_photo action id -> (event type, mode, alert level) when the photo was asked for
        self._photo_cause: Dict[str, Tuple[str, str, str]] = {}
        self._background: List[threading.Thread] = []
        self._confronted: set = set()  # photo_described ids that already got a confrontation
        # take_photo ids the owner asked for -> "quiet" (Snapshot), "speak" (who's at the
        # door?), "late" (already answered "I can't see the door", drop it when it comes)
        self._owner_photos: Dict[str, str] = {}
        self._look_timers: Dict[str, threading.Timer] = {}
        self._rules_stored: set = set()  # add_rule action ids the owner router already stored
        self.look_timeout_s = 25.0  # vision on the Mac takes a few seconds; then give up

    @classmethod
    def from_settings(cls, settings: Optional[Dict[str, Any]] = None, use_llm: bool = True,
                      log: Optional[EventLog] = None, mode: str = Mode.HOME,
                      **callbacks: Any) -> "Pipeline":
        settings = settings or load_settings()
        say = callbacks.get("say", print)
        brain = Brain.from_settings(settings, use_llm=use_llm, log=say)
        alerts = settings.get("alerts", {})
        modes = ModeState(passphrase(settings), alerts.get("alarm_window_s", 60), mode,
                          min_gap_s=alerts.get("min_gap_s", 8),
                          motion_grace_s=alerts.get("motion_grace_s", 15),
                          visitor_quiet_s=alerts.get("visitor_quiet_s", 120))
        rules = callbacks.pop("rules", None) or RuleStore.from_settings(settings)
        for problem in rules.skipped:
            say(f"[rules] {problem}")
        assessor = callbacks.pop("assessor", None) or ThreatAssessor.from_settings(
            settings, use_llm=use_llm, log=say)
        instructions = callbacks.pop("instructions", None) or InstructionStore.from_settings(settings)
        for problem in instructions.skipped:
            say(f"[instructions] {problem}")
        router = callbacks.pop("router", None) or owner.OwnerRouter(
            brain.fast_client if use_llm else None, brain.fast_model, log=say)
        if use_llm:
            brain.warm_up(getattr(assessor, "client", None))
        return cls(brain, modes, log or EventLog.from_settings(settings), rules=rules,
                   assessor=assessor, instructions=instructions, router=router, **callbacks)

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

    def owner_snapshot(self, reason: str = SNAPSHOT_REASON, action: Optional[Action] = None,
                       speak: bool = False) -> Action:
        """The owner asked for a photo (dashboard Snapshot). A take_photo with source
        dashboard goes to integration's camera; its photo_described gets the threat label
        but no visitor greeting. Pass `action` to know its id before the lock is free."""
        if action is None:
            action = snapshot_action(reason)
        with self.lock:
            self._owner_photos[action.id] = "speak" if speak else "quiet"
            self._emit(action, Result(), [])
        return action

    def _look(self, question: Event, result: Result, done: List[Action]) -> None:
        """'Who's at the door?': say "Let me look.", take a fresh photo, and answer when
        its description arrives (or say we can't see, if it doesn't in time)."""
        self._emit(make_action(ActionType.SPEAK, {"text": look.LOOKING}, Source.BRAIN,
                               caused_by=question.id), result, done)
        photo = snapshot_action(look.LOOK_REASON, caused_by=question.id)
        self._owner_photos[photo.id] = "speak"
        self._emit(photo, result, done)
        timer = threading.Timer(self.look_timeout_s, self._look_timed_out, args=(photo,))
        timer.daemon = True
        self._look_timers[photo.id] = timer
        timer.start()

    def _look_timed_out(self, photo: Action) -> None:
        with self.lock:
            self._look_timers.pop(photo.id, None)
            if self._owner_photos.get(photo.id) != "speak":
                return
            self._owner_photos[photo.id] = "late"
            self.say(f"  [look] no photo after {self.look_timeout_s:.0f}s")
            self._emit(make_action(ActionType.SPEAK, {"text": look.CANT_SEE}, Source.BRAIN,
                                   caused_by=photo.id), Result(), [])

    def tick(self, force: bool = False) -> Optional[Result]:
        """End a motion grace period that's due (the timer calls this): no doorbell came,
        so the warning goes up, the photo is taken and the warning line spoken. A
        visitor who rang meanwhile cancelled it in modes/. force=True: end it now
        (the backup demo at rehearsal speed)."""
        with self.lock:
            events = self.modes.end_grace(force)
            if not events:
                return None
            result = Result()
            mode, motion_id = self.modes.mode, events[0].caused_by
            for e in events:
                self._process(e, result, 0)
            for action in warning_actions(motion_id, mode):
                self._emit(action, result, [])
                if action.type == ActionType.TAKE_PHOTO:   # the threat label: motion, no ring
                    self._photo_cause[action.id] = (EventType.MOTION, mode, self.modes.alert)
            return result

    def _start_grace_timer(self) -> None:
        timer = threading.Timer(self.modes.motion_grace_s + 0.05, self._grace_timer_fired)
        timer.daemon = True
        timer.start()

    def _grace_timer_fired(self) -> None:
        try:
            self.tick()
        except Exception as e:  # a timer thread must never crash the app
            self.say(f"[pipeline] grace timer failed: {e!r}")

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
        greet = event.type == EventType.DOORBELL and self._greet_visitor(event, alert)
        outcome = self.modes.last_outcome if event.type == EventType.MOTION else None
        if outcome == MotionOutcome.GRACE:
            self._start_grace_timer()   # no doorbell in time: the warning (tick)
        for action in baseline_actions(event, mode, alert, outcome):
            if (action.type == ActionType.SPEAK and action.args.get("text") == DOORBELL_GREETING
                    and not greet):
                continue   # an alert is on, or they already heard it: no friendly greeting again
            self._emit(action, result, done)
        # Passphrase disarm: the lights Faraday turned on go off (plain code, logged).
        for action in merge(done, lights_off_on_disarm(event, self.log.recent(500))):
            self._emit(action, result, done)
        if self.rules is not None:
            for action in merge(done, self.rules.fire(event, mode)):
                self._emit(action, result, done)
        if event.type == EventType.PHOTO_DESCRIBED:
            cause = self._photo_cause.get(event.caused_by or "")
            if (cause and cause[0] == EventType.MOTION and cause[1] in Mode.ARMED
                    and cause[2] == AlertLevel.ALARM):
                self._confront_later(event)  # the threat label below won't repeat it

        if (event.type == EventType.DOORBELL and mode == Mode.AWAY and greet
                and self.instructions is not None):
            # Right after the fixed greeting: the owner's words for visitors ("tell them
            # I'm in the back garden"). Delivery words wait until the visitor says why.
            words = [owner_instructions.spoken_line(i) for i in self.instructions.relevant("visitor")]
            text = ""
            for w in words:                      # whole sentences only, within the speak limit
                if len((text + " " + w).strip()) <= 240:
                    text = (text + " " + w).strip()
            if text:
                self._emit(make_action(ActionType.SPEAK, {"text": text}, Source.BRAIN,
                                       caused_by=event.id), result, done)

        if event.type == EventType.PHOTO_DESCRIBED:
            # The threat label comes first, so it can shape what Faraday says next.
            threat = self._assess(event, mode)
            self._process(threat, result, depth + 1)
            mode = self.modes.mode
            if (threat.data["category"] == ThreatCategory.SUSPICIOUS and mode in Mode.ARMED):
                self._confront_later(event)  # early: don't wait for the alarm
            if event.caused_by in self._owner_photos:
                how = self._owner_photos.pop(event.caused_by)
                timer = self._look_timers.pop(event.caused_by or "", None)
                if timer is not None:
                    timer.cancel()
                if how == "speak":  # "who's at the door?": the answer, from plain code
                    self._emit(make_action(ActionType.SPEAK,
                                           {"text": look.look_line(event, threat.data["category"])},
                                           Source.BRAIN, caused_by=event.id), result, done)
                return  # the owner asked for this photo: there's no visitor to answer

        if owner.is_owner_speech(event, mode):
            # "Hey Faraday, ...": the owner's own router, never the visitor conversation.
            self._owner_request(event, result, done, mode)
        elif (event.type == EventType.SPEECH_HEARD and mode == Mode.NIGHT
              and event.data.get("channel") is None
              and look.is_look_request(str(event.data.get("text", "")))):
            self._look(event, result, done)   # typed at Night (dashboard box, sim): as before
        else:
            self._decide(event, mode, result, done)
        self._software_actions(result, done, depth)

    def _decide(self, event: Event, mode: str, result: Result, done: List[Action]) -> None:
        """Bonsai (or the fallback rules) for everything that isn't an owner request."""
        # A wide window so caused_by chains (photo -> take_photo -> doorbell) can be
        # traced; the prompt itself only shows the last brain.recent_lines entries.
        recent = [e for e in self.log.recent(TRACE_WINDOW) if e.get("id") != event.id]
        try:
            decision = self.brain.decide(event, mode, recent)
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

    def _software_actions(self, result: Result, done: List[Action], depth: int) -> None:
        """set_mode back through modes/ (which refuses unsafe changes), add_rule stored,
        summarize_log spoken."""
        for action in list(done):
            if action.type == ActionType.SET_MODE:
                by = ChangedBy.RULE if action.source == Source.RULES else ChangedBy.BRAIN
                for e in self.modes.arm(action.args["mode"], by,
                                        f"{by.capitalize()} chose set_mode.", caused_by=action.id):
                    self._process(e, result, depth + 1)
            elif (action.type == ActionType.ADD_RULE and self.rules is not None
                  and action.id not in self._rules_stored):
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

    def _greet_visitor(self, bell: Event, alert: str) -> bool:
        """The friendly doorbell greeting only while the alert is normal (never next to
        the alarm line, never to someone already labelled suspicious), and once per
        visit: a second press within GREET_AGAIN_S gets no greeting."""
        if alert != AlertLevel.NORMAL:
            return False
        for entry in self.log.recent(TRACE_WINDOW):
            if (entry.get("kind") == "action" and entry.get("type") == ActionType.SPEAK
                    and (entry.get("args") or {}).get("text") == DOORBELL_GREETING):
                age = _age_s(entry.get("timestamp"), bell.timestamp)
                if age is not None and 0 <= age <= GREET_AGAIN_S:
                    return False
        return True

    def _owner_request(self, event: Event, result: Result, done: List[Action], mode: str) -> None:
        """One owner request, one fixed intent (brain/owner.py). Plain code does it and
        answers with a fixed line. Owner speech is never saved as a visitor message."""
        text = str(event.data.get("text", ""))
        if REDACTED in text:
            return   # the passphrase: modes/ has already disarmed (or refused); nothing to add
        route = self.router.route(text)

        def say(text: str) -> None:
            self._emit(make_action(ActionType.SPEAK, {"text": text}, Source.BRAIN,
                                   caused_by=event.id), result, done)

        def act(kind: str, args: Dict[str, Any]) -> None:
            self._emit(make_action(kind, args, Source.BRAIN, caused_by=event.id), result, done)

        intent = route.intent
        if mode in Mode.ARMED:
            # Armed: a wake request changes nothing. Only the passphrase does (modes/).
            if intent in owner.ARMED_OK.get(mode, set()):
                self._look(event, result, done)
            elif intent == owner.PASSPHRASE:
                say(lines.SAY_PASSPHRASE)
            elif intent == owner.UNCLEAR:
                say(lines.REPEAT if route.cut_off else lines.SORRY)
            else:
                say(lines.ARMED_NO_CHANGE)
            return
        if intent in (owner.ARM_AWAY, owner.ARM_NIGHT):
            target = Mode.AWAY if intent == owner.ARM_AWAY else Mode.NIGHT
            say(lines.AWAY_ON if target == Mode.AWAY else lines.NIGHT_ON)
            act(ActionType.SET_MODE, {"mode": target})
        elif intent == owner.PASSPHRASE:
            say(lines.ALREADY_HOME)
        elif intent == owner.SUMMARY:
            act(ActionType.SUMMARIZE_LOG, {"minutes": 1440})
        elif intent == owner.LOOK:
            self._look(event, result, done)
        elif intent == owner.LIGHT:
            for outlet, on in route.lights:
                act(ActionType.OUTLET_ON if on else ActionType.OUTLET_OFF, {"outlet": outlet})
            say(" ".join(lines.light_line(outlet, on) for outlet, on in route.lights))
        elif intent == owner.ADD_INSTRUCTION:
            if self.instructions is None or not self._owner_instruction(event, result, done):
                say(lines.REPEAT)
        elif intent == owner.ADD_RULE:
            rule = route.rule or self._rule_from_model(event)
            stored = None
            if rule is not None and self.rules is not None:
                try:
                    action = make_action(ActionType.ADD_RULE, rule, Source.BRAIN, caused_by=event.id)
                except ValueError as e:
                    self.say(f"  [rules] refused: {e}")
                else:
                    # Stored now, so the spoken line tells the truth; _software_actions skips it.
                    stored = self.rules.add(action.args, description=text, caused_by=action.id)
                    self.say(f"  [rules] {'stored ' + stored['id'] if stored else 'refused'}: {text}")
                    if stored:
                        self._rules_stored.add(action.id)
                        self._emit(action, result, done)
            say(lines.RULE_SAVED if stored else lines.REPEAT)
        else:
            say(lines.REPEAT if route.cut_off else lines.SORRY)

    def _rule_from_model(self, event: Event) -> Optional[Dict[str, Any]]:
        """add_rule args written by Bonsai when the keyword parser couldn't. Only the
        add_rule action is taken from the answer; the guard rails have checked it."""
        recent = [e for e in self.log.recent(TRACE_WINDOW) if e.get("id") != event.id]
        try:
            decision = self.brain.decide(event, Mode.HOME, recent)
        except Exception as e:
            self.say(f"[pipeline] rule from Bonsai failed: {e!r}")
            return None
        rule = next((a for a in decision.actions if a.type == ActionType.ADD_RULE), None)
        return dict(rule.args) if rule else None

    def _owner_instruction(self, event: Event, result: Result, done: List[Action]) -> bool:
        """'If a delivery comes, ask them to leave it by the side door.' At Home only (the
        owner is talking). Stores it and confirms; refuses anything beyond words."""
        text = str(event.data.get("text", ""))
        parsed = owner_instructions.parse_instruction(text)
        if parsed is not None:
            ins = self.instructions.add(parsed, said=text, caused_by=event.id)
            line = owner_instructions.confirmation(ins) if ins else INSTRUCTION_REFUSED
            self.say(f"  [instructions] {'stored ' + ins['id'] if ins else 'refused'}: {text}")
        else:
            which = owner_instructions.forget_request(text)
            if which is None:
                return False
            gone = self.instructions.forget(which)
            what = "all your instructions" if which == "all" else f"the {which} instruction"
            line = f"Okay. I've forgotten {what}." if gone else "There was no instruction to forget."
        self._emit(make_action(ActionType.SPEAK, {"text": line}, Source.BRAIN, caused_by=event.id),
                   result, done)
        return True

    def _summarize(self, action: Action, result: Result, done: List[Action]) -> None:
        """Spoken summary of the log: Bonsai from plain-code facts, else a simple list."""
        happened = summary.collect(summary.window(self.log.recent(500),
                                                  int(action.args.get("minutes", 60))))
        text = None
        if self.brain.client is not None and not happened.empty:
            self._emit(make_action(ActionType.SPEAK, {"text": CHECKING}, Source.BRAIN,
                                   caused_by=action.id), result, done)
            text = self.brain.summarize(summary.facts(happened))
            if text:
                text = summary.ensure_messages(text, happened)
        text = text or summary.spoken_list(happened)
        self._emit(make_action(ActionType.SPEAK, {"text": text}, Source.BRAIN,
                               caused_by=action.id), result, done)

    def _assess(self, photo: Event, mode: str) -> Event:
        """The threat_assessed event for this photo. Never raises: rules decide on any error."""
        recent = [e for e in self.log.recent(TRACE_WINDOW) if e.get("id") != photo.id]
        voices = self._recent_voices(recent)
        ctx = threat.context_for(photo, mode, recent, voices)
        try:
            result = self.assessor.assess(ctx)
        except Exception as e:
            self.say(f"[threat] assessor failed: {e!r}")
            cat, why = threat.rules(ctx)
            result = threat.Assessment(cat, why, "rules")
        return threat.event_for(result, photo, voices)

    @staticmethod
    def _recent_voices(recent: List[Dict[str, Any]]) -> Optional[int]:
        """Voice count from diarization. speech_heard has no such field yet (NV will add
        it, see PLAN.md); until then this is None and the label works without it."""
        for e in reversed(recent):
            if e.get("type") == EventType.SPEECH_HEARD:
                voices = (e.get("data") or {}).get("voices")
                return voices if isinstance(voices, int) else None
        return None

    def _confront_later(self, event: Event) -> None:
        """Ask Bonsai for one confrontation line without blocking the pipeline."""
        if event.id in self._confronted:
            return  # one line per photo, whether the alarm or the threat label asked first
        self._confronted.add(event.id)
        description = str(event.data.get("description", ""))

        def run() -> None:
            line = self.brain.confront(description)
            if not line and not look.photo_failed(event):
                line = lines.confront_line(description)   # Bonsai's line unusable: code's
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
                if not self.modes.armed:  # disarmed meanwhile: never confront after that
                    self.say(f"  [brain] confrontation dropped, the house was disarmed: {line}")
                    return
                self._emit(action, Result(), [])

        thread = threading.Thread(target=run, name="faraday-confront", daemon=True)
        self._background.append(thread)
        thread.start()


def snapshot_action(reason: str = SNAPSHOT_REASON, caused_by: Optional[str] = None) -> Action:
    """The take_photo for a photo the owner asked for (source dashboard)."""
    return make_action(ActionType.TAKE_PHOTO, {"reason": reason}, Source.DASHBOARD,
                       caused_by=caused_by)
