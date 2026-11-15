"""Guard rails: plain code that runs after Bonsai answers.

1. parse_reply() turns the model's message into raw {"type", "args"} items. It
   accepts real tool calls, and also JSON written into the text (small models do
   that). Text that isn't JSON at all raises GarbageReply.
2. filter_actions() keeps only items that are known, offered for this event,
   valid per shared.validate_action(), and safe. Everything else is dropped with
   a reason. Nothing here can make the house less armed:
   - set_mode is dropped whenever the current mode is away or night,
   - set_mode to home is rejected by shared validation for every source,
   - add_rule may not contain set_mode at all, so a rule can't change mode later.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shared import Action, ActionType, Event, Mode, Source, make_action
from shared.actions import MESSAGE_MAX_CHARS, SPEAK_MAX_CHARS

from .tools import ALIASES

MAX_ACTIONS = 4

RawItem = Dict[str, Any]
Dropped = Tuple[Any, str]


class GarbageReply(Exception):
    """The model answered, but not with anything we can read as actions."""


# ---- parsing ---------------------------------------------------------------

def parse_reply(message: Dict[str, Any]) -> List[RawItem]:
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        if not isinstance(tool_calls, list):
            raise GarbageReply(f"tool_calls is not a list: {tool_calls!r}"[:200])
        return [_from_tool_call(call) for call in tool_calls]

    content = message.get("content") or ""
    if not isinstance(content, str):
        raise GarbageReply(f"content is not text: {content!r}"[:200])
    if not content.strip():
        return []  # the model chose to do nothing
    items: List[RawItem] = []
    for obj in _json_objects(content):
        items.extend(_from_json(obj))
    if not items:
        raise GarbageReply(f"no actions in reply: {content[:200]!r}")
    return items


def _from_tool_call(call: Any) -> RawItem:
    fn = call.get("function") if isinstance(call, dict) else None
    if not isinstance(fn, dict):
        return {"type": None, "args": None}
    return {"type": fn.get("name"), "args": _load_args(fn.get("arguments"))}


def _load_args(args: Any) -> Any:
    if args is None or args == "":
        return {}
    if isinstance(args, str):
        try:
            return json.loads(args)
        except ValueError:
            return None  # dropped later as bad arguments
    return args


def _json_objects(text: str) -> List[Any]:
    """Every top-level JSON object or array embedded in text, left to right."""
    decoder = json.JSONDecoder()
    found, i = [], 0
    while i < len(text):
        if text[i] in "{[":
            try:
                obj, end = decoder.raw_decode(text, i)
                found.append(obj)
                i = end
                continue
            except ValueError:
                pass
        i += 1
    return found


def _from_json(obj: Any) -> List[RawItem]:
    """Accept {"actions": [...]}, a list, {"type","args"} or {"name","arguments"}."""
    if isinstance(obj, list):
        return [item for o in obj for item in _from_json(o)]
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("actions"), list):
        return [item for o in obj["actions"] for item in _from_json(o)]
    if "type" in obj:
        return [{"type": obj["type"], "args": _load_args(obj.get("args"))}]
    if "name" in obj:
        return [{"type": obj["name"], "args": _load_args(obj.get("arguments", obj.get("parameters")))}]
    if isinstance(obj.get("function"), dict):
        return [_from_tool_call(obj)]
    return []


# ---- filtering -------------------------------------------------------------

def filter_actions(items: Sequence[Any], event: Event, mode: str,
                   allowed: Sequence[str]) -> Tuple[List[Action], List[Dropped]]:
    kept: List[Action] = []
    dropped: List[Dropped] = []
    seen = set()
    for item in items:
        action, reason = _check(item, event, mode, allowed)
        if action is None:
            dropped.append((item, reason))
            continue
        key = (action.type, json.dumps(action.args, sort_keys=True))
        if key in seen:
            dropped.append((item, "duplicate"))
            continue
        if len(kept) >= MAX_ACTIONS:
            dropped.append((item, f"more than {MAX_ACTIONS} actions"))
            continue
        seen.add(key)
        kept.append(action)
    return kept, dropped


def _check(item: Any, event: Event, mode: str,
           allowed: Sequence[str]) -> Tuple[Optional[Action], str]:
    if not isinstance(item, dict):
        return None, "not an object"
    kind = item.get("type")
    if isinstance(kind, str) and kind in ALIASES:  # arm_away / arm_night -> set_mode
        kind, fixed = ALIASES[kind]
        item = {"type": kind, "args": dict(fixed)}
    if not isinstance(kind, str) or kind not in ActionType.ALL:
        return None, f"unknown action {kind!r}"
    if kind not in allowed:
        return None, f"{kind} not offered for {event.type} in {mode}"
    args = item.get("args")
    if not isinstance(args, dict):
        return None, "bad arguments (not an object)"
    args = _tidy(kind, dict(args))

    if kind == ActionType.SET_MODE:
        if mode in Mode.ARMED:
            return None, "the AI cannot leave away/night"
        if args.get("mode") == mode:
            return None, "already in that mode"
    if kind == ActionType.ADD_RULE:
        steps = args.get("then")
        if isinstance(steps, list) and any(
                isinstance(s, dict) and s.get("type") == ActionType.SET_MODE for s in steps):
            return None, "rules may not change mode"

    try:
        return make_action(kind, args, Source.BRAIN, caused_by=event.id), ""
    except ValueError as e:
        return None, str(e)


def _tidy(kind: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Harmless clean-ups only: trim text and cut it to the allowed length."""
    limit = {ActionType.SPEAK: SPEAK_MAX_CHARS,
             ActionType.SAVE_MESSAGE: MESSAGE_MAX_CHARS}.get(kind)
    if limit and isinstance(args.get("text"), str):
        args["text"] = _shorten(args["text"].strip(), limit)
    if kind == ActionType.SET_MODE and isinstance(args.get("mode"), str):
        args["mode"] = args["mode"].strip().lower()
    return args


