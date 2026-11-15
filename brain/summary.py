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

import re

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
    threats: Dict[str, int] = field(default_factory=dict)  # threat label -> count

    @property
    def empty(self) -> bool:
        return not (self.doorbells or self.motions or self.messages or self.seen or self.rules
                    or self.threats)


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
        elif kind == EventType.THREAT_ASSESSED:
            cat = str(info.get("category", ""))
            if cat and cat not in ("nobody", "unknown"):
                h.threats[cat] = h.threats.get(cat, 0) + 1
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
    if h.threats:
        out.append("The camera judged: " + ", ".join(
            f"{n} {cat}" for cat, n in sorted(h.threats.items())) + ".")
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
    if h.threats.get("suspicious"):
        n = h.threats["suspicious"]
        parts.append(f"{n} suspicious visit" + ("s" if n > 1 else ""))
    if h.threats.get("delivery"):
        n = h.threats["delivery"]
        parts.append(f"{n} deliver" + ("ies" if n > 1 else "y"))
    if h.messages:
        parts.append(f"{len(h.messages)} message" + ("s" if len(h.messages) > 1 else ""))
    if h.rules:
        parts.append(f"{len(h.rules)} new rule" + ("s" if len(h.rules) > 1 else ""))
    text = "While you were out: " + ", ".join(parts) + "."
    if h.messages:
        text += f" Latest message: {h.messages[-1]}"
    return _short(text, 240)


# Words too common to prove the message was mentioned ("door" is in most summaries).
_STOP = {"the", "and", "for", "with", "that", "this", "left", "from", "have", "about", "your",
         "you", "are", "was", "his", "her", "its", "but", "not", "can", "will", "just",
         "door", "front", "side", "back", "house", "home", "here", "there", "outside", "please"}


def ensure_messages(text: str, h: Happened) -> str:
    """Bonsai's summary sometimes leaves out the visitor's message (1/5 in the bench),
    and that's the one thing the owner must hear. If the latest message isn't
    mentioned, plain code adds it, still within 240 characters."""
    if not h.messages:
        return text
    latest = h.messages[-1]
    lowered = text.lower()
    keywords = [w for w in re.findall(r"[a-z0-9']+", latest.lower()) if len(w) >= 3 and w not in _STOP]
    if "message" in lowered or any(w in lowered for w in keywords[:4]):
        return text
    extra = f" Visitor message: {latest}"
    room = 240 - len(text)
    if room < 30:  # make room by shortening Bonsai's part
        text = _short(text, 240 - min(len(extra), 120))
        room = 240 - len(text)
    return text + _short(extra, room)


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3].rsplit(" ", 1)[0] + "..."
