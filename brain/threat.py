"""Threat assessment: what does the scene at the door look like?

After every photo_described, the scene is classified as delivery, visitor,
suspicious, nobody or unknown, with a one-line reason, and sent as a threat_assessed
event (FORMATS.md). Honest and grounded (owner, 2026-11-15):

0. Plain code first, no model: zero people from NV's object detector is always
   "nobody". If vision failed, the label comes from the detector's facts plus the
   context (rang or not, the mode); only if the detector failed too is it
   "unknown: couldn't analyse the photo", never a guess (owner, Mac test).
   A visitor who says package, parcel, delivery or courier is a delivery: the
   detector can't see boxes (owner, Mac test). It never lowers "suspicious".
1. Otherwise Bonsai (the fast model, see llm fast_model) answers through one
   forced-choice tool, `classify(category, reason)`. Inputs: the 9B's factual
   description, the detector's people count, objects and visible faces, the mode,
   the time of day, why the photo was taken, the doorbell/motion history and the
   voice count from diarization.
2. Plain code validates the answer:
   - the category must be one of the four (unknown is code's alone),
   - strong warning signs in the description (a mask, a covered face, a crowbar,
     trying the handle...) make it suspicious whatever Bonsai said: code may escalate,
   - "nobody" while a person is detected or described is not accepted,
   - someone who rang with no warning signs is a visitor or a delivery,
   - the reason must name what was seen ("1 person, backpack", "holding a box",
     "face covered") or the context ("stayed at the door without ringing while
     armed"); if Bonsai's doesn't, code writes it from the facts.
3. If Bonsai is off, fails, or gives nothing usable, simple rules decide.

The label shapes what Faraday says and may escalate the alert (in modes/). It can
never lower an alert, switch lights off, change the mode or disarm.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from shared import Event, EventType, Mode, Source, ThreatCategory, make_event

from . import look
from .core import _clean_line
from .fallback import _DELIVERY
from .guard import _json_objects

C = ThreatCategory

STRONG = re.compile(
    r"\b(mask(ed)?|balaclava|crowbar|weapon|knife|gun|pry(ing)?|forc(e|es|ing)( open)?|"
    r"break(ing)? (in|into|the)|smash\w*|climb\w*|"
    r"(trying|tries|tried|testing|rattl\w+) (the )?(door|handle|lock|window)|"
    r"(peer\w*|look\w*|peek\w*) (in|into|through) (the )?window|hid(e|es|ing)|crouch\w*|"
    r"face (is )?covered|covering (his|her|their) face)\b", re.I)
PERSON = re.compile(r"\b(person|people|man|men|woman|women|someone|somebody|figure|courier|"
                    r"driver|delivery|postman|kid|child|children|visitor|guy|lady|boy|girl|"
                    r"he|she|they)\b", re.I)
NOBODY = re.compile(r"\b(no one|nobody|empty|no person|no people|no visible person|"
                    r"nothing (is )?(there|visible))\b", re.I)
PACKAGE = re.compile(r"\b(package|parcel|box|boxes|delivery|courier|carton|envelope|mail|"
                     r"uniform|delivering)\b", re.I)
# A face the camera can't see. Not a warning sign by itself (so no early confrontation),
# but it isn't "a plain face" either: someone who rang with it is not simply a visitor.
FACE_HIDDEN = re.compile(
    r"\b(face (is |was )?(not visible|hidden|obscured|concealed|turned away|out of view|covered by \w+)|"
    r"hidden face|no face (is )?visible|(can'?t|cannot) see (the|his|her|their) face|"
    r"(scarf|hood|hat|cap) (pulled )?(down )?(over|covering) (the|his|her|their) face)\b", re.I)
_WEAPONS = {"knife", "scissors", "baseball bat"}
_STOP = {"this", "that", "with", "from", "they", "them", "there", "their", "look", "looks", "like",
         "likely", "appears", "seems", "door", "front", "porch", "camera", "photo", "image", "scene",
         "night", "visible", "detected", "someone", "something", "probably", "maybe", "about", "near",
         "into", "while", "just", "standing"}
_NEG_RING = re.compile(r"\b(without (ringing|a ring|the bell|the doorbell|a doorbell)|didn'?t ring|did not ring|"
                       r"no ring\w*|no doorbell|no bell|never rang|not ring\w*|didn'?t use the (door)?bell)\b", re.I)
_POS_RING = re.compile(r"\b(rang|rings?|ringing|doorbell|bell)\b", re.I)
REASON_MAX = 160
SYSTEM_PROMPT = (
    "You judge one photo of a home's front door for a home guard. Call classify once. "
    "delivery: anyone holding or carrying a box, package, parcel or envelope, or a courier, "
    "even if they rang the doorbell. visitor: someone who rang with nothing to deliver. "
    "suspicious: someone hiding, forcing entry, covering their face, or prowling without "
    "ringing while the owner is away or asleep. nobody: no person in view. "
    "Trust the object detector's people count; it can't see boxes, so read the description "
    "for them. The reason names what you see ('1 person, holding a box', 'face covered') "
    "or the context ('rang the doorbell')."
)
TOOL = {"type": "function", "function": {
    "name": "classify",
    "description": "The scene at the door, with one short reason.",
    "parameters": {"type": "object", "properties": {
        "category": {"type": "string", "enum": sorted(C.ALL - {C.UNKNOWN})},
        "reason": {"type": "string"}}, "required": ["category", "reason"]}}}


@dataclass
class Assessment:
    category: str
    reason: str
    via: str                      # "bonsai" or "rules"
    model: Optional[str] = None
    elapsed_s: Optional[float] = None
    note: str = ""                # what code changed, if anything (for logs and the bench)


@dataclass
class Context:
    description: str
    mode: str
    cause: str = ""               # event type that led to the photo ("doorbell", "motion", "")
    reason: str = ""              # the take_photo reason ("doorbell while away")
    when: Optional[datetime] = None
    doorbells: int = 0            # in the last few minutes
    motions: int = 0
    voices: Optional[int] = None  # from diarization, when NV adds it
    people: Optional[int] = None  # NV's object detector (None: no detector ran)
    faces: Optional[int] = None   # visible faces (only meaningful close up)
    detections: Optional[List[Dict[str, Any]]] = None   # [{"label", "count", "confidence"}]
    vision_ok: bool = True        # False: no description from the vision model
    said: str = ""                # a delivery word the visitor said this visit ("package")

    @property
    def night(self) -> bool:
        hour = (self.when or datetime.now()).hour
        return hour >= 21 or hour < 6


def people_seen(ctx: Context) -> Optional[int]:
    """The detector's people count; a visible face counts as a person too (the face
    detector saw someone the object detector missed). None: no detector ran."""
    if ctx.people is None:
        return None
    return max(ctx.people, ctx.faces or 0)


def _plural(label: str, n: int) -> str:
    if n == 1:
        return label
    if label.endswith(("s", "x", "ch", "sh")):
        return f"{n} {label}es"
    if label.endswith("fe"):
        return f"{n} {label[:-2]}ves"
    if label.endswith("y") and label[-2:-1] not in "aeiou":
        return f"{n} {label[:-1]}ies"
    return f"{n} {label}s"


def facts(ctx: Context) -> str:
    """What the detector saw, short: "1 person, backpack" ("" without a detector).
    Anything that could be a weapon comes first, so it's never cut off."""
    if ctx.people is None and not ctx.detections:
        return ""
    people = people_seen(ctx)
    if people is None:
        people = next((int(d.get("count", 0)) for d in ctx.detections or [] if d.get("label") == "person"), 0)
    parts = [f"{people} {'person' if people == 1 else 'people'}"] if people else []
    things = [d for d in ctx.detections or [] if str(d.get("label", "")) not in ("", "person")]
    things.sort(key=lambda d: str(d.get("label")) not in _WEAPONS)        # weapons first, stable
    for d in things[:3]:
        parts.append(_plural(str(d["label"]), int(d.get("count", 1) or 1)))
    return ", ".join(parts)


