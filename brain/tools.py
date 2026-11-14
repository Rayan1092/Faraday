"""Tool definitions for Bonsai, one per action type, and which ones each event gets.

Bonsai is small, so each event is offered only the tools that make sense for it,
and set_mode is never offered while the house is armed. The offered set is also
the whitelist the guard rails enforce afterwards.
"""
from __future__ import annotations

from typing import Any, Dict, List

from shared import ActionType, EventType, Mode, Outlet

_OUTLETS = sorted(Outlet.ALL)
_RULE_STEPS = sorted(ActionType.ALL - {ActionType.ADD_RULE})


def _tool(name: str, description: str, props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": required},
    }}


TOOLS: Dict[str, Dict[str, Any]] = {
    ActionType.SET_MODE: _tool(
        "set_mode", "Arm the house: away when the owner leaves, night at bedtime.",
        {"mode": {"type": "string", "enum": sorted(Mode.ARMED)}}, ["mode"]),
    ActionType.OUTLET_ON: _tool(
        "outlet_on", "Turn a light on.",
        {"outlet": {"type": "string", "enum": _OUTLETS}}, ["outlet"]),
    ActionType.OUTLET_OFF: _tool(
        "outlet_off", "Turn a light off.",
        {"outlet": {"type": "string", "enum": _OUTLETS}}, ["outlet"]),
    ActionType.SPEAK: _tool(
        "speak", "Say one short sentence out loud.",
        {"text": {"type": "string"}}, ["text"]),
    ActionType.TAKE_PHOTO: _tool(
        "take_photo", "Photograph the front door.",
        {"reason": {"type": "string"}}, []),
    ActionType.SAVE_MESSAGE: _tool(
        "save_message", "A visitor says something for the owner: save their words.",
        {"text": {"type": "string"}, "sender": {"type": "string"}}, ["text"]),
    ActionType.SUMMARIZE_LOG: _tool(
        "summarize_log", "The owner asks what happened: summarize the log.",
        {"minutes": {"type": "integer"}}, []),
    ActionType.ADD_RULE: _tool(
        "add_rule", "The owner says 'from now on' or 'whenever': store the rule.",
        {"when": {"type": "object", "properties": {
            "event": {"type": "string", "enum": sorted(EventType.ALL - {EventType.MODE_CHANGED})},
            "modes": {"type": "array", "items": {"type": "string", "enum": sorted(Mode.ALL)}}},
            "required": ["event"]},
         "then": {"type": "array", "items": {"type": "object", "properties": {
             "type": {"type": "string", "enum": _RULE_STEPS},
             "args": {"type": "object"}}, "required": ["type", "args"]}},
         "description": {"type": "string"}},
        ["when", "then"]),
}

_A = ActionType
_SPEECH_COMMON = [_A.SPEAK, _A.SUMMARIZE_LOG, _A.ADD_RULE, _A.OUTLET_ON, _A.OUTLET_OFF]

# Tools per (event type, mode). An empty list means no model call at all.
# mode_changed is answered in plain code and never reaches Bonsai.
_BY_EVENT_MODE: Dict[str, Dict[str, List[str]]] = {
    EventType.DOORBELL: {m: [_A.TAKE_PHOTO, _A.SPEAK] for m in Mode.ALL},
    EventType.MOTION: {Mode.HOME: [],
                       Mode.AWAY: [_A.TAKE_PHOTO, _A.SPEAK],
                       Mode.NIGHT: [_A.OUTLET_ON, _A.TAKE_PHOTO]},
    EventType.PHOTO_DESCRIBED: {m: [_A.SPEAK] for m in Mode.ALL},
    # At home the owner is talking; when armed it is most likely a visitor.
    EventType.SPEECH_HEARD: {Mode.HOME: [_A.SET_MODE] + _SPEECH_COMMON,
                             Mode.AWAY: [_A.SAVE_MESSAGE] + _SPEECH_COMMON,
                             Mode.NIGHT: [_A.SAVE_MESSAGE] + _SPEECH_COMMON},
    EventType.MODE_CHANGED: {m: [_A.SPEAK] for m in Mode.ALL},
}


def allowed_actions(event_type: str, mode: str) -> List[str]:
    names = list(_BY_EVENT_MODE.get(event_type, {}).get(mode, []))
    if mode in Mode.ARMED and ActionType.SET_MODE in names:
        names.remove(ActionType.SET_MODE)  # the AI can never leave away/night
    return names


def tools_for(event_type: str, mode: str) -> List[Dict[str, Any]]:
    return [TOOLS[name] for name in allowed_actions(event_type, mode)]
