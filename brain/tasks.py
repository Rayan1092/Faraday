"""The owner's standing tasks through Bonsai: one tool set, no presets.

The router (brain/owner.py) sends anything about a standing task here: "remind me...",
"if a delivery comes, tell them...", "whenever someone rings at night...", "at 9 PM...",
"actually make that the side door", "what are my tasks?", "cancel the reminder". The
smart Bonsai (the 9B on the Mac) calls one of four tools:

    remember(trigger, action, ...)    a new task
    update_task(task_id, ...)         change one ("actually make that the side door")
    remove_task(task_id)              cancel one, or "all"
    list_tasks()                      say what's set

Plain code checks the answer against the owner's words before anything is stored
(rules/tasks.py then checks the task itself):
- the trigger, "at night", the time, the lights and the mode must be in the words;
- every content word of what Faraday will say must come from the owner (for an update,
  from the owner or the task being changed), so Bonsai can't invent it.
Faraday then says back in one sentence what it understood (rules/tasks.confirmation).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from shared import Mode, Outlet

from rules import instructions as oi
from rules import tasks as T

PROMPT = (
    "You keep the owner's standing tasks for their home guard. Call exactly one tool. "
    "remember: a new task. update_task: change a task, the last one unless they name another "
    "(\"actually make that the side door\"). remove_task: cancel one. list_tasks: they ask what is set. "
    "Triggers: home (the owner gets home), doorbell, delivery, motion, mode_change, time (HH:MM, "
    "24-hour), owner_speaks (the next time they talk to you). night: only if they say at night. "
    "Actions: say_owner (remind the owner), say_visitor (tell whoever is at the door), lights_on, "
    "lights_off, set_mode (away or night), take_photo. text: what to say, in the owner's own words, "
    "starting with the verb.")

_PROPS = {
    "trigger": {"type": "string", "enum": list(T.TRIGGERS)},
    "night": {"type": "boolean"},
    "time": {"type": "string", "description": "HH:MM, 24-hour"},
    "mode": {"type": "string", "enum": [Mode.HOME, Mode.AWAY, Mode.NIGHT], "description": "mode_change: which mode"},
    "action": {"type": "string", "enum": list(T.ACTIONS)},
    "text": {"type": "string"},
    "outlets": {"type": "array", "items": {"type": "string", "enum": sorted(Outlet.ALL)}},
    "to_mode": {"type": "string", "enum": [Mode.AWAY, Mode.NIGHT]},
}


def _tool(name: str, description: str, props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": props, "required": required}}}


TOOLS = [
    _tool("remember", "A new standing task.", _PROPS, ["trigger", "action"]),
    _tool("update_task", "Change a task.", {"task_id": {"type": "string"}, **_PROPS}, []),
    _tool("remove_task", "Cancel a task.", {"task_id": {"type": "string", "description": "a task id, or all"}},
          ["task_id"]),
    _tool("list_tasks", "Say which tasks are set.", {}, []),
]
REMEMBER, UPDATE, REMOVE, LIST = "remember", "update", "remove", "list"

# ---- what the owner's words back ----------------------------------------------------

_TRIGGER_WORDS = {
    T.HOME: r"\b(home|get back|come back|back|arrive\w*|return\w*|walk in|get in)\b",
    T.DOORBELL: r"\b(door ?bell|bell|rings?|ringing|rang|knocks?|knocking|visitors?|guests?|someone|somebody|"
                r"anyone|anybody|people|at the door|to the door)\b",
    T.DELIVERY: r"\b(deliver\w*|packages?|parcels?|couriers?|amazon|fedex|ups|dhl|usps|mail\w*|postman|"
                r"drivers?|boxes|box)\b",
    T.MOTION: r"\b(motion|movement|moves?|moving|walks?|walking|outside|activity|someone|somebody|anyone)\b",
    T.MODE_CHANGE: r"\b(mode|arm\w*|disarm\w*|leav\w*|bed|sleep\w*|away|night)\b",
    T.OWNER_SPEAKS: r"\b(talk|speak|hear from me|next time i)\b",
}
_NIGHT = re.compile(r"\b(at night|night ?time|tonight|overnight|after dark|in the night|nights|every night)\b", re.I)
_ALWAYS = re.compile(r"\b(whenever|every ?time|each time|every day|every night|every morning|daily|always|"
                     r"from now on)\b", re.I)
_ONCE = re.compile(r"\b(next time|once|just this time|today|tonight|tomorrow)\b", re.I)
_OFF = re.compile(r"\b(off|out)\b", re.I)
_ON = re.compile(r"\bon\b", re.I)
_PHOTO = re.compile(r"\b(photos?|pictures?|snapshots?|snap|record\w*)\b", re.I)
_TO_MODE = {Mode.NIGHT: re.compile(r"\b(night|bed|sleep\w*)\b", re.I),
            Mode.AWAY: re.compile(r"\b(away|arm\w*|leav\w*|out)\b", re.I)}
_OUTLET_WORDS = {Outlet.PORCH_LIGHT: re.compile(r"\b(porch|front light|outside light|outdoor light)\b", re.I),
                 Outlet.LIVING_ROOM_LAMP: re.compile(r"\b(lamp|living ?room)\b", re.I),
                 Outlet.SPARE: re.compile(r"\b(spare|third outlet|outlet (3|three))\b", re.I)}
_BOTH = re.compile(r"\b(both|all)\b.*\b(lights?|lamps?)\b|\b(the )?lights\b", re.I)
_ALL = re.compile(r"\b(all|every|everything)\b", re.I)
_CORRECTION = re.compile(r"^\s*(?:(?:hey|ok|okay)\s+)?(?:faraday[\s,.!]*)?(?:actually|instead|no|sorry|wait|"
                         r"correction|change|make (?:that|it))\b", re.I)
# Words that carry no content: the rest of what Faraday says must be the owner's.
_PLAIN = {"the", "a", "an", "to", "of", "at", "by", "on", "in", "it", "its", "and", "or", "please", "them",
          "him", "her", "they", "their", "your", "you", "you're", "is", "be", "are", "owner", "owner's", "my",
          "me", "i", "i'm", "we", "our", "that", "this", "for", "with", "from", "up", "out", "there", "so",
          "just", "will", "can", "should", "need", "needs"}
_LEAD_IN = re.compile(r"^(?:to\s+|that\s+|remember\s+to\s+|don'?t\s+forget\s+to\s+|remind\s+(?:me|you)\s+(?:to|that)\s+)+",
                      re.I)
# Only part of a task's words ("the side door", "by the gate"): it goes in place of the old part.
_PART = re.compile(r"^(?:the|a|an|my|your|our|by|at|near|next|on|in|behind|under|beside|to the)\b", re.I)
_HEAD = re.compile(r"(?:\b(?:the|a|an|my|your|our)\s+)?(?:\w+\s+){0,2}?(\w+)$", re.I)


def words_time(text: str) -> Optional[str]:
    """The clock time in the owner's words, as HH:MM: "9 PM" -> "21:00", "7:30 am",
    "21:00", "noon", "midnight". None if there's none."""
    t = text.lower().replace(".", "")
    if re.search(r"\bnoon\b|\bmidday\b", t):
        return "12:00"
    if re.search(r"\bmidnight\b", t):
        return "00:00"
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a m|p m)\b", t)
    if m:
        h, mins = int(m.group(1)), int(m.group(2) or 0)
        if not 1 <= h <= 12 or mins > 59:
            return None
        pm = m.group(3).startswith("p")
        h = (h % 12) + (12 if pm else 0)
        return f"{h:02d}:{mins:02d}"
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s+(tonight|this evening|in the evening|at night|this afternoon|"
                  r"in the afternoon|in the morning|tomorrow morning)\b", t)
    if m and 1 <= int(m.group(1)) <= 12 and int(m.group(2) or 0) <= 59:
        h = int(m.group(1)) % 12 + (0 if m.group(3).endswith("morning") else 12)
        return f"{h:02d}:{int(m.group(2) or 0):02d}"
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"\b(\d{1,2})\s*o'?clock\b(?:\s+(in the (morning|evening|afternoon)|at night|tonight))?", t)
    if m and 1 <= int(m.group(1)) <= 12:
        h = int(m.group(1)) % 12
        later = m.group(2) and not m.group(2).endswith("morning")
        return f"{h + (12 if later else 0):02d}:00"
    return None


