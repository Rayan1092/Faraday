"""Baseline responses: the mode table in docs/PLAN.md, in plain code.

Lights and photos for doorbell and motion always happen, even if Bonsai is
slow, wrong or down. Warnings (alert levels) are handled by modes/. The brain
adds speech and judgment on top, and merge() drops anything it repeats.
"""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Tuple

from shared import Action, ActionType, Event, EventType, Mode, Outlet, Source, make_action

_PHOTO = ActionType.TAKE_PHOTO
_ON = ActionType.OUTLET_ON

# (event type, mode) -> [(action type, args)]. Keep in sync with PLAN.md "Mode table".
TABLE: Dict[Tuple[str, str], List[Tuple[str, dict]]] = {
    (EventType.DOORBELL, Mode.HOME): [(_PHOTO, {"reason": "doorbell"})],
    (EventType.DOORBELL, Mode.AWAY): [(_PHOTO, {"reason": "doorbell while away"})],
    (EventType.DOORBELL, Mode.NIGHT): [(_ON, {"outlet": Outlet.PORCH_LIGHT}),
                                       (_PHOTO, {"reason": "doorbell at night"})],
    (EventType.MOTION, Mode.HOME): [],
    (EventType.MOTION, Mode.AWAY): [(_PHOTO, {"reason": "motion while away"})],
    (EventType.MOTION, Mode.NIGHT): [(_ON, {"outlet": Outlet.PORCH_LIGHT}),
                                     (_PHOTO, {"reason": "motion at night"})],
}


def baseline_actions(event: Event, mode: str) -> List[Action]:
    return [make_action(kind, args, Source.RULES, caused_by=event.id)
            for kind, args in TABLE.get((event.type, mode), [])]


def _key(action: Action) -> Tuple[str, str]:
    """What makes two actions 'the same thing' for one event."""
    if action.type in (ActionType.TAKE_PHOTO, ActionType.SUMMARIZE_LOG):
        return (action.type, "")  # one photo / one summary per event is enough
    if action.type in (ActionType.OUTLET_ON, ActionType.OUTLET_OFF):
        return ("outlet", action.args["outlet"])  # on and off for one outlet conflict
    return (action.type, json.dumps(action.args, sort_keys=True))


def merge(existing: Iterable[Action], extra: Iterable[Action]) -> List[Action]:
    """The actions from `extra` that don't repeat or contradict `existing` (or each other)."""
    seen = {_key(a) for a in existing}
    kept = []
    for action in extra:
        key = _key(action)
        if key not in seen:
            seen.add(key)
            kept.append(action)
    return kept
