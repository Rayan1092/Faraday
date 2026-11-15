"""Owner instructions: standing requests for what Faraday tells visitors.

    "Hey Faraday, if a delivery comes, ask them to leave it by the side door."

All plain code, no AI:
- parse_instruction() finds "if/when <who> comes, ask/tell them (to) <request>" and
  "ask/tell <who> to <request>". <who> is a delivery, a visitor or anyone.
- Only the owner can add them: the pipeline accepts them at Home only, because
  anything said while armed could be a visitor.
- They only shape speech. A request that mentions lights, modes, the alarm, locks,
  doors opening or codes is refused. The text is only ever used as words to say:
  in the visitor prompt, and as the delivery / greeting line.
- One instruction per audience (a new delivery instruction replaces the old one).
- Kept in logs/instructions.json so they survive a restart; listed and removed on
  the dashboard, or by voice ("forget the delivery instruction").
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from shared import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[1]

Instruction = Dict[str, Any]

DELIVERY, VISITOR, ANYONE = "delivery", "visitor", "anyone"
AUDIENCES = (DELIVERY, VISITOR, ANYONE)
NAMES = {DELIVERY: "Delivery", VISITOR: "Visitor", ANYONE: "Anyone"}
IF_WHO = {DELIVERY: "a delivery comes", VISITOR: "a visitor comes", ANYONE: "someone comes"}
MAX_REQUEST = 120

_WHO = {
    # longest first: "delivery drivers" must not stop at "delivery"
    DELIVERY: r"(?:a |the |any )?(?:delivery (?:people|persons?|drivers?|guys?|men|man|woman|women)"
              r"|deliveries|delivery|packages?|parcels?|couriers?|amazon|ups|fedex|dhl|usps"
              r"|mail ?(?:man|men|carriers?)|postman|postmen|postie|drivers?)",
    VISITOR: r"(?:a |the |any )?(?:visitor|guest|friend|neighbou?r)s?",
    ANYONE: r"(?:someone|somebody|anyone|anybody|people|a person|whoever)",
}
_ARRIVE = (r"(?:comes?|came|arrives?|shows? up|turns? up|rings?|knocks?|visits?|drops? (?:by|off)"
           r"|is (?:here|at the door)|stops? by)")
_WAKE = r"^(?:(?:hey|ok|okay|hi)\s+)?(?:faraday[\s,.!:]*)?"
# "I want you to tell him...", "can you ask them...", "please tell...": a lead-in before the verb.
_LEAD = (r"(?P<lead>(?:i\s+(?:want|need|would\s+like)\s+you\s+to|i'?d\s+like\s+you\s+to"
         r"|(?:can|could|would|will)\s+you(?:\s+please)?|make\s+sure\s+(?:you|to))\s+)?")
_ASK = _LEAD + r"(?:please\s+)?(?P<verb>ask|tell|let(?=\s+\w+\s+know\b))\s+"


def _who_group() -> str:
    return "|".join(f"(?P<{k}>{v})" for k, v in _WHO.items())


# "if a delivery comes, ask them to leave it by the side door"
_IF_FORM = re.compile(
    _WAKE + r"(?:if|when|whenever)\s+(?:" + _who_group() + r")\b(?:\s+\w+){0,3}?\s+" + _ARRIVE +
    r"\b[^,;]*?[,;]?\s*(?:then\s+)?" + _ASK +
    r"(?:them|him|her|the\s+\w+)\s+(?:know\s+)?(?P<to>to\s+|that\s+)?(?P<req>.+)$", re.I)

# "if a delivery comes, leave it by the side door": the request said straight to them
_IF_DO_FORM = re.compile(
    _WAKE + r"(?:if|when|whenever)\s+(?:" + _who_group().replace("?P<", "?P<i_") + r")\b(?:\s+\w+){0,3}?\s+"
    + _ARRIVE + r"\b[^,;]*?[,;]?\s*(?:then\s+)?(?:(?:they|he|she)\s+(?:should|can|must)\s+|have\s+them\s+)?"
    r"(?P<req>(?:please\s+)?(?:leave|put|drop|place|wait|knock|ring|come|go|call|text|use|try)\b.+)$", re.I)
# A request that stops mid-sentence ("ask them to", "tell visitors that") is not one.
_CUT = re.compile(r"(?:^|\s)(?:to|that|the|a|an|and|or|but|at|for|with|of|my|your|their|"
                  r"is|are|if|when|so|then|please)$", re.I)

# "tell delivery drivers to leave packages by the side door"
_DIRECT_FORM = re.compile(
    _WAKE + _ASK + r"(?:" + _who_group().replace("?P<", "?P<d_") + r")\b(?:\s+who\s+\w+)?\s+"
    r"(?P<to>to\s+|that\s+)?(?P<req>.+)$", re.I)

# Never: instructions only shape speech.
_FORBIDDEN = re.compile(
    r"\b(?:disarm\w*|arm|alarm|siren|unlock\w*|lock|locks|open\s+the\s+(?:door|gate|garage)"
    r"|let\s+\w+\s+in(?:side|to)?|come\s+(?:on\s+)?in(?:side|to)?|inside|indoors|open\s+(?:up|it|the\s+\w+)"
    r"|keys?|code|passphrase|password|pin"
    r"|lights?|lamps?|outlets?|switch\w*|turn\s+(?:on|off)|power|mode|security|camera|wi-?fi)\b", re.I)

_FORGET = re.compile(r"\b(?:forget|cancel|delete|remove|clear|drop)\b.*\binstructions?\b", re.I)


def parse_instruction(text: str) -> Optional[Dict[str, str]]:
    """{"audience", "verb", "form", "request"} if this is an owner instruction, else None.
    form is "to" ("ask them to leave it...") or "say" ("tell them I'm in the garden")."""
    text = text.strip().rstrip(".!?")
    for pattern, prefix in ((_IF_FORM, ""), (_IF_DO_FORM, "i_"), (_DIRECT_FORM, "d_")):
        m = pattern.match(text)
        if not m:
            continue
        audience = next(a for a in AUDIENCES if m.group(prefix + a))
        req = m.group("req").strip().rstrip(".!,?")
        req = re.sub(r"^please\s+", "", req, flags=re.I)
        groups = m.groupdict()
        verb = (groups.get("verb") or "ask").lower()
        form = "to" if (groups.get("to") or "").lower().startswith("to") or verb == "ask" else "say"
        if not 2 < len(req) <= MAX_REQUEST or _CUT.search(req) or _NOT_A_REQUEST.match(req):
            return None
        if groups.get("lead") and not groups.get("to"):
            # "Can you tell a delivery came?" is a question, not an instruction (review):
            # after a lead-in, the request must start with "to" or "that".
            return None
        return {"audience": audience, "verb": verb, "form": form, "request": req}
    return None


# A request that starts with what the visitor should do: "leave it by the side door".
_DO = re.compile(r"^(?:leave|put|drop|place|wait|knock|ring|come|go|call|text|use|try|bring|take|sign|"
                 r"keep|walk|hand|give|check|send|stay)\b", re.I)
# Things the owner says about themselves: "I'm at the gym", "the owner is out".
_SAY = re.compile(r"^(?:i|i'm|i'll|i've|i'd|we|we're|we'll|the owner|my|our|nobody|no one|it's)\b", re.I)
# Words that aren't content: everything else in a request must come from the owner.
_PLAIN = {"the", "a", "an", "to", "of", "at", "by", "on", "it", "its", "and", "or", "please", "them",
          "him", "her", "they", "their", "your", "you", "is", "be", "owner"}
# A question or a statement, not a request: "came", "is at the door", "what he wants".
_NOT_A_REQUEST = re.compile(r"^(?:is|are|was|were|came|comes|has|have|had|what|who|whom|whose|whether|"
                            r"if|how|why|when|where|did|does)\b", re.I)
# The 2B echoing the asking part: "tell him to ...", "let him know ...", "have them ...".
_ASKING = re.compile(r"^(?:(?:i\s+want\s+you\s+to|can\s+you|could\s+you|please)\s+)?"
                     r"(?:tell|ask|let|have|get)\s+(?:them|him|her|the\s+\w+|visitors?|everyone|anyone|"
                     r"couriers?|drivers?)\s+(?:know\s+)?(?:to\s+|that\s+)?", re.I)
_TO_OWNER = re.compile(r"^(?:let|tell|ask|remind|show|send)\s+me\b", re.I)


def from_model(args: Any, said: str) -> Optional[Dict[str, str]]:
    """An instruction the 2B pulled out of natural speech (owner, Mac test: "If a
    delivery comes, I want you to tell him to leave the package by the side door" got
    "Sorry, I missed the end of that"). Code validates it: a known audience backed by
    the words, a complete request of 3 to 120 characters, and nearly every word of the
    request taken from what the owner said, so the model can't invent one. The store
    refuses anything beyond words (is_forbidden). None if it doesn't hold."""
    if not isinstance(args, dict):
        return None
    audience = str(args.get("audience", "")).strip().lower()
    req = " ".join(str(args.get("request", "")).split()).strip().rstrip(".!,?")
    if _TO_OWNER.match(req):
        return None                               # "let me know": meant for the owner, not a visitor
    req = _ASKING.sub("", req)
    req = re.sub(r"^(?:please\s+|to\s+|that\s+)+", "", req, flags=re.I)
    if (audience not in AUDIENCES or not 2 < len(req) <= MAX_REQUEST or _CUT.search(req)
            or _NOT_A_REQUEST.match(req) or re.search(r"\b(?:if|when|whenever)\b", req, re.I)):
        return None
    if not (_DO.match(req) or _SAY.match(req)):
        return None                               # neither a request nor the owner's own news
    backed = [a for a in AUDIENCES if re.search(r"\b(?:" + _WHO[a] + r")\b", said, re.I)]
    if not backed:
        return None                               # nobody named: not an instruction for visitors
    if audience not in backed:
        audience = backed[0]                      # the owner's words decide who it's for
    heard = {w[:4] for w in re.findall(r"[a-z0-9']+", said.lower())}
    words = [w for w in re.findall(r"[a-z0-9']+", req.lower()) if w not in _PLAIN]
    if not words or any(w[:4] not in heard for w in words):
        return None                               # every content word from the owner (review)
    form = "to" if _DO.match(req) else "say"
    return {"audience": audience, "verb": "ask" if form == "to" else "tell", "form": form, "request": req}


