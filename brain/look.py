"""'Who's at the door?': eyes at the door for the owner (and for a blind owner).

Plain code, no AI for the decision:
- is_look_request() spots the question ("who's at the door?", "what do you see?",
  "is anyone outside?").
- The pipeline answers "Checking the camera.", takes a FRESH photo (a take_photo with
  source dashboard, like the Snapshot button) and waits for its description.
- look_line() is what Faraday says: how many people the detector counted, plus the
  threat label in words ("One person is at the front door. It looks like a
  delivery."). Never the description: spoken lines never describe the person (owner,
  Mac test); the description is on the dashboard. If the camera failed, or nothing
  came back in time: "I can't see the door right now."
"""
from __future__ import annotations

import re
from typing import Optional

from shared import Event, Source

from .lines import CANT_SEE, LABEL_WORDS, LOOKING, NOBODY_AT_DOOR  # noqa: E402,F401  (one tone)
LOOK_REASON = "Owner asked who's at the door"
MAX_DESCRIPTION = 190

_LOOK = re.compile(
    r"\b(?:who(?:'s| is| was)?\s+(?:at|outside|by|near)\s+(?:the\s+)?(?:front\s+)?(?:door|porch|gate)"
    r"|who(?:'s| is)\s+(?:there|outside|out\s+there|ringing|knocking)"
    r"|who\s+(?:rang|knocked|just\s+rang)"
    r"|what\s+(?:do|can)\s+you\s+see"
    r"|what(?:'s| is)\s+(?:at|outside|by)\s+(?:the\s+)?(?:front\s+)?(?:door|porch)"
    r"|what(?:'s| is)\s+(?:happening|going\s+on)\s+(?:at|outside)\s+(?:the\s+)?(?:front\s+)?(?:door|porch)"
    r"|(?:is|are)\s+(?:there\s+)?(?:any|some)(?:one|body|people)\s+(?:at\s+the\s+(?:front\s+)?door|outside|there|on\s+the\s+porch)"
    r"|(?:take\s+a\s+)?look\s+(?:at\s+the\s+(?:front\s+)?door|outside)"
    r"|describe\s+(?:the\s+)?(?:front\s+)?(?:door|porch|visitor|camera))\b", re.I)



def is_look_request(text: str) -> bool:
    return bool(_LOOK.search(text or ""))


def photo_failed(photo: Event) -> bool:
    """The camera gave no photo, or nothing described it. NV's camera worker sends
    `described_by` "bonsai" (vision) or "detector" (vision failed, the object detector
    wrote the description: still worth saying), and source "camera" with no
    described_by when there's no description at all ("Photo saved.")."""
    data = photo.data or {}
    if not data.get("photo_path") or not str(data.get("description", "")).strip():
        return True
    if data.get("described_by") in ("bonsai", "detector"):
        return False
    return photo.source == Source.CAMERA


def short_description(text: str, limit: int = MAX_DESCRIPTION) -> str:
    """The description, cut at a sentence end if it's long."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 40 else cut.rsplit(" ", 1)[0] + "."


_COUNT = {1: "One person is", 2: "Two people are", 3: "Three people are", 4: "Four people are"}


def people_line(photo: Event) -> str:
    """How many people the detector counted (a visible face counts too), in words."""
    data = photo.data or {}
    people, faces = data.get("people"), data.get("faces")
    if not isinstance(people, int):
        return "Someone is at the front door."
    n = max(people, faces if isinstance(faces, int) else 0)
    if n == 0:
        return NOBODY_AT_DOOR
    return f"{_COUNT.get(n, f'{n} people are')} at the front door."


def look_line(photo: Event, category: Optional[str]) -> str:
    """What Faraday says back: the people count plus the label, or CANT_SEE. Only facts
    from code: never the description, never an object (owner, Mac test)."""
    if photo_failed(photo):
        return CANT_SEE
    if category == "nobody":
        return NOBODY_AT_DOOR
    first = people_line(photo)
    if first == NOBODY_AT_DOOR:
        return NOBODY_AT_DOOR
    return f"{first} {LABEL_WORDS.get(category or '', '')}".strip()