def grounded(reason: str, ctx: Context) -> bool:
    """Does this reason name what was seen (words from the description or the detector)
    or true context (rang / didn't ring)? A reason that claims a ring that didn't happen,
    or the other way round, is not grounded."""
    text = (reason or "").lower()
    if not text:
        return False
    neg = _NEG_RING.search(text)
    if neg:
        return not ctx.doorbells and ctx.cause == EventType.MOTION
    if _POS_RING.search(text):
        return bool(ctx.doorbells)
    if NOBODY.search(text):     # "No one visible": true only if the photo agrees
        return people_seen(ctx) == 0 or bool(NOBODY.search(ctx.description or ""))
    seen = set(re.findall(r"[a-z]+", (ctx.description or "").lower()))
    for d in ctx.detections or []:
        seen.update(re.findall(r"[a-z]+", str(d.get("label", "")).lower()))
    if people_seen(ctx):
        seen.update({"person", "people"})
    stems = {w[:5] for w in seen if len(w) >= 4 and w not in _STOP}
    words = {w[:5] for w in re.findall(r"[a-z]+", text) if len(w) >= 4 and w not in _STOP}
    return bool(words & stems)


def agrees(category: str, reason: str) -> bool:
    """Does the reason fit the label? A person label with "no one visible", or "nobody"
    with a person in the reason, contradicts itself (owner, Mac retest)."""
    text = reason or ""
    if category in (C.VISITOR, C.DELIVERY, C.SUSPICIOUS):
        return not NOBODY.search(text)
    if category == C.NOBODY:
        return bool(NOBODY.search(text)) or not PERSON.search(text)
    return True


