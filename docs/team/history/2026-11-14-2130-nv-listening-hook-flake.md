# NV: listening_hook, reconnect flake, 2B check (2026-11-14 21:30)

Dell's three items from PLAN.md.

- **listening_hook:** `integration/__main__.py` sets `app.board.listening_hook = lambda: voice.listening`.
  - `Voice.listening` is now exact. It is true only while a visitor or Listen window is recording.
  - It is false during the wake listener, while a requested window waits for Faraday to finish speaking, and while muted.
  - Tests: `Windows.test_listening_is_true_only_while_the_window_records`, `test_wake_window_is_not_listening`, and `BuiltApp.test_listening_hook_drives_api_state` (`build_app` → `/api/state` `listening` false → true → false).
- **Reconnect flake** (`test_reconnect_during_alarm_restores_red`, about 1 in 13 full runs). Reproduced on demand by slowing the fake's `led` commands. There were two causes:
  1. **Test:** the fake noticed rx pauses of 0.3 s or more, but the driver re-sends red only after pauses of `LONG_PAUSE_S` (0.6 s) or more. Fix: `Flow` now uses `charge_min_off_s = LONG_PAUSE_S`, so every pause the fake notices is one the driver handles.
  2. **Driver** (`hardware/flipper/device.py`): a pending red re-send was counted from the moment it was scheduled. The reconnect's own LED batch then kept rx paused, so the re-send fired before the firmware's rewrite, and the rewrite wiped it. Fix: a pending re-send now restarts its count after every batch.
  - Regression test: `BusyMachine` in `integration/tests/test_app.py`, with `FakeDevice.led_time_s` 0.12 and 0.18. It fails on the old driver.
  - Known limit: above about 0.2 s per LED command (20 times slower than the real Flipper), every re-send triggers another. The old code did the same.
- **2B loaded check:** already in #32. `start_mac.sh` fails unless both `model` and `fast_model` are downloaded and loaded.

The full suite passes: 427 tests, 3 opt-in tests skipped.
