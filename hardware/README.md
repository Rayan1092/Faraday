# hardware/

**Owner: NV laptop.**

These are the device clients. Each one turns a device into `shared.Event`s or carries out a `shared.Action`. Nothing here decides anything.

Modules:
- `flipper/`: **done (Step 1)**, see [flipper/README.md](flipper/README.md). Serial CLI, rx → doorbell/motion events, outlet sends, red LED + buzz at alarm only, auto-reconnect.
- `audio.py` + `speech.py`: **done**. Hands-free listening windows with energy VAD (`sounddevice`) → `captures/audio/*.wav` → STT (Argmax URL from settings) → `speech_heard`. TTS: argmax-cli command on the Mac, pyttsx3 on Windows. The mic is muted while Faraday talks. Tests: `python -m unittest hardware.tests.test_speech`.
- `camera.py` + `vision.py`: **done**. OpenCV snapshot → `captures/photos/<time>.jpg` → Bonsai vision (LM Studio image input, 512 px) → `photo_described`. If vision fails: "Photo saved.". Tests: `python -m unittest hardware.tests.test_camera` (`FARADAY_LIVE_VISION=nv_backup` adds a live LM Studio check).

Quick device checks (real hardware): `python -m hardware.check camera | say "text" | record 4 | mute | devices`.

All of it must run on macOS and Windows, with OS-specific values coming from `config/settings.json` (loaded by `hardware/settings.py`).
