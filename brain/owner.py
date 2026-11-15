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
3. Anything else is "unclear": "Sorry, I didn't catch that." A request that was
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
INTENTS = (ARM_AWAY, ARM_NIGHT, PASSPHRASE, SUMMARY, LOOK, ADD_RULE, ADD_INSTRUCTION, LIGHT, UNCLEAR)
# What may run while the house is armed (nothing that changes anything).
ARMED_OK = {Mode.NIGHT: {LOOK}, Mode.AWAY: set()}

SYSTEM_PROMPT = (
    "You sort one request the owner said to their home guard. Call route once. "
    "arm_away: leaving the house. arm_night: going to bed. passphrase: wants to disarm. "
    "summary: what happened, visitors, messages. look: who or what is at the door. "
    "add_rule: 'when X happens, do Y' with lights or photos. add_instruction: words to pass "
    "on to a visitor or delivery. light: turn a light on or off. unclear: anything else, "
    "or a request that stops before the end.")
TOOL = {"type": "function", "function": {
    "name": "route", "description": "The owner's request.",
    "parameters": {"type": "object", "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "outlet": {"type": "string", "enum": sorted(Outlet.ALL)},
        "on": {"type": "boolean"}}, "required": ["intent"]}}}

# Words that back each intent. Weak evidence: enough to accept the model's pick.
_WEAK = {
    ARM_AWAY: re.compile(r"\b(heading out|leaving|leave|going out|off to|i'?m off|out for|bye|"
                         r"away|arm)\b", re.I),
    ARM_NIGHT: re.compile(r"\b(good ?night|night|bed|bedtime|sleep|sleeping)\b", re.I),
    PASSPHRASE: re.compile(r"\b(disarm|pass ?phrase|password|unlock|turn off the alarm|"
                           r"stop the alarm|switch off the alarm)\b", re.I),
    SUMMARY: re.compile(r"\b(happen\w*|summar\w*|recap|visitors?|messages?|who came|"
                        r"anyone (come|came|stop\w*|ring|rang)|news|log|while i was)\b", re.I),
    LOOK: re.compile(r"\b(door|outside|porch|camera|see|look)\b", re.I),
    ADD_RULE: re.compile(r"\b(when(ever)?|every time|each time|from now on)\b", re.I),
    ADD_INSTRUCTION: re.compile(r"\b(if|when(ever)?)\b.*\b(deliver\w*|courier|parcel|package|visitor|"
                                r"someone|anyone|somebody|guest|friend)\b|\b(tell|ask|let)\b.*\b"
                                r"(them|visitors?|deliver\w*|courier)\b", re.I),
    LIGHT: re.compile(r"\b(lights?|lamp|porch|outlet)\b", re.I),
}
_RULE_EVENT = re.compile(r"\b(motion|movement|moves?|door ?bell|rings?|knocks?)\b", re.I)
_ON_OFF = re.compile(r"\b(on|off)\b", re.I)
# A request that stops mid-sentence: "ask them to", "if a delivery comes,".
_DANGLING = re.compile(r"(\b(to|the|a|an|and|or|but|if|when|whenever|that|them|by|at|for|with|of|"
                       r"my|your|please|ask|tell|say|is|are|so|then|it|leave|put|in|on)|,)\s*$", re.I)
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
    instruction: Optional[Dict[str, str]] = None


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


def light_slots(text: str) -> tuple:
    """(outlet, on) from the words, either may be None."""
    m = fb._LIGHT.search(text)
    phrase = (m.group(0) if m else text).lower()
    outlet = None
    if re.search(r"\b(living room|lamp)\b", phrase):
        outlet = Outlet.LIVING_ROOM_LAMP
    elif re.search(r"\bspare\b", phrase):
        outlet = Outlet.SPARE
    elif re.search(r"\b(porch|lights?)\b", phrase):
        outlet = Outlet.PORCH_LIGHT
    state = _ON_OFF.findall(m.group(0) if m else text)
    on = None if not state else state[-1].lower() == "on"
    return outlet, on