_BOXY = re.compile(r"\b(package|parcel|box|boxes|carton|envelope)\b", re.I)
_COURIER = re.compile(r"\bcouriers?\b", re.I)


def delivery_word(text: str) -> str:
    """The delivery word a visitor said ("package", "courier", "FedEx"), or ""."""
    m = _DELIVERY.search(text or "")
    return m.group(0) if m else ""


def reason_for(ctx: Context, category: str, sign: str = "") -> str:
    """A reason made only from facts: the detector, the description's own words, the
    doorbell, the mode. Never a guess."""
    seen = facts(ctx)
    if category == C.NOBODY:
        parts = ["No person detected" if people_seen(ctx) == 0 else "No one visible"]
        objects = [p for p in seen.split(", ") if p and "person" not in p and "people" not in p]
        parts += [f"{', '.join(objects)} in view"] if objects else []
    elif category == C.UNKNOWN:
        parts = ["Couldn't analyse the photo"] + ([f"the detector saw {seen}"] if seen else [])
    else:
        parts = [seen] if seen else []
        text = ctx.description or ""
        if category == C.SUSPICIOUS:
            parts.append(sign or ("stayed at the door without ringing while armed"
                                  if ctx.cause == EventType.MOTION and not ctx.doorbells
                                  else "behaving suspiciously"))
        elif category == C.DELIVERY:
            box = _BOXY.search(text) if ctx.vision_ok else None
            if box:
                word = box.group(0).lower()
                parts.append(f"holding a {'box' if word == 'boxes' else word}")
            elif ctx.said:
                parts.append(f'said "{ctx.said.lower()}"')
            else:     # only the description's own words: never an invented package
                word = (_first(PACKAGE, text) or "delivery").lower()
                parts.append({"courier": "a courier", "uniform": "in a uniform", "mail": "carrying mail",
                              "delivery": "a delivery", "delivering": "delivering"}.get(word, word))
        if category in (C.DELIVERY, C.VISITOR):
            if not ctx.vision_ok and ctx.faces:
                parts.append("face visible")
            parts.append("rang the doorbell" if ctx.doorbells else "at the door")
    line = ", ".join(p for p in parts if p)
    return (line[0].upper() + line[1:] + ".") if line else "Someone at the door."


def rules(ctx: Context) -> Tuple[str, str]:
    """The plain-code answer. Also the fallback when Bonsai can't be used."""
    text = ctx.description or ""
    if people_seen(ctx) == 0:
        return C.NOBODY, reason_for(ctx, C.NOBODY)        # zero people detected: always nobody
    if not ctx.vision_ok:
        if people_seen(ctx) is None:
            return C.UNKNOWN, reason_for(ctx, C.UNKNOWN)  # vision and the detector both failed
        return _from_detector(ctx)
    if STRONG.search(text):
        return C.SUSPICIOUS, reason_for(ctx, C.SUSPICIOUS, _first(STRONG, text))
    if FACE_HIDDEN.search(text):
        return C.SUSPICIOUS, reason_for(ctx, C.SUSPICIOUS, _first(FACE_HIDDEN, text))
    if ctx.people is None and (NOBODY.search(text) or not PERSON.search(text)):
        return C.NOBODY, reason_for(ctx, C.NOBODY)        # what the visitor said never overrides it
    if ctx.cause == EventType.MOTION and ctx.mode in Mode.ARMED and not ctx.doorbells:
        # Owner 2026-11-15: stayed at the door without ringing = suspicious, box or not.
        return C.SUSPICIOUS, reason_for(ctx, C.SUSPICIOUS)
    if PACKAGE.search(text) or ctx.said:
        return C.DELIVERY, reason_for(ctx, C.DELIVERY)
    return C.VISITOR, reason_for(ctx, C.VISITOR)


