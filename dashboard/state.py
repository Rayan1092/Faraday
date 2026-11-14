"""The dashboard's data feed: one JSON snapshot built from the log.

build_state() is a pure function over log entries (already redacted by log/),
so the dashboard needs no state of its own, survives restarts, and can't show
anything the log doesn't hold. The page polls it about once a second.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from shared import ActionType, AlertLevel, EventType, Mode, Outlet

Entry = Dict[str, Any]

DEFAULT_LED = {"home": "green", "away": "blue", "night": "purple",
               "warning": "yellow", "alarm": "red"}
OUTLET_NAMES = {Outlet.PORCH_LIGHT: "Porch light", Outlet.LIVING_ROOM_LAMP: "Living room lamp",
                Outlet.SPARE: "Spare outlet"}
FEED_SIZE = 25
TALK_SIZE = 8


def status(mode: str, alert: str, led: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The colour the Flipper LED shows: an alert beats the mode."""
    led = {**DEFAULT_LED, **(led or {})}
    if alert in (AlertLevel.WARNING, AlertLevel.ALARM):
        return {"color": led[alert], "label": alert.upper()}
    return {"color": led.get(mode, "green"), "label": mode.upper()}


def build_state(entries: Iterable[Entry], mode: str, alert: str,
                rules: Iterable[Dict[str, Any]] = (), led: Optional[Dict[str, str]] = None,
                ) -> Dict[str, Any]:
    entries = [e for e in entries if isinstance(e, dict)]
    lights = {name: False for name in OUTLET_NAMES}
    feed: List[Dict[str, Any]] = []
    talk: List[Dict[str, str]] = []
    messages: List[Dict[str, str]] = []
    photo: Optional[Dict[str, Any]] = None
    summary: Optional[Dict[str, str]] = None
    notice: Optional[Dict[str, str]] = None
    photo_reason: Dict[str, str] = {}
    summary_ids = set()
    mode_then = Mode.HOME  # the mode at each point in the log, for "quiet" doorbells

    for e in entries:
        kind, typ = e.get("kind"), e.get("type")
        info = (e.get("data") if kind == "event" else e.get("args")) or {}
        if not isinstance(info, dict):
            continue
        clock = str(e.get("timestamp", ""))[11:19]
        level = "info"

        if typ == EventType.MODE_CHANGED:
            mode_then = info.get("mode", mode_then)
            level = "ok" if mode_then == Mode.HOME else "info"
        elif typ == EventType.ALERT_CHANGED:
            level = {"warning": "warn", "alarm": "alarm"}.get(info.get("level"), "ok")
        elif typ == EventType.DOORBELL and mode_then == Mode.NIGHT:
            level = "warn"
            notice = {"time": clock, "text": "Doorbell at night"}
        elif typ == EventType.SPEECH_HEARD:
            talk.append({"time": clock, "who": "heard", "text": str(info.get("text", ""))})
        elif typ == ActionType.SPEAK:
            talk.append({"time": clock, "who": "said", "text": str(info.get("text", ""))})
            if e.get("caused_by") in summary_ids:
                summary = {"time": clock, "text": str(info.get("text", ""))}
        elif typ in (ActionType.OUTLET_ON, ActionType.OUTLET_OFF):
            if info.get("outlet") in lights:
                lights[info["outlet"]] = typ == ActionType.OUTLET_ON
        elif typ == ActionType.TAKE_PHOTO:
            photo_reason[str(e.get("id"))] = str(info.get("reason", ""))
        elif typ == EventType.PHOTO_DESCRIBED:
            path = str(info.get("photo_path", ""))
            photo = {"time": clock, "description": str(info.get("description", "")),
                     "url": "/media/" + path if path else None,
                     "reason": photo_reason.get(str(e.get("caused_by")), "")}
        elif typ == ActionType.SAVE_MESSAGE:
            messages.append({"time": clock, "text": str(info.get("text", "")),
                             "sender": str(info.get("sender", "visitor"))})
        elif typ == ActionType.SUMMARIZE_LOG:
            summary_ids.add(e.get("id"))

        feed.append({"time": clock, "kind": kind, "type": typ, "source": e.get("source"),
                     "text": describe(e, info), "level": level, "id": e.get("id")})

    return {
        "mode": mode,
        "alert": alert,
        "status": status(mode, alert, led),
        "offline": True,
        "feed": list(reversed(feed[-FEED_SIZE:])),  # newest first
        "talk": talk[-TALK_SIZE:],                    # oldest first, like a chat
        "photo": photo,
        "lights": [{"id": k, "name": OUTLET_NAMES[k], "on": v} for k, v in lights.items()],
        "messages": list(reversed(messages[-5:])),
        "summary": summary,
        "notice": notice,
        "rules": [rule_view(r) for r in rules],
        "version": entries[-1].get("id") if entries else None,
    }


