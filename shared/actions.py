"""Action definitions. Must match docs/FORMATS.md exactly.

An action is something Faraday should do. The AI may only produce the eight
types below. There is no disarm action: disarming is a passphrase check in
plain code (modes/), never something the AI or a rule can ask for.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Optional

from .common import Mode, Outlet, Source, new_id, now_iso
from .events import EventType, check_fields


class ActionType:
    SET_MODE = "set_mode"
    OUTLET_ON = "outlet_on"
    OUTLET_OFF = "outlet_off"
    SPEAK = "speak"
    TAKE_PHOTO = "take_photo"
    SAVE_MESSAGE = "save_message"
    SUMMARIZE_LOG = "summarize_log"
    ADD_RULE = "add_rule"
    ALL = frozenset({SET_MODE, OUTLET_ON, OUTLET_OFF, SPEAK, TAKE_PHOTO,
                     SAVE_MESSAGE, SUMMARIZE_LOG, ADD_RULE})
    # Run by integration/ on real hardware. The rest run in Dell-owned code.
    HARDWARE = frozenset({OUTLET_ON, OUTLET_OFF, SPEAK, TAKE_PHOTO})


SPEAK_MAX_CHARS = 240
MESSAGE_MAX_CHARS = 500
RULE_MAX_ACTIONS = 3


# ---- per-type args ---------------------------------------------------------

@dataclass
class SetModeArgs:
    mode: str                 # "away" or "night" only. Never "home"


@dataclass
class OutletArgs:             # used by outlet_on and outlet_off
    outlet: str               # Outlet.*


@dataclass
class SpeakArgs:
    text: str                 # <= 240 chars, spoken through the speaker


@dataclass
class TakePhotoArgs:
    reason: str = ""          # short, shown on dashboard


@dataclass
class SaveMessageArgs:
    text: str                 # the message to keep for the homeowner
    sender: str = "visitor"


@dataclass
class SummarizeLogArgs:
    minutes: int = 60         # look back this far, 1..1440


@dataclass
class AddRuleArgs:
    when: Dict[str, Any]      # {"event": "<event type>", "modes": ["night", ...]}
    then: List[Dict[str, Any]]  # 1..3 of {"type": <action type>, "args": {...}}
    description: str = ""     # the user's words, for the dashboard


ACTION_ARGS = {
    ActionType.SET_MODE: SetModeArgs,
    ActionType.OUTLET_ON: OutletArgs,
    ActionType.OUTLET_OFF: OutletArgs,
    ActionType.SPEAK: SpeakArgs,
    ActionType.TAKE_PHOTO: TakePhotoArgs,
    ActionType.SAVE_MESSAGE: SaveMessageArgs,
    ActionType.SUMMARIZE_LOG: SummarizeLogArgs,
    ActionType.ADD_RULE: AddRuleArgs,
}


# ---- envelope --------------------------------------------------------------

@dataclass
class Action:
    type: str
    source: str
    args: Dict[str, Any]
    timestamp: str = field(default_factory=now_iso)
    id: str = field(default_factory=lambda: new_id("act"))
    caused_by: Optional[str] = None   # id of the event that led to this action

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": "action", "id": self.id, "type": self.type,
                "timestamp": self.timestamp, "source": self.source,
                "caused_by": self.caused_by, "args": self.args}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Action":
        errors = validate_action(d)
        if errors:
            raise ValueError("bad action: " + "; ".join(errors))
        return cls(type=d["type"], source=d["source"], args=dict(d["args"]),
                   timestamp=d["timestamp"], id=d["id"], caused_by=d.get("caused_by"))

    @classmethod
    def from_json(cls, s: str) -> "Action":
        return cls.from_dict(json.loads(s))


def make_action(type: str, args: Any, source: str,
                caused_by: Optional[str] = None) -> Action:
    """Build and validate an action. `args` may be a *Args dataclass or a dict."""
    payload = asdict(args) if is_dataclass(args) else dict(args)
    action = Action(type=type, source=source, args=payload, caused_by=caused_by)
    errors = validate_action(action.to_dict())
    if errors:
        raise ValueError("bad action: " + "; ".join(errors))
    return action


# ---- validation ------------------------------------------------------------

def validate_args(type: str, args: Any, where: str = "args") -> List[str]:
    """Check one action's args. Also used for the actions inside add_rule."""
    if type not in ActionType.ALL:
        return [f"unknown action type '{type}'"]
    errors = check_fields(ACTION_ARGS[type], args, where)
    if errors:
        return errors

    if type == ActionType.SET_MODE and args["mode"] not in Mode.ARMED:
        errors.append(f"{where}.mode must be one of {sorted(Mode.ARMED)} "
                      "(disarming is passphrase only)")
    elif type in (ActionType.OUTLET_ON, ActionType.OUTLET_OFF):
        if args["outlet"] not in Outlet.ALL:
            errors.append(f"{where}.outlet '{args['outlet']}' unknown")
    elif type == ActionType.SPEAK:
        text = str(args["text"]).strip()
        if not text or len(text) > SPEAK_MAX_CHARS:
            errors.append(f"{where}.text must be 1..{SPEAK_MAX_CHARS} chars")
    elif type == ActionType.SAVE_MESSAGE:
        text = str(args["text"]).strip()
        if not text or len(text) > MESSAGE_MAX_CHARS:
            errors.append(f"{where}.text must be 1..{MESSAGE_MAX_CHARS} chars")
    elif type == ActionType.SUMMARIZE_LOG:
        minutes = args.get("minutes", 60)
        if not isinstance(minutes, int) or not 1 <= minutes <= 1440:
            errors.append(f"{where}.minutes must be an int 1..1440")
    elif type == ActionType.ADD_RULE:
        errors += _validate_rule(args, where)
    return errors


def _validate_rule(args: Dict[str, Any], where: str) -> List[str]:
    errors: List[str] = []
    when, then = args["when"], args["then"]
    if not isinstance(when, dict) or when.get("event") not in EventType.ALL:
        errors.append(f"{where}.when.event must be an event type")
    elif not set(when.get("modes", [])) <= Mode.ALL:
        errors.append(f"{where}.when.modes has an unknown mode")
    elif set(when.keys()) - {"event", "modes"}:
        errors.append(f"{where}.when only takes 'event' and 'modes'")
    if not isinstance(then, list) or not 1 <= len(then) <= RULE_MAX_ACTIONS:
        return errors + [f"{where}.then must list 1..{RULE_MAX_ACTIONS} actions"]
    for i, step in enumerate(then):
        step_where = f"{where}.then[{i}]"
        if not isinstance(step, dict) or set(step.keys()) != {"type", "args"}:
            errors.append(f"{step_where} must be {{'type', 'args'}}")
        elif step["type"] == ActionType.ADD_RULE:
            errors.append(f"{step_where} rules cannot add rules")
        else:
            errors += validate_args(step["type"], step["args"], f"{step_where}.args")
    return errors


def validate_action(d: Dict[str, Any]) -> List[str]:
    """Return a list of problems; empty list means the action is valid."""
    if not isinstance(d, dict):
        return ["action must be an object"]
    errors: List[str] = []
    if d.get("kind") != "action":
        errors.append("kind must be 'action'")
    for key in ("id", "type", "timestamp", "source", "args"):
        if key not in d:
            errors.append(f"missing '{key}'")
    if errors:
        return errors
    if d["source"] not in Source.ALL:
        errors.append(f"unknown source '{d['source']}'")
    return errors + validate_args(d["type"], d["args"])
