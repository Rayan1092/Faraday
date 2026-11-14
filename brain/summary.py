"""summarize_log: "What happened while I was out?"

1. window(): the log entries since the house was last armed ("while I was
   out"), capped at `minutes`.
2. collect(): counts and highlights in plain code (doorbells, motion, alarm,
   messages, what the camera saw, lights, new rules).
3. facts(): those as short lines for Bonsai, which turns them into a spoken
   summary (Brain.summarize).
4. spoken_list(): the plain-code fallback, a simple list that always works.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from shared import ActionType, EventType, Mode

Entry = Dict[str, Any]
_LIGHT_NAMES = {"porch_light": "porch light", "living_room_lamp": "living room lamp",
                "spare": "spare outlet"}


def _when(entry: Entry) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(entry.get("timestamp", "")))
    except ValueError:
        return None


def window(entries: Iterable[Entry], minutes: int = 60,
           now: Optional[datetime] = None) -> List[Entry]:
    """Entries since the last time the house was armed, and no older than `minutes`."""
    entries = list(entries)
    now = now or datetime.now().astimezone()
    oldest = now - timedelta(minutes=minutes)
    start = 0
    for i, e in enumerate(entries):
        if (e.get("type") == EventType.MODE_CHANGED
                and (e.get("data") or {}).get("mode") in Mode.ARMED
                and (e.get("data") or {}).get("previous") == Mode.HOME):
            start = i
    picked = []
    for e in entries[start:]:
        t = _when(e)
        if t is not None and t >= oldest:
            picked.append(e)
    return picked


@dataclass
class Happened:
    doorbells: int = 0
    motions: int = 0
    alarm: bool = False
    warning: bool = False
    messages: List[str] = field(default_factory=list)
    seen: List[str] = field(default_factory=list)
    lights: List[str] = field(default_factory=list)
    rules: List[str] = field(default_factory=list)
    armed_at: str = ""
    disarmed: bool = False

    @property
    def empty(self) -> bool:
        return not (self.doorbells or self.motions or self.messages or self.seen or self.rules)


def collect(entries: Iterable[Entry]) -> Happened:
    h = Happened()
    for e in entries:
        kind, info = e.get("type"), (e.get("data") or e.get("args") or {})
        if kind == EventType.DOORBELL:
            h.doorbells += 1
        elif kind == EventType.MOTION:
            h.motions += 1
        elif kind == EventType.ALERT_CHANGED:
            h.alarm |= info.get("level") == "alarm"
            h.warning |= info.get("level") in ("warning", "alarm")
        elif kind == EventType.MODE_CHANGED:
            if info.get("mode") in Mode.ARMED and not h.armed_at:
                h.armed_at = str(e.get("timestamp", ""))[11:16]
            if info.get("changed_by") == "passphrase":
                h.disarmed = True
        elif kind == EventType.PHOTO_DESCRIBED:
            text = str(info.get("description", "")).strip()
            if text and text not in h.seen:
                h.seen.append(text)
        elif kind == ActionType.SAVE_MESSAGE:
            h.messages.append(str(info.get("text", "")).strip())
        elif kind == ActionType.OUTLET_ON:
            name = _LIGHT_NAMES.get(info.get("outlet"), str(info.get("outlet")))
            if name not in h.lights:
                h.lights.append(name)
        elif kind == ActionType.ADD_RULE:
            h.rules.append(str(info.get("description", "")).strip() or "a new rule")
    return h


def facts(h: Happened) -> List[str]:
    """Short lines for Bonsai. Kept small: Bonsai is small."""
    if h.empty:
        return ["Nothing happened."]
    out = []
    if h.armed_at:
        out.append(f"The house was armed at {h.armed_at}.")
    if h.doorbells:
        out.append(f"The doorbell rang {_times(h.doorbells)}.")
    if h.motions:
        level = " The alarm went off." if h.alarm else (" A warning was raised." if h.warning else "")
        out.append(f"Motion at the door {_times(h.motions)}.{level}")
    for text in h.seen[-2:]:
        out.append(f"The camera saw: {_short(text, 100)}")
    for text in h.messages[-3:]:
        out.append(f"Visitor message: {_short(text, 120)}")
    if h.lights:
        out.append("Lights turned on: " + ", ".join(h.lights) + ".")
    for text in h.rules[-2:]:
        out.append(f"New rule: {_short(text, 80)}")
    return out


def spoken_list(h: Happened) -> str:
    """The plain-code fallback: a simple list, max 240 characters."""
    if h.empty:
        return "Nothing happened while you were out."
    parts = []
    if h.doorbells:
        parts.append(f"the doorbell rang {_times(h.doorbells)}")
    if h.motions:
        parts.append(f"there was motion {_times(h.motions)}"
                     + (" and the alarm went off" if h.alarm else ""))
    if h.messages:
        parts.append(f"{len(h.messages)} message" + ("s" if len(h.messages) > 1 else ""))
    if h.rules:
        parts.append(f"{len(h.rules)} new rule" + ("s" if len(h.rules) > 1 else ""))
    text = "While you were out: " + ", ".join(parts) + "."
    if h.messages:
        text += f" Latest message: {h.messages[-1]}"
    return _short(text, 240)


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3].rsplit(" ", 1)[0] + "..."