def describe(e: Entry, info: Dict[str, Any]) -> str:
    """One short human line per log entry, for the live feed."""
    typ = e.get("type")
    if typ == EventType.DOORBELL:
        return "Doorbell pressed"
    if typ == EventType.MOTION:
        return "Motion at the door"
    if typ == EventType.SPEECH_HEARD:
        return f'Heard: "{info.get("text", "")}"'
    if typ == EventType.PHOTO_DESCRIBED:
        return f"Camera: {info.get('description', '')}"
    if typ == EventType.MODE_CHANGED:
        by = info.get("changed_by", "")
        return f"Mode {str(info.get('mode', '')).upper()}" + (f" ({by})" if by else "")
    if typ == EventType.ALERT_CHANGED:
        return f"Alert {str(info.get('level', '')).upper()}"
    if typ == ActionType.SPEAK:
        return f'Said: "{info.get("text", "")}"'
    if typ in (ActionType.OUTLET_ON, ActionType.OUTLET_OFF):
        name = OUTLET_NAMES.get(info.get("outlet"), str(info.get("outlet")))
        return f"{name} {'ON' if typ == ActionType.OUTLET_ON else 'OFF'}"
    if typ == ActionType.TAKE_PHOTO:
        return "Photo" + (f": {info['reason']}" if info.get("reason") else "")
    if typ == ActionType.SAVE_MESSAGE:
        return f'Message saved: "{info.get("text", "")}"'
    if typ == ActionType.ADD_RULE:
        return "Rule added: " + (info.get("description") or rule_text(info))
    if typ == ActionType.SUMMARIZE_LOG:
        return "Summary asked for"
    if typ == ActionType.SET_MODE:
        return f"Chose {info.get('mode')} mode"
    return str(typ)


def rule_text(rule: Dict[str, Any]) -> str:
    """'motion at night -> living room lamp on' from a stored rule or add_rule args."""
    when = rule.get("when") or {}
    modes = when.get("modes") or []
    trigger = str(when.get("event", "?")).replace("_", " ")
    if modes:
        trigger += " in " + "/".join(modes)
    steps = []
    for step in rule.get("then") or []:
        args = step.get("args") or {}
        if step.get("type") in (ActionType.OUTLET_ON, ActionType.OUTLET_OFF):
            name = OUTLET_NAMES.get(args.get("outlet"), str(args.get("outlet"))).lower()
            steps.append(f"{name} {'on' if step['type'] == ActionType.OUTLET_ON else 'off'}")
        elif step.get("type") == ActionType.SPEAK:
            steps.append(f'say "{args.get("text", "")}"')
        else:
            steps.append(str(step.get("type", "?")).replace("_", " "))
    return f"{trigger} → {', '.join(steps)}"


def rule_view(rule: Dict[str, Any]) -> Dict[str, str]:
    return {"id": str(rule.get("id", "")), "text": rule_text(rule),
            "description": str(rule.get("description", ""))}
