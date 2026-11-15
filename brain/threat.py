"""Threat assessment: what does the scene at the door look like?

After every photo_described, the scene is classified as delivery, visitor,
suspicious or nobody, with a one-line reason, and sent as a threat_assessed
event (FORMATS.md).

1. Bonsai (the fast model, see llm fast_model) answers through one forced-choice
   tool, `classify(category, reason)`. Inputs: the photo description, the mode,
   the time of day, why the photo was taken, recent doorbell/motion, and the voice
   count from diarization when integration provides it.
2. Plain code validates the answer:
   - the category must be one of the four,
   - strong warning signs in the description (a mask, a crowbar, trying the
     handle...) make it suspicious whatever Bonsai said: code may escalate,
   - "nobody" while the description clearly mentions a person is not accepted.
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

from .core import _clean_line
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
REASON_MAX = 160
SYSTEM_PROMPT = (
    "You judge one photo of a home's front door for a home guard. Call classify once. "
    "delivery: someone dropping off a package. visitor: someone who rang to talk or visit. "
    "suspicious: someone hiding, forcing entry, covering their face, or prowling without "
    "ringing while the owner is away or asleep. nobody: no person in view."
)
TOOL = {"type": "function", "function": {
    "name": "classify",
    "description": "The scene at the door, with one short reason.",
    "parameters": {"type": "object", "properties": {
        "category": {"type": "string", "enum": sorted(C.ALL)},
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

    @property
    def night(self) -> bool:
        hour = (self.when or datetime.now()).hour
        return hour >= 21 or hour < 6


def rules(ctx: Context) -> Tuple[str, str]:
    """The plain-code answer. Also the fallback when Bonsai can't be used."""
    text = ctx.description or ""
    if STRONG.search(text):
        return C.SUSPICIOUS, "Warning signs in the photo: " + _first(STRONG, text) + "."
    if NOBODY.search(text) or not PERSON.search(text):
        return C.NOBODY, "No one is visible at the door."
    if ctx.cause == EventType.MOTION and ctx.mode in Mode.ARMED and not ctx.doorbells:
        # Owner 2026-11-15: stayed at the door without ringing = suspicious, box or not.
        return C.SUSPICIOUS, "Stayed at the door without ringing while the house is armed."
    if PACKAGE.search(text):
        return C.DELIVERY, "Carrying a package" + (" and rang the doorbell." if ctx.doorbells else ".")
    return C.VISITOR, "Someone at the door" + (" who rang the doorbell." if ctx.doorbells else ".")


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
    lines.append(f"Camera sees: {ctx.description}")
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
    if STRONG.search(ctx.description or "") and category != C.SUSPICIOUS:
        return C.SUSPICIOUS, rule_reason, f"code escalated {category} to suspicious"
    if category == C.NOBODY and PERSON.search(ctx.description or "") and not NOBODY.search(ctx.description or ""):
        return rule_cat, rule_reason, "code rejected nobody: a person is described"
    if category == C.SUSPICIOUS and ctx.doorbells and rule_cat in (C.VISITOR, C.DELIVERY):
        # Owner 2026-11-15: walked up and rang, no warning signs = a visitor or a delivery.
        return rule_cat, rule_reason, "code: rang the doorbell with no warning signs"
    clean = _clean_line(reason) or rule_reason
    if len(clean) > REASON_MAX:
        clean = clean[:REASON_MAX - 3].rsplit(" ", 1)[0] + "..."
    return category, clean, ""


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
        if self.client is None:
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
    for e in entries:
        t = _when(e.get("timestamp", ""))
        if when and t and 0 <= (when - t).total_seconds() <= window_s:
            if e.get("type") == EventType.DOORBELL and (since is None or t >= since):
                doorbells += 1
            motions += e.get("type") == EventType.MOTION
    return Context(description=str(photo.data.get("description", "")), mode=mode,
                   cause=str(origin.get("type", "")),
                   reason=str((shot.get("args") or {}).get("reason", "")),
                   when=when, doorbells=doorbells, motions=motions, voices=voices)


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
