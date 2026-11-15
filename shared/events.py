"""Event definitions. Must match docs/FORMATS.md exactly.

An event is something that happened. Events flow hardware/sim -> integration
-> brain/modes/rules/log/dashboard. Every event is one JSON object; the log
is one event or action per line (logs/events.jsonl).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass, MISSING
from typing import Any, Dict, List, Optional

from .common import AlertLevel, Mode, Source, new_id, now_iso


class EventType:
    DOORBELL = "doorbell"
    MOTION = "motion"
    SPEECH_HEARD = "speech_heard"
    PHOTO_DESCRIBED = "photo_described"
    MODE_CHANGED = "mode_changed"
    ALERT_CHANGED = "alert_changed"
    THREAT_ASSESSED = "threat_assessed"
    RECORDING_SAVED = "recording_saved"
    ALL = frozenset({DOORBELL, MOTION, SPEECH_HEARD, PHOTO_DESCRIBED, MODE_CHANGED,
                     ALERT_CHANGED, THREAT_ASSESSED, RECORDING_SAVED})


# ---- per-type data ---------------------------------------------------------

@dataclass
class DoorbellData:
    key: str                  # "0x00DEC608"
    protocol: str = "Princeton"
    bits: int = 24
    packets: int = 1          # packets seen in this press
    duration_ms: int = 0      # first to last packet


@dataclass
class MotionData:
    key: str                  # "0x00C5AFAE"
    protocol: str = "Princeton"
    bits: int = 24
    packets: int = 1          # ~17 per trigger, merged into this one event
    duration_ms: int = 0


@dataclass
class SpeechHeardData:
    text: str                           # transcript. Passphrase is redacted before logging
    audio_path: Optional[str] = None    # "captures/audio/....wav", relative to repo root
    duration_s: Optional[float] = None
    voices: Optional[int] = None        # distinct voices (Argmax diarization), if it finished in time
    channel: Optional[str] = None       # SpeechChannel.*: "owner" (after "Hey Faraday") or "visitor"
    owner_voice: Optional[bool] = None  # owner requests: the enrolled owner's voice? (display only)


class SpeechChannel:
    """Who a spoken line was for (PLAN Q29). Typed text has no channel."""
    OWNER = "owner"       # after the wake phrase, or answering Faraday's question to the owner
    VISITOR = "visitor"   # a listening window: doorbell, Faraday's question to a visitor
    ALL = frozenset({OWNER, VISITOR})


@dataclass
class PhotoDescribedData:
    photo_path: str                     # "captures/photos/....jpg"
    description: str                    # one or two short sentences from Bonsai vision
    model: Optional[str] = None         # LM Studio model id that described it
    described_by: Optional[str] = None  # "bonsai" (vision LLM) or "detector" (object detector only)
    people: Optional[int] = None        # people the object detector counted
    faces: Optional[int] = None         # faces visible to the camera (face detector)
    detections: Optional[List[Dict[str, Any]]] = None   # [{"label", "count", "confidence"}]


DESCRIBED_BY = frozenset({"bonsai", "detector"})
DETECTION_KEYS = frozenset({"label", "count", "confidence"})


class ChangedBy:
    PASSPHRASE = "passphrase"  # the only way to reach HOME from an armed mode
    BRAIN = "brain"
    RULE = "rule"
    DASHBOARD = "dashboard"
    STARTUP = "startup"
    ALL = frozenset({PASSPHRASE, BRAIN, RULE, DASHBOARD, STARTUP})


@dataclass
class ModeChangedData:
    mode: str                 # Mode.*
    previous: str             # Mode.*
    changed_by: str           # ChangedBy.*
    reason: str = ""          # short human text for the dashboard


@dataclass
class AlertChangedData:
    level: str                # AlertLevel.*
    previous: str             # AlertLevel.*
    reason: str = ""          # short human text for the dashboard


# The only allowed (previous, level) steps. Going back to normal is emitted by
# modes/ only from the passphrase disarm; that is enforced in modes/, since the
# event itself does not carry the passphrase.
ALERT_TRANSITIONS = frozenset({
    (AlertLevel.NORMAL, AlertLevel.WARNING),   # first motion while armed
    (AlertLevel.WARNING, AlertLevel.ALARM),    # motion again within 60 s
    (AlertLevel.WARNING, AlertLevel.NORMAL),   # passphrase disarm
    (AlertLevel.ALARM, AlertLevel.NORMAL),     # passphrase disarm
})


class ThreatCategory:
    """What the camera scene looks like. It only shapes what Faraday says, and
    'suspicious' can escalate (never lower) the alert in modes/."""
    DELIVERY = "delivery"
    VISITOR = "visitor"
    SUSPICIOUS = "suspicious"
    NOBODY = "nobody"
    UNKNOWN = "unknown"        # vision and the detector both failed: never a guess (FORMATS.md)
    ALL = frozenset({DELIVERY, VISITOR, SUSPICIOUS, NOBODY, UNKNOWN})


THREAT_VIA = frozenset({"bonsai", "rules"})
THREAT_REASON_MAX = 200


@dataclass
class ThreatAssessedData:
    category: str                       # ThreatCategory.*
    reason: str = ""                    # one short line for the dashboard and log
    via: str = "rules"                  # "bonsai" or "rules" (the plain-code fallback)
    model: Optional[str] = None         # LM Studio model id when via is "bonsai"
    voices: Optional[int] = None        # voice count from diarization, if known


@dataclass
class RecordingSavedData:
    """About 10 s of camera frames saved when the alarm went off (PLAN Q33).
    Written straight to the log by integration/ (not run through the brain)."""
    path: str                           # "captures/alarm-20261115-013000/", repo-relative folder
    frames: int                         # JPEG frames saved (frame-0001.jpg, ...)
    duration_s: float                   # first to last frame
    video_path: Optional[str] = None    # "captures/alarm-.../clip.avi" (MJPG) if it could be written
    reason: str = ""                    # "alarm"


EVENT_DATA = {
    EventType.DOORBELL: DoorbellData,
    EventType.MOTION: MotionData,
    EventType.SPEECH_HEARD: SpeechHeardData,
    EventType.PHOTO_DESCRIBED: PhotoDescribedData,
    EventType.MODE_CHANGED: ModeChangedData,
    EventType.ALERT_CHANGED: AlertChangedData,
    EventType.THREAT_ASSESSED: ThreatAssessedData,
    EventType.RECORDING_SAVED: RecordingSavedData,
}


# ---- envelope --------------------------------------------------------------

@dataclass
class Event:
    type: str
    source: str
    data: Dict[str, Any]
    timestamp: str = field(default_factory=now_iso)
    id: str = field(default_factory=lambda: new_id("evt"))
    caused_by: Optional[str] = None   # id of the event/action that led to this one

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": "event", "id": self.id, "type": self.type,
                "timestamp": self.timestamp, "source": self.source,
                "caused_by": self.caused_by, "data": self.data}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Event":
        errors = validate_event(d)
        if errors:
            raise ValueError("bad event: " + "; ".join(errors))
        return cls(type=d["type"], source=d["source"], data=dict(d["data"]),
                   timestamp=d["timestamp"], id=d["id"], caused_by=d.get("caused_by"))

    @classmethod
    def from_json(cls, s: str) -> "Event":
        return cls.from_dict(json.loads(s))


def make_event(type: str, data: Any, source: str,
               caused_by: Optional[str] = None) -> Event:
    """Build and validate an event. `data` may be a *Data dataclass or a dict."""
    payload = asdict(data) if is_dataclass(data) else dict(data)
    event = Event(type=type, source=source, data=payload, caused_by=caused_by)
    errors = validate_event(event.to_dict())
    if errors:
        raise ValueError("bad event: " + "; ".join(errors))
    return event


# ---- validation ------------------------------------------------------------

def check_fields(cls: type, data: Any, where: str) -> List[str]:
    """Required keys present, no unknown keys. Shared with actions.py."""
    if not isinstance(data, dict):
        return [f"{where} must be an object"]
    names = {f.name for f in fields(cls)}
    required = {f.name for f in fields(cls)
                if f.default is MISSING and f.default_factory is MISSING}
    errors = [f"{where} missing '{k}'" for k in sorted(required - data.keys())]
    errors += [f"{where} has unknown key '{k}'" for k in sorted(data.keys() - names)]
    return errors


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _photo_errors(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if data.get("described_by") is not None and data["described_by"] not in DESCRIBED_BY:
        errors.append(f"data.described_by must be one of {sorted(DESCRIBED_BY)} or null")
    for key in ("people", "faces"):
        v = data.get(key)
        if v is not None and (not _whole(v) or v < 0):
            errors.append(f"data.{key} must be a whole number >= 0 or null")
    dets = data.get("detections")
    if dets is not None:
        if not isinstance(dets, list):
            return errors + ["data.detections must be a list or null"]
        for i, item in enumerate(dets):
            where = f"data.detections[{i}]"
            if not isinstance(item, dict) or set(item) != DETECTION_KEYS:
                errors.append(f"{where} must have exactly label, count, confidence")
                continue
            if not isinstance(item["label"], str) or not item["label"].strip():
                errors.append(f"{where}.label must be a non-empty string")
            if not _whole(item["count"]) or item["count"] < 1:
                errors.append(f"{where}.count must be a whole number >= 1")
            c = item["confidence"]
            if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1:
                errors.append(f"{where}.confidence must be a number from 0 to 1")
    return errors


def validate_event(d: Dict[str, Any]) -> List[str]:
    """Return a list of problems; empty list means the event is valid."""
    if not isinstance(d, dict):
        return ["event must be an object"]
    errors: List[str] = []
    if d.get("kind") != "event":
        errors.append("kind must be 'event'")
    for key in ("id", "type", "timestamp", "source", "data"):
        if key not in d:
            errors.append(f"missing '{key}'")
    if errors:
        return errors
    if d["type"] not in EventType.ALL:
        return [f"unknown event type '{d['type']}'"]
    if d["source"] not in Source.ALL:
        errors.append(f"unknown source '{d['source']}'")
    data = d["data"]
    errors += check_fields(EVENT_DATA[d["type"]], data, "data")
    if errors:
        return errors
    if d["type"] == EventType.MODE_CHANGED:
        for key in ("mode", "previous"):
            if data[key] not in Mode.ALL:
                errors.append(f"data.{key} '{data[key]}' is not a mode")
        if data["changed_by"] not in ChangedBy.ALL:
            errors.append(f"data.changed_by '{data['changed_by']}' unknown")
        if (data["mode"] == Mode.HOME and data["previous"] != Mode.HOME
                and data["changed_by"] not in (ChangedBy.PASSPHRASE, ChangedBy.STARTUP)):
            errors.append("only the passphrase can disarm (go to home)")
    if d["type"] == EventType.ALERT_CHANGED:
        if d["source"] != Source.MODES:
            errors.append("alert_changed must have source 'modes'")
        for key in ("level", "previous"):
            if data[key] not in AlertLevel.ALL:
                errors.append(f"data.{key} '{data[key]}' is not an alert level")
        if not errors and (data["previous"], data["level"]) not in ALERT_TRANSITIONS:
            errors.append(f"alert cannot go from '{data['previous']}' to '{data['level']}'")
    if d["type"] == EventType.THREAT_ASSESSED:
        if d["source"] != Source.BRAIN:
            errors.append("threat_assessed must have source 'brain'")
        if data["category"] not in ThreatCategory.ALL:
            errors.append(f"data.category '{data['category']}' is not a threat category")
        if data.get("via", "rules") not in THREAT_VIA:
            errors.append(f"data.via must be one of {sorted(THREAT_VIA)}")
        if len(str(data.get("reason", ""))) > THREAT_REASON_MAX:
            errors.append(f"data.reason must be at most {THREAT_REASON_MAX} characters")
        voices = data.get("voices")
        if voices is not None and (not isinstance(voices, int) or isinstance(voices, bool)
                                   or voices < 0):
            errors.append("data.voices must be a whole number or null")
    if d["type"] == EventType.PHOTO_DESCRIBED:
        errors += _photo_errors(data)
    if d["type"] == EventType.RECORDING_SAVED:
        if not str(data["path"]).strip():
            errors.append("data.path is empty")
        if not _whole(data["frames"]) or data["frames"] < 0:
            errors.append("data.frames must be a whole number >= 0")
        dur = data["duration_s"]
        if isinstance(dur, bool) or not isinstance(dur, (int, float)) or dur < 0:
            errors.append("data.duration_s must be a number >= 0")
        if data.get("video_path") is not None and not isinstance(data["video_path"], str):
            errors.append("data.video_path must be a string or null")
        if not isinstance(data.get("reason", ""), str):
            errors.append("data.reason must be a string")
    if d["type"] == EventType.SPEECH_HEARD and not str(data["text"]).strip():
        errors.append("data.text is empty")
    if d["type"] == EventType.SPEECH_HEARD and data.get("voices") is not None:
        v = data["voices"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            errors.append("data.voices must be a whole number >= 1")
    if d["type"] == EventType.SPEECH_HEARD and data.get("owner_voice") is not None \
            and not isinstance(data["owner_voice"], bool):
        errors.append("data.owner_voice must be true, false or null")
    if d["type"] == EventType.SPEECH_HEARD and data.get("channel") is not None:
        if data["channel"] not in SpeechChannel.ALL:
            errors.append(f"data.channel must be one of {sorted(SpeechChannel.ALL)} or null")
    return errors
