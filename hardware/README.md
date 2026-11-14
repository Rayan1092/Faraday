# hardware/

**Owner: NV laptop.**

These are the device clients. Each one turns a device into `shared.Event`s or carries out a `shared.Action`. Nothing here decides anything.

Planned modules:
- `flipper.py`: serial CLI on the port from settings. Runs `subghz rx`, parses Princeton packets, turns doorbell holds and merged motion bursts into events. Sends with `subghz tx_from_file` (pause rx, send, resume). Sets the LED.
- `audio.py`: mic recording and speaker playback (`sounddevice`, `soundfile`).
- `speech.py`: STT client (Argmax `/v1/audio/transcriptions` on the Mac, stand-in on Windows) → `speech_heard`. TTS (argmax-cli subprocess on the Mac, stand-in on Windows).
- `camera.py`: OpenCV snapshot → `captures/photos/*.jpg`.

All of it must run on macOS and Windows, with OS-specific values coming from `config/settings.json`.
