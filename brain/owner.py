"""The owner's requests: what "Hey Faraday, ..." asks for.

Owner speech (speech_heard.channel "owner", or untagged speech at Home) never goes
through the visitor conversation. It gets one of a fixed set of intents:

    arm_away, arm_night, passphrase, summary, look, add_rule, add_instruction,
    light, unclear

1. The small Bonsai (2B) picks one through a forced-choice `route` tool.
2. Plain code checks it against the words. An intent with nothing in the words
   to back it is not accepted. A clear keyword match (rules, instructions,
   lights, "who's at the door?", leaving, bedtime, "what happened") wins over
   the model. With no model, or no usable answer, the keywords decide.
3. Anything else is "unclear": "Sorry, I can't help with that. You can ask who's at
   the door, what happened, or arm the house." (while armed: "The system is armed.
   I can't make changes right now.", never a word about a passphrase). A request that was
   cut off ("if a delivery comes, ask them to") or an instruction or rule that
   can't be built asks the owner to say it again. Owner speech is never saved as
   a visitor message.

What each intent then does is the pipeline's job (brain/pipeline.py). While the
house is armed, a wake request changes nothing: only the passphrase does that.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from shared import Event, EventType, Mode, Outlet, SpeechChannel

from rules import instructions as oi

from . import fallback as fb
from . import look

ARM_AWAY, ARM_NIGHT, PASSPHRASE, SUMMARY, LOOK = "arm_away", "arm_night", "passphrase", "summary", "look"
ADD_RULE, ADD_INSTRUCTION, LIGHT, UNCLEAR = "add_rule", "add_instruction", "light", "unclear"
TASK = "task"     # a standing task: remember, change, cancel or list (brain/tasks.py)
INTENTS = (ARM_AWAY, ARM_NIGHT, PASSPHRASE, SUMMARY, LOOK, TASK, ADD_RULE, ADD_INSTRUCTION, LIGHT, UNCLEAR)
# What may run while the house is armed (nothing that changes anything).
ARMED_OK = {Mode.NIGHT: {LOOK}, Mode.AWAY: set()}

SYSTEM_PROMPT = (
    "You sort one request the owner said to their home guard. Call route once. "
    "arm_away: leaving the house now. arm_night: going to bed now. passphrase: wants to disarm. "
    "summary: what happened, visitors, messages. look: who or what is at the door. "
    "task: something to remember for later, change or cancel: a reminder, words for a visitor "
    "or delivery, what to do when something happens or at a time, or which tasks are set. "
    "light: turn a light on or off now. unclear: anything else (then topic: what they asked "
    "for, in a few words), or a request that stops before the end.")
TOOL = {"type": "function", "function": {
    "name": "route", "description": "The owner's request.",
    "parameters": {"type": "object", "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "outlet": {"type": "string", "enum": sorted(Outlet.ALL)},
        "on": {"type": "boolean"},
        "topic": {"type": "string"}}, "required": ["intent"]}}}
# What makes a standing task plain (routing only; brain/tasks.py reads the task). Review:
# a time or "actually" alone must not steal "I'm heading out, back at 6 pm", "Actually,
# turn on the lamp" or "What happened at 2 pm?".
_WAKE_RX = r"^\s*(?:(?:hey|ok|okay)\s+)?(?:faraday[\s,.!]*)?"
_PAST_QUESTION = re.compile(_WAKE_RX + r"(?:what|who|did|was|were|has|have|is there|are there|how|"
                            r"when (?:did|was|were))\b|" + _WAKE_RX + r"any\b.*\?\s*$", re.I)
_TASK_WORDS = re.compile(r"\bremind (?:me|us)\b(?!\s+(?:what|who|whether|if|how)\b)"
                         r"|\b(?:set|add) (?:a |an )?(?:reminder|task)\b|\b(?:my|the|any|all) (?:tasks?|reminders?)\b"
                         r"|\b(?:what|which) (?:tasks?|reminders?)\b|\bnext time (?:i|we) (?:talk|speak)\b", re.I)
_NUM = r"(?:\d{1,2}(?::\d{2})?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_TIME_WORDS = re.compile(r"\b(?:at|by|around) (?:" + _NUM + r"|noon|midnight)\b"
                         r"|\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)(?!\w)"
                         r"|\bin (?:an?|half an|\d+|one|two|five|ten|fifteen|twenty|thirty) (?:minutes?|mins?|hours?)\b"
                         r"|\blater (?:tonight|today|on)\b", re.I)
_TIME_ASIDE = re.compile(r"\b(?:back|return\w*|home|wake me|see you|until|till)\b[^.?!]{0,12}$", re.I)
_EVENT_CLAUSE = re.compile(r"\b(?:if|when|whenever|every ?time|each time|once)\b[^.?!]*\b(?:door ?bell|bell|rings?|"
                           r"knocks?|motion|movement|moves|deliver\w*|packages?|parcels?|couriers?|visitors?|guests?|"
                           r"someone comes|anyone comes|get home|get back|come home|i'?m home|arrive\w*)\b"
                           r"|^\s*at night\b", re.I)
_AUDIENCE_SHOULD = re.compile(r"\b(?:couriers?|deliver\w*|visitors?|guests?|drivers?|anyone|someone|whoever)\b"
                              r"[^.?!]*\b(?:should|must|needs? to|has to)\b", re.I)
_CORRECTION_START = re.compile(_WAKE_RX + r"(?:actually|instead|change (?:that|it)|make (?:that|it))\b[\s,]*", re.I)
_NOW_INTENTS = (ARM_AWAY, ARM_NIGHT, LIGHT, LOOK, SUMMARY, PASSPHRASE)
_CHANGE = re.compile(r"\b(?:change|move|update|cancel|forget|remove|delete|scrap|swap|replace)\b", re.I)
_TASK_THING = re.compile(r"\b(?:deliver\w*|spot|reminders?|tasks?|instructions?|thing|package|parcel|side door|"
                         r"main door|back door|front door)\b", re.I)
# Words that make a request about a task or the visitor's words, never about leaving now.
_NOT_LEAVING = re.compile(r"\b(?:cancel|forget|remove|delete|make (?:that|it)|change|instead|deliver\w*|"
                          r"packages?|parcels?|couriers?|reminders?|tasks?|instructions?|spot|tell (?:them|him|her)|"
                          r"ask (?:them|him|her))\b", re.I)


def clearly_arming(text: str) -> Optional[str]:
    """'away' or 'night' only when the request is plainly about leaving or going to bed now
    (owner, Mac: "cancel that leaving it by the side door" armed the house). Never a
    shortcut on its own: the model must pick arming too (OwnerRouter._check)."""
    if _NEGATION.search(text) or _NOT_LEAVING.search(text):
        return None
    return fb.arm_intent(text)


def is_task(text: str, has_tasks: bool = False) -> bool:
    """Is this something to do later (a standing task), not now?"""
    if _PAST_QUESTION.match(text) and not re.search(r"\b(?:tasks?|reminders?)\b", text, re.I):
        return False                      # "What happened at 2 pm?", "Did anyone ring at 3 pm?"
    if _TASK_WORDS.search(text):
        return True
    m = _CORRECTION_START.match(text)
    if m:
        rest = text[m.end():]
        now = strong(rest) in _NOW_INTENTS or (clearly_arming(rest) is not None)
        if now and not (_TIME_WORDS.search(rest) or _EVENT_CLAUSE.search(rest)):
            return False                  # "Actually, I'm heading out", "Make it night mode"
        if has_tasks:
            return True                   # "Actually, make that the side door"
    if (has_tasks and _CHANGE.search(text) and _TASK_THING.search(text)
            and not fb._LIGHT.search(text) and not _WEAK[PASSPHRASE].search(text)
            and not re.search(r"\b(?:alarm|lights?|lamp)\b", text, re.I)):
        return True                       # "change the delivery spot to the main door", "cancel the side door thing"
    if _EVENT_CLAUSE.search(text) or _AUDIENCE_SHOULD.search(text):
        return True
    t = _TIME_WORDS.search(text)
    if t:
        if fb.arm_intent(text) is not None and _TIME_ASIDE.search(text[:t.start()]):
            return False                  # "I'm heading out, back at 6 pm": arm now
        return True                       # "Switch to night mode at 9": later, never now
    return False

# When the keyword parser can't read an instruction, the 2B pulls it out; code checks
# it (rules/instructions.from_model). Owner, Mac test: "any phrasing".
EXTRACT_PROMPT = (
    "Pull the owner's instruction for visitors out of what they said. Call instruction once. "
    "audience: delivery (couriers, packages, parcels), visitor (guests, friends, neighbours) or "
    "anyone. request: what to tell them, in the owner's own words, starting with the verb, "
    "for example 'leave the package by the side door' or 'come back after six'.")
EXTRACT_TOOL = {"type": "function", "function": {
    "name": "instruction", "description": "Who the instruction is for, and what to tell them.",
    "parameters": {"type": "object", "properties": {
        "audience": {"type": "string", "enum": list(oi.AUDIENCES)},
        "request": {"type": "string"}}, "required": ["audience", "request"]}}}

# Words that back each intent. Weak evidence: enough to accept the model's pick.
_WEAK = {
    ARM_AWAY: re.compile(r"\b(heading out|leaving|leave|going out|off to|i'?m off|out for|bye|"
                         r"away|arm)\b", re.I),
    ARM_NIGHT: re.compile(r"\b(good ?night|night|bed|bedtime|sleep|sleeping)\b", re.I),
    PASSPHRASE: re.compile(r"\b(disarm|pass ?phrase|password|unlock|turn off the alarm|"
                           r"stop the alarm|switch off the alarm)\b", re.I),
    SUMMARY: re.compile(r"\b(happen\w*|summar\w*|recap|visitors?|messages?|who came|"
                        r"anyone (come|came|stop\w*|ring|rang)|news|log|while i was)\b", re.I),
    # "look up the news" is not a look at the door (bench)
    LOOK: re.compile(r"\b(door|outside|porch|camera|see)\b|\blook\b(?!\s+(?:up|into|for|forward)\b)", re.I),
    ADD_RULE: re.compile(r"\b(when(ever)?|every time|each time|from now on)\b", re.I),
    ADD_INSTRUCTION: re.compile(r"\b(if|when(ever)?)\b.*\b(deliver\w*|couriers?|parcels?|packages?|visitors?|"
                                r"someone|anyone|somebody|guests?|friends?|amazon|fedex|ups|dhl|usps|drivers?)\b"
                                r"|\b(tell|ask|let)\b.*\b(them|him|her|visitors?|deliver\w*|couriers?|drivers?|"
                                r"anyone|everyone|guests?)\b", re.I),
    LIGHT: re.compile(r"\b(lights?|lamp|porch|outlet)\b", re.I),
    TASK: re.compile(r"\b(remind\w*|remember|whenever|every ?time|each time|when|if|next time|tell|ask|"
                     r"let \w+ know|cancel|forget|remove|delete|actually|instead|make (that|it)|"
                     r"change (that|it)|tasks?|reminders?|instructions?|rules?|noon|midnight)\b"
                     r"|\b\d{1,2}(:\d{2})?\s*(a\.?m\.?|p\.?m\.?|o'?clock)", re.I),
}
# Out of scope: a topic Bonsai named, checked by code, or one plain category, or nothing.
_VERBS = {"check", "tell", "find", "look", "search", "send", "text", "call", "phone", "play", "order", "buy",
          "book", "get", "read", "show", "give", "make", "email", "message", "answer", "start", "stop",
          "open", "translate", "count", "calculate", "recommend", "schedule", "pay", "browse", "stream"}
_CATEGORIES = [(re.compile(r"\b(weather|forecast|rain\w*|snow\w*|temperature)\b", re.I), "check the weather"),
               (re.compile(r"\b(texts?|sms|e-?mails?|call|phone|whatsapp)\b", re.I), "send texts or make calls"),
               (re.compile(r"\b(search|google|look up|internet|online|web|news|website)\b", re.I),
                "look things up online"),
               (re.compile(r"\b(music|songs?|playlist|radio|podcasts?)\b", re.I), "play music"),
               (re.compile(r"\b(order|buy|shop\w*)\b", re.I), "order things")]


def topic_for(text: str, model_topic: Any = None) -> str:
    """What the owner asked for, for "Sorry, I can't <topic>." Bonsai's topic only if it's
    short, has no numbers and shares a word with what was said; else a plain category."""
    topic = re.sub(r"^(?:to|wants? to|the owner wants to)\s+", "", " ".join(str(model_topic or "").split()),
                   flags=re.I).strip().rstrip(".!?").lower()
    words = re.findall(r"[a-z']+", topic)
    heard = {w[:4] for w in re.findall(r"[a-z0-9']+", text.lower())}
    if (1 <= len(words) <= 6 and not re.search(r"[0-9\"<>{}]", topic)
            and any(len(w) >= 4 and w[:4] in heard for w in words)):
        topic = re.sub(r"\bmy\b", "your", re.sub(r"\bme\b", "you", topic))
        return topic if words[0] in _VERBS else f"help with {topic}"
    return next((name for rx, name in _CATEGORIES if rx.search(text)), "")
_RULE_EVENT = re.compile(r"\b(motion|movement|moves?|door ?bell|rings?|knocks?)\b", re.I)
_ON_OFF = re.compile(r"\b(on|off)\b", re.I)
# A request that stops mid-sentence: "ask them to", "if a delivery comes,".
_DANGLING = re.compile(r"(\b(to|the|a|an|and|or|but|if|when|whenever|that|them|at|for|with|of|"
                       r"my|your|please|ask|tell|say|is|are|so|then|you|can|could|would|will)|,)\s*$", re.I)
_NEGATION = re.compile(r"\b(don'?t|do not|not yet|never|no need|stop)\b", re.I)
_QUESTION = re.compile(r"^\s*(is|are|was|were|did|do|does|have|has)\b|\?\s*$", re.I)
_SUMMARY_STRONG = re.compile(r"\b(what did i miss|any (messages?|visitors?)|did (any|some)(one|body) "
                             r"(come|came|ring|rang|stop))\b", re.I)
_RULE_TRIGGER = re.compile(r"\b(when(ever)?|every time|each time|from now on)\b", re.I)
_TELL = re.compile(r"\b(tell|ask|say|let)\b", re.I)
_IF_ONLY = re.compile(r"^\s*(if|when(ever)?)\b[^,]*$", re.I)   # "if a delivery comes" and nothing else


@dataclass
class Route:
    intent: str
    via: str                                   # "bonsai", "code" or "rules" (no usable model answer)
    model_intent: Optional[str] = None         # what the 2B said, before code checked it
    outlet: Optional[str] = None
    on: Optional[bool] = None
    cut_off: bool = False                      # unclear because it stopped mid-sentence
    elapsed_s: Optional[float] = None
    note: str = ""
    rule: Optional[Dict[str, Any]] = None      # add_rule args, when code could build them
    lights: List[tuple] = field(default_factory=list)   # [(outlet, on)] for "light"
    instruction: Optional[Dict[str, str]] = None
    topic: str = ""                            # unclear: what they asked for ("check the weather")


def is_owner_speech(event: Event, mode: str) -> bool:
    """Owner speech: tagged "owner" by integration (after "Hey Faraday"). Untagged
    speech counts as the owner at Home and as a visitor while armed (as before)."""
    if event.type != EventType.SPEECH_HEARD:
        return False
    channel = event.data.get("channel")
    if channel == SpeechChannel.OWNER:
        return True
    if channel == SpeechChannel.VISITOR:
        return False
    return mode == Mode.HOME


def is_visitor_speech(event: Event, mode: str) -> bool:
    return event.type == EventType.SPEECH_HEARD and not is_owner_speech(event, mode)


def cut_off(text: str) -> bool:
    words = text.strip()
    return bool(words) and (bool(_DANGLING.search(words.rstrip(".!? "))) or bool(_IF_ONLY.match(words))
                            and oi.parse_instruction(words) is None)


def _outlet_in(text: str) -> Optional[str]:
    if re.search(r"\b(living room|lamp)\b", text, re.I):
        return Outlet.LIVING_ROOM_LAMP
    if re.search(r"\bspare\b", text, re.I):
        return Outlet.SPARE
    if re.search(r"\b(porch|lights?)\b", text, re.I):
        return Outlet.PORCH_LIGHT
    return None


def light_requests(text: str) -> List[tuple]:
    """[(outlet, on), ...] from the words: "porch light off and the lamp on" gives two.
    A part with no on/off takes the nearest one said ("the porch light and the lamp on")."""
    parts = [p for p in re.split(r"\band\b|,|;", text) if p.strip()]
    found = [(_outlet_in(p), (_ON_OFF.findall(p) or [None])[-1]) for p in parts]
    found = [(o, s.lower() if s else None) for o, s in found if o or s]
    states = [s for _, s in found]
    out: List[tuple] = []
    for i, (outlet, state) in enumerate(found):
        if outlet is None:
            continue
        if state is None:
            later = [s for s in states[i + 1:] if s]
            earlier = [s for s in states[:i] if s]
            state = later[0] if later else earlier[-1] if earlier else None
        if state is not None and (outlet, state == "on") not in out:
            out.append((outlet, state == "on"))
    return out


def light_slots(text: str) -> tuple:
    """(outlet, on) for the first light asked for; either may be None."""
    reqs = light_requests(text)
    if reqs:
        return reqs[0]
    return _outlet_in(text), None


def strong(text: str, tasks: bool = False, has_tasks: bool = False) -> Optional[str]:
    """The intent the words make plain, or None. Order matters: a rule or an
    instruction mentions lights and doors too. tasks=True: a standing task ("remind me",
    "at 9 PM", "actually make that...") comes first, so "at 9 PM switch to night mode"
    is never arming now."""
    if tasks and is_task(text, has_tasks):
        return TASK
    parsed = oi.parse_instruction(text)
    if parsed is not None and oi.is_forbidden(parsed) and fb.parse_rule(text) is not None:
        return ADD_RULE     # "when someone rings, ... turn on the porch light": a rule (review)
    if parsed is not None or oi.forget_request(text) is not None:
        return ADD_INSTRUCTION
    if fb.parse_rule(text) is not None or (_RULE_TRIGGER.search(text) and _RULE_EVENT.search(text)):
        return ADD_RULE     # never arm or switch now what was said as a rule for later
    if look.is_look_request(text):
        return LOOK
    if _WEAK[PASSPHRASE].search(text):
        return PASSPHRASE
    if fb._LIGHT.search(text):
        return LIGHT
    # No keyword arming (owner, Mac: "cancel that leaving it by the side door" armed the
    # house). Arming comes only from the model, backed by plainly leaving or bedtime words.
    if fb.is_summary_request(text) or _SUMMARY_STRONG.search(text):
        return SUMMARY
    return None


def backed(intent: str, text: str) -> bool:
    """Is there anything in the words for this intent? Arming changes the house, so
    it needs the plain words for it ("heading out", "goodnight") and no "don't"."""
    if intent in (ARM_AWAY, ARM_NIGHT):
        want = Mode.AWAY if intent == ARM_AWAY else Mode.NIGHT
        return clearly_arming(text) == want
    if intent == ADD_RULE:
        return bool(_WEAK[ADD_RULE].search(text) and _RULE_EVENT.search(text))
    rx = _WEAK.get(intent)
    return bool(rx and rx.search(text))


class OwnerRouter:
    def __init__(self, client: Optional[Any] = None, model: Optional[str] = None,
                 log: Callable[[str], None] = print):
        self.client, self.model, self.log = client, model, log
        # True in the product (Pipeline.from_settings): instructions, rules and reminders are
        # one standing-task intent, read by the smart Bonsai (brain/tasks.py).
        self.tasks_mode = False
        self.has_tasks: Callable[[], bool] = lambda: False   # "actually ..." is a correction only then
        # The smart Bonsai (9B on the Mac) must agree before the house arms (owner, Mac).
        self.confirm_client: Optional[Any] = None

    def route(self, text: str, extract: bool = True) -> Route:
        """extract=False (while armed, where nothing is stored anyway): never ask the 2B
        to pull out an instruction."""
        text = str(text or "").strip()
        clear = strong(text, self.tasks_mode, self.tasks_mode and self.has_tasks())
        model_intent, args, elapsed, via, failed = None, {}, None, "rules", False
        if self.client is not None and text:
            started = time.perf_counter()
            try:
                reply = self.client.chat([{"role": "system", "content": SYSTEM_PROMPT},
                                          {"role": "user", "content": f'Owner said: "{text}"'}], [TOOL])
                model_intent, args = parse(reply)
            except Exception as e:
                self.log(f"[owner] Bonsai failed: {e}")
                failed = True
            elapsed = time.perf_counter() - started
        if self.client is None and clear is None:
            # No model configured at all (rules-only runs, NV's rehearsal): nothing for a
            # keyword to override, so plainly leaving or bedtime words may arm.
            arm = clearly_arming(text)
            clear = ARM_AWAY if arm == Mode.AWAY else ARM_NIGHT if arm == Mode.NIGHT else None
        intent, note = self._check(text, clear, model_intent)
        refused_arm = False
        if (intent in (ARM_AWAY, ARM_NIGHT) and extract and self.confirm_client is not None
                and self.confirm_client is not self.client and model_intent is not None):
            started = time.perf_counter()
            try:
                second, _ = parse(self.confirm_client.chat(
                    [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": f'Owner said: "{text}"'}], [TOOL]))
            except Exception as e:
                self.log(f"[owner] the smart model's check failed: {e}")
                second = None
            elapsed = (elapsed or 0.0) + (time.perf_counter() - started)
            if second != intent:
                intent, note = UNCLEAR, f"the smart model said {second}, not {intent}: not arming"
                refused_arm = True
        if model_intent is not None:
            via = "bonsai" if intent == model_intent else "code"
        r = Route(intent, via, model_intent, elapsed_s=elapsed, note=note,
                  cut_off=refused_arm)   # a refused arm asks to say it again, not "I can't help"
        if r.intent in (TASK, ADD_INSTRUCTION, ADD_RULE) and self.tasks_mode:
            # A standing task: the smart Bonsai reads it (brain/tasks.py). What the keywords
            # read stays as the fallback when there's no model.
            r.intent, r.instruction, r.rule = TASK, oi.parse_instruction(text), fb.parse_rule(text)
            self.log(f"[owner] {text!r} -> task" + (f" (2B said {model_intent})" if model_intent
                                                   and model_intent != TASK else "")
                     + (f", {elapsed:.2f}s" if elapsed is not None else ""))
            return r
        if r.intent == TASK:            # no task store (older callers): the keywords decide
            r.intent = (ADD_INSTRUCTION if oi.parse_instruction(text) or oi.forget_request(text)
                        else ADD_RULE if fb.parse_rule(text) else UNCLEAR)
        self._fill(r, text, args)
        if r.intent == UNCLEAR:
            r.topic = topic_for(text, args.get("topic") if isinstance(args, dict) else None)
        if (extract and not failed and r.intent == UNCLEAR and not cut_off(text) and self.client is not None
                and (model_intent == ADD_INSTRUCTION or clear == ADD_INSTRUCTION)
                and oi.names_audience(text)):
            # The keywords couldn't read it, the 2B says it's an instruction, the owner
            # named who it's for, and it isn't cut off: the 2B pulls it out, code checks it.
            started = time.perf_counter()
            ins = self.extract_instruction(text)
            r.elapsed_s = (r.elapsed_s or 0.0) + (time.perf_counter() - started)
            if ins is not None:
                r.intent, r.instruction, r.cut_off = ADD_INSTRUCTION, ins, False
                r.via, r.note = "bonsai", "the 2B wrote the instruction, code checked it"
        self.log(f"[owner] {text!r} -> {r.intent}" + (f" (2B said {model_intent})" if model_intent
                                                      and model_intent != r.intent else "")
                 + (f", {elapsed:.2f}s" if elapsed is not None else "") + (f": {r.note}" if r.note else ""))
        return r

    def extract_instruction(self, text: str) -> Optional[Dict[str, str]]:
        """{"audience", "verb", "form", "request"} from natural speech via the 2B, checked
        by code (rules/instructions.from_model), or None."""
        try:
            reply = self.client.chat([{"role": "system", "content": EXTRACT_PROMPT},
                                      {"role": "user", "content": f'Owner said: "{text}"'}], [EXTRACT_TOOL])
        except Exception as e:
            self.log(f"[owner] instruction extraction failed: {e}")
            return None
        args: Dict[str, Any] = {}
        for call in (reply or {}).get("tool_calls") or [] if isinstance(reply, dict) else []:
            fn = call.get("function") if isinstance(call, dict) else None
            raw = fn.get("arguments") if isinstance(fn, dict) else None
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except ValueError:
                args = {}
            if args:
                break
        ins = oi.from_model(args, text)
        self.log(f"[owner] instruction from the 2B: {ins}" if ins
                 else f"[owner] the 2B's instruction didn't hold: {args}")
        return ins

    @staticmethod
    def _check(text: str, clear: Optional[str], model: Optional[str]) -> tuple:
        if model == UNCLEAR and clear is None:
            return UNCLEAR, ""
        if clear is not None:
            # The words make it plain; the 2B agreeing needs nothing more (bench: "Could you
            # tell any couriers..." was rejected because the weak words missed "couriers").
            return clear, (f"code: the words say {clear}" if model and model != clear else "")
        if model is not None and model != UNCLEAR and backed(model, text):
            return model, ""
        if model is not None and model != UNCLEAR:
            return UNCLEAR, f"code rejected {model}: nothing in the words for it"
        return UNCLEAR, ""

    @staticmethod
    def _fill(r: Route, text: str, args: Dict[str, Any]) -> None:
        """Slots for the intent; an intent that can't be completed becomes unclear."""
        if r.intent == LIGHT:
            if _QUESTION.search(text):
                r.intent, r.note = UNCLEAR, "light: a question, not a request"
                return
            r.lights = light_requests(text)
            if not r.lights:
                outlet = _outlet_in(text) or (args.get("outlet") if args.get("outlet") in Outlet.ALL else None)
                on = (_ON_OFF.findall(text) or [None])[-1]
                if outlet and on:
                    r.lights = [(outlet, on.lower() == "on")]
            if r.lights:
                r.outlet, r.on = r.lights[0]
            else:
                r.intent, r.note = UNCLEAR, "light: which one, or on or off, wasn't said"
        elif r.intent == ADD_INSTRUCTION:
            r.instruction = oi.parse_instruction(text)
            if r.instruction is None and oi.forget_request(text) is None:
                r.intent, r.cut_off, r.note = UNCLEAR, True, "instruction incomplete"
        elif r.intent == ADD_RULE:
            r.rule = fb.parse_rule(text)       # None: the pipeline asks Bonsai to write it
            if r.rule is None and cut_off(text):
                r.intent, r.cut_off, r.note = UNCLEAR, True, "rule incomplete"
        if r.intent == UNCLEAR and not r.cut_off:
            r.cut_off = cut_off(text) or bool(_WEAK[ADD_INSTRUCTION].search(text) and _TELL.search(text)
                                              and oi.parse_instruction(text) is None)


def parse(reply: Any) -> tuple:
    """(intent, args) from a model reply, or (None, {})."""
    if not isinstance(reply, dict):
        return None, {}
    for call in reply.get("tool_calls") or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict):
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = None
        if isinstance(args, dict) and args.get("intent") in INTENTS:
            return args["intent"], args
        if fn.get("name") in INTENTS:          # a model that called the intent as a tool
            return fn["name"], args if isinstance(args, dict) else {}
    content = reply.get("content")
    if isinstance(content, str):
        m = re.search(r"\b(" + "|".join(INTENTS) + r")\b", content)
        if m:
            return m.group(1), {}
    return None, {}
