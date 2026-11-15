"""Owner voice check (display only): is this "Hey Faraday" request the owner's voice?

Enroll about 10 s of the owner's voice once. For each owner request, the
enrollment and the request are joined into one wav (with a short silence
between) and run through `argmax-cli diarize` once:
- every voice in the request part is the enrollment's speaker -> owner (True)
- another voice is in the request part -> not the owner (False)
- diarization failed or found nothing -> unknown (None)
With RTTM output (argmax-cli prints it) the check is per time range. With only
a speaker count it falls back to the owner's rule: one voice = owner, two =
unknown.

The result goes on speech_heard as `owner_voice`. It is DISPLAY ONLY: nothing
is allowed or refused because of it (owner 2026-11-14: enforce for disarm only
if it is right at least 9 times out of 10 on the Mac).

    python3 -m hardware.owner_voice enroll            # record ~10 s of your voice once
    python3 -m hardware.owner_voice check some.wav    # owner / not the owner / unknown
    python3 -m hardware.owner_voice trial             # 10 tries, prints the score
"""
from __future__ import annotations

import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from hardware.diarize import Diarizer, parse_rttm, parse_voice_count
from hardware.settings import REPO_ROOT

log = logging.getLogger("hardware.owner_voice")

DEFAULT_ENROLLMENT = "cache/owner_voice.wav"     # gitignored, survives --fresh
GAP_S = 0.5
MIN_TURN_S = 0.3        # shorter turns in the request part are ignored (a cough, a click)
RATE = 16000


def decide(rttm: List[Tuple[float, float, str]], enrol_end_s: float,
           min_turn_s: float = MIN_TURN_S) -> Optional[bool]:
    """True if every speaker heard after the enrollment is the enrollment's main
    speaker, False if someone else speaks there, None if it can't tell."""
    enrol: Dict[str, float] = {}
    request: Dict[str, float] = {}
    for start, dur, spk in rttm:
        end = start + dur
        before = max(0.0, min(end, enrol_end_s) - start)
        after = max(0.0, end - max(start, enrol_end_s))
        if before > 0:
            enrol[spk] = enrol.get(spk, 0.0) + before
        if after > 0:
            request[spk] = request.get(spk, 0.0) + after
    if not enrol or not request:
        return None
    owner = max(enrol, key=enrol.get)
    others = {s for s, d in request.items() if s != owner and d >= min_turn_s}
    if others:
        return False
    return request.get(owner, 0.0) >= min_turn_s or None


def _read_mono(path: Path) -> Tuple[np.ndarray, int]:
    import soundfile as sf
    data, rate = sf.read(str(path), dtype="int16", always_2d=True)
    return data[:, 0], rate


class OwnerVoice:
    def __init__(self, diarizer: Diarizer, enrollment: Path, gap_s: float = GAP_S):
        self.diarizer, self.enrollment, self.gap_s = diarizer, enrollment, gap_s
        self.last: Dict[str, Any] = {}

    @classmethod
    def from_settings(cls, settings: Dict[str, Any],
                      diarizer: Optional[Diarizer] = None) -> Optional["OwnerVoice"]:
        """None if there is no diarize command or nobody has enrolled yet."""
        cfg = settings.get("audio", {}).get("voice_check", {}) or {}
        if cfg.get("enabled") is False:
            return None
        diarizer = diarizer or Diarizer.from_settings(settings)
        path = Path(cfg.get("enrollment") or DEFAULT_ENROLLMENT)
        if not path.is_absolute():
            path = REPO_ROOT / path
        if diarizer is None or not path.exists():
            return None
        return cls(diarizer, path)

    @staticmethod
    def grace_s(settings: Dict[str, Any]) -> float:
        cfg = settings.get("audio", {}).get("voice_check", {}) or {}
        return float(cfg.get("grace_s", 2.0))

    def join(self, request: Path, out: Path) -> float:
        """Write enrollment + silence + request to `out`. Returns where the request
        part starts, in seconds."""
        import soundfile as sf
        enrol, rate = _read_mono(self.enrollment)
        req, req_rate = _read_mono(request)
        if req_rate != rate:                         # both come from our Recorder (16 kHz)
            idx = np.linspace(0, len(req) - 1, int(len(req) * rate / req_rate)).astype(int)
            req = req[idx]
        gap = np.zeros(int(self.gap_s * rate), dtype=np.int16)
        sf.write(str(out), np.concatenate([enrol, gap, req]), rate, subtype="PCM_16")
        return (len(enrol) + len(gap)) / float(rate)

    def check(self, request: Path) -> Optional[bool]:
        """True = the owner's voice, False = someone else, None = can't tell."""
        t0 = time.monotonic()
        self.last = {}
        try:
            with tempfile.TemporaryDirectory(prefix="faraday-voice-") as tmp:
                joined = Path(tmp) / "joined.wav"
                split = self.join(Path(request), joined)
                out = self.diarizer.run(joined)
        except Exception as e:
            log.warning("owner voice check failed: %r", e)
            self.last = {"error": repr(e)}
            return None
        if out is None:
            self.last = {"error": "diarize failed"}
            return None
        segments = parse_rttm(out)
        if segments:
            result = decide(segments, split)
            how = "rttm"
        else:
            n = parse_voice_count(out)              # the owner's rule on a plain count
            result = None if not n else (n == 1)
            how = "count"
        self.last = {"result": result, "how": how, "segments": len(segments),
                     "seconds": round(time.monotonic() - t0, 1)}
        return result


