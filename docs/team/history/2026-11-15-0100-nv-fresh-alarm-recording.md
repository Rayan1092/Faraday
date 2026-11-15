# NV: --fresh, lights off at start, alarm recording (2026-11-15 01:00)

Owner items 7 and 8. Built on Windows with the fake Flipper and a fake camera; the Mac confirms.

## Item 7

- **`python3 -m integration --fresh`** (`integration/fresh.py`): before anything starts, moves `logs/` (events.jsonl, rules.json, instructions.json, …) and `captures/` (photos, audio, alarm recordings) to `old_runs/<YYYYmmdd-HHMMSS>/`. Nothing is deleted, and empty folders are recreated. `old_runs/` and `cache/` are gitignored. The TTS cache and the models stay.
- **All lights off at every start:** `App` sends `outlet_off` to every outlet right after the Flipper starts. Each one is logged as an action with source `system`, so Dell's Lights panel shows them off, and the skip-repeat state starts as "off". Setting `integration.lights_off_at_start` (default true).
  - **Mac risk:** we know a repeated *on* toggles the outlet. If the outlets also toggle on an *off* code, a lamp that was off would turn on at start. Check this first on the Mac. If it happens, set `"lights_off_at_start": false`.

## Item 8

- **On `alert_changed` → `alarm`:** `CameraFeed.record(10 s)` saves every frame (about 10 per second) to `captures/alarm-<time>/frame-0001.jpg …`, plus `clip.avi` (MJPG, written here by OpenCV: 16 KB for 11 test frames). Only one recording runs at a time. The setting is `camera.alarm_record_s`, default 10.
- **When it ends:** `integration/` writes a new **`recording_saved`** event straight to the log (`shared/events.py` `RecordingSavedData`: path, frames, duration_s, video_path, reason; source camera; caused_by the alert). It doesn't go through the pipeline, so the brain isn't asked about it. The console prints "Recording saved: …". Dell shows it (PLAN Q32, FORMATS.md).
- With the stand-in camera (no webcam) nothing is recorded.

## Settings (Mac `config/settings.json`)

Under `"integration"`: add `"lights_off_at_start": true` (or false, see the risk above).
Under `"camera"`: add `"alarm_record_s": 10`.
Both are optional; these are the code defaults.

## Tests

`integration/tests/test_fresh_alarm.py`:
- the archive moves logs and captures, keeps the cache, handles nothing to move, and handles two runs in the same second
- every outlet off, logged as system, dashboard all off, skip-repeat knows "off"
- off by default for app-level tests
- an alarm records once and writes a valid `recording_saved`
- no camera means no recording; a warning doesn't record
- `CameraFeed.record` on the fake capture
