"""One-at-a-time hardware checks (real devices!). Run when you're at the hardware.

    python -m hardware.check camera            # snapshot -> captures/photos + Bonsai description
    python -m hardware.check vision [photo]    # every no-think strategy, 9B + 4B, timed; Dell's brain on 9B/2B
    python -m hardware.check say "Hello"       # speaker: TTS for this OS
    python -m hardware.check record 4          # mic: 4 s -> captures/audio/*.wav -> STT (if any)
    python -m hardware.check mute              # mic stays deaf while Faraday talks
    python -m hardware.check devices           # list audio devices + camera index tip

Uses config/settings.json (falls back to the example).
"""
from __future__ import annotations

import argparse
import sys
import time

from hardware.settings import load_settings


def camera(settings) -> int:
    from hardware.camera import PhotoCamera
    t0 = time.monotonic()
    cam = PhotoCamera.from_settings(settings)
    ev = cam.take()
    print(f"{time.monotonic() - t0:.1f}s  source={ev.source}  photo={ev.data['photo_path'] or '(none)'}")
    print(f"description: {ev.data['description']}  (by {ev.data.get('described_by') or 'nobody'})")
    if ev.data.get("detections") is not None:
        print(f"detector: people={ev.data.get('people')} faces={ev.data.get('faces')} "
              + ", ".join(f"{d['label']} x{d['count']} {d['confidence']:.2f}" for d in ev.data["detections"]))
    if ev.data["photo_path"] and cam.vision is not None and cam.vision.last.get("error"):
        print(f"vision ({cam.vision.model}) failed: {cam.vision.last['error']}")
        print("details: python -m hardware.check vision")
    return 0 if ev.data["photo_path"] else 1


def _model_info(base_url: str, model: str) -> str:
    """LM Studio's own view of the model: loaded or not, vlm (images) or llm."""
    import requests
    try:
        data = requests.get(base_url + "/api/v0/models", timeout=5).json().get("data", [])
    except Exception as e:
        return f"can't read {base_url}/api/v0/models ({e!r})"
    loaded = [m.get("id") for m in data if m.get("state") == "loaded"]
    me = next((m for m in data if m.get("id") == model), None)
    if me is None:
        return f"'{model}' is NOT in LM Studio's model list. Loaded: {', '.join(loaded) or 'nothing'}"
    return (f"state={me.get('state')} type={me.get('type')} (vlm = takes images) "
            f"arch={me.get('arch')} quant={me.get('quantization')}. Loaded: {', '.join(loaded) or 'nothing'}")


def _downloaded(base_url: str) -> list:
    import requests
    try:
        return [m.get("id") for m in requests.get(base_url + "/v1/models", timeout=5).json().get("data", [])]
    except Exception:
        return []