def names_audience(text: str) -> bool:
    """Does the owner name who it's for (a delivery, a visitor, anyone)?"""
    return any(re.search(r"\b(?:" + _WHO[a] + r")\b", text or "", re.I) for a in AUDIENCES)


def is_forbidden(parsed: Dict[str, str]) -> bool:
    """True if the request tries to do more than say something."""
    return bool(_FORBIDDEN.search(parsed["request"]))


def forget_request(text: str) -> Optional[str]:
    """'delivery', 'visitor', 'anyone' or 'all' for "forget the delivery instruction"."""
    if not _FORGET.search(text):
        return None
    for audience in AUDIENCES:
        if re.search(_WHO[audience], text, re.I):
            return audience
    return "all"


# ---- turning an instruction into words ---------------------------------------------

_SWAPS = [(r"\bI'm\b", "the owner is"), (r"\bI am\b", "the owner is"), (r"\bI'll\b", "the owner will"),
          (r"\bI will\b", "the owner will"), (r"\bI've\b", "the owner has"), (r"\bI'd\b", "the owner would"),
          (r"\bmyself\b", "the owner"), (r"\bmine\b", "the owner's"), (r"\bmy\b", "the owner's"),
          (r"\bme\b", "the owner"), (r"\bI\b", "the owner")]


