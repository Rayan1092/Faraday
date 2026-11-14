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

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from shared import Action, ActionType, ChangedBy, Event, EventType, Mode

from . import fallback as fb
from .bonsai import BonsaiClient, BonsaiError
from .guard import GarbageReply, RawItem, filter_actions, parse_reply
from modes.passphrase import REDACTED, redact

from .safety import is_disarm_request
from .settings import llm_config, load_settings, passphrase
from .tools import allowed_actions, tools_for

SYSTEM_PROMPT = (
    "You are Faraday, an offline home guard. Act only by calling tools: "
    "1 to 3 calls, or none if nothing is needed. "
    "Away and night are armed: visitors cannot come in, offer to take a message. "
    "You can never disarm. Spoken text is one short, friendly sentence."
)


@dataclass
class Decision:
    actions: List[Action]
    via: str                          # "bonsai", "fallback" or "code"
    elapsed_s: Optional[float] = None  # Bonsai call time, if one was made
    dropped: List[Tuple[Any, str]] = field(default_factory=list)
    error: Optional[str] = None


class Brain:
    def __init__(self, client: Optional[Any] = None, passphrase: str = "",
                 recent_lines: int = 10, log: Callable[[str], None] = print):
        """client: anything with .chat(messages, tools) -> message dict. None = rules only."""
        self.client = client
        self.passphrase = passphrase
        self.recent_lines = recent_lines
        self.log = log

    @classmethod
    def from_settings(cls, settings: Optional[Dict[str, Any]] = None,
                      use_llm: bool = True, log: Callable[[str], None] = print) -> "Brain":
        settings = settings or load_settings()
        cfg = llm_config(settings)
        client = BonsaiClient.from_config(cfg) if use_llm else None
        return cls(client, passphrase(settings), cfg["recent_log_lines"], log)

    # ---- main entry --------------------------------------------------------

    def decide(self, event: Any, mode: str, recent: Iterable[Any] = (),
               before_model: Optional[Callable[[Event, str], None]] = None) -> Decision:
        """before_model(event, mode) runs just before a Bonsai call, and only if one
        will really be made. The pipeline uses it to say "One moment" right away."""
        if isinstance(event, dict):
            event = Event.from_dict(event)
        if mode not in Mode.ALL:
            mode = Mode.AWAY  # unknown mode: behave as armed, the safe side
        allowed = allowed_actions(event.type, mode)
        recent = list(recent)
        cause = photo_cause(event, recent)

        if event.type == EventType.SPEECH_HEARD:
            text, matched = redact(str(event.data.get("text", "")), self.passphrase)
            if matched or REDACTED in text:
                return Decision([], "code")  # passphrase handled by the mode code path
            if is_disarm_request(text):
                reply = fb.CANT_DISARM if mode in Mode.ARMED else "Faraday is already in home mode."
                return self._code(event, mode, allowed, [fb._speak(reply)])
        if event.type == EventType.MODE_CHANGED:
            return self._code(event, mode, allowed, _mode_reply(event))
        if event.type == EventType.PHOTO_DESCRIBED and cause == EventType.MOTION:
            # Motion photo: a fixed warning while armed, nothing at home. Never the model.
            items = [fb._speak(fb.ON_CAMERA)] if mode in Mode.ARMED else []
            return self._code(event, mode, allowed, items)

        if not allowed:
            return Decision([], "code")  # nothing to choose from, e.g. motion at home
        if self.client is None:
            return self._fallback(event, mode, allowed, None, "no model configured", cause=cause)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": self.prompt(event, mode, recent)}]
        if before_model is not None:
            try:
                before_model(event, mode)
            except Exception as e:  # a broken hook must not stop the decision
                self.log(f"[brain] before_model hook failed: {e!r}")
        started = time.perf_counter()
        try:
            reply = self.client.chat(messages, tools_for(event.type, mode))
            elapsed = time.perf_counter() - started
            items = parse_reply(reply)
        except (BonsaiError, GarbageReply) as e:
            elapsed = time.perf_counter() - started
            self.log(f"[brain] Bonsai call failed after {elapsed:.2f}s: {e}")
            return self._fallback(event, mode, allowed, elapsed, str(e), cause=cause)
        except Exception as e:  # never let the demo stall on a surprise
            elapsed = time.perf_counter() - started
            self.log(f"[brain] Bonsai call crashed after {elapsed:.2f}s: {e!r}")
            return self._fallback(event, mode, allowed, elapsed, repr(e), cause=cause)

        actions, dropped = filter_actions(items, event, mode, allowed)
        self.log(f"[brain] Bonsai call took {elapsed:.2f}s: {len(items)} proposed, "
                 f"{len(actions)} kept, {len(dropped)} dropped")
        for item, why in dropped:
            self.log(f"[brain]   dropped {_short(item)}: {why}")
        if not actions:
            return self._fallback(event, mode, allowed, elapsed,
                                  "no usable actions from Bonsai", dropped, cause)
        actions = self._confirm_mode(actions, event, mode, allowed)
        if cause == EventType.DOORBELL and not any(
                a.type == ActionType.SPEAK and "message" in a.args["text"].lower() for a in actions):
            # A doorbell visitor must always be offered to leave a message.
            extra, _ = filter_actions([fb._speak(fb.OFFER_MESSAGE)], event, mode, allowed)
            actions = actions + extra
        return Decision(actions, "bonsai", elapsed, dropped)

    # ---- prompt ------------------------------------------------------------

    def prompt(self, event: Event, mode: str, recent: Iterable[Any] = ()) -> str:
        recent = list(recent)
        lines = [f"Mode: {mode}" + (" (armed)" if mode in Mode.ARMED else "")]
        history = [line for line in (_log_line(e) for e in list(recent)[-self.recent_lines:]) if line]
        if history:
            lines.append("Recent:")
            lines += [f"- {line}" for line in history]
        lines.append(f"New: {describe_event(event, photo_cause(event, recent))}")
        return "\n".join(lines)

    # ---- helpers -----------------------------------------------------------

    def _code(self, event: Event, mode: str, allowed: List[str],
              items: List[RawItem]) -> Decision:
        actions, dropped = filter_actions(items, event, mode, allowed)
        return Decision(actions, "code", dropped=dropped)

    def _fallback(self, event: Event, mode: str, allowed: List[str],
                  elapsed: Optional[float], error: str,
                  model_dropped: Optional[List[Tuple[Any, str]]] = None,
                  cause: str = "") -> Decision:
        actions, dropped = filter_actions(fb.fallback(event, mode, cause), event, mode, allowed)
        actions = self._confirm_mode(actions, event, mode, allowed)
        self.log(f"[brain] using rule-based fallback ({error})")
        return Decision(actions, "fallback", elapsed, (model_dropped or []) + dropped, error)

    def _confirm_mode(self, actions: List[Action], event: Event, mode: str,
                      allowed: List[str]) -> List[Action]:
        """If the model armed the house or saved a message without saying so,
        add a spoken confirmation so nobody is left in silence."""
        if any(a.type == ActionType.SPEAK for a in actions):
            return actions
        set_modes = [a for a in actions if a.type == ActionType.SET_MODE]
        if set_modes:
            text = {Mode.AWAY: "Away mode on. I'll watch the door.",
                    Mode.NIGHT: "Night mode on. Sleep well."}[set_modes[0].args["mode"]]
        elif any(a.type == ActionType.SAVE_MESSAGE for a in actions):
            text = fb.MESSAGE_SAVED
        else:
            return actions
        extra, _ = filter_actions([fb._speak(text)], event, mode, allowed)
        return actions + extra