def _from_detector(ctx: Context) -> Tuple[str, str]:
    """Vision failed but the detector saw someone (owner, Mac test): the label comes
    from its facts plus the context, never "unknown"."""
    weapon = next((str(d.get("label")) for d in ctx.detections or []
                   if str(d.get("label")) in _WEAPONS), "")
    if weapon:
        return C.SUSPICIOUS, reason_for(ctx, C.SUSPICIOUS, f"{weapon} in view")
    text = ctx.description or ""       # the detector's own sentence ("No face is visible: covered ...")
    for rx in (STRONG, FACE_HIDDEN):
        if rx.search(text):            # review: never less careful just because vision is down
            return C.SUSPICIOUS, reason_for(ctx, C.SUSPICIOUS, _first(rx, text))
    if ctx.cause == EventType.MOTION and ctx.mode in Mode.ARMED and not ctx.doorbells:
        return C.SUSPICIOUS, reason_for(ctx, C.SUSPICIOUS)   # stayed without ringing while armed
    if ctx.said:
        return C.DELIVERY, reason_for(ctx, C.DELIVERY)
    return C.VISITOR, reason_for(ctx, C.VISITOR)


def _first(rx: "re.Pattern[str]", text: str) -> str:
    m = rx.search(text)
    return m.group(0).lower() if m else ""


def prompt(ctx: Context) -> str:
    when = ctx.when or datetime.now()
    lines = [f"Mode: {ctx.mode}" + (" (armed)" if ctx.mode in Mode.ARMED else ""),
             f"Time: {when:%H:%M} ({'night' if ctx.night else 'day'})"]
    if ctx.reason:
        lines.append(f"Photo taken because: {ctx.reason}")
    lines.append(f"Doorbell in the last few minutes: {'yes' if ctx.doorbells else 'no'}; "
                 f"motion: {ctx.motions} time(s)")
    if ctx.voices is not None:
        lines.append(f"Voices heard: {ctx.voices}")
    if ctx.people is not None:
        seen = facts(ctx) or "no people, no objects"
        # Faces only when some were seen: zero can just mean too far away to tell.
        lines.append(f"Object detector: {seen}" + (f"; faces visible: {ctx.faces}" if ctx.faces else ""))
    lines.append(f"Camera sees: {ctx.description}")
    if ctx.said:
        lines.append(f'The visitor said: "{ctx.said}"')
    return "\n".join(lines)


def parse(reply: Any) -> Tuple[Optional[str], str]:
    """(category, reason) from a model reply, or (None, "") if nothing usable."""
    if not isinstance(reply, dict):
        return None, ""
    candidates: List[Dict[str, Any]] = []
    for call in reply.get("tool_calls") or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if isinstance(fn, dict):
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = None
            if isinstance(args, dict):
                candidates.append(args)
    content = reply.get("content")
    if isinstance(content, str):
        for obj in _json_objects(content):
            if isinstance(obj, dict):
                candidates.append(obj.get("arguments", obj) if isinstance(obj.get("arguments"), dict) else obj)
        if not candidates:  # a bare word answer like "delivery - holding a box"
            m = re.search(r"\b(delivery|visitor|suspicious|nobody)\b", content.lower())
            if m:
                candidates.append({"category": m.group(1), "reason": content})
    for c in candidates:
        cat = str(c.get("category", "")).strip().lower()
        if cat in C.ALL:
            return cat, str(c.get("reason", ""))
    return None, ""


