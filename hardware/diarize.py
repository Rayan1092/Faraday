"""Speaker count for visitor recordings: `argmax-cli diarize` -> distinct voices.

Runs alongside STT (integration/voice.py starts it in a thread), so it never
holds up the reply. The command comes from settings (stt.<os>.diarize.command,
"{wav}" filled in), because the exact argmax-cli flags are confirmed only on
the Mac. The output parser accepts the usual shapes: RTTM lines, JSON segment
lists, a JSON speaker count, SPEAKER_00 style labels, or "N speakers".

    d = Diarizer.from_settings(settings)      # None if not configured for this OS
    n = d.count_voices(Path("captures/audio/x.wav"))   # int or None
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hardware.settings import for_os

log = logging.getLogger("hardware.diarize")

_SPEAKER_KEYS = ("speaker", "speakerId", "speaker_id", "speakerID", "label", "spk")
_COUNT_KEYS = ("speakers", "num_speakers", "numSpeakers", "speaker_count", "speakerCount")


def _from_json(obj: Any) -> Optional[int]:
    if isinstance(obj, dict):
        for k in _COUNT_KEYS:
            v = obj.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
            if isinstance(v, list):
                return len({json.dumps(x, sort_keys=True) for x in v}) or None
        for k in ("segments", "turns", "results", "diarization"):
            if isinstance(obj.get(k), list):
                return _from_json(obj[k])
        return None
    if isinstance(obj, list):
        labels = set()
        for seg in obj:
            if isinstance(seg, dict):
                for k in _SPEAKER_KEYS:
                    if k in seg and seg[k] is not None:
                        labels.add(str(seg[k]))
                        break
        return len(labels) or None
    return None


def parse_voice_count(text: str) -> Optional[int]:
    """Distinct speakers in argmax-cli diarize output, or None if it can't tell."""
    text = text or ""
    stripped = text.strip()
    if stripped[:1] in "[{":
        try:
            n = _from_json(json.loads(stripped))
            if n:
                return n
        except ValueError:
            pass
    rttm = {parts[7] for parts in (l.split() for l in text.splitlines())
            if len(parts) >= 8 and parts[0] == "SPEAKER"}
    if rttm:
        return len(rttm)
    labels = {m.group(1).lstrip("0") or "0"
              for m in re.finditer(r"\bspeaker[_\s#-]?(\d+)\b", text, re.I)}
    if labels:
        return len(labels)
    m = (re.search(r"\b(\d+)\s+(?:distinct\s+|unique\s+)?speakers?\b", text, re.I)
         or re.search(r"\bspeakers?(?:\s+count)?\s*[:=]\s*(\d+)\b", text, re.I))
    if m:
        n = int(m.group(1))
        return n if n >= 1 else None
    return None


def parse_rttm(text: str) -> List[Tuple[float, float, str]]:
    """RTTM lines -> [(start_s, duration_s, speaker)]. argmax-cli diarize prints RTTM
    when --rttm-path isn't given (Sources/ArgmaxCLI/DiarizeCLI.swift)."""
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[0] == "SPEAKER":
            try:
                out.append((float(parts[3]), float(parts[4]), parts[7]))
            except ValueError:
                continue
    return out


class Diarizer:
    def __init__(self, command: List[str], timeout_s: float = 20.0):
        self.command, self.timeout_s = command, timeout_s

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> Optional["Diarizer"]:
        cfg = (for_os(settings.get("stt", {})) or {}).get("diarize") or {}
        command = cfg.get("command")
        if not command or cfg.get("enabled") is False:
            return None
        return cls(list(command), float(cfg.get("timeout_s", 20)))

    @staticmethod
    def grace_s(settings: Dict[str, Any]) -> float:
        cfg = (for_os(settings.get("stt", {})) or {}).get("diarize") or {}
        return float(cfg.get("grace_s", 0))

    def build(self, wav: Path) -> List[str]:
        return [part.replace("{wav}", str(wav)) for part in self.command]

    def run(self, wav: Path) -> Optional[str]:
        """The command's raw output (stdout, then stderr), or None if it failed."""
        try:
            proc = subprocess.run(self.build(Path(wav)), capture_output=True, text=True,
                                  timeout=self.timeout_s)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("diarize failed: %r", e)
            return None
        if proc.returncode != 0:
            log.warning("diarize exited %s: %s", proc.returncode, (proc.stderr or proc.stdout)[-300:])
            return None
        return (proc.stdout or "") + "\n" + (proc.stderr or "")

    def count_voices(self, wav: Path) -> Optional[int]:
        out = self.run(wav)
        if out is None:
            return None
        n = parse_voice_count(out)
        if n is None:
            log.warning("diarize output not understood: %s", out.strip()[-200:])
        return n