def _mode_reply(event: Event) -> List[RawItem]:
    changed_by, new = event.data.get("changed_by"), event.data.get("mode")
    if changed_by == ChangedBy.PASSPHRASE:
        return [fb._speak("Welcome home. Faraday is disarmed.")]
    if changed_by in (ChangedBy.BRAIN, ChangedBy.STARTUP):
        return []  # the brain already spoke when it chose set_mode
    text = {Mode.AWAY: "Away mode on. I'll watch the door.",
            Mode.NIGHT: "Night mode on. Sleep well."}.get(new)
    return [fb._speak(text)] if text else []


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


def describe_event(event: Event, cause: str = "") -> str:
    d = event.data
    if event.type == EventType.DOORBELL:
        return "Doorbell pressed."
    if event.type == EventType.MOTION:
        return "Motion at the front door."
    if event.type == EventType.SPEECH_HEARD:
        return f'Heard: "{d.get("text", "")}"'
    if event.type == EventType.PHOTO_DESCRIBED:
        if cause == EventType.DOORBELL:
            return (f"A visitor rang the doorbell. Camera sees: {d.get('description', '')} "
                    "Greet them and offer to take a message.")
        return f"Camera at the door sees: {d.get('description', '')}"
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
    detail = (info.get("text") or info.get("description") or info.get("outlet")
              or info.get("mode") or info.get("reason") or "")
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