def check(category: str, reason: str, ctx: Context) -> Tuple[str, str, str]:
    """Code's say on Bonsai's answer. Returns (category, reason, note)."""
    rule_cat, rule_reason = rules(ctx)
    if people_seen(ctx) == 0 or not ctx.vision_ok:
        return rule_cat, rule_reason, ("" if category == rule_cat else f"code: {rule_cat}, not a guess")
    if category == C.UNKNOWN:
        return rule_cat, rule_reason, "code: the photo was analysed, so not unknown"
    if rule_cat == C.SUSPICIOUS and category != C.SUSPICIOUS:
        # warning signs, a hidden face, or stayed without ringing while armed: code escalates
        return C.SUSPICIOUS, rule_reason, f"code escalated {category} to suspicious"
    if category == C.NOBODY and ((people_seen(ctx) or 0) > 0 or (
            PERSON.search(ctx.description or "") and not NOBODY.search(ctx.description or ""))):
        return rule_cat, rule_reason, "code rejected nobody: a person is detected or described"
    if category == C.SUSPICIOUS and ctx.doorbells and rule_cat in (C.VISITOR, C.DELIVERY):
        # Owner 2026-11-15: walked up and rang, plain face, no warning signs = visitor/delivery.
        return rule_cat, rule_reason, "code: rang the doorbell with no warning signs"
    if category == C.VISITOR and rule_cat == C.DELIVERY and (
            _BOXY.search(ctx.description or "") or ctx.said or _COURIER.search(ctx.description or "")):
        # Owner, Mac test: a box or a courier in the description, or the visitor said
        # "package": a delivery, even though they rang. (Not "uniform" or "mail" alone.)
        return C.DELIVERY, rule_reason, "code: a package in the description or in what they said"
    clean = _clean_line(reason) or ""
    note = ""
    if not grounded(clean, ctx):
        if category != rule_cat:
            # A pick with nothing seen behind it: the rules decide, from the facts.
            return rule_cat, rule_reason, f"code: {category} named nothing seen, so the rules decided"
        note = f"code wrote the reason (2B said: {clean[:60]})" if clean else "code wrote the reason"
        clean = rule_reason
    if not agrees(category, clean):
        # Owner, Mac retest: "visitor" with the reason "doorbell while away, no one visible".
        note = f"code wrote the reason: the 2B's contradicted {category} ({clean[:60]})"
        clean = reason_for(ctx, category)
    if len(clean) > REASON_MAX:
        clean = clean[:REASON_MAX - 3].rsplit(" ", 1)[0] + "..."
    return category, clean, note


class ThreatAssessor:
    def __init__(self, client: Optional[Any] = None, model: Optional[str] = None, log=print):
        self.client, self.model, self.log = client, model, log

    @classmethod
    def from_settings(cls, settings: Dict[str, Any], use_llm: bool = True, log=print) -> "ThreatAssessor":
        """Uses the profile's fast_model (the small Bonsai) if set, else its model."""
        from .bonsai import BonsaiClient
        from .settings import llm_config
        cfg = dict(llm_config(settings))
        cfg["model"] = cfg.get("fast_model") or cfg["model"]
        return cls(BonsaiClient.from_config(cfg, log) if use_llm else None, cfg["model"], log)

    def assess(self, ctx: Context) -> Assessment:
        if self.client is None or people_seen(ctx) == 0 or not ctx.vision_ok:
            # No model, nobody detected, or nothing to judge (vision failed): plain code.
            cat, why = rules(ctx)
            return Assessment(cat, why, "rules")
        started = time.perf_counter()
        try:
            reply = self.client.chat([{"role": "system", "content": SYSTEM_PROMPT},
                                      {"role": "user", "content": prompt(ctx)}], [TOOL])
        except Exception as e:
            elapsed = time.perf_counter() - started
            self.log(f"[threat] Bonsai failed after {elapsed:.2f}s: {e}")
            cat, why = rules(ctx)
            return Assessment(cat, why, "rules", elapsed_s=elapsed, note="bonsai failed")
        elapsed = time.perf_counter() - started
        category, reason = parse(reply)
        if category is None:
            self.log(f"[threat] unusable answer after {elapsed:.2f}s, rules decide")
            cat, why = rules(ctx)
            return Assessment(cat, why, "rules", elapsed_s=elapsed, note="unusable answer")
        category, reason, note = check(category, reason, ctx)
        self.log(f"[threat] Bonsai took {elapsed:.2f}s: {category}" + (f" ({note})" if note else ""))
        return Assessment(category, reason, "bonsai", self.model, elapsed, note)