def vision(settings, photo=None) -> int:
    """Every no-think strategy on the vision model (and the 4B if downloaded), timed,
    on the newest photo. Picks the vision model's first working strategy (the 4B
    instead only if the 9B misses the 5 s target and the 4B makes it) and saves it;
    then Dell's brain client on the 9B and 2B (does its no-think probe work here?)."""
    from pathlib import Path
    from hardware.settings import REPO_ROOT
    from hardware.vision import (FIX_HINT, PROMPT, STRATEGIES, Vision, _save_cache, encode_image,
                                 test_image)
    v = Vision.from_settings(settings)
    llm = settings["llm"]
    prof = llm.get("profiles", {}).get(llm.get("profile"), {})
    print(f"profile={llm.get('profile')} vision model={v.model}  POST {v.url}")
    print(f"timeout={v.timeout_s:.0f}s  max_tokens={v.max_tokens}  reasoning_effort={v.reasoning_effort}")
    if v.max_tokens > 200:
        print(f'WARNING: llm.vision_max_tokens is {v.max_tokens}. Set "vision_max_tokens": 120, in '
              "config/settings.json: more tokens only let the model think longer.")
    models = [v.model]
    four_b = "ternary-bonsai-2-4b-mlx"
    if four_b != v.model and four_b in _downloaded(v.base_url):
        models.append(four_b)                       # the owner: measure the 4B too
    for m in models:
        print(f"LM Studio {m}: {_model_info(v.base_url, m)}")
    if photo:
        path = Path(photo)
    else:
        photos = sorted((REPO_ROOT / settings.get("paths", {}).get("captures", "captures") / "photos")
                        .glob("*.jpg"), key=lambda p: p.stat().st_mtime)
        path = photos[-1] if photos else None
    b64 = encode_image(path) if path else test_image()
    print(f"photo: {path or 'a small test image (no photo in captures/photos yet)'}")
    prompt = PROMPT
    try:
        import cv2
        from hardware.detect import Detector, hint
        det = Detector.from_settings(settings)
        frame = cv2.imread(str(path)) if path else None
        if det is not None and frame is not None:
            info = det.analyse(frame)
            if info is not None:
                print(f"detector ({det.engine}, {det.last_ms:.0f} ms): {hint(info)}")
                if info["detections"]:
                    prompt += f" Hint: {hint(info)}"
    except Exception as e:
        print(f"detector unavailable: {e!r}")
    print()
    print(f"{'model':28} {'strategy':9} {'time':>6}  {'result':9} answer")
    picks = {}                                       # model -> (strategy, seconds)
    for m in models:
        try:                                         # load it first: no row's time includes loading
            Vision(v.base_url, m, 120.0, v.reasoning_effort, 1, use_cache=False)._ask(
                b64, prompt, "prefill", 120.0)
        except Exception:
            pass                                     # the rows show the error
        vm = Vision(v.base_url, m, v.timeout_s, v.reasoning_effort, v.max_tokens, use_cache=False)
        rows = []
        for s in STRATEGIES:
            try:
                r = vm._ask(b64, prompt, s, v.timeout_s)
            except Exception as e:
                print(f"{m:28} {s:9} {'-':>6}  ERROR     {e!r}"[:200])
                continue
            ok = bool(r["answer"]) and not r["thinks"]
            result = ("ok" if ok else "thinks+ok" if r["answer"] else "THINKS" if r["thinks"]
                      else "TOO LONG" if r.get("too_long") else "empty")
            shown = r["answer"] or f"(finish={r['finish']}, reasoning={r['reasoning_chars']} chars)"
            print(f"{m:28} {s:9} {r['seconds']:5.1f}s  {result:9} {shown}"[:240])
            rows.append(r)
        clean = [r for r in rows if r["answer"] and not r["thinks"]]
        answered = [r for r in rows if r["answer"]]
        if clean or answered:                        # the app's order; else the fastest that answered
            r = clean[0] if clean else min(answered, key=lambda x: x["seconds"])
            picks[m] = (r["strategy"], r["seconds"])
    print()
    choice = pick_model(picks, v.model)
    if choice is None:
        print("NO strategy stops the thinking on this server. Photos will be described by the "
              f"object detector until it's fixed: {FIX_HINT}. Then run this again.")
        code = 1
    else:
        s, secs = picks[choice]
        print(f"USE: {choice} with '{s}' ({secs:.1f} s, {'under' if secs < 5 else 'OVER'} the 5 s target).")
        if choice != v.model:
            print(f'  In config/settings.json under "llm" add:  "vision_model": "{choice}",')
            print("  (start_mac.sh then loads it next to the 9B and the 2B)")
        for m, (s2, _secs) in picks.items():
            _save_cache(f"{v.base_url}|{m}", s2)     # the app skips the probe from its next start
        print("  Saved: the app uses it from its next start (cache/vision_mode.json).")
        code = 0
    print()
    _brain_check(settings, prof)
    return code


def pick_model(picks, preferred, target_s: float = 5.0):
    """picks = {model: (strategy, seconds)} of the models that work. The preferred
    (vision) model stays unless it misses the target and another makes it; a model
    is recommended over it only then, or when it doesn't work at all."""
    fast = [m for m in picks if picks[m][1] < target_s]
    if preferred in picks and (picks[preferred][1] < target_s or not fast):
        return preferred
    if fast:
        return fast[0]
    return min(picks, key=lambda m: picks[m][1], default=None)


