"""Standing tasks: everything the owner asks Faraday to remember, in one tool.

    "Remind me to take out the trash when I get home."
    "If a delivery comes, tell them to use the main door."
    "Whenever someone rings at night, turn on both lights."
    "At 9 PM, switch to night mode."
    "Actually, make that the side door."

Bonsai turns the owner's words into a task with its tools (brain/tasks.py: remember,
update_task, remove_task, list_tasks). Plain code here checks the task, stores it,
says back in one sentence what it understood, and runs it. One tool replaces the
separate instructions, rules and reminders (owner, 2026-11-15).

A task is one trigger and one action:
- trigger: home (the passphrase disarm: the owner is back), doorbell, delivery, motion,
  mode_change (to one mode, or any), time ("21:00"), owner_speaks (the next time the
  owner talks to Faraday). night=True: only at night (Night mode, or 21:00 to 06:00).
- action: say_owner ("Reminder: take out the trash."), say_visitor (words for whoever
  is at the door), lights_on / lights_off (one or more outlets), set_mode (away or night:
  never home, the AI can never disarm; only from Home), take_photo.
- repeat: "once" (gone after it runs) or "always".
Words only ever become speech. Kept in logs/tasks.json so they survive a restart.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from shared import (Action, ActionType, ChangedBy, Event, EventType, Mode, Outlet, Source,
                    ThreatCategory, make_action, new_id, now_iso)

from . import instructions as oi

REPO_ROOT = Path(__file__).resolve().parents[1]
Task = Dict[str, Any]

HOME, DOORBELL, DELIVERY, MOTION, MODE_CHANGE, TIME, OWNER_SPEAKS = (
    "home", "doorbell", "delivery", "motion", "mode_change", "time", "owner_speaks")
TRIGGERS = (HOME, DOORBELL, DELIVERY, MOTION, MODE_CHANGE, TIME, OWNER_SPEAKS)
SAY_OWNER, SAY_VISITOR, LIGHTS_ON, LIGHTS_OFF, SET_MODE, TAKE_PHOTO = (
    "say_owner", "say_visitor", "lights_on", "lights_off", "set_mode", "take_photo")
ACTIONS = (SAY_OWNER, SAY_VISITOR, LIGHTS_ON, LIGHTS_OFF, SET_MODE, TAKE_PHOTO)
ONCE, ALWAYS = "once", "always"
MAX_TEXT = 120
MAX_TASKS = 20
DELIVERY_REPEAT_S = 120          # one delivery visit can be labelled more than once
OUTLET_NAMES = {Outlet.PORCH_LIGHT: "the porch light", Outlet.LIVING_ROOM_LAMP: "the living room lamp",
                Outlet.SPARE: "the spare outlet"}
_SECRET = re.compile(r"\b(?:pass ?phrase|password|pin|code)\b", re.I)
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
FIELDS = ("trigger", "night", "mode", "time", "action", "text", "outlets", "to_mode", "repeat")


def is_night(mode: str, now: datetime) -> bool:
    """Night mode, or 21:00 to 06:00."""
    return mode == Mode.NIGHT or now.hour >= 21 or now.hour < 6


def check_task(task: Any) -> List[str]:
    """Problems with a task; empty means it may be stored and run."""
    if not isinstance(task, dict):
        return ["a task must be an object"]
    problems: List[str] = []
    trigger, action = task.get("trigger"), task.get("action")
    if trigger not in TRIGGERS:
        problems.append(f"unknown trigger {trigger!r}")
    if action not in ACTIONS:
        problems.append(f"unknown action {action!r}")
    if trigger == TIME and not _TIME.match(str(task.get("time") or "")):
        problems.append("a time task needs HH:MM")
    if trigger == MODE_CHANGE and task.get("mode") not in (None, Mode.HOME, Mode.AWAY, Mode.NIGHT):
        problems.append("unknown mode")
    if task.get("repeat") not in (ONCE, ALWAYS):
        problems.append("repeat is once or always")
    if not isinstance(task.get("night", False), bool):
        problems.append("night is true or false")
    text = task.get("text")
    if action in (SAY_OWNER, SAY_VISITOR):
        if not isinstance(text, str) or not 2 < len(text.strip()) <= MAX_TEXT:
            problems.append("words of 3 to 120 characters")
        elif _SECRET.search(text):
            problems.append("never a passphrase or a code")
        elif action == SAY_VISITOR and oi.is_forbidden({"request": text}):
            problems.append("words for a visitor only: no lights, modes, alarm, locks or codes")
    if action == SAY_VISITOR and trigger not in (DOORBELL, DELIVERY):
        problems.append("words for a visitor need a doorbell or a delivery")
    if action in (LIGHTS_ON, LIGHTS_OFF):
        outlets = task.get("outlets")
        if not isinstance(outlets, list) or not outlets or any(o not in Outlet.ALL for o in outlets):
            problems.append("which lights")
    if action == SET_MODE:
        if task.get("to_mode") not in (Mode.AWAY, Mode.NIGHT):
            problems.append("only away or night: Faraday can never disarm")
        if trigger in (MODE_CHANGE, HOME):
            problems.append("a mode change can't change the mode again")
    return problems


def normalize(task: Dict[str, Any]) -> Task:
    """Only the known fields, tidied."""
    out: Task = {k: task.get(k) for k in FIELDS}
    out["night"] = bool(out.get("night"))
    out["repeat"] = out.get("repeat") if out.get("repeat") in (ONCE, ALWAYS) else default_repeat(out)
    if isinstance(out.get("text"), str):
        out["text"] = " ".join(out["text"].split()).strip().rstrip(".!,?")
    if out.get("action") in (LIGHTS_ON, LIGHTS_OFF):
        outlets = out.get("outlets") if isinstance(out.get("outlets"), list) else []
        out["outlets"] = [o for o in OUTLET_NAMES if o in outlets] + [o for o in outlets if o not in OUTLET_NAMES]
    else:
        out["outlets"] = []
    if out.get("action") != SET_MODE:
        out["to_mode"] = None
    if out.get("trigger") != TIME:
        out["time"] = None
    if out.get("trigger") != MODE_CHANGE:
        out["mode"] = None
    return out


def default_repeat(task: Dict[str, Any]) -> str:
    """A reminder is said once; words for visitors and house actions stand."""
    if task.get("action") == SAY_OWNER or task.get("trigger") in (TIME, OWNER_SPEAKS):
        return ONCE
    return ALWAYS


# ---- words ---------------------------------------------------------------------------

_YOU = [(r"\bI'm\b", "you're"), (r"\bI am\b", "you are"), (r"\bI'll\b", "you'll"), (r"\bI've\b", "you've"),
        (r"\bI'd\b", "you'd"), (r"\bmyself\b", "yourself"), (r"\bmine\b", "yours"), (r"\bmy\b", "your"),
        (r"\bme\b", "you"), (r"\bI\b", "you")]
_THAT = re.compile(r"^(?:the|a|an|my|your|our|it|it's|i|i'm|we|we're|there|that|this|he|she|they|you|"
                   r"someone|somebody|nobody|mom|dad)\b", re.I)


def second_person(text: str) -> str:
    """The owner's "my" as Faraday says it back: "call my mom" -> "call your mom"."""
    for pattern, repl in _YOU:
        text = re.sub(pattern, repl, text, flags=re.I)
    return text


