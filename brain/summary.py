"""summarize_log: "What happened while I was out?"

1. window(): the log entries since the house was last armed ("while I was
   out"), capped at `minutes`.
2. collect(): counts, times and highlights in plain code (doorbells, motion,
   warning, alarm, deliveries, messages, lights, new rules).
3. facts(): the events as short lines for Bonsai, with no times and no counts:
   Bonsai only phrases them (Brain.summarize). Owner, Mac test: it said "the alarm
   went off at 03:40", the log said 03:49.
4. final_text(): Bonsai's wording, if it has no time or number of its own and
   mentions the alarm exactly when there was one (verified()), followed by code's
   own sentence with the times and counts (numbers_line()). Otherwise spoken_list(),
   code's whole summary.
Never what the camera saw: spoken lines never describe the person (owner, Mac test).
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from shared import ActionType, EventType, Mode

from .lines import NOTHING_HAPPENED

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
    doorbell_times: List[str] = field(default_factory=list)   # "HH:MM", in order
    motions: int = 0
    alarm: bool = False
    alarm_at: str = ""
    warning: bool = False
    warning_at: str = ""
    messages: List[str] = field(default_factory=list)
    seen: List[str] = field(default_factory=list)
    lights: List[str] = field(default_factory=list)
    rules: List[str] = field(default_factory=list)
    armed_at: str = ""
    disarmed: bool = False
    threats: Dict[str, int] = field(default_factory=dict)  # threat label -> count (one per photo)

    @property
    def empty(self) -> bool:
        return not (self.doorbells or self.motions or self.messages or self.rules or self.threats)

    def times(self) -> List[str]:
        return [t for t in [self.armed_at, self.warning_at, self.alarm_at] + self.doorbell_times if t]


def _clock(entry: Entry) -> str:
    return str(entry.get("timestamp", ""))[11:16]


def collect(entries: Iterable[Entry]) -> Happened:
    h = Happened()
    labels: Dict[str, str] = {}          # photo -> its latest label (a relabel replaces it)
    for e in entries:
        kind, info = e.get("type"), (e.get("data") or e.get("args") or {})
        if kind == EventType.DOORBELL:
            h.doorbells += 1
            h.doorbell_times.append(_clock(e))
        elif kind == EventType.MOTION:
            h.motions += 1
        elif kind == EventType.ALERT_CHANGED:
            level = info.get("level")
            if level == "alarm":
                h.alarm = True
                h.alarm_at = h.alarm_at or _clock(e)
            if level in ("warning", "alarm"):
                h.warning = True
                h.warning_at = h.warning_at or _clock(e)
        elif kind == EventType.MODE_CHANGED:
            if info.get("mode") in Mode.ARMED and not h.armed_at:
                h.armed_at = str(e.get("timestamp", ""))[11:16]
            if info.get("changed_by") == "passphrase":
                h.disarmed = True
        elif kind == EventType.THREAT_ASSESSED:
            labels[str(e.get("caused_by") or e.get("id"))] = str(info.get("category", ""))
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
    for cat in labels.values():
        if cat and cat not in ("nobody", "unknown"):
            h.threats[cat] = h.threats.get(cat, 0) + 1
    return h


def facts(h: Happened) -> List[str]:
    """The events for Bonsai, with no times and no counts (code adds those, review).
    Kept small: Bonsai is small."""
    if h.empty:
        return ["Nothing happened."]
    out = []
    if h.armed_at:
        out.append("The house was armed.")
    if h.doorbells:
        out.append("The doorbell rang.")
    if h.alarm:
        out.append("The alarm went off after someone stayed at the door without ringing.")
    elif h.warning:
        out.append("Someone stayed at the door without ringing, and got the warning.")
    elif h.motions:
        out.append("There was motion at the door.")
    for text in h.messages[-3:]:
        out.append(f"Visitor message: {_short(text, 120)}")
    kinds = [{"delivery": "a delivery", "visitor": "a visitor", "suspicious": "someone suspicious"}[c]
             for c in ("delivery", "visitor", "suspicious") if h.threats.get(c)]
    if kinds:
        out.append("The camera saw " + _and(kinds) + ".")
    if h.lights:
        out.append("Lights turned on: " + ", ".join(h.lights) + ".")
    for text in h.rules[-2:]:
        out.append(f"New rule: {_short(text, 80)}")
    return out


def spoken_list(h: Happened) -> str:
    """Code's own summary, with the log's times and counts: max 240 characters."""
    if h.empty:
        return NOTHING_HAPPENED
    parts = []
    if h.doorbells:
        last = h.doorbell_times[-1] if h.doorbell_times else ""
        parts.append(f"the doorbell rang {_times(h.doorbells)}" + (f", last at {last}" if last else ""))
    if h.alarm:
        parts.append("the alarm went off" + (f" at {h.alarm_at}" if h.alarm_at else ""))
    elif h.warning:
        parts.append("a warning" + (f" at {h.warning_at}" if h.warning_at else ""))
    elif h.motions:
        parts.append(f"there was motion {_times(h.motions)}")
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
    text = (f"Since you armed the house at {h.armed_at}: " if h.armed_at else "While you were out: ")
    text += ", ".join(parts) + "."
    if h.messages:
        text += f" Latest message: {h.messages[-1]}"
    return _short(text, 240)


_TIME = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")
_WORDS = {"once": 1, "twice": 2, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def _hm(hour: str, minute: str) -> Tuple[int, int]:
    return int(hour) % 12, int(minute)


def verified(text: str, h: Happened) -> bool:
    """Is Bonsai's wording safe to speak? It must hold no time and no count of its own
    (those come from code: numbers_line), except words quoted from a visitor's message,
    and mention the alarm exactly when there was one (and a warning only if there was
    one). Review: checking times against the log's set let "the alarm went off at
    <the warning's time>" through."""
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    quoted = set(re.findall(r"[a-z0-9:']+", " ".join(h.messages + h.rules).lower()))
    for m in _TIME.finditer(lowered):
        if m.group(0) not in quoted:
            return False
    for token in re.findall(r"\b\d+\b", _TIME.sub(" ", lowered)):
        if token not in quoted:
            return False
    for word in re.findall(r"\b(" + "|".join(w for w in _WORDS if w != "one") + r"|times)\b", lowered):
        if word not in quoted:
            return False
    if bool(re.search(r"\balarm", lowered)) != h.alarm:
        return False
    if "warning" in lowered and not h.warning:
        return False
    return True


