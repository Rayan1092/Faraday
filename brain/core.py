"""The brain: event + mode + recent log -> list of actions.

Order of work for each event:
1. Plain code first: mode_changed replies, disarm requests and passphrase text
   never reach the model.
2. Otherwise ask Bonsai with a short prompt and only the tools that fit the event.
3. Guard rails (guard.py) keep only valid, safe actions.
4. If Bonsai fails, times out, returns garbage or nothing usable, use the
   rule-based fallback (fallback.py), which goes through the same guard rails.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from shared import Action, ActionType, ChangedBy, Event, EventType, Mode, SpeechChannel, ThreatCategory

from . import fallback as fb
from . import lines as spoken
from .bonsai import BonsaiClient, BonsaiError
from .guard import GarbageReply, RawItem, filter_actions, parse_reply
from modes.passphrase import REDACTED, redact
from rules import instructions as oi

from .safety import is_disarm_request
from .settings import llm_config, load_settings, passphrase
from .tools import allowed_actions, tools_for

from .prompts import PROFILES, pick as pick_profile  # noqa: E402

# The "small" profile, kept under the old names (tests and docs refer to them).
SYSTEM_PROMPT = PROFILES["small"]["system"]
CONFRONT_PROMPT = PROFILES["small"]["confront"]
SUMMARY_PROMPT = PROFILES["small"]["summary"]


@dataclass
class Decision:
    actions: List[Action]
    via: str                          # "bonsai", "fallback" or "code"
    elapsed_s: Optional[float] = None  # Bonsai call time, if one was made
    dropped: List[Tuple[Any, str]] = field(default_factory=list)
    error: Optional[str] = None
    fixups: List[str] = field(default_factory=list)  # what plain code added or corrected
    role: Optional[str] = None  # "fast" or "smart": which Bonsai answered


class Brain:
    def __init__(self, client: Optional[Any] = None, passphrase: str = "",
                 recent_lines: int = 10, log: Callable[[str], None] = print):
        """client: anything with .chat(messages, tools) -> message dict. None = rules only."""
        self.client = client
        self.passphrase = passphrase
        self.recent_lines = recent_lines
        self.prompts: Dict[str, Any] = PROFILES["small"]
        self.prompt_profile = "small"
        self.fast_client = client         # the small Bonsai (2B on the Mac); same as client if unset
        self.fast_model: Optional[str] = None
        self.smart_model: Optional[str] = None
        self.force_role: Optional[str] = None  # the bench can force "fast" or "smart"
        self.instructions: Optional[Any] = None  # rules.InstructionStore, set by the pipeline
        self.log = log

    @classmethod
    def from_settings(cls, settings: Optional[Dict[str, Any]] = None,
                      use_llm: bool = True, log: Callable[[str], None] = print) -> "Brain":
        settings = settings or load_settings()
        cfg = llm_config(settings)
        client = BonsaiClient.from_config(cfg, log) if use_llm else None
        brain = cls(client, passphrase(settings), cfg["recent_log_lines"], log)
        brain.use_prompts(pick_profile(cfg["model"], settings.get("llm", {}).get("prompt_profile")))
        brain.smart_model = cfg["model"]
        fast = cfg.get("fast_model")
        if use_llm and fast and fast != cfg["model"]:
            brain.fast_client = BonsaiClient.from_config({**cfg, "model": fast}, log)
            brain.fast_model = fast
        else:
            brain.fast_client, brain.fast_model = client, cfg["model"]
        return brain

    def warm_up(self, *others: Any) -> None:
        """Start each Bonsai client's thinking probe in the background (brain/bonsai.py)."""
        seen = set()
        for client in (self.client, self.fast_client) + others:
            if client is not None and id(client) not in seen and hasattr(client, "warm_up"):
                seen.add(id(client))
                client.warm_up()

    def thinking_report(self, *others: Any) -> List[Dict[str, Any]]:
        """For the bench: per client, the model, chat or raw, and thinking seen."""
        out, seen = [], set()
        for client in (self.client, self.fast_client) + others:
            if client is not None and id(client) not in seen and hasattr(client, "probe_result"):
                seen.add(id(client))
                out.append({"model": client.model, "no_think": client.no_think, "uses": client.mode,
                            "probe": client.probe_result, "thinking_seen": client.thinking_seen})
        return out

    # ---- right-sized models ------------------------------------------------
    # fast (Bonsai 2B on the Mac): quick owner commands at home, and the threat label
    #   (brain/threat.py). smart (9B): anything said to a visitor, the confrontation,
    #   the summary, and vision (NV's hardware/vision.py uses the same smart model).

    def role_for(self, event: Event, mode: str) -> str:
        """'fast' for quick owner commands at home, 'smart' for anything a visitor hears."""
        if self.force_role:
            return self.force_role
        if (event.type == EventType.SPEECH_HEARD and mode == Mode.HOME
                and event.data.get("channel") != SpeechChannel.VISITOR):
            return "fast"
        return "smart"

    def use_prompts(self, name: str) -> None:
        """Switch prompt profile ("small" for 2B/4B, "9b" for the Mac mini's 9B)."""
        self.prompts = PROFILES[pick_profile("", name)]
        self.prompt_profile = name
        if self.prompts.get("recent_lines"):
            self.recent_lines = int(self.prompts["recent_lines"])

    # ---- main entry --------------------------------------------------------

    def decide(self, event: Any, mode: str, recent: Iterable[Any] = (),
               before_model: Optional[Callable[[Event, str], None]] = None) -> Decision:
        """before_model(event, mode) runs just before a Bonsai call, and only if one
        will really be made. (The "One moment" filler that used it is gone: owner, Mac test.)"""
        if isinstance(event, dict):
            event = Event.from_dict(event)
        if mode not in Mode.ALL:
            mode = Mode.AWAY  # unknown mode: behave as armed, the safe side
        tool_mode = mode
        channel = event.data.get("channel") if event.type == EventType.SPEECH_HEARD else None
        if channel == SpeechChannel.VISITOR:
            tool_mode = Mode.AWAY   # a visitor gets the visitor tools in any mode: talk, message
        allowed = allowed_actions(event.type, tool_mode)
        if channel == SpeechChannel.OWNER and ActionType.SAVE_MESSAGE in allowed:
            allowed.remove(ActionType.SAVE_MESSAGE)   # owner speech is never a visitor message
        recent = list(recent)
        cause = photo_cause(event, recent)
        label = threat_label(event, recent)
        ins = self.instructions_for(event, mode, cause, label, recent)

        if event.type == EventType.SPEECH_HEARD:
            text, matched = redact(str(event.data.get("text", "")), self.passphrase)
            if matched or REDACTED in text:
                return Decision([], "code")  # passphrase handled by the mode code path
            if is_disarm_request(text):
                reply = (fb.CANT_DISARM if mode in Mode.ARMED
                         else fb.CANT_HELP if channel == SpeechChannel.VISITOR   # a visitor at Home
                         else spoken.ALREADY_HOME)
                return self._code(event, mode, allowed, [fb._speak(reply)])
            if mode == Mode.HOME and fb.is_summary_request(text):
                # Skip one model round trip: the summary itself is Bonsai's job (summarize()).
                return self._code(event, mode, allowed, [
                    {"type": ActionType.SUMMARIZE_LOG, "args": {"minutes": 1440}}])
        if event.type == EventType.MODE_CHANGED:
            return self._code(event, mode, allowed, _mode_reply(event))
        if event.type == EventType.PHOTO_DESCRIBED:
            # A photo is never answered by the model (owner, Mac test): the doorbell
            # greeting is fixed and spoken by code at the ring, motion lines come from
            # the baseline, the alarm line from confront(). Bonsai only replies to
            # what a visitor says.
            return Decision([], "code")

        if not allowed:
            return Decision([], "code")  # nothing to choose from, e.g. motion at home
        role = self.role_for(event, mode)
        client = self.fast_client if role == "fast" else self.client
        if client is None:
            return self._fallback(event, tool_mode, allowed, None, "no model configured", cause=cause,
                                  label=label, ins=ins, recent=recent)
        system = self.prompts["system"] if role == "smart" else PROFILES[
            pick_profile(self.fast_model or "")]["system"]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": self.prompt(event, mode, recent, ins)}]
        if before_model is not None:
            try:
                before_model(event, mode)
            except Exception as e:  # a broken hook must not stop the decision
                self.log(f"[brain] before_model hook failed: {e!r}")
        started = time.perf_counter()
        try:
            reply = client.chat(messages, [t for t in tools_for(event.type, tool_mode)
                                           if t["function"]["name"] in allowed
                                           or t["function"]["name"] in ("arm_away", "arm_night")])
            elapsed = time.perf_counter() - started
            items = parse_reply(reply)
        except (BonsaiError, GarbageReply) as e:
            elapsed = time.perf_counter() - started
            self.log(f"[brain] Bonsai call failed after {elapsed:.2f}s: {e}")
            return self._fallback(event, tool_mode, allowed, elapsed, str(e), cause=cause, label=label,
                                  ins=ins, recent=recent)
        except Exception as e:  # a surprise must never stall the house
            elapsed = time.perf_counter() - started
            self.log(f"[brain] Bonsai call crashed after {elapsed:.2f}s: {e!r}")
            return self._fallback(event, tool_mode, allowed, elapsed, repr(e), cause=cause, label=label,
                                  ins=ins, recent=recent)

        actions, dropped = filter_actions(items, event, mode, allowed)
        self.log(f"[brain] Bonsai call took {elapsed:.2f}s: {len(items)} proposed, "
                 f"{len(actions)} kept, {len(dropped)} dropped")
        for item, why in dropped:
            self.log(f"[brain]   dropped {_short(item)}: {why}")
        if not actions:
            return self._fallback(event, tool_mode, allowed, elapsed,
                                  "no usable actions from Bonsai", dropped, cause, label, ins, recent)
        # Plain-code fix-ups on top of the model's answer. Each one is recorded, so the
        # benchmark can tell "Bonsai got it right" from "code fixed it".
        fixups: List[str] = []
        had_message = any(a.type == ActionType.SAVE_MESSAGE for a in actions)
        actions = self._keep_promised_message(actions, event, mode, allowed, recent)
        if not had_message and any(a.type == ActionType.SAVE_MESSAGE for a in actions):
            # Count it even when the number of actions didn't change (speech swapped out).
            fixups.append("saved the message Bonsai promised")
        actions, fixed = self._match_arm_words(actions, event, mode, allowed)
        if fixed:
            dropped = dropped + [fixed]
            fixups.append(fixed[1])
        before = len(actions)
        actions = self._confirm_mode(actions, event, mode, allowed)
        if len(actions) != before:
            fixups.append("added a spoken confirmation")
        self._delivery_reworded = False
        actions, passed = self._pass_on_delivery(actions, event, mode, allowed, ins, recent)
        if passed:
            fixups.append("corrected: code's delivery reply")
        elif self._delivery_reworded:
            fixups.append("reworded: code's delivery reply")      # not counted against the model
        return Decision(actions, "bonsai", elapsed, dropped, fixups=fixups, role=role)

    def instructions_for(self, event: Event, mode: str, cause: str, label: str,
                         recent: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        """The owner's instructions that fit this visitor moment (words only): a doorbell
        or delivery photo, or a visitor talking while the house is armed."""
        if self.instructions is None:
            return []
        if event.type == EventType.PHOTO_DESCRIBED and (cause == EventType.DOORBELL
                                                        or label == ThreatCategory.DELIVERY):
            return self.instructions.relevant(label)
        if event.type == EventType.SPEECH_HEARD and (mode in Mode.ARMED or event.data.get("channel")
                                                     == SpeechChannel.VISITOR):
            label = latest_label(recent, event.timestamp, VISIT_S)
            if fb.mentions_delivery(str(event.data.get("text", ""))):
                label = ThreatCategory.DELIVERY
            return self.instructions.relevant(label)
        return []

    def _pass_on_delivery(self, actions: List[Action], event: Event, mode: str, allowed: List[str],
                          ins: Iterable[Dict[str, Any]], recent: Iterable[Any]) -> Tuple[List[Action], bool]:
        """Code's guarantee for a visitor talking about a delivery (Mac tests: a visitor
        with a package only got "I saved your message"; with no instruction, "where
        should I leave it?" got "I can take a picture of the package and save it for
        you"). The reply is code's, Bonsai's own lines are dropped:
        - with the owner's delivery instruction: "Please leave it by the side door.
          I'll let the owner know it arrived."
        - without: "You can leave it by the door. I'll let the owner know it arrived."
        The visitor's words are saved for the owner. Said once per visit; after that a
        delivery mention just gets "Thank you. I'll pass that along." and is saved."""
        if event.type != EventType.SPEECH_HEARD or ActionType.SPEAK not in allowed:
            return actions, False
        channel = event.data.get("channel")
        if channel == SpeechChannel.OWNER or (channel is None and mode == Mode.HOME):
            return actions, False            # the owner, not a courier
        text = str(event.data.get("text", ""))
        recent = list(recent)
        if not (fb.mentions_delivery(text)
                or latest_label(recent, event.timestamp, VISIT_S) == ThreatCategory.DELIVERY):
            return actions, False
        delivery_ins = next((i for i in ins if i["audience"] == oi.DELIVERY), None)
        line = (spoken.delivery_line(oi.spoken_line(delivery_ins)) if delivery_ins
                else spoken.DELIVERY_DEFAULT)
        said = [l.lower() for l in _said_since(recent, event.timestamp, VISIT_S)]
        first = (oi.spoken_line(delivery_ins) if delivery_ins else spoken.DELIVERY_DEFAULT).lower()
        if any(first in l for l in said):
            line = spoken.MESSAGE_SAVED     # already told this visit: once is enough
        kept = [a for a in actions if a.type != ActionType.SPEAK]
        items: List[RawItem] = []
        if (ActionType.SAVE_MESSAGE in allowed
                and not any(a.type == ActionType.SAVE_MESSAGE for a in kept)):
            items.append({"type": ActionType.SAVE_MESSAGE, "args": {"text": text[:500], "sender": "visitor"}})
        items.append(fb._speak(line))
        extra, _ = filter_actions(items, event, mode, allowed)
        # For the benchmark: did Bonsai itself do the part that matters (save the message,
        # and say the owner's words if there are some)? Then code only reworded the line,
        # which isn't a correction of the model.
        bonsai_ok = any(a.type == ActionType.SAVE_MESSAGE for a in actions) and (
            delivery_ins is None or any(a.type == ActionType.SPEAK and oi.mentions(a.args["text"], delivery_ins)
                                        for a in actions))
        self._delivery_reworded = bonsai_ok
        return kept + extra, not bonsai_ok

    # ---- free-text calls (no tools) ----------------------------------------

    def confront(self, description: str) -> Optional[str]:
        """ONE short line telling an intruder to leave, mentioning what the camera sees.
        None if there's no model or it fails: plain code has already spoken."""
        return self._say_one(self.prompts["confront"], f"Camera sees: {description}", "confrontation")

    def summarize(self, facts: List[str]) -> Optional[str]:
        """A short spoken summary of these log facts. None if no model or it fails."""
        if not facts:
            return None
        return self._say_one(self.prompts["summary"], "\n".join(f"- {f}" for f in facts), "summary")

    def _say_one(self, system: str, user: str, what: str) -> Optional[str]:
        if self.client is None:
            return None
        started = time.perf_counter()
        try:
            reply = self.client.chat([{"role": "system", "content": system},
                                      {"role": "user", "content": user}], None)
            text = reply.get("content") if isinstance(reply, dict) else None
        except Exception as e:
            self.log(f"[brain] {what} call failed after {time.perf_counter() - started:.2f}s: {e}")
            return None
        elapsed = time.perf_counter() - started
        line = _clean_line(text)
        self.log(f"[brain] Bonsai {what} took {elapsed:.2f}s" + ("" if line else ": unusable reply"))
        return line

    # ---- prompt ------------------------------------------------------------

    def prompt(self, event: Event, mode: str, recent: Iterable[Any] = (),
               ins: Iterable[Dict[str, Any]] = ()) -> str:
        recent = list(recent)
        lines = [f"Mode: {mode}" + (" (armed)" if mode in Mode.ARMED else "")]
        if event.type == EventType.SPEECH_HEARD and event.data.get("channel") == SpeechChannel.VISITOR:
            lines.append("A visitor is talking at the door, not the owner.")
        history = [line for line in (_log_line(e) for e in list(recent)[-self.recent_lines:]) if line]
        if history:
            lines.append("Recent:")
            lines += [f"- {line}" for line in history]
        lines.append(f"New: {describe_event(event, photo_cause(event, recent), threat_label(event, recent))}")
        ins = list(ins)[:3]
        if ins:
            lines.append("The owner asked you to pass this on (say it, nothing else):")
            lines += [f"- {oi.prompt_line(i)}" for i in ins]
        return "\n".join(lines)

    # ---- helpers -----------------------------------------------------------

    def _code(self, event: Event, mode: str, allowed: List[str],
              items: List[RawItem]) -> Decision:
        actions, dropped = filter_actions(items, event, mode, allowed)
        return Decision(actions, "code", dropped=dropped)

    def _fallback(self, event: Event, mode: str, allowed: List[str],
                  elapsed: Optional[float], error: str,
                  model_dropped: Optional[List[Tuple[Any, str]]] = None,
                  cause: str = "", label: str = "",
                  ins: Iterable[Dict[str, Any]] = (), recent: Iterable[Any] = ()) -> Decision:
        ins = list(ins)
        delivery = next((oi.spoken_line(i) for i in ins if i["audience"] == oi.DELIVERY), None)
        also = [oi.spoken_line(i) for i in ins if i["audience"] != oi.DELIVERY]
        actions, dropped = filter_actions(fb.fallback(event, mode, cause, label, delivery, also),
                                          event, mode, allowed)
        if not any(a.type == ActionType.SAVE_MESSAGE for a in actions):
            # never "I'll pass that along" when nothing was saved (owner speech, say)
            actions = [a for a in actions if not (a.type == ActionType.SPEAK
                                                  and a.args["text"] == fb.MESSAGE_SAVED)]
        actions = self._confirm_mode(actions, event, mode, allowed)
        actions, _ = self._pass_on_delivery(actions, event, mode, allowed, ins, recent)
        self.log(f"[brain] using rule-based fallback ({error})")
        return Decision(actions, "fallback", elapsed, (model_dropped or []) + dropped, error)

    def _match_arm_words(self, actions: List[Action], event: Event, mode: str,
                         allowed: List[str]) -> Tuple[List[Action], Optional[Tuple[Any, str]]]:
        """If the owner clearly said "heading out" or "goodnight" and Bonsai armed the
        other mode, use what they said. Code wins over the model on plain words."""
        if event.type != EventType.SPEECH_HEARD:
            return actions, None
        want = fb.arm_intent(str(event.data.get("text", "")))
        arms = [a for a in actions if a.type == ActionType.SET_MODE]
        if want is None or not arms or all(a.args["mode"] == want for a in arms):
            return actions, None
        keep = [a for a in actions if a.type not in (ActionType.SET_MODE, ActionType.SPEAK)]
        fixed, _ = filter_actions([{"type": ActionType.SET_MODE, "args": {"mode": want}}],
                                  event, mode, allowed)
        self.log(f"[brain]   corrected set_mode {arms[0].args['mode']} -> {want} (owner's words)")
        return fixed + keep, ({"type": "set_mode", "args": dict(arms[0].args)},
                              f"corrected to {want} from the owner's words")

    def _keep_promised_message(self, actions: List[Action], event: Event, mode: str,
                               allowed: List[str], recent: Iterable[Any] = ()) -> List[Action]:
        """Save the visitor's words in plain code when Bonsai didn't:
        - Bonsai said "I'll take that message" but never saved it (3/10 in the benchmark).
        - Faraday had just asked "Would you like to leave a message?" and Bonsai answered
          the visitor's reply with another greeting (NV live run). Then the
          reply *is* the message, unless the visitor declined."""
        if (event.type != EventType.SPEECH_HEARD or ActionType.SAVE_MESSAGE not in allowed
                or any(a.type == ActionType.SAVE_MESSAGE for a in actions)):
            return actions
        text = str(event.data.get("text", ""))[:500]
        promised = any(a.type == ActionType.SPEAK and _PROMISE.search(a.args["text"]) for a in actions)
        last = _last_said(recent).strip()
        # an offer is a question about a message ("Would you like to leave a message?"),
        # not a confirmation ("Thank you. I'll pass that along.")
        offered = ("message" in last.lower() and last.endswith("?")
                   and not _DECLINE.match(text))
        if not (promised or offered):
            return actions
        items = [{"type": ActionType.SAVE_MESSAGE, "args": {"text": text, "sender": "visitor"}}]
        if not promised:
            # Bonsai's own line ignored the message (e.g. a second greeting): replace it.
            actions = [a for a in actions if a.type != ActionType.SPEAK]
            items.append(fb._speak(fb.MESSAGE_SAVED))
        extra, _ = filter_actions(items, event, mode, allowed)
        return extra + actions

    def _confirm_mode(self, actions: List[Action], event: Event, mode: str,
                      allowed: List[str]) -> List[Action]:
        """If the model armed the house or saved a message without saying so,
        add a spoken confirmation so nobody is left in silence."""
        if any(a.type == ActionType.SPEAK for a in actions):
            return actions
        set_modes = [a for a in actions if a.type == ActionType.SET_MODE]
        if set_modes:
            text = {Mode.AWAY: spoken.AWAY_ON, Mode.NIGHT: spoken.NIGHT_ON}[set_modes[0].args["mode"]]
        elif any(a.type == ActionType.SAVE_MESSAGE for a in actions):
            text = fb.MESSAGE_SAVED
        elif any(a.type == ActionType.ADD_RULE for a in actions):
            text = fb.RULE_SAVED
        else:
            return actions
        extra, _ = filter_actions([fb._speak(text)], event, mode, allowed)
        return actions + extra


def _clean_line(text: Any) -> Optional[str]:
    """Plain spoken text from a model reply, or None. No JSON, no tags, max 240 chars."""
    if not isinstance(text, str):
        return None
    text = re.sub(r"<[^>]+>", " ", text)          # stray tool/think tags
    text = " ".join(text.replace("*", "").split()).strip().strip('"').strip()
    if not text or text[0] in "{[":
        return None
    if len(text) > 240:
        cut = text[:240]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[:end + 1] if end > 40 else cut.rsplit(" ", 1)[0]
    return text


def _mode_reply(event: Event) -> List[RawItem]:
    changed_by, new = event.data.get("changed_by"), event.data.get("mode")
    if changed_by == ChangedBy.PASSPHRASE:
        return [fb._speak(spoken.DISARMED)]
    if changed_by in (ChangedBy.BRAIN, ChangedBy.STARTUP):
        return []  # the brain already spoke when it chose set_mode
    text = {Mode.AWAY: spoken.AWAY_ON, Mode.NIGHT: spoken.NIGHT_ON}.get(new)
    return [fb._speak(text)] if text else []


# Bonsai promising to pass something on ("I'll pass that along", "I'll take your message").
_PROMISE = re.compile(r"\b(message|pass (it|that|this|them) (on|along)|let (the owner|them|him|her) know)\b", re.I)
_DECLINE = re.compile(r"^\s*(no|nope|nah|not now|no thanks?|no thank you)\b[\s,.!]*(thanks?|thank you)?[\s.!]*$", re.I)


def _last_said(recent: Iterable[Any]) -> str:
    """The last thing Faraday said, from the recent log entries."""
    for entry in reversed(list(recent)):
        if (isinstance(entry, dict) and entry.get("kind") == "action"
                and entry.get("type") == ActionType.SPEAK):
            return str((entry.get("args") or {}).get("text", ""))
    return ""


def photo_cause(event: Event, recent: Iterable[Any]) -> str:
    """Why a photo was taken, traced through caused_by in the log:
    photo_described -> take_photo action -> the event that caused it.

    Returns "doorbell", "motion", another event type, or "" if it can't be traced.
    If the event that caused it has scrolled out of the log, the take_photo
    reason ("motion at night") is used instead.
    """
    if event.type != EventType.PHOTO_DESCRIBED or not event.caused_by:
        return ""
    by_id = {}
    for entry in recent:
        if hasattr(entry, "to_dict"):
            entry = entry.to_dict()
        if isinstance(entry, dict) and entry.get("id"):
            by_id[entry["id"]] = entry
    photo = by_id.get(event.caused_by)
    if not photo or photo.get("type") != ActionType.TAKE_PHOTO:
        return ""
    origin = by_id.get(photo.get("caused_by") or "")
    if origin and origin.get("kind") == "event":
        return str(origin.get("type", ""))
    reason = str((photo.get("args") or {}).get("reason", "")).lower()
    for kind in (EventType.DOORBELL, EventType.MOTION):
        if kind in reason:
            return kind
    return ""


VISIT_S = 180   # one visit at the door: a label or a line older than this is another visit


def _age_s(stamp: Any, now: Any) -> Optional[float]:
    try:
        return (datetime.fromisoformat(str(now)) - datetime.fromisoformat(str(stamp))).total_seconds()
    except (TypeError, ValueError):
        return None


def latest_label(recent: Iterable[Any], now: Any = None, within_s: Optional[float] = None) -> str:
    """The most recent threat_assessed category in the log (for a visitor talking).
    With now/within_s, only a label from this visit counts."""
    label = ""
    for entry in recent:
        if hasattr(entry, "to_dict"):
            entry = entry.to_dict()
        if isinstance(entry, dict) and entry.get("type") == EventType.THREAT_ASSESSED:
            age = _age_s(entry.get("timestamp"), now) if now is not None else None
            if within_s is not None and age is not None and age > within_s:
                continue
            label = str((entry.get("data") or {}).get("category", ""))
    return label


def _said_since(recent: Iterable[Any], now: Any, within_s: float) -> List[str]:
    """What Faraday said in the last within_s seconds."""
    out = []
    for entry in recent:
        if (isinstance(entry, dict) and entry.get("kind") == "action"
                and entry.get("type") == ActionType.SPEAK):
            age = _age_s(entry.get("timestamp"), now)
            if age is None or age <= within_s:
                out.append(str((entry.get("args") or {}).get("text", "")))
    return out


def threat_label(event: Event, recent: Iterable[Any]) -> str:
    """The threat_assessed category for this photo_described, from the log, or ""."""
    if event.type != EventType.PHOTO_DESCRIBED:
        return ""
    for entry in recent:
        if hasattr(entry, "to_dict"):
            entry = entry.to_dict()
        if (isinstance(entry, dict) and entry.get("type") == EventType.THREAT_ASSESSED
                and entry.get("caused_by") == event.id):
            return str((entry.get("data") or {}).get("category", ""))
    return ""


def describe_event(event: Event, cause: str = "", label: str = "") -> str:
    d = event.data
    if event.type == EventType.DOORBELL:
        return "Doorbell pressed."
    if event.type == EventType.MOTION:
        return "Motion at the front door."
    if event.type == EventType.SPEECH_HEARD:
        return f'Heard: "{d.get("text", "")}"'
    if event.type == EventType.PHOTO_DESCRIBED:
        if label == ThreatCategory.DELIVERY:
            return (f"A delivery person is at the door. Camera sees: {d.get('description', '')} "
                    "Greet them and tell them where to leave the package.")
        if cause == EventType.DOORBELL:
            return (f"A visitor rang the doorbell. Camera sees: {d.get('description', '')} "
                    "Greet them and offer to take a message.")
        return f"Camera at the door sees: {d.get('description', '')}"
    if event.type == EventType.THREAT_ASSESSED:
        return f"The camera scene looks like: {d.get('category')} ({d.get('reason', '')})"
    if event.type == EventType.MODE_CHANGED:
        return f"Mode changed to {d.get('mode')}."
    return event.type


def _log_line(entry: Any) -> str:
    """One short line per log entry. Unreadable entries are skipped."""
    if hasattr(entry, "to_dict"):
        entry = entry.to_dict()
    if not isinstance(entry, dict) or "type" not in entry:
        return ""
    clock = str(entry.get("timestamp", ""))[11:19]
    kind, info = entry["type"], entry.get("data") or entry.get("args") or {}
    if not isinstance(info, dict):
        info = {}
    if kind == EventType.PHOTO_DESCRIBED:
        detail = "a photo"            # never the description: nothing spoken describes the person
    elif kind == EventType.THREAT_ASSESSED:
        detail = str(info.get("category", ""))
    else:
        detail = (info.get("text") or info.get("outlet") or info.get("mode")
                  or info.get("reason") or "")
    detail = str(detail)
    if len(detail) > 60:
        detail = detail[:57] + "..."
    return f"{clock} {kind} {detail}".strip()


def _short(item: Any) -> str:
    text = repr(item)
    return text if len(text) <= 120 else text[:117] + "..."


# ---- module-level seam used by integration/ -----------------------------------

_default: Optional[Brain] = None


def handle_event(event: Any, mode: str = Mode.HOME,
                 recent: Iterable[Any] = ()) -> List[Action]:
    """Seam for integration/: event (+ current mode, recent log) -> actions."""
    global _default
    if _default is None:
        _default = Brain.from_settings()
    return _default.decide(event, mode, recent).actions