def clock(hhmm: str) -> str:
    """"21:00" -> "9 PM", "07:30" -> "7:30 AM", "12:00" -> "noon"."""
    h, m = (int(x) for x in hhmm.split(":"))
    if (h, m) == (12, 0):
        return "noon"
    if (h, m) == (0, 0):
        return "midnight"
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


def _outlets(task: Task) -> str:
    names = [OUTLET_NAMES.get(o, o) for o in task.get("outlets") or []]
    return " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]


def when_words(task: Task) -> str:
    """The trigger in words: "when you get home", "whenever someone rings at night"."""
    trigger, once = task.get("trigger"), task.get("repeat") == ONCE
    visitor = task.get("action") == SAY_VISITOR
    if trigger == HOME:
        words = "when you get home" if once else "every time you get home"
    elif trigger == DOORBELL:
        words = "if someone rings" if visitor else ("the next time someone rings" if once else "whenever someone rings")
    elif trigger == DELIVERY:
        words = "if a delivery comes" if visitor else ("the next time a delivery comes" if once
                                                       else "whenever a delivery comes")
    elif trigger == MOTION:
        words = "the next time there's motion" if once else "whenever there's motion"
    elif trigger == MODE_CHANGE:
        what = {Mode.HOME: "the house is disarmed", Mode.AWAY: "away mode starts",
                Mode.NIGHT: "night mode starts"}.get(task.get("mode"), "the mode changes")
        words = ("the next time " if once else "whenever ") + what
    elif trigger == TIME:
        words = f"at {clock(task['time'])}" if once else f"every day at {clock(task['time'])}"
    elif trigger == OWNER_SPEAKS:
        words = "the next time you talk to me" if once else "every time you talk to me"
    else:
        words = "when it happens"
    if task.get("night") and trigger not in (TIME,):
        words += " at night"
    return words