def _content(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _PLAIN]


def grounded(text: str, *sources: str) -> bool:
    """Is every content word of `text` in the sources (4-letter stems)? The first word
    may be a plain request verb Bonsai chose ("leave" for the owner's "put")."""
    heard = {w[:4] for s in sources for w in re.findall(r"[a-z0-9']+", (s or "").lower())}
    words = _content(text)
    if words and oi._DO.match(text.strip()) and words[0][:4] not in heard:
        words = words[1:]
    return all(w[:4] in heard for w in words)


def _outlets_in(text: str) -> List[str]:
    named = [o for o, rx in _OUTLET_WORDS.items() if rx.search(text)]
    if named:
        return named
    if _BOTH.search(text):
        return [Outlet.PORCH_LIGHT, Outlet.LIVING_ROOM_LAMP]
    if re.search(r"\blight\b", text, re.I):
        return [Outlet.PORCH_LIGHT]
    return []


@dataclass
class TaskPlan:
    op: str = ""                         # remember, update, remove, list; "" when nothing held
    task: Optional[Dict[str, Any]] = None  # remember: the task; update: the changed task
    task_id: Optional[str] = None        # update / remove ("all" removes every task)
    note: str = ""
    elapsed_s: Optional[float] = None
    via: str = "bonsai"
    model: Dict[str, Any] = field(default_factory=dict)   # the tool call, for the log and the bench