def _third_person(text: str) -> str:
    for pattern, repl in _SWAPS:
        text = re.sub(pattern, repl, text, flags=re.I)
    return text


def spoken_line(ins: Instruction) -> str:
    """What Faraday says to the visitor: "Please leave it by the side door." /
    "The owner is in the back garden." (owner's "I/my" become "the owner")."""
    req = _third_person(str(ins["request"]))
    if ins.get("form") == "to":
        return f"Please {req}."
    return req[0].upper() + req[1:] + "."


def confirmation(ins: Instruction) -> str:
    """What Faraday says to the owner after storing it."""
    to = "to " if ins.get("form") == "to" else ""
    verb = "ask" if ins.get("form") == "to" and ins.get("verb") == "ask" else "tell"
    return f"Understood. If {IF_WHO[ins['audience']]}, I'll {verb} them {to}{ins['request']}."


def owner_words(ins: Instruction) -> str:
    """The instruction in the owner's words: "ask them to leave it by the side door"."""
    to = "to " if ins.get("form") == "to" else ""
    verb = {"let": "let them know"}.get(ins.get("verb", "tell"), f"{ins.get('verb', 'tell')} them")
    return f"{verb} {to}{ins['request']}"


def prompt_line(ins: Instruction) -> str:
    """One short line for the visitor prompt."""
    who = {DELIVERY: "a delivery", VISITOR: "a visitor", ANYONE: "anyone"}[ins["audience"]]
    return f"For {who}: {owner_words(ins)}"


