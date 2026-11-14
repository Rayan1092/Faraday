"""One-at-a-time hardware checks (real devices!). Run when you're at the hardware.

    python -m hardware.check camera            # snapshot -> captures/photos + Bonsai description
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
    ev = PhotoCamera.from_settings(settings).take()
    print(f"{time.monotonic() - t0:.1f}s  source={ev.source}  photo={ev.data['photo_path'] or '(none)'}")
    print(f"description: {ev.data['description']}")
    return 0 if ev.data["photo_path"] else 1


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
    ap.add_argument("what", choices=["camera", "say", "record", "mute", "devices"])
    ap.add_argument("arg", nargs="?", default=None)
    a = ap.parse_args(argv)
    s = load_settings()
    if a.what == "camera":
        return camera(s)
    if a.what == "say":
        return say(s, a.arg or "Hello, I'm Faraday. Away mode on.")
    if a.what == "record":
        return record(s, float(a.arg or 4))
    if a.what == "mute":
        return mute(s)
    return devices(s)


if __name__ == "__main__":
    sys.exit(main())
