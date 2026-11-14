"""Tool definitions for Bonsai, one per action type, and which ones each event gets.

Bonsai is small, so each event is offered only the tools that make sense for it,
and set_mode is never offered while the house is armed. The offered set is also
the whitelist the guard rails enforce afterwards.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from shared import ActionType, EventType, Mode, Outlet

_OUTLETS = sorted(Outlet.ALL)
_RULE_STEPS = sorted(ActionType.ALL - {ActionType.ADD_RULE})
# Rules the model may offer: the sensor/speech events only, not the modes/ events.
_RULE_EVENTS = sorted({EventType.DOORBELL, EventType.MOTION,
                       EventType.SPEECH_HEARD, EventType.PHOTO_DESCRIBED})


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
        "speak", "Say one short sentence out loud. Can be used with other tools.",
        {"text": {"type": "string"}}, ["text"]),
    ActionType.TAKE_PHOTO: _tool(
        "take_photo", "Photograph the front door.",
        {"reason": {"type": "string"}}, []),
    ActionType.SAVE_MESSAGE: _tool(
        "save_message", "Save what a visitor wants the owner to know: deliveries, names, "
        "requests. Use it whenever a visitor gives any information.",
        {"text": {"type": "string"}, "sender": {"type": "string"}}, ["text"]),
    ActionType.SUMMARIZE_LOG: _tool(
        "summarize_log", "The owner asks what happened: summarize the log.",
        {"minutes": {"type": "integer"}}, []),
    ActionType.ADD_RULE: _tool(
        "add_rule", "Only for sentences with 'when', 'whenever' or 'from now on': store the rule.",
        {"when": {"type": "object", "properties": {
            "event": {"type": "string", "enum": _RULE_EVENTS},
            "modes": {"type": "array", "items": {"type": "string", "enum": sorted(Mode.ALL)}}},
            "required": ["event"]},
         "then": {"type": "array", "items": {"type": "object", "properties": {
             "type": {"type": "string", "enum": _RULE_STEPS},
             "args": {"type": "object"}}, "required": ["type", "args"]}},
         "description": {"type": "string"}},
        ["when", "then"]),
}

_A = ActionType
_LIGHTS = [_A.OUTLET_ON, _A.OUTLET_OFF]

# Tools per (event type, mode). An empty list means no model call at all.
# Photos and lights for doorbell/motion come from rules/baseline.py (plain code),
# so the model is only asked for what it adds on top: the doorbell greeting,
# on photo_described once the camera has looked, and conversations.
# mode_changed and alert_changed are answered in plain code and never reach Bonsai.
_BY_EVENT_MODE: Dict[str, Dict[str, List[str]]] = {
    EventType.DOORBELL: {m: [] for m in Mode.ALL},
    # Motion is fully plain code: baseline lights/photo, modes/ alert, and the
    # "You're on camera" warning after the photo (core.py).
    EventType.MOTION: {m: [] for m in Mode.ALL},
    EventType.PHOTO_DESCRIBED: {m: [_A.SPEAK] for m in Mode.ALL},
    # At home the owner is talking; when armed it is most likely a visitor.
    # Who is talking decides what they may ask for:
    # - home: the owner. Arm, lights, rules, "what happened?".
    # - away: nobody is home, so it's a visitor. Messages and talk only: no
    #   summary of the house log, no rules, no lights.
    # - night: the owner (asleep inside) or a visitor. Rules and lights, but no summary.
    EventType.SPEECH_HEARD: {Mode.HOME: [_A.SET_MODE, _A.SPEAK, _A.SUMMARIZE_LOG, _A.ADD_RULE] + _LIGHTS,
                             Mode.AWAY: [_A.SAVE_MESSAGE, _A.SPEAK],
                             Mode.NIGHT: [_A.SAVE_MESSAGE, _A.SPEAK, _A.ADD_RULE] + _LIGHTS},
    EventType.MODE_CHANGED: {m: [_A.SPEAK] for m in Mode.ALL},
}


def allowed_actions(event_type: str, mode: str) -> List[str]:
    names = list(_BY_EVENT_MODE.get(event_type, {}).get(mode, []))
    if mode in Mode.ARMED and ActionType.SET_MODE in names:
        names.remove(ActionType.SET_MODE)  # the AI can never leave away/night
    return names


# set_mode is shown to Bonsai as two tools with no arguments. With a "mode"
# argument the 4B kept writing "home" (0/10 for "I'm heading out" in the
# benchmark): with these there's nothing to type. guard.py maps them back.
ALIASES: Dict[str, Tuple[str, Dict[str, Any]]] = {
    "arm_away": (ActionType.SET_MODE, {"mode": Mode.AWAY}),
    "arm_night": (ActionType.SET_MODE, {"mode": Mode.NIGHT}),
}
ARM_TOOLS = [
    _tool("arm_away", "Owner is leaving: 'heading out', 'leaving', 'going out', 'bye'.", {}, []),
    _tool("arm_night", "Owner is going to sleep: 'goodnight', 'going to bed'. Not for leaving.",
          {}, []),
]


def tools_for(event_type: str, mode: str) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    for name in allowed_actions(event_type, mode):
        tools += ARM_TOOLS if name == ActionType.SET_MODE else [TOOLS[name]]
    return tools
