"""One-at-a-time hardware checks (real devices!). Run when you're at the hardware.

    python -m hardware.check camera            # snapshot -> captures/photos + Bonsai description
    python -m hardware.check vision [photo]    # Bonsai vision only, on the newest photo; says why it fails
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
        named = getattr(cam.detector, "named", None)
        if named is not None:
            print(f"detector: objects named at {named:.2f} or more (camera.detector.object_score)")
        print(f"detector: people={ev.data.get('people')} faces={ev.data.get('faces')} "
              + ", ".join(f"{d['label']} x{d['count']} {d['confidence']:.2f}" for d in ev.data["detections"]))
    if ev.data["photo_path"]:
        import cv2
        from hardware.settings import REPO_ROOT
        img = cv2.imread(str(REPO_ROOT / ev.data["photo_path"]))
        if img is not None:
            h, w = img.shape[:2]
            shape = "16:9" if abs(w / h - 16 / 9) < 0.02 else "4:3" if abs(w / h - 4 / 3) < 0.02 else f"{w / h:.2f}:1"
            print(f"frame: {w}x{h} ({shape}). The live stream is always this whole frame.")
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


def vision(settings, photo=None) -> int:
    from pathlib import Path
    from hardware.settings import REPO_ROOT
    from hardware.vision import Vision
    v = Vision.from_settings(settings)
    print(f"profile={settings['llm'].get('profile')} model={v.model}")
    print(f"POST {v.url}  timeout={v.timeout_s:.0f}s  max_tokens={v.max_tokens}  "
          f"reasoning_effort={v.reasoning_effort}")
    print(f"LM Studio: {_model_info(v.base_url, v.model)}")
    if photo:
        path = Path(photo)
    else:
        photos = sorted((REPO_ROOT / settings.get("paths", {}).get("captures", "captures") / "photos")
                        .glob("*.jpg"), key=lambda p: p.stat().st_mtime)
        if not photos:
            print("no photo in captures/photos: run  python -m hardware.check camera  first")
            return 1
        path = photos[-1]
    print(f"photo: {path} (sent as JPEG, long side shrunk to 512 px, base64 data URL)")
    warm = v.warm_up()
    print(f"warm-up (tiny image, loads the vision part): {warm} s" if warm is not None
          else "warm-up FAILED (see the warning above)")
    hint_line = None
    try:
        import cv2
        from hardware.detect import Detector, hint
        det = Detector.from_settings(settings)
        frame = cv2.imread(str(path))
        if det is not None and frame is not None:
            info = det.analyse(frame)
            if info is None:
                print(f"detector ({det.engine}) FAILED on this photo (see the warning above)")
            else:
                print(f"detector ({det.engine}, {det.last_ms:.0f} ms): {hint(info)}")
                hint_line = hint(info) if info["detections"] else None
    except Exception as e:
        print(f"detector unavailable: {e!r}")
    text = v.describe(path, hint=hint_line)
    last = v.last
    print(f"{last.get('elapsed_s')}s  HTTP {last.get('status', '-')}  image {last.get('image_kb', '?')} KB  "
          f"finish_reason={last.get('finish_reason')}  reasoning={last.get('reasoning_chars', 0)} chars  "
          f"attempts={last.get('attempts')}  max_tokens={last.get('max_tokens')}")
    if "content" in last:
        print(f"raw answer: {last['content']!r}")
    print(f"description: {text}" if text else f"FAILED: {last.get('error')}")
    return 0 if text else 1


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