def context_for(photo: Event, mode: str, recent: Iterable[Any], voices: Optional[int] = None,
                window_s: float = 180) -> Context:
    """Build the inputs from the photo_described event and the recent log."""
    entries = [e.to_dict() if hasattr(e, "to_dict") else e for e in recent]
    by_id = {e.get("id"): e for e in entries if isinstance(e, dict)}
    shot = by_id.get(photo.caused_by or "") or {}
    origin = by_id.get(shot.get("caused_by") or "") or {}
    when = _when(photo.timestamp)
    # A photo caused by motion only counts doorbells from that motion on: someone who
    # walked up and stayed isn't "a visitor who rang" because of an earlier visitor.
    since = _when(origin.get("timestamp", "")) if origin.get("type") == EventType.MOTION else None
    doorbells = motions = 0
    said = ""
    # The visit starts at the first doorbell in the window (a second press keeps what
    # was said after the first), else at the motion that led to the photo. No doorbell
    # and no motion (a Snapshot): what any visitor said earlier doesn't count.
    bells = [t for t in (_when(e.get("timestamp", "")) for e in entries
                         if e.get("type") == EventType.DOORBELL)
             if t and when and 0 <= (when - t).total_seconds() <= window_s]
    visit = min(bells) if bells else (
        _when(origin.get("timestamp", "")) if origin.get("type") == EventType.MOTION else None)
    for e in entries:
        t = _when(e.get("timestamp", ""))
        if when and t and 0 <= (when - t).total_seconds() <= window_s:
            if e.get("type") == EventType.DOORBELL and (since is None or t >= since):
                doorbells += 1
            motions += e.get("type") == EventType.MOTION
            if (e.get("type") == EventType.SPEECH_HEARD and visit is not None and t >= visit
                    and _visitor_speech(e, mode)):
                said = delivery_word(str((e.get("data") or {}).get("text", ""))) or said
    data = photo.data or {}
    by = data.get("described_by")
    # Vision worked if the vision model wrote the description; "detector" means it
    # failed and the object detector wrote it; no described_by: NV's "camera" source
    # means nothing described it (the sim and older events: as before).
    vision_ok = by == "bonsai" or (by is None and not look.photo_failed(photo))
    people = data.get("people")
    faces = data.get("faces")
    dets = data.get("detections")
    return Context(description=str(data.get("description", "")), mode=mode,
                   cause=str(origin.get("type", "")),
                   reason=str((shot.get("args") or {}).get("reason", "")),
                   when=when, doorbells=doorbells, motions=motions, voices=voices,
                   people=people if isinstance(people, int) else None,
                   faces=faces if isinstance(faces, int) else None,
                   detections=dets if isinstance(dets, list) else None,
                   vision_ok=vision_ok, said=said)


def _visitor_speech(entry: Dict[str, Any], mode: str) -> bool:
    """A visitor said it: from a visitor window, or untagged while armed."""
    channel = (entry.get("data") or {}).get("channel")
    return channel == "visitor" or (channel is None and mode in Mode.ARMED)


def latest_label(entries: Iterable[Any], since: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The newest threat_assessed entry in the log (at or after `since`), or None."""
    found = None
    for e in entries:
        e = e.to_dict() if hasattr(e, "to_dict") else e
        if isinstance(e, dict) and e.get("type") == EventType.THREAT_ASSESSED:
            t = _when(e.get("timestamp", ""))
            if since is None or (t is not None and t >= since):
                found = e
    return found


def relabel_for_speech(speech: Event, mode: str, entries: Iterable[Any],
                       window_s: float = 300) -> Optional[Event]:
    """The visitor said a delivery word after this visit's photo was labelled: a new
    threat_assessed (delivery) for that photo, so the dashboard updates. None if
    nothing to change: no delivery word, not a visitor, no label for this visit yet
    (the photo's label then reads the speech itself), or already delivery or
    suspicious (a label never gets less careful)."""
    data = speech.data or {}
    word = delivery_word(str(data.get("text", "")))
    if not word or not _visitor_speech({"data": data}, mode):
        return None
    entries = [e.to_dict() if hasattr(e, "to_dict") else e for e in entries]
    now = _when(speech.timestamp)
    bells = [t for t in (_when(e.get("timestamp", "")) for e in entries
                         if isinstance(e, dict) and e.get("type") == EventType.DOORBELL) if t]
    visit = max(bells) if bells else None
    if visit is None or now is None or (now - visit).total_seconds() > window_s:
        return None
    label = latest_label(entries, since=visit)
    if label is None:
        return None
    info = label.get("data") or {}
    if info.get("category") in (C.DELIVERY, C.SUSPICIOUS):
        return None
    reason = f'Said "{word.lower()}". {info.get("reason", "")}'.strip()
    return make_event(EventType.THREAT_ASSESSED,
                      {"category": C.DELIVERY, "reason": reason[:200], "via": "rules", "model": None,
                       "voices": info.get("voices")},
                      Source.BRAIN, caused_by=label.get("caused_by"))


def _when(stamp: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(stamp))
    except ValueError:
        return None


def event_for(result: Assessment, photo: Event, voices: Optional[int] = None) -> Event:
    return make_event(EventType.THREAT_ASSESSED,
                      {"category": result.category, "reason": result.reason[:200],
                       "via": result.via, "model": result.model if result.via == "bonsai" else None,
                       "voices": voices},
                      Source.BRAIN, caused_by=photo.id)