def do_words(task: Task) -> str:
    """The action as Faraday would do it: "remind you to take out the trash"."""
    action, text = task.get("action"), str(task.get("text") or "")
    if action == SAY_OWNER:
        text = second_person(re.sub(r"^(?:to|that)\s+", "", text, flags=re.I))
        return f"remind you {'that' if _THAT.match(text) else 'to'} {text}"
    if action == SAY_VISITOR:
        ins = visitor_instruction(task)
        to = "to " if ins["form"] == "to" else ""
        return f"tell them {to}{oi._third_person(ins['request'])}"
    if action == LIGHTS_ON:
        return f"turn on {_outlets(task)}"
    if action == LIGHTS_OFF:
        return f"turn off {_outlets(task)}"
    if action == SET_MODE:
        return f"switch to {task.get('to_mode')} mode"
    if action == TAKE_PHOTO:
        return "take a photo"
    return "do that"


def describe(task: Task) -> str:
    """One line: "when you get home, I'll remind you to take out the trash"."""
    return f"{when_words(task)}, I'll {do_words(task)}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def confirmation(task: Task) -> str:
    return f"Okay. {_cap(describe(task))}."


def updated_line(task: Task) -> str:
    return f"Okay. Now {describe(task)}."


def removed_line(task: Task) -> str:
    return f"Okay. I won't {do_words(task)} {when_words(task)}."


def list_line(tasks: List[Task]) -> str:
    if not tasks:
        return "You have no tasks set."
    n = len(tasks)
    shown = "; ".join(describe(t) for t in tasks[:4])
    more = f"; and {n - 4} more on the dashboard" if n > 4 else ""
    return f"You have {n} task{'s' if n != 1 else ''}: {shown}{more}."


def reminder_line(task: Task) -> str:
    """What Faraday says to the owner when a say_owner task runs."""
    text = second_person(re.sub(r"^(?:to|that)\s+", "", str(task.get("text") or ""), flags=re.I))
    return f"Reminder: {text}."


def visitor_instruction(task: Task) -> Dict[str, str]:
    """A say_visitor task as an owner instruction (rules/instructions.py), so the visitor
    prompt, the greeting and the delivery reply use it unchanged."""
    text = str(task.get("text") or "")
    text = re.sub(r"^(?:to|that)\s+", "", text, flags=re.I)
    form = "to" if oi._DO.match(text) else "say"
    return {"id": task.get("id", ""), "audience": oi.DELIVERY if task.get("trigger") == DELIVERY else oi.ANYONE,
            "verb": "ask" if form == "to" else "tell", "form": form, "request": text,
            "said": task.get("said", "")}


# ---- store ---------------------------------------------------------------------------