def strong(text: str) -> Optional[str]:
    """The intent the words make plain, or None. Order matters: a rule or an
    instruction mentions lights and doors too."""
    if oi.parse_instruction(text) is not None or oi.forget_request(text) is not None:
        return ADD_INSTRUCTION
    if fb.parse_rule(text) is not None:
        return ADD_RULE
    if look.is_look_request(text):
        return LOOK
    if _WEAK[PASSPHRASE].search(text):
        return PASSPHRASE
    if fb._LIGHT.search(text):
        return LIGHT
    arm = fb.arm_intent(text)
    if arm is not None:
        return ARM_AWAY if arm == Mode.AWAY else ARM_NIGHT
    if fb.is_summary_request(text):
        return SUMMARY
    return None


def backed(intent: str, text: str) -> bool:
    """Is there anything in the words for this intent?"""
    if intent == ADD_RULE:
        return bool(_WEAK[ADD_RULE].search(text) and _RULE_EVENT.search(text))
    rx = _WEAK.get(intent)
    return bool(rx and rx.search(text))


class OwnerRouter:
    def __init__(self, client: Optional[Any] = None, model: Optional[str] = None,
                 log: Callable[[str], None] = print):
        self.client, self.model, self.log = client, model, log

    def route(self, text: str) -> Route:
        text = str(text or "").strip()
        clear = strong(text)
        model_intent, args, elapsed, via = None, {}, None, "rules"
        if self.client is not None and text:
            started = time.perf_counter()
            try:
                reply = self.client.chat([{"role": "system", "content": SYSTEM_PROMPT},
                                          {"role": "user", "content": f'Owner said: "{text}"'}], [TOOL])
                model_intent, args = parse(reply)
            except Exception as e:
                self.log(f"[owner] Bonsai failed: {e}")
            elapsed = time.perf_counter() - started
        intent, note = self._check(text, clear, model_intent)
        if model_intent is not None:
            via = "bonsai" if intent == model_intent else "code"
        r = Route(intent, via, model_intent, elapsed_s=elapsed, note=note)
        self._fill(r, text, args)
        self.log(f"[owner] {text!r} -> {r.intent}" + (f" (2B said {model_intent})" if model_intent
                                                      and model_intent != r.intent else "")
                 + (f", {elapsed:.2f}s" if elapsed is not None else "") + (f": {r.note}" if r.note else ""))
        return r

    @staticmethod
    def _check(text: str, clear: Optional[str], model: Optional[str]) -> tuple:
        if model == UNCLEAR and clear is None:
            return UNCLEAR, ""
        if clear is not None and model != clear:
            return clear, (f"code: the words say {clear}" if model else "")
        if model is not None and model != UNCLEAR and backed(model, text):
            return model, ""
        if model is not None and model != UNCLEAR:
            return UNCLEAR, f"code rejected {model}: nothing in the words for it"
        return UNCLEAR, ""

    @staticmethod
    def _fill(r: Route, text: str, args: Dict[str, Any]) -> None:
        """Slots for the intent; an intent that can't be completed becomes unclear."""
        if r.intent == LIGHT:
            outlet, on = light_slots(text)
            r.outlet = outlet or (args.get("outlet") if args.get("outlet") in Outlet.ALL else None)
            r.on = on
            if r.outlet is None or r.on is None:
                r.intent, r.note = UNCLEAR, "light: which one, or on or off, wasn't said"
        elif r.intent == ADD_INSTRUCTION:
            r.instruction = oi.parse_instruction(text)
            if r.instruction is None and oi.forget_request(text) is None:
                r.intent, r.cut_off, r.note = UNCLEAR, True, "instruction incomplete"
        elif r.intent == ADD_RULE:
            r.rule = fb.parse_rule(text)       # None: the pipeline asks Bonsai to write it
        if r.intent == UNCLEAR and not r.cut_off:
            r.cut_off = cut_off(text) or bool(_WEAK[ADD_INSTRUCTION].search(text)
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
