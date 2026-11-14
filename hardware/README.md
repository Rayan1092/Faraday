# hardware/

**Owner: NV laptop.**

These are the device clients. Each one turns a device into `shared.Event`s or carries out a `shared.Action`. Nothing here decides anything.

Planned modules:
- `flipper/`: **done (Step 1)**, see [flipper/README.md](flipper/README.md). Serial CLI, rx → doorbell/motion events, outlet sends, LED + buzz, auto-reconnect.
- `audio.py`: mic recording and speaker playback (`sounddevice`, `soundfile`).
- `speech.py`: STT client (Argmax `/v1/audio/transcriptions` on the Mac, stand-in on Windows) → `speech_heard`. TTS (argmax-cli subprocess on the Mac, stand-in on Windows).
- `camera.py` + `vision.py`: **done**. OpenCV snapshot → `captures/photos/<time>.jpg` → Bonsai vision (LM Studio image input, 512 px) → `photo_described`. If vision fails: "Photo saved.". Tests: `python -m unittest hardware.tests.test_camera` (`FARADAY_LIVE_VISION=nv_backup` adds a live LM Studio check).

All of it must run on macOS and Windows, with OS-specific values coming from `config/settings.json` (loaded by `hardware/settings.py`).