def _brain_check(settings, prof) -> None:
    """Dell's brain client (brain/bonsai.py, #42) on the 9B and 2B, built the way
    the app builds it: its probe says whether the model thinks, then one real call
    through whatever it picked. OK = an answer with no thinking."""
    import time
    from hardware.vision import FIX_HINT
    try:
        from brain.bonsai import BonsaiClient
        from brain.settings import llm_config
        cfg = llm_config(settings)
    except Exception as e:
        print(f"brain check skipped: {e!r}")
        return
    for m in dict.fromkeys([cfg.get("model"), cfg.get("fast_model")]):
        if not m:
            continue
        notes = []
        c = BonsaiClient.from_config(dict(cfg, model=m), notes.append)
        t0 = time.perf_counter()
        try:
            mode = c.probe()
            msg = c.chat([{"role": "user", "content": "Say OK."}])
        except Exception as e:
            print(f"brain {m}: FAILED {e!r}")
            continue
        took = time.perf_counter() - t0
        reply = str(msg.get("content") or "").strip()
        endpoint, chars = c.last.get("endpoint"), c.last.get("thought_chars", 0) or 0
        if reply and not chars and endpoint == "raw":
            verdict = "OK: thinks by default; Dell's no-think prompt (/v1/completions) stops it"
        elif reply and not chars:
            verdict = "OK: no thinking (chat)"
        else:
            verdict = (f"PROBLEM: it thinks ({chars} chars via {endpoint}, probe said {mode}). "
                       f"Fix for the brain and vision at once: {FIX_HINT}")
        print(f"brain {m}: {verdict}. {took:.1f} s, reply {reply[:30]!r}")
        for n in notes:
            print(f"    {n}")


def say(settings, text: str) -> int:
    from hardware.speech import TextToSpeech
    tts = TextToSpeech.from_settings(settings)
    print(f"engine: {type(tts).__name__}")
    t0 = time.monotonic()
    ok = tts.say(text)
    print(f"{'OK' if ok else 'FAILED'} in {time.monotonic() - t0:.1f}s")
    return 0 if ok else 1


def record(settings, seconds: float) -> int:
    import numpy as np
    import soundfile as sf
    from hardware.audio import Recorder
    from hardware.speech import SpeechToText
    rec = Recorder.from_settings(settings)
    print(f"recording {seconds:.0f}s, speak now...")
    rec.start()
    time.sleep(seconds)
    path, secs = rec.stop()
    if path is None:
        print("nothing recorded (wrong mic? see: python -m hardware.check devices)")
        return 1
    data, _ = sf.read(str(path), dtype="int16")
    peak = int(np.abs(data).max()) if len(data) else 0
    print(f"saved {path} ({secs:.1f}s, peak {peak}/32767{'  <- very quiet' if peak < 1000 else ''})")
    stt = SpeechToText.from_settings(settings)
    if not stt.available:
        print("no STT on this OS (stt kind standin); the wav is saved")
        return 0
    t0 = time.monotonic()
    text = stt.transcribe(path)
    print(f"STT {time.monotonic() - t0:.1f}s: {text!r}")
    return 0 if text else 1


def mute(settings) -> int:
    from hardware.audio import MicGate, Recorder
    from hardware.speech import TextToSpeech
    gate = MicGate()
    rec, tts = Recorder.from_settings(settings, gate), TextToSpeech.from_settings(settings, gate)
    rec.start()
    t0 = time.monotonic()
    tts.say("This sentence should not be recorded.")
    talked = time.monotonic() - t0
    time.sleep(1.0)
    _, kept = rec.stop()
    total = time.monotonic() - t0
    print(f"talked {talked:.1f}s of {total:.1f}s; mic kept {kept:.1f}s (expect about {total - talked:.1f}s)")
    return 0 if kept < total - talked + 0.5 else 1


def devices(_settings) -> int:
    import sounddevice as sd
    print(sd.query_devices())
    print("Set audio.mic_device / audio.speaker_device to a number or name above;"
          " camera.index picks the webcam (0, 1, ...).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hardware.check")
    ap.add_argument("what", choices=["camera", "vision", "say", "record", "mute", "devices"])
    ap.add_argument("arg", nargs="?", default=None)
    a = ap.parse_args(argv)
    s = load_settings()
    if a.what == "camera":
        return camera(s)
    if a.what == "vision":
        return vision(s, a.arg)
    if a.what == "say":
        return say(s, a.arg or "Hello, I'm Faraday. Away mode on.")
    if a.what == "record":
        return record(s, float(a.arg or 4))
    if a.what == "mute":
        return mute(s)
    return devices(s)


if __name__ == "__main__":
    sys.exit(main())
