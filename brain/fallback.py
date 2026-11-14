"""Rule-based answers used when Bonsai errors, times out or returns garbage.

Simple keyword rules so the demo never stalls. Output goes through the same
guard rails as the model's output.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from shared import ActionType, Event, EventType, Mode

RawItem = Dict[str, Any]

GREETING_ARMED = "Hello. Nobody can come to the door right now. Would you like to leave a message?"
GREETING_HOME = "Hello. Someone will be with you shortly. Would you like to leave a message?"
CANT_DISARM = "I can't disarm. Only the owner can do that."
ON_CAMERA = "You're on camera. The owner has been alerted."
MESSAGE_SAVED = "Thank you. I saved your message."
OFFER_MESSAGE = "Would you like to leave a message?"


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
    """Doorbell/motion photos and lights are guaranteed by rules/baseline.py, not here.

    photo_cause: for photo_described, the event type that led to the photo ("doorbell",
    "motion"), traced through caused_by. Motion photos are answered in core.py.
    """
    armed = mode in Mode.ARMED
    if event.type in (EventType.DOORBELL, EventType.MOTION):
        return []
    if event.type == EventType.PHOTO_DESCRIBED:
        if photo_cause == EventType.MOTION:
            return [_speak(ON_CAMERA)] if armed else []
        return [_speak(GREETING_ARMED if armed else GREETING_HOME)]
    if event.type == EventType.SPEECH_HEARD:
        return _speech(str(event.data.get("text", "")), mode)
    return []


def _speech(text: str, mode: str) -> List[RawItem]:
    armed = mode in Mode.ARMED
    if _SUMMARY.search(text):
        return [{"type": ActionType.SUMMARIZE_LOG, "args": {"minutes": 180}}]
    if _BED.search(text) and not armed:
        return [{"type": ActionType.SET_MODE, "args": {"mode": Mode.NIGHT}},
                _speak("Night mode on. Sleep well.")]
    if _LEAVING.search(text) and not armed:
        return [{"type": ActionType.SET_MODE, "args": {"mode": Mode.AWAY}},
                _speak("Away mode on. I'll watch the door.")]
    light = _LIGHT.search(text)
    if light:
        phrase = light.group(0).lower()
        words = phrase.split()
        on = (words[1] if words[0] == "turn" else words[-1]) == "on"
        outlet = "living_room_lamp" if ("living" in phrase or "lamp" in phrase) else "porch_light"
        return [_outlet(on, outlet)]
    if armed:
        return [{"type": ActionType.SAVE_MESSAGE, "args": {"text": text[:500], "sender": "visitor"}},
                _speak(MESSAGE_SAVED)]
    return [_speak("Sorry, I can't help with that.")]
