"""The dashboard's data feed: one JSON snapshot built from the log.

build_state() is a pure function over log entries (already redacted by log/),
so the dashboard needs no state of its own, survives restarts, and can't show
anything the log doesn't hold. The page polls it about once a second.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from shared import ActionType, AlertLevel, ChangedBy, EventType, Mode, Outlet

from brain.fallback import CANT_DISARM

Entry = Dict[str, Any]

# The dashboard's own status colours. Not the Flipper LED, which stays off except on alarm.
STATUS_COLORS = {"home": "green", "away": "blue", "night": "purple",
               "warning": "yellow", "alarm": "red"}
OUTLET_NAMES = {Outlet.PORCH_LIGHT: "Porch light", Outlet.LIVING_ROOM_LAMP: "Living room lamp",
                Outlet.SPARE: "Spare outlet"}
FEED_SIZE = 25
TALK_SIZE = 8


def status(mode: str, alert: str, colors: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The dashboard's status colour: an alert beats the mode."""
    colors = {**STATUS_COLORS, **(colors or {})}
    if alert in (AlertLevel.WARNING, AlertLevel.ALARM):
        return {"color": colors[alert], "label": alert.upper()}
    return {"color": colors.get(mode, "green"), "label": mode.upper()}


DISARMED_FOR_S = 20      # how long the "passphrase accepted" banner stays
REFUSED_FOR_S = 60       # how long "the AI can't disarm" stays while still armed
NIGHT_BELL_FOR_S = 120   # how long the quiet night doorbell banner stays


def build_state(entries: Iterable[Entry], mode: str, alert: str,
                rules: Iterable[Dict[str, Any]] = (), colors: Optional[Dict[str, str]] = None,
                network: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None,
                newest_photo: Optional[Dict[str, Any]] = None,
                ) -> Dict[str, Any]:
    """newest_photo: {"path", "taken"} of the newest file in captures/photos/, shown
    right away, before its description arrives."""
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
    last_disarm = last_refusal = last_night_bell = last_bell = None
    bell_mode = Mode.HOME
    described: Dict[str, Dict[str, Any]] = {}  # photo_path -> its photo_described view

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
            if info.get("changed_by") == ChangedBy.PASSPHRASE:
                last_disarm = _when(e)
        elif typ == EventType.ALERT_CHANGED:
            level = {"warning": "warn", "alarm": "alarm"}.get(info.get("level"), "ok")
        elif typ == EventType.DOORBELL:
            level = "warn"
            last_bell, bell_mode = _when(e), mode_then
            if mode_then == Mode.NIGHT:
                notice = {"time": clock, "text": "Doorbell at night"}
                last_night_bell = last_bell
        elif typ == EventType.SPEECH_HEARD:
            talk.append({"time": clock, "who": "heard", "text": str(info.get("text", ""))})
        elif typ == ActionType.SPEAK:
            talk.append({"time": clock, "who": "said", "text": str(info.get("text", ""))})
            if info.get("text") == CANT_DISARM:
                last_refusal = _when(e)
                level = "safety"
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
                     "reason": photo_reason.get(str(e.get("caused_by")), ""), "path": path,
                     "taken": _when(e)}
            described[path] = photo
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
        "status": status(mode, alert, colors),
        "offline": (network or {}).get("offline", True),
        "network": network or {"offline": True, "detail": "not checked"},
        "banner": banner(mode, alert, now or datetime.now().astimezone(),
                         last_disarm, last_refusal, last_night_bell, last_bell, bell_mode),
        "feed": list(reversed(feed[-FEED_SIZE:])),  # newest first
        "talk": talk[-TALK_SIZE:],                    # oldest first, like a chat
        "photo": _photo_view(photo, described, newest_photo),
        "lights": [{"id": k, "name": OUTLET_NAMES[k], "on": v} for k, v in lights.items()],
        "messages": list(reversed(messages[-5:])),
        "summary": summary,
        "notice": notice,
        "rules": [rule_view(r) for r in rules],
        "version": entries[-1].get("id") if entries else None,
    }


def _when(entry: Entry) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(entry.get("timestamp", "")))
    except ValueError:
        return None


def _within(t: Optional[datetime], now: datetime, seconds: float) -> bool:
    if t is None:
        return False
    try:
        return 0 <= (now - t).total_seconds() <= seconds
    except TypeError:  # naive vs aware timestamps: treat as old
        return False


DOORBELL_FOR_S = 15     # how long "DOORBELL PRESSED" stays


def _photo_view(described_last: Optional[Dict[str, Any]], described: Dict[str, Dict[str, Any]],
                newest: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Show the newest file on disk as soon as it exists, with its description
    if one has arrived, else "Looking at the photo..."."""
    def clean(view: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return None if view is None else {k: v for k, v in view.items() if k not in ("path", "taken")}

    if not newest:
        return clean(described_last)
    path = str(newest["path"])
    if path in described:
        return clean(described[path])
    last_time = described_last.get("taken") if described_last else None
    try:
        older = last_time is not None and newest["taken"] <= last_time
    except TypeError:
        older = False
    if older:  # the newest file is older than the last description (e.g. a sim photo)
        return clean(described_last)
    return {"time": newest["taken"].strftime("%H:%M:%S"), "description": "Looking at the photo...",
            "url": "/media/" + path, "reason": ""}


def banner(mode: str, alert: str, now: datetime, last_disarm: Optional[datetime],
           last_refusal: Optional[datetime], last_night_bell: Optional[datetime],
           last_bell: Optional[datetime] = None, bell_mode: str = Mode.HOME,
           ) -> Optional[Dict[str, str]]:
    """The one big message for the audience, most important first."""
    if alert == AlertLevel.ALARM:
        return {"kind": "alarm", "title": "ALARM",
                "text": "Motion again within a minute. Both lights on. Owner alerted."}
    if mode == Mode.HOME and _within(last_disarm, now, DISARMED_FOR_S):
        return {"kind": "ok", "title": "DISARMED BY PASSPHRASE",
                "text": "Checked in plain code. The AI can't do this."}
    if (mode in Mode.ARMED and _within(last_refusal, now, REFUSED_FOR_S)
            and (last_disarm is None or last_refusal is None or last_disarm < last_refusal)):
        return {"kind": "safety", "title": "THE AI CAN'T DISARM",
                "text": "Only the owner's passphrase can."}
    if _within(last_bell, now, DOORBELL_FOR_S):
        text = {Mode.HOME: "Someone is at the door.",
                Mode.AWAY: "Taking a photo. Faraday will answer the visitor.",
                Mode.NIGHT: "Porch light on, photo taken, no sound."}.get(bell_mode, "")
        return {"kind": "bell", "title": "DOORBELL PRESSED", "text": text}
    if alert == AlertLevel.WARNING:
        return {"kind": "warn", "title": "WARNING", "text": "Motion while armed. Porch light on."}
    if mode == Mode.NIGHT and _within(last_night_bell, now, NIGHT_BELL_FOR_S):
        return {"kind": "warn", "title": "DOORBELL AT NIGHT",
                "text": "Porch light on, photo taken, no sound."}
    return None


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