def numbers_line(h: Happened) -> str:
    """Code's sentence with the log's times and counts, after Bonsai's wording."""
    parts = []
    if h.doorbells:
        last = h.doorbell_times[-1] if h.doorbell_times else ""
        parts.append(f"the doorbell rang {_times(h.doorbells)}" + (f", last at {last}" if last else ""))
    if h.alarm and h.alarm_at:
        parts.append(f"the alarm went off at {h.alarm_at}")
    elif h.warning and h.warning_at:
        parts.append(f"the warning was at {h.warning_at}")
    if not parts:
        return ""
    line = _and(parts) + "."
    return line[0].upper() + line[1:]


def final_text(bonsai: Optional[str], h: Happened) -> Tuple[str, str]:
    """(what to say, "bonsai" or "code"). Bonsai only phrases the events; every time and
    count spoken comes from code. Code's own summary if Bonsai's wording doesn't hold."""
    if bonsai and verified(bonsai, h):
        text = ensure_messages(bonsai, h)
        tail = numbers_line(h)
        combined = f"{text.rstrip()} {tail}".strip() if tail else text
        if len(combined) <= 240:
            return combined, "bonsai"
    return spoken_list(h), "code"


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


def _unique(items: List[str]) -> List[str]:
    return list(dict.fromkeys(i for i in items if i))


def _and(items: List[str]) -> str:
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3].rsplit(" ", 1)[0] + "..."