def check_words(task: Dict[str, Any], said: str, old: Optional[Dict[str, Any]] = None) -> tuple:
    """(task, problem): the task with only what the owner's words back. `old`: the task
    being changed, whose words count too."""
    task = dict(task)
    for k in ("time", "mode", "to_mode", "text"):
        if task.get(k) is not None and not isinstance(task.get(k), str):
            task[k] = None
    if isinstance(task.get("text"), str):
        task["text"] = _LEAD_IN.sub("", " ".join(task["text"].split()).strip().rstrip(".!,?"))
    if task.get("trigger") == T.MODE_CHANGE and task.get("mode") == Mode.HOME:
        task["trigger"], task["mode"] = T.HOME, None          # only the passphrase reaches Home
    trigger, action = task.get("trigger"), task.get("action")
    if old is None or trigger != old.get("trigger"):
        if trigger == T.TIME:
            at = words_time(said)
            if at is None:
                return task, "no time in the words"
            task["time"] = at                                 # the owner's time, not Bonsai's
        elif trigger in _TRIGGER_WORDS and not re.search(_TRIGGER_WORDS[trigger], said, re.I):
            return task, f"nothing in the words for {trigger}"
    elif trigger == T.TIME and words_time(said):
        task["time"] = words_time(said)
    night = bool(_NIGHT.search(said))
    if old is None or night:
        task["night"] = night if trigger != T.TIME else False
    if action in (T.SAY_OWNER, T.SAY_VISITOR):
        text = str(task.get("text") or "")
        partial = bool(_PART.match(text)) and not _PART.match(str((old or {}).get("text") or ""))
        if old is not None and old.get("text") and text and (partial or not grounded(text, said)):
            # "actually make that the side door": "the side door" takes the place of the
            # matching words of the old task ("use the main door" -> "use the side door").
            text = _merge(old["text"], text)
            task["text"] = text
        if not text or not grounded(text, said, (old or {}).get("text", "")):
            return task, "the words to say weren't the owner's"
    if action in (T.LIGHTS_ON, T.LIGHTS_OFF):
        if _OFF.search(said) and not _ON.search(said):
            task["action"] = T.LIGHTS_OFF
        elif _ON.search(said) and not _OFF.search(said):
            task["action"] = T.LIGHTS_ON
        named = _outlets_in(said)
        if named:
            task["outlets"] = named
        elif old is None or not task.get("outlets"):
            return task, "which lights wasn't said"
    if action == T.SET_MODE:
        want = [m for m, rx in _TO_MODE.items() if rx.search(said.split(" switch", 1)[-1])]
        if task.get("to_mode") not in want:
            if len(want) != 1:
                return task, "which mode wasn't said"
            task["to_mode"] = want[0]
    if action == T.TAKE_PHOTO and not _PHOTO.search(said) and old is None:
        return task, "no photo in the words"
    if old is None or "repeat" not in task or task.get("repeat") is None:
        task["repeat"] = (T.ALWAYS if _ALWAYS.search(said) else T.ONCE if _ONCE.search(said)
                          else T.default_repeat(task))
    return task, ""


def _merge(old_text: str, new: str) -> str:
    """Put the new words in place of the old words with the same last word."""
    new = re.sub(r"^(?:make (?:that|it)|change (?:that|it) to|to)\s+", "", new.strip(), flags=re.I)
    head = (_HEAD.search(new) or [None, None])[1]
    if not head:
        return new
    m = re.search(r"(?:\b(?:the|a|an|my|your|our)\s+)?(?:\w+\s+){0,2}?" + re.escape(head) + r"\b", old_text, re.I)
    return old_text[:m.start()] + new + old_text[m.end():] if m else new


def _call(reply: Any) -> tuple:
    """(tool name, args) from a model reply, or ("", {})."""
    for call in (reply or {}).get("tool_calls") or [] if isinstance(reply, dict) else []:
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict):
            continue
        raw = fn.get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            args = {}
        if isinstance(args, dict) and fn.get("name") in ("remember", "update_task", "remove_task", "list_tasks"):
            return fn["name"], args
    return "", {}


def summaries(tasks: List[Dict[str, Any]]) -> str:
    """The tasks for the prompt, newest first (at most 5, short)."""
    if not tasks:
        return "(none)"
    out = []
    for i, t in enumerate(reversed(tasks[-5:])):
        out.append(f"- {t['id']}{' (last)' if i == 0 else ''}: {T.describe(t)}")
    return "\n".join(out)