def label(result: Optional[bool]) -> str:
    return {True: "owner", False: "NOT the owner", None: "unknown"}[result]


# ---- command line ---------------------------------------------------------------------
def _record(settings: Dict[str, Any], seconds: float) -> Optional[Path]:
    from hardware.audio import Recorder
    rec = Recorder.from_settings(settings)
    rec.start()
    time.sleep(seconds)
    path, secs = rec.stop()
    return path


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    from hardware.settings import load_settings
    ap = argparse.ArgumentParser(prog="python3 -m hardware.owner_voice")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll", help="record about 10 s of the owner's voice")
    e.add_argument("--seconds", type=float, default=10.0)
    c = sub.add_parser("check", help="check one wav")
    c.add_argument("wav")
    t = sub.add_parser("trial", help="record several short requests and score them")
    t.add_argument("--n", type=int, default=10)
    t.add_argument("--seconds", type=float, default=4.0)
    a = ap.parse_args(argv)
    settings = load_settings()
    cfg = settings.get("audio", {}).get("voice_check", {}) or {}
    enrollment = Path(cfg.get("enrollment") or DEFAULT_ENROLLMENT)
    if not enrollment.is_absolute():
        enrollment = REPO_ROOT / enrollment
    if a.cmd == "enroll":
        input(f"Speak normally for {a.seconds:.0f} s after you press Enter (read anything aloud)...")
        path = _record(settings, a.seconds)
        if path is None:
            print("nothing recorded (wrong mic? python3 -m hardware.check devices)")
            return 1
        data, rate = _read_mono(path)
        peak = int(np.abs(data).max()) if len(data) else 0
        enrollment.parent.mkdir(parents=True, exist_ok=True)
        path.replace(enrollment)
        print(f"enrolled: {enrollment} ({len(data) / rate:.1f} s, peak {peak}/32767"
              f"{'  <- very quiet, record again closer to the mic' if peak < 2000 else ''})")
        return 0
    checker = OwnerVoice.from_settings(settings)
    if checker is None:
        print("not ready: enroll first (python3 -m hardware.owner_voice enroll), and stt.mac.diarize "
              "needs a command")
        return 1
    if a.cmd == "check":
        result = checker.check(Path(a.wav))
        print(f"{label(result)}  {checker.last}")
        return 0
    score = 0
    for i in range(1, a.n + 1):
        input(f"try {i}/{a.n}: press Enter, then say a request for {a.seconds:.0f} s...")
        path = _record(settings, a.seconds)
        result = checker.check(path) if path is not None else None
        score += result is True
        print(f"   {label(result)}  ({checker.last.get('seconds', '?')} s)")
    print(f"owner recognised {score}/{a.n} times. Enforce for disarm only if 9/10 or better,"
          f" and run it once more with someone else speaking (they should get NOT the owner).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