# ---- promises Faraday can't keep -------------------------------------------
# Faraday can only talk, take a message and pass on the owner's words. Owner, Mac
# retest: Bonsai told someone "I will check the app settings for you".
_COMMIT = re.compile(
    r"\b(?:i(?:'ll|\s+will|\s+can|\s+could|\s+shall|'d|\s+would|'m\s+going\s+to|\s+am\s+going\s+to)"
    r"|let\s+me|allow\s+me\s+to|we(?:'ll|\s+will|\s+can)|would\s+you\s+like\s+me\s+to|do\s+you\s+want\s+me\s+to"
    r"|shall\s+i|should\s+i)\s+", re.I)
_DOING = re.compile(
    r"\bi(?:'m|\s+am)\s+(?:now\s+|just\s+)?(?:checking|calling|contacting|texting|emailing|sending|opening|"
    r"unlocking|looking|searching|notifying|alerting|getting|fetching|fixing|connecting|transferring|paging|"
    r"ringing|phoning|messaging)\b"
    r"|\bi(?:'ve|\s+have)\s+(?:just\s+|already\s+)?(?:called|contacted|texted|messaged|sent|paged|alerted|"
    r"phoned|emailed|opened|unlocked|checked|fixed)\b"
    r"|\b(?:wait|moment|hold on|one sec\w*)\b[^.!?]*\bwhile i\b"
    r"|(?:^|[.!?,]\s*)(?:checking (?:now|that|on it|for you|with)|calling|contacting|looking into|sending|"
    r"opening)\b", re.I)
# What may follow "I'll / I can / let me": passing things on, never doing them (review:
# the first verb alone let "I'll let you in" and "let me take a look" through).
_ALLOWED = re.compile(
    r"(?:(?:also|just|now|only|gladly|happily|certainly|definitely|personally|be\s+(?:happy|glad|sure)\s+to)\s+)*"
    r"(?:not\b|never\b|let\s+(?!you\b)[\w']+(?:\s+[\w']+)?\s+know\b|tell\s+(?!you\b)[\w']+|"
    r"pass\s+(?:on|along|it|that|this|them|your\s+messages?|the\s+messages?|messages?)\b|"
    r"take\s+(?:(?:a|your|the|that|down(?:\s+(?:a|your|the))?)\s+)?(?:\w+\s+)?(?:messages?|note|name|number)\b|"
    r"save\s+(?:a|your|the|it|that)\b|note\s+(?:it|that|this|down|your)\b|record\s+(?:a|your|the)\s+message\b|"
    r"make\s+sure\s+(?:[\w']+\s+){1,2}(?:gets?|hears?|knows?|sees?|reach\w*)\b|"
    r"give\s+(?:the\s+owner|the\s+homeowner|them|him|her|[\w']+)\s+(?:your|the)\s+message\b|"
    r"put\s+(?:it|that|this)\s+in\b|keep\s+(?:watch|(?:it|that)\s+in\s+mind)\b|relay\b|"
    r"share\s+(?:it|that|this|your)\b|say\b|help\s+(?:you\s+)?leave\s+a\s+message\b|"
    r"help\s+(?:you\s+)?with\s+(?:a\s+|the\s+|your\s+)?(?:deliver\w*|packages?|parcels?|messages?)\b|"
    r"know\b|hear\b|ask\s+(?:the\s+owner|the\s+homeowner|them|him|her)\b|watch\s+(?:the\s+)?(?:door|house)\b)",
    re.I)
_BARE_HELP = re.compile(r"help\b", re.I)
_SENTENCES = re.compile(r"[^.!?]+[.!?]*")


def claims_ability(text: str, skip: Optional[Any] = None) -> bool:
    """Does this line promise something Faraday can't do ("I'll check the settings",
    "let me call the owner", "I'm opening the gate", "I've called the police")? Taking a
    message, letting the owner know and helping with a delivery are fine. A question
    ("Is there anything I can help you with?") offers nothing. `skip(sentence)`: True for a
    sentence that passes on the owner's own words ("We'll be back at five.")."""
    text = str(text or "").replace("’", "'").replace("‘", "'")
    for sentence in _SENTENCES.findall(text):
        sentence = sentence.strip()
        if not sentence or (skip is not None and skip(sentence)):
            continue
        if _DOING.search(sentence):
            return True
        question = sentence.endswith("?")
        for m in _COMMIT.finditer(sentence):
            if _ALLOWED.match(sentence, m.end()) or (question and _BARE_HELP.match(sentence, m.end())):
                continue      # "Is there anything I can help you with?" offers nothing
            return True
    return False


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;: ")
    return cut or text[:limit]