class TaskStore:
    def __init__(self, path: Union[str, Path]):
        path = Path(path)
        self.path = path if path.is_absolute() else REPO_ROOT / path
        self._lock = threading.Lock()
        self._tasks: List[Task] = []
        self.skipped: List[str] = []
        self._load()

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "TaskStore":
        logs_dir = settings.get("paths", {}).get("logs", "logs")
        return cls(Path(logs_dir) / "tasks.json")

    # ---- changes -----------------------------------------------------------------

    def add(self, task: Dict[str, Any], said: str = "", caused_by: Optional[str] = None) -> Optional[Task]:
        """Store a task. Words for visitors at the same moment replace the old ones (one
        instruction per audience, as before). None if refused."""
        task = normalize(task)
        if check_task(task):
            return None
        task.update({"id": new_id("task"), "said": str(said or "").strip()[:240], "created": now_iso(),
                     "caused_by": caused_by, "last_run": None})
        with self._lock:
            keep = []
            for t in self._tasks:
                same = all(t.get(k) == task.get(k) for k in FIELDS)
                replaced = (task["action"] == SAY_VISITOR and t["action"] == SAY_VISITOR
                            and t["trigger"] == task["trigger"])
                if not (same or replaced):
                    keep.append(t)
            self._tasks = (keep + [task])[-MAX_TASKS:]
            self._save()
        return dict(task)

    def update(self, task_id: str, changes: Dict[str, Any]) -> Optional[Task]:
        """Change some fields of a task. None if there's no such task or the result is refused."""
        with self._lock:
            old = next((t for t in self._tasks if t["id"] == task_id), None)
            if old is None:
                return None
            merged = dict(old)
            merged.update({k: v for k, v in changes.items() if k in FIELDS and v is not None})
            if "repeat" not in changes:
                merged["repeat"] = old.get("repeat")
            new = normalize(merged)
            if check_task(new):
                return None
            for k in ("id", "said", "created", "caused_by"):
                new[k] = old.get(k)
            new["last_run"] = None
            self._tasks = [new if t["id"] == task_id else t for t in self._tasks]
            self._save()
            return dict(new)

    def remove(self, task_id: str) -> bool:
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if t["id"] != task_id]
            if len(self._tasks) != before:
                self._save()
                return True
        return False

    def clear(self) -> int:
        with self._lock:
            gone = len(self._tasks)
            self._tasks = []
            if gone:
                self._save()
        return gone

    def forget(self, audience: str) -> int:
        """The instruction store's "forget the delivery instruction" ('all' for every task
        with words for visitors)."""
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if not (
                t["action"] == SAY_VISITOR and (audience == "all" or
                                                visitor_instruction(t)["audience"] == audience
                                                or (audience == oi.VISITOR and t["trigger"] == DOORBELL)))]
            gone = before - len(self._tasks)
            if gone:
                self._save()
        return gone

    # ---- reading -----------------------------------------------------------------

    def all(self) -> List[Task]:
        with self._lock:
            return json.loads(json.dumps(self._tasks))

    def get(self, task_id: str) -> Optional[Task]:
        return next((t for t in self.all() if t["id"] == task_id), None)

    def last(self) -> Optional[Task]:
        tasks = self.all()
        return tasks[-1] if tasks else None

    def relevant(self, label: str = "") -> List[Dict[str, str]]:
        """Words for the visitor at this moment, as owner instructions: a delivery gets the
        delivery and doorbell ones, anyone else who rang the doorbell ones; nothing for
        suspicious or nobody."""
        if label in (ThreatCategory.SUSPICIOUS, ThreatCategory.NOBODY):
            return []
        want = (DELIVERY, DOORBELL) if label == ThreatCategory.DELIVERY else (DOORBELL,)
        return [visitor_instruction(t) for t in self.all()
                if t["action"] == SAY_VISITOR and t["trigger"] in want]

    # ---- running -----------------------------------------------------------------

    def fire(self, event: Event, mode: str, now: Optional[datetime] = None) -> List[Action]:
        """Actions from the tasks this event triggers (source rules). Words for visitors
        are not run here: the visitor conversation uses relevant()."""
        now = now or datetime.now()
        out: List[Action] = []
        for task in self.all():
            if task["action"] == SAY_VISITOR or not self._matches(task, event, mode, now):
                continue
            actions = self._actions(task, mode, event.id)
            if actions:
                out += actions
                self._ran(task, now)
        return out

    def on_owner_speech(self, mode: str, caused_by: Optional[str] = None,
                        now: Optional[datetime] = None) -> List[Action]:
        """The tasks for "the next time you talk to me"."""
        now = now or datetime.now()
        out: List[Action] = []
        for task in self.all():
            if task["trigger"] == OWNER_SPEAKS and (not task["night"] or is_night(mode, now)):
                actions = self._actions(task, mode, caused_by)
                if actions:
                    out += actions
                    self._ran(task, now)
        return out

    def due(self, mode: str, now: Optional[datetime] = None) -> List[Action]:
        """The clock tasks due now (the pipeline's clock calls this)."""
        now = now or datetime.now()
        out: List[Action] = []
        for task in self.all():
            if task["trigger"] != TIME or task["time"] != now.strftime("%H:%M"):
                continue
            if (task.get("last_run") or "")[:10] == now.strftime("%Y-%m-%d"):
                continue                      # already ran today
            actions = self._actions(task, mode, None)
            self._ran(task, now)              # a missed reminder (owner away) is not repeated
            out += actions
        return out

    def next_time(self, now: Optional[datetime] = None) -> Optional[float]:
        """Seconds until the next clock task, or None."""
        now = now or datetime.now()
        waits = []
        for task in self.all():
            if task["trigger"] == TIME:
                h, m = (int(x) for x in task["time"].split(":"))
                at = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if at <= now - timedelta(seconds=30):
                    at += timedelta(days=1)
                waits.append(max(0.0, (at - now).total_seconds()))
        return min(waits) if waits else None

    def _matches(self, task: Task, event: Event, mode: str, now: datetime) -> bool:
        trigger, data = task["trigger"], event.data or {}
        if task.get("night") and not is_night(mode, now):
            return False
        if trigger == DOORBELL:
            return event.type == EventType.DOORBELL
        if trigger == MOTION:
            return event.type == EventType.MOTION
        if trigger == DELIVERY:
            if event.type != EventType.THREAT_ASSESSED or data.get("category") != ThreatCategory.DELIVERY:
                return False
            last = task.get("last_run")
            try:
                recent = last and (now - datetime.fromisoformat(last).replace(tzinfo=None)).total_seconds() \
                    < DELIVERY_REPEAT_S
            except ValueError:
                recent = False
            return not recent
        if trigger == HOME:
            return (event.type == EventType.MODE_CHANGED and data.get("mode") == Mode.HOME
                    and data.get("changed_by") == ChangedBy.PASSPHRASE)
        if trigger == MODE_CHANGE:
            return (event.type == EventType.MODE_CHANGED and data.get("changed_by") != ChangedBy.STARTUP
                    and data.get("mode") != data.get("previous")
                    and task.get("mode") in (None, data.get("mode")))
        return False

    @staticmethod
    def _actions(task: Task, mode: str, caused_by: Optional[str]) -> List[Action]:
        action = task["action"]
        try:
            if action == SAY_OWNER:
                if mode == Mode.AWAY:
                    return []                 # the owner is out: never said to whoever is at the door
                return [make_action(ActionType.SPEAK, {"text": reminder_line(task)}, Source.RULES,
                                    caused_by=caused_by)]
            if action in (LIGHTS_ON, LIGHTS_OFF):
                kind = ActionType.OUTLET_ON if action == LIGHTS_ON else ActionType.OUTLET_OFF
                return [make_action(kind, {"outlet": o}, Source.RULES, caused_by=caused_by)
                        for o in task["outlets"]]
            if action == SET_MODE:
                if mode != Mode.HOME:
                    return []                 # only arming from Home; never leaves an armed mode
                return [make_action(ActionType.SET_MODE, {"mode": task["to_mode"]}, Source.RULES,
                                    caused_by=caused_by)]
            if action == TAKE_PHOTO:
                return [make_action(ActionType.TAKE_PHOTO, {"reason": "standing task"}, Source.RULES,
                                    caused_by=caused_by)]
        except ValueError:
            return []
        return []

    def _ran(self, task: Task, now: datetime) -> None:
        with self._lock:
            if task.get("repeat") == ONCE:
                self._tasks = [t for t in self._tasks if t["id"] != task["id"]]
            else:
                for t in self._tasks:
                    if t["id"] == task["id"]:
                        t["last_run"] = now.isoformat(timespec="seconds")
            self._save()

    # ---- file --------------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.skipped.append(f"could not read {self.path.name}: {e}")
            return
        for task in data.get("tasks", []) if isinstance(data, dict) else []:
            problems = check_task(task) if isinstance(task, dict) else ["not an object"]
            if problems or not isinstance(task.get("id"), str):
                self.skipped.append(f"skipped task {task!r:.80}: {'; '.join(problems) or 'no id'}")
                continue
            self._tasks.append(task)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"tasks": self._tasks}, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
