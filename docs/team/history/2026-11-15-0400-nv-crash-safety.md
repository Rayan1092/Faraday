# NV: crash safety (2026-11-15 04:00)

Owner item 4: if the camera, Argmax, Bonsai or the Flipper fails mid-demo, the app keeps running, logs it clearly and recovers when the part comes back.

## What was already true

Each part already recovers on its own:
- the Flipper driver reconnects
- the camera feed reopens, and a failed snapshot gives "Camera unavailable."
- STT, TTS, vision and diarization fail one request at a time and try again on the next
- Dell's brain falls back to plain code per call

None of it crashes a thread. What was missing was saying so clearly, and tests.

## New: `integration/health.py`

| Part | Watched by |
|---|---|
| Flipper | the driver's connected flag |
| Camera | the feed's failed flag |
| Argmax STT | an HTTP probe of the `argmax-cli serve` address |
| Bonsai | an HTTP probe of LM Studio's `/v1/models` |
| Argmax TTS | the speech worker, after every line |

- **Console:** a change prints one line, for example `[health] Argmax STT DOWN: not answering at … (start it: argmax-cli serve). Faraday keeps running and retries.` and later `[health] Argmax STT back`.
- **Log:** warning when a part goes down, info when it's back.
- **Startup:** for the first 10 s parts are still coming up, so nothing is reported down then.
- **Settings:** the probes run every 5 s (`integration.health_interval_s`, `integration.health_grace_s`).
- **Dashboard:** `GET /api/status` on the controls server has `"health": {part: {"ok", "detail", "since"}}`, so Dell's dashboard can show it.

## Tests

`integration/tests/test_crash_safety.py` uses the real wiring (`build_app` with the hardware swapped). Each part goes down mid-run and comes back, about 18 s for all five:
- **Flipper unplugged:** DOWN is printed, the app keeps speaking, a light command waits and is sent once back, and a doorbell works again.
- **Camera won't open:** "Camera unavailable." at once and DOWN, then back and a real photo.
- **Argmax STT server dies:** DOWN, a wake request isn't understood but nothing breaks, then back and the same request arms Away.
- **TTS command fails:** DOWN and the radio keeps working, then back.
- **LM Studio dies:** DOWN, the photo falls back (detector) instead of hanging, then back and Bonsai describes again.

MAC_TEST.md has an optional crash check (steps 20-22).

## Review (fresh reviewer agent) and fixes

- **Camera:** it was judged only while the feed ran, and `_touch()` cleared the flag, so a dead camera could print a false "back". Now it is judged by real frames (`CameraFeed.frame_age()`). A dead, idle camera is retried every 20 s, so "back" means frames came.
- **HTTP probes:** LM Studio serves one request at a time, so a probe waiting behind a 9B call could print a false DOWN mid-demo. STT and LM Studio now get a plain TCP connect: a busy server still accepts, a crashed one refuses. This also leaves no lines in their logs. A timeout counts only after two polls in a row.
- **Bonsai:** an ejected or crashed 9B looked fine. Now, every 30 s, LM Studio's `/api/v0/models` must show the profile's model as loaded. If that call is busy, the last answer is kept.
- **Small ones:**
  - `since` is the time of the change, not of the last poll.
  - `report()` never raises (a broken console can't stop the speech worker or the health thread).
  - Transitions are appended to `logs/health.log` and logged at INFO, so there are no duplicate console lines.
- **Tests:**
  - console output from start-up is cleared before the outage
  - the Bonsai test asserts the detector fallback and a photo
  - the test servers really stop listening (refused) and come back on the same port
  - new `HealthUnit` tests: grace, `since`, one line per change, TCP refused/timeout, a broken check or printer, camera "back" only with frames
