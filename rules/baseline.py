"""Baseline responses: the mode table in docs/PLAN.md, in plain code.

Lights, photos and the fixed warning lines for doorbell and motion always
happen, even if Bonsai is slow, wrong or down. Lights escalate with the alert
level that modes/ has just set. The brain adds speech and judgment on top, and
merge() drops anything it repeats.
"""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional, Tuple

from shared import (Action, ActionType, AlertLevel, ChangedBy, Event, EventType, Mode, Outlet,
                    Source, make_action)

ANNOUNCE_DOORBELL = "Someone is at the door."
WARNING_LINE = "You're on camera. The owner has been alerted."
ALARM_LINE = "Alarm. You are being recorded. Leave now."

_PHOTO = ActionType.TAKE_PHOTO
_ON = ActionType.OUTLET_ON
_SAY = ActionType.SPEAK
_PORCH = (_ON, {"outlet": Outlet.PORCH_LIGHT})
_LAMP = (_ON, {"outlet": Outlet.LIVING_ROOM_LAMP})

Step = Tuple[str, dict]

# Doorbell: (mode) -> steps. The alert level doesn't matter for the doorbell.
DOORBELL: Dict[str, List[Step]] = {
    Mode.HOME: [(_SAY, {"text": ANNOUNCE_DOORBELL})],
    Mode.AWAY: [(_PHOTO, {"reason": "doorbell while away"})],
    Mode.NIGHT: [_PORCH, (_PHOTO, {"reason": "doorbell at night"})],  # quiet: no speech
}

# Motion: (mode, alert level after this motion) -> steps. Keep in sync with PLAN.md.
MOTION: Dict[Tuple[str, str], List[Step]] = {
    (Mode.AWAY, AlertLevel.WARNING): [_PORCH, (_PHOTO, {"reason": "motion while away"}),
                                      (_SAY, {"text": WARNING_LINE})],
    (Mode.AWAY, AlertLevel.ALARM): [_PORCH, _LAMP, (_PHOTO, {"reason": "alarm while away"}),
                                    (_SAY, {"text": ALARM_LINE})],
    (Mode.NIGHT, AlertLevel.WARNING): [_PORCH, (_PHOTO, {"reason": "motion at night"})],
    (Mode.NIGHT, AlertLevel.ALARM): [_PORCH, _LAMP, (_PHOTO, {"reason": "alarm at night"})],
}


def steps_for(event_type: str, mode: str, alert: str) -> List[Step]:
    if event_type == EventType.DOORBELL:
        return DOORBELL.get(mode, [])
    if event_type == EventType.MOTION:
        return MOTION.get((mode, alert), [])  # home, or armed with no alert: nothing
    return []


def baseline_actions(event: Event, mode: str,
                     alert: Optional[str] = AlertLevel.NORMAL) -> List[Action]:
    """The guaranteed actions for this event, given the mode and the alert level
    modes/ set for it (motion while armed is always warning or alarm)."""
    return [make_action(kind, args, Source.RULES, caused_by=event.id)
            for kind, args in steps_for(event.type, mode, alert or AlertLevel.NORMAL)]


def lights_off_on_disarm(event: Event, entries: Iterable[dict]) -> List[Action]:
    """On a passphrase disarm, switch off every light Faraday itself turned on.

    Reads the log: an outlet whose last command was outlet_on from the rules or the
    brain gets an outlet_off (source rules). Lights the owner switched on from the
    dashboard are left alone, and lights already off aren't sent again.
    """
    if (event.type != EventType.MODE_CHANGED
            or event.data.get("changed_by") != ChangedBy.PASSPHRASE):
        return []
    last: Dict[str, Tuple[str, str]] = {}
    for e in entries:
        if e.get("kind") == "action" and e.get("type") in (ActionType.OUTLET_ON, ActionType.OUTLET_OFF):
            outlet = (e.get("args") or {}).get("outlet")
            if outlet:
                last[outlet] = (e["type"], str(e.get("source")))
    return [make_action(ActionType.OUTLET_OFF, {"outlet": outlet}, Source.RULES, caused_by=event.id)
            for outlet, (kind, source) in sorted(last.items())
            if kind == ActionType.OUTLET_ON and source in (Source.RULES, Source.BRAIN)]


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
