"""Baseline responses: the mode table in docs/PLAN.md, in plain code.

Lights, photos and the fixed warning lines for doorbell and motion always
happen, even if Bonsai is slow, wrong or down. Lights escalate with the alert
level that modes/ has just set. The brain adds speech and judgment on top, and
merge() drops anything it repeats.

Motion while armed (owner, 2026-11-15): the porch light goes on at once, and that's
all until modes/ decides. A visitor who walks up and rings is never warned.
- No doorbell within the grace period: warning_actions() (photo + warning line),
  run by the pipeline when modes/ raises the warning.
- Motion again 8 to 60 s after the warning: the alarm steps (both lights, photo,
  the alarm line), in Away and in Night.
"""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional, Tuple

from shared import (Action, ActionType, AlertLevel, ChangedBy, Event, EventType, Mode, Outlet,
                    Source, make_action)

from modes.state import MotionOutcome

ANNOUNCE_DOORBELL = "Someone is at the front door."
# Away: spoken by code the moment the doorbell rings, then the visitor's listening
# window opens. No second greeting from Bonsai; Bonsai only replies to what they say.
DOORBELL_GREETING = ("Hello. No one can come to the door right now. "
                     "I can take a message or help with a delivery.")
WARNING_LINE = "Please be aware this property is monitored and you are on camera."
ALARM_LINE = ("This property is protected. You are being recorded and the owner has been notified. "
              "Please leave now.")

_PHOTO = ActionType.TAKE_PHOTO
_ON = ActionType.OUTLET_ON
_SAY = ActionType.SPEAK
_PORCH = (_ON, {"outlet": Outlet.PORCH_LIGHT})
_LAMP = (_ON, {"outlet": Outlet.LIVING_ROOM_LAMP})

Step = Tuple[str, dict]

# Doorbell: (mode) -> steps. The alert level doesn't matter for the doorbell.
DOORBELL: Dict[str, List[Step]] = {
    Mode.HOME: [(_SAY, {"text": ANNOUNCE_DOORBELL})],
    Mode.AWAY: [(_SAY, {"text": DOORBELL_GREETING}), (_PHOTO, {"reason": "doorbell while away"})],
    Mode.NIGHT: [_PORCH, (_PHOTO, {"reason": "doorbell at night"})],  # quiet: no speech
}

# Motion while armed, by what modes/ made of it (MotionOutcome). Keep in sync with PLAN.md.
_WHERE = {Mode.AWAY: "while away", Mode.NIGHT: "at night"}
ALARM_STEPS: Dict[str, List[Step]] = {
    m: [_PORCH, _LAMP, (_PHOTO, {"reason": f"alarm {where}"}), (_SAY, {"text": ALARM_LINE})]
    for m, where in _WHERE.items()}
# When the grace period ends with no doorbell (modes/ end_grace -> warning).
WARNING_STEPS: Dict[str, List[Step]] = {
    m: [(_PHOTO, {"reason": f"motion {where}, no doorbell"}), (_SAY, {"text": WARNING_LINE})]
    for m, where in _WHERE.items()}


def motion_steps(mode: str, outcome: Optional[str], alert: str = AlertLevel.NORMAL) -> List[Step]:
    if mode not in Mode.ARMED:
        return []                                      # home: nothing
    if outcome is None:                                # no outcome given: from the alert alone
        outcome = MotionOutcome.ALARM if alert == AlertLevel.ALARM else MotionOutcome.GRACE
    if outcome == MotionOutcome.ALARM:
        return ALARM_STEPS[mode]
    if outcome == MotionOutcome.NONE:
        return [_PORCH, _LAMP]                         # the alarm is already on
    return [_PORCH]                                    # grace, pending, quiet, ignored


def steps_for(event_type: str, mode: str, alert: str, outcome: Optional[str] = None) -> List[Step]:
    if event_type == EventType.DOORBELL:
        return DOORBELL.get(mode, [])
    if event_type == EventType.MOTION:
        return motion_steps(mode, outcome, alert)
    return []


def baseline_actions(event: Event, mode: str, alert: Optional[str] = AlertLevel.NORMAL,
                     outcome: Optional[str] = None) -> List[Action]:
    """The guaranteed actions for this event, given the mode, the alert level modes/
    set for it, and for motion what modes/ made of it (modes.MotionOutcome)."""
    return [make_action(kind, args, Source.RULES, caused_by=event.id)
            for kind, args in steps_for(event.type, mode, alert or AlertLevel.NORMAL, outcome)]


def warning_actions(motion_id: Optional[str], mode: str) -> List[Action]:
    """Photo + warning line when a grace period ends with no doorbell, caused by the
    motion that started it (so the threat label knows nobody rang)."""
    return [make_action(kind, args, Source.RULES, caused_by=motion_id)
            for kind, args in WARNING_STEPS.get(mode, [])]


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
