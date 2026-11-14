"""Rule-based answers used when Bonsai errors, times out or returns garbage.

Simple keyword rules so the demo never stalls. Output goes through the same
guard rails as the model's output.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from shared import ActionType, Event, EventType, Mode

RawItem = Dict[str, Any]

GREETING_ARMED = "Hello. Nobody can come to the door right now. Would you like to leave a message?"
GREETING_HOME = "Hello. Someone will be with you shortly. Would you like to leave a message?"
CANT_DISARM = "I can't disarm. Only the owner can do that."
MESSAGE_SAVED = "Thank you. I saved your message."
OFFER_MESSAGE = "Would you like to leave a message?"
RULE_SAVED = "Okay. I'll remember that."


def _speak(text: str) -> RawItem:
    return {"type": ActionType.SPEAK, "args": {"text": text}}


def _outlet(on: bool, outlet: str) -> RawItem:
    return {"type": ActionType.OUTLET_ON if on else ActionType.OUTLET_OFF,
            "args": {"outlet": outlet}}


_LEAVING = re.compile(r"\b(heading out|leaving|going out|i'?m off|away mode|bye)\b", re.I)
_BED = re.compile(r"\b(good ?night|going to (bed|sleep)|night mode|bedtime)\b", re.I)
_SUMMARY = re.compile(r"\b(what happened|while i was (out|away)|summar\w*|any (news|visitors))\b", re.I)
_LIGHT = re.compile(r"\bturn (on|off) (the )?(porch( light)?|living room( lamp)?|lamp|lights?)\b"
                    r"|\b(porch( light)?|living room( lamp)?|lamp|lights?) (on|off)\b", re.I)


def fallback(event: Event, mode: str, photo_cause: str = "") -> List[RawItem]:
    """Doorbell/motion photos, lights and warning lines are rules/baseline.py's job.

    photo_cause: for photo_described, the event type that led to the photo ("doorbell",
    "motion"), traced through caused_by.
    """
    if event.type == EventType.PHOTO_DESCRIBED:
        if photo_cause == EventType.MOTION or mode == Mode.NIGHT:
            return []  # motion lines are spoken by the baseline; night stays quiet
        return [_speak(GREETING_ARMED if mode in Mode.ARMED else GREETING_HOME)]
    if event.type == EventType.SPEECH_HEARD:
        return _speech(str(event.data.get("text", "")), mode)
    return []


def is_summary_request(text: str) -> bool:
    return bool(_SUMMARY.search(text))


def _speech(text: str, mode: str) -> List[RawItem]:
    armed = mode in Mode.ARMED
    if _SUMMARY.search(text) and mode == Mode.HOME:
        return [{"type": ActionType.SUMMARIZE_LOG, "args": {"minutes": 180}}]
    rule = parse_rule(text)
    if rule and mode != Mode.AWAY:
        return [{"type": ActionType.ADD_RULE, "args": rule}, _speak(RULE_SAVED)]
    if _BED.search(text) and not armed:
        return [{"type": ActionType.SET_MODE, "args": {"mode": Mode.NIGHT}},
                _speak("Night mode on. Sleep well.")]
    if _LEAVING.search(text) and not armed:
        return [{"type": ActionType.SET_MODE, "args": {"mode": Mode.AWAY}},
                _speak("Away mode on. I'll watch the door.")]
    light = _LIGHT.search(text)
    if light and mode != Mode.AWAY:
        phrase = light.group(0).lower()
        words = phrase.split()
        on = (words[1] if words[0] == "turn" else words[-1]) == "on"
        return [_outlet(on, _outlet_name(phrase))]
    if armed:
        return [{"type": ActionType.SAVE_MESSAGE, "args": {"text": text[:500], "sender": "visitor"}},
                _speak(MESSAGE_SAVED)]
    return [_speak("Sorry, I can't help with that.")]


# ---- rules by voice, when Bonsai can't --------------------------------------

_RULE_START = re.compile(r"\b(when(ever)?|every time|each time|if)\b", re.I)
_RULE_EVENT = [(re.compile(r"\b(motion|movement|moves?|someone (walks|comes) by)\b", re.I), EventType.MOTION),
               (re.compile(r"\b(door ?bell|rings?|knocks?)\b", re.I), EventType.DOORBELL)]
_RULE_MODES = [(re.compile(r"\b(at night|night ?time|night mode|tonight)\b", re.I), Mode.NIGHT),
               (re.compile(r"\b(while i'?m (away|out)|when i'?m (away|out)|away mode)\b", re.I), Mode.AWAY),
               (re.compile(r"\b(while i'?m home|when i'?m home|home mode)\b", re.I), Mode.HOME)]
_RULE_LIGHT = re.compile(r"\bturn (on|off) (the )?(porch( light)?|living room( lamp)?|lamp|spare)"
                         r"|\b(porch( light)?|living room( lamp)?|lamp|spare)( outlet)? (on|off)\b", re.I)
_RULE_PHOTO = re.compile(r"\b(take|snap) a (photo|picture)\b", re.I)


def _outlet_name(phrase: str) -> str:
    if "living" in phrase or "lamp" in phrase:
        return "living_room_lamp"
    return "spare" if "spare" in phrase else "porch_light"


def parse_rule(text: str) -> Optional[Dict[str, Any]]:
    """'When there's motion at night, turn on the living room lamp too.' -> add_rule args.

    Plain keyword matching, used only when Bonsai can't answer. Returns None
    unless it finds a trigger word, an event and at least one action.
    """
    if not _RULE_START.search(text):
        return None
    event = next((kind for rx, kind in _RULE_EVENT if rx.search(text)), None)
    if event is None:
        return None
    then: List[Dict[str, Any]] = []
    for m in _RULE_LIGHT.finditer(text):
        phrase = m.group(0).lower()
        words = phrase.split()
        on = (words[1] if words[0] == "turn" else words[-1]) == "on"
        then.append({"type": ActionType.OUTLET_ON if on else ActionType.OUTLET_OFF,
                     "args": {"outlet": _outlet_name(phrase)}})
    if _RULE_PHOTO.search(text) and event != EventType.PHOTO_DESCRIBED:
        then.append({"type": ActionType.TAKE_PHOTO, "args": {"reason": "rule"}})
    if not then:
        return None
    modes = [mode for rx, mode in _RULE_MODES if rx.search(text)]
    return {"when": {"event": event, "modes": modes}, "then": then[:3],
            "description": text.strip()[:200]}