def key_words(ins: Instruction) -> List[str]:
    """Content words that show the line was passed on ("side", "door")."""
    stop = {"the", "a", "an", "it", "to", "by", "at", "on", "in", "of", "and", "or", "for", "with",
            "them", "please", "leave", "put", "drop", "is", "are", "be", "i", "i'm", "my", "me",
            "you", "your", "that", "this", "there", "here", "will", "can", "just"}
    return [w for w in re.findall(r"[a-z0-9']+", str(ins["request"]).lower()) if w not in stop]


def mentions(text: str, ins: Instruction) -> bool:
    """Did this spoken line pass the instruction on (most of its key words)?"""
    words = key_words(ins)
    if not words:
        return True
    said = set(re.findall(r"[a-z0-9']+", text.lower()))
    hits = sum(w in said for w in words)
    # Short requests need every key word ("side door" is not "front door");
    # longer ones two thirds, so a rephrased line still counts.
    return hits == len(words) if len(words) <= 3 else hits * 3 >= len(words) * 2


# ---- store ---------------------------------------------------------------------------

class InstructionStore:
    def __init__(self, path: Union[str, Path]):
        path = Path(path)
        self.path = path if path.is_absolute() else REPO_ROOT / path
        self._lock = threading.Lock()
        self._items: List[Instruction] = []
        self.skipped: List[str] = []
        self._load()

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "InstructionStore":
        logs_dir = settings.get("paths", {}).get("logs", "logs")
        return cls(Path(logs_dir) / "instructions.json")

    def add(self, parsed: Dict[str, str], said: str = "", caused_by: Optional[str] = None
            ) -> Optional[Instruction]:
        """Store it, replacing an older one for the same audience. None if refused."""
        if parsed.get("audience") not in AUDIENCES or is_forbidden(parsed):
            return None
        ins = {"id": new_id("ins"), "audience": parsed["audience"], "verb": parsed.get("verb", "tell"),
               "form": parsed.get("form", "say"), "request": str(parsed["request"])[:MAX_REQUEST],
               "said": said.strip()[:240], "created": now_iso(), "caused_by": caused_by}
        with self._lock:
            self._items = [i for i in self._items if i["audience"] != ins["audience"]] + [ins]
            self._save()
        return dict(ins)

    def remove(self, ins_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i["id"] != ins_id]
            if len(self._items) != before:
                self._save()
                return True
        return False

    def forget(self, audience: str) -> int:
        """Remove by audience ('all' for every one). Returns how many went."""
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if audience != "all" and i["audience"] != audience]
            gone = before - len(self._items)
            if gone:
                self._save()
        return gone

    def all(self) -> List[Instruction]:
        with self._lock:
            return json.loads(json.dumps(self._items))

    def get(self, audience: str) -> Optional[Instruction]:
        return next((i for i in self.all() if i["audience"] == audience), None)

    def relevant(self, label: str = "") -> List[Instruction]:
        """The ones for this threat label: delivery -> delivery + anyone; visitor (or a
        doorbell with no label) -> visitor + anyone; nothing for suspicious / nobody."""
        if label in ("suspicious", "nobody"):
            return []
        want = {DELIVERY: (DELIVERY, ANYONE)}.get(label, (VISITOR, ANYONE))
        return [i for i in self.all() if i["audience"] in want]

    # ---- file --------------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.skipped.append(f"could not read {self.path.name}: {e}")
            return
        for ins in data.get("instructions", []) if isinstance(data, dict) else []:
            ok = (isinstance(ins, dict) and isinstance(ins.get("id"), str)
                  and ins.get("audience") in AUDIENCES and isinstance(ins.get("request"), str)
                  and 0 < len(ins["request"]) <= MAX_REQUEST and not is_forbidden(ins))
            if not ok:
                self.skipped.append(f"skipped instruction {ins!r:.80}")
                continue
            self._items.append(ins)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"instructions": self._items}, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.path)
