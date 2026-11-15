"""Pick Faraday's voice on the Mac: render one line with a few seeds and
temperatures (and the 1.7B model with a calm instruction), time each, play
them one by one, then print the settings lines for the one you liked.

    python3 -m hardware.tts_audition                 # render + play all, numbered
    python3 -m hardware.tts_audition --no-play       # render + time only
    python3 -m hardware.tts_audition --line "Hello. Would you like to leave a message?"
    python3 -m hardware.tts_audition --pick 4        # print the settings lines for take 4

Uses tts.mac.command from settings (the argmax-cli path). The 1.7B model is
downloaded by argmax-cli on first use: run this once with Wi-Fi ON.
Wavs go to captures/tts_audition/, the table to captures/tts_audition/takes.json.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from hardware.settings import REPO_ROOT, for_os, load_settings

LINE = "Hello, I'm Faraday. Nobody can come to the door right now. Would you like to leave a message?"
OUT = REPO_ROOT / "captures" / "tts_audition"
CALM = "Speak calmly and warmly"


def takes() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for seed in (7, 42, 2026):
        for temp in (0.3, 0.6, 0.9):
            out.append({"model": "0.6b", "seed": seed, "temperature": temp})
    for seed in (7, 42):
        out.append({"model": "1.7b", "seed": seed, "temperature": 0.6, "instruction": CALM})
    for i, t in enumerate(out, 1):
        t["take"] = i
    return out


def base_command(settings: Dict[str, Any]) -> List[str]:
    cfg = for_os(settings.get("tts", {})) or {}
    cmd = list(cfg.get("command") or [])
    if not cmd:
        raise SystemExit("no tts.<os>.command in settings (this is for the Mac's argmax-cli)")
    return [p for p in cmd if p != "--play"]


def render(cmd: List[str], line: str, take: Dict[str, Any], out: Path) -> Dict[str, Any]:
    args = [p.replace("{text}", line) for p in cmd]
    args += ["--seed", str(take["seed"]), "--temperature", str(take["temperature"]),
             "--model", take["model"]]
    if take.get("instruction"):
        args += ["--instruction", take["instruction"]]
    # no dots in the name: the CLI drops everything after the last dot ("t0.3" -> "t0")
    stem = out / f"take{take['take']:02d}"
    for old in out.glob(f"take{take['take']:02d}.*"):
        old.unlink()
    args += ["--output-path", str(stem), "--output-format", "wav"]
    t0 = time.monotonic()
    timeout = 1200 if take["model"] == "1.7b" else 300      # the first 1.7B run downloads it
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return dict(take, seconds=round(time.monotonic() - t0, 1), ok=False, wav=None,
                    error=f"timed out after {timeout} s")
    took = time.monotonic() - t0
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    wav = Path(lines[-1]) if lines and lines[-1].lower().endswith(".wav") else Path(str(stem) + ".wav")
    if not wav.exists():
        found = sorted(out.glob(f"take{take['take']:02d}*.wav"))
        wav = found[0] if found else wav
    result = dict(take, seconds=round(took, 1), ok=proc.returncode == 0 and wav.exists(),
                  wav=str(wav) if wav.exists() else None)
    if not result["ok"]:
        result["error"] = ((proc.stderr or "") + (proc.stdout or ""))[-300:]
    else:
        import soundfile as sf
        result["audio_s"] = round(sf.info(str(wav)).duration, 1)
    return result


def settings_lines(take: Dict[str, Any]) -> str:
    voice = {"seed": take["seed"], "temperature": take["temperature"], "top_k": 50,
             "model": take["model"], "instruction": take.get("instruction")}
    return ('In config/settings.json, under "tts" -> "mac", set this line:\n'
            f'      "voice": {json.dumps(voice)},')


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m hardware.tts_audition")
    ap.add_argument("--line", default=LINE)
    ap.add_argument("--no-play", action="store_true")
    ap.add_argument("--only", choices=["0.6b", "1.7b"], help="only this model")
    ap.add_argument("--pick", type=int, help="print the settings lines for this take number")
    a = ap.parse_args(argv)
    all_takes = takes()
    if a.pick:
        chosen = next((t for t in all_takes if t["take"] == a.pick), None)
        if chosen is None:
            print(f"no take {a.pick}; there are {len(all_takes)}")
            return 1
        print(settings_lines(chosen))
        return 0
    settings = load_settings()
    cmd = base_command(settings)
    help_text = subprocess.run([cmd[0], "tts", "--help"], capture_output=True, text=True).stdout
    for flag in ("--seed", "--temperature", "--model", "--instruction", "--output-format"):
        print(f"  argmax-cli tts {flag}: {'yes' if flag in help_text else 'NOT FOUND'}")
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for take in all_takes:
        if a.only and take["model"] != a.only:
            continue
        print(f"take {take['take']:2d}: {take['model']} seed {take['seed']} temperature {take['temperature']}"
              + (f' "{take["instruction"]}"' if take.get("instruction") else "") + " ...", end=" ", flush=True)
        r = render(cmd, a.line, take, OUT)
        results.append(r)
        print(f"{r['seconds']} s" + (f" for {r['audio_s']} s of audio" if r["ok"] else f"  FAILED {r.get('error', '')}"))
        (OUT / "takes.json").write_text(json.dumps(results, indent=2), encoding="utf-8")   # as we go
    for model in ("0.6b", "1.7b"):
        times = [r["seconds"] for r in results if r["ok"] and r["model"] == model]
        if times:
            print(f"{model}: {min(times)}-{max(times)} s per line (includes loading the model)")
    if not a.no_play:
        from hardware.audio import play_wav
        for r in results:
            if r["ok"]:
                print(f"playing take {r['take']} ({r['model']}, seed {r['seed']}, temperature {r['temperature']})")
                play_wav(Path(r["wav"]))
                time.sleep(0.6)
    print("\nPick the calmest, most natural take, then:  python3 -m hardware.tts_audition --pick <number>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