def plan(text: str, tasks: List[Dict[str, Any]], client: Any, log: Callable[[str], None] = print) -> TaskPlan:
    """Ask Bonsai which task tool fits, and check its answer against the words."""
    text = " ".join(str(text or "").split())
    started = time.perf_counter()
    try:
        reply = client.chat([{"role": "system", "content": PROMPT},
                             {"role": "user", "content": f"Tasks now:\n{summaries(tasks)}\nOwner said: \"{text}\""}],
                            TOOLS)
    except Exception as e:
        log(f"[tasks] Bonsai failed: {e}")
        return TaskPlan(note=f"Bonsai failed: {e}", elapsed_s=time.perf_counter() - started)
    name, args = _call(reply)
    result = check(name, args, text, tasks)
    result.elapsed_s = time.perf_counter() - started
    result.model = {"tool": name, "args": args}
    log(f"[tasks] Bonsai: {name or 'no tool'} {json.dumps(args)[:200]} -> {result.op or 'refused'}"
        + (f" ({result.note})" if result.note else "") + f", {result.elapsed_s:.2f}s")
    return result


def check(name: str, args: Dict[str, Any], text: str, tasks: List[Dict[str, Any]]) -> TaskPlan:
    """Code's say on one tool call."""
    by_id = {t["id"]: t for t in tasks}
    last = tasks[-1] if tasks else None
    if name == "list_tasks":
        return TaskPlan(LIST)
    if name == "remove_task":
        target = str(args.get("task_id") or "")
        if target == "all":
            return TaskPlan(REMOVE, task_id="all") if _ALL.search(text) else TaskPlan(note="'all' wasn't said")
        task = by_id.get(target) or _named(text, tasks) or last
        return TaskPlan(REMOVE, task=task, task_id=task["id"]) if task else TaskPlan(note="no task to remove")
    if name == "update_task" or (name == "remember" and last is not None and _CORRECTION.search(text)):
        old = by_id.get(str(args.get("task_id") or "")) or last
        if old is None:
            return TaskPlan(note="no task to change")
        changes = {k: v for k, v in args.items() if k in T.FIELDS and v not in (None, "", [])}
        if name == "remember":       # a correction Bonsai stored as new: only what it changes
            changes = {k: v for k, v in changes.items() if k in ("text", "time", "outlets", "to_mode", "night")}
        merged = dict(old)
        merged.update(changes)
        merged, problem = check_words(merged, text, old=old)
        if problem:
            return TaskPlan(note=problem)
        new = T.normalize(merged)
        problems = T.check_task(new)
        return TaskPlan(UPDATE, task=new, task_id=old["id"]) if not problems else TaskPlan(note=problems[0])
    if name == "remember":
        task, problem = check_words({k: args.get(k) for k in T.FIELDS}, text)
        if problem:
            return TaskPlan(note=problem)
        task = T.normalize(task)
        problems = T.check_task(task)
        return TaskPlan(REMEMBER, task=task) if not problems else TaskPlan(note=problems[0])
    return TaskPlan(note="no task tool called")


def _named(text: str, tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The task the owner's words point at ("the delivery one", "the reminder")."""
    for t in reversed(tasks):
        if t["trigger"] == T.DELIVERY and re.search(_TRIGGER_WORDS[T.DELIVERY], text, re.I):
            return t
        if t["action"] == T.SAY_OWNER and re.search(r"\bremind\w*\b", text, re.I):
            return t
        if t["trigger"] == T.TIME and words_time(text) and words_time(text) == t.get("time"):
            return t
    return None


# ---- without a model: what the keywords read ------------------------------------------

def from_instruction(parsed: Dict[str, str]) -> Dict[str, Any]:
    """An owner instruction the keyword parser read, as a task."""
    trigger = T.DELIVERY if parsed.get("audience") == oi.DELIVERY else T.DOORBELL
    return {"trigger": trigger, "action": T.SAY_VISITOR, "text": parsed["request"], "repeat": T.ALWAYS,
            "night": False}


def from_rule(rule: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """An add_rule the keywords (or Bonsai's rule) built, as a task, when it fits one."""
    when, steps = rule.get("when") or {}, rule.get("then") or []
    trigger = {"doorbell": T.DOORBELL, "motion": T.MOTION}.get(when.get("event"))
    if trigger is None or not steps:
        return None
    night = Mode.NIGHT in (when.get("modes") or [])
    kinds = {s.get("type") for s in steps}
    if kinds <= {"outlet_on"} or kinds <= {"outlet_off"}:
        action = T.LIGHTS_ON if kinds == {"outlet_on"} else T.LIGHTS_OFF
        return {"trigger": trigger, "action": action, "night": night, "repeat": T.ALWAYS,
                "outlets": [s["args"]["outlet"] for s in steps if isinstance(s.get("args"), dict)]}
    if kinds == {"take_photo"}:
        return {"trigger": trigger, "action": T.TAKE_PHOTO, "night": night, "repeat": T.ALWAYS}
    return None
