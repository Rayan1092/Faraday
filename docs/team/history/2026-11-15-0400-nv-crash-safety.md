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
