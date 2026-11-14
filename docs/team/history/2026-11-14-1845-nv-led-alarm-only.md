# Flipper LED: off except at alarm (NV)

- **Laptop / branch / PR:** NV, `nv/led-alarm-only`, PR "NV: Flipper LED off except at alarm".
- **What changed:**
  - **Rule (owner's decision after the real-hardware LED test):** the Flipper LED is off in every mode and at warning. Only an alarm shows red plus three buzzes, and it goes off again at the passphrase disarm. No green/blue/purple/yellow.
  - `hardware/flipper/device.py`: `apply_event()` now acts only on `alert_changed` (alarm → red + buzz, anything else → off) and ignores `mode_changed`. The driver ignores `flipper.led` in the settings. "Off" needs no re-sends (the firmware keeps red/green at 0 while listening), so there is no LED traffic at all outside an alarm. Red is still re-sent 2 s after a long rx pause and every 15 s, but only while an alarm is on.
  - `manual_test.py`: `c` toggles red/off, `x` alarm, `r` normal; mode keys show the LED staying off.
  - Tests updated in `hardware/flipper/test_flipper.py` and `integration/tests/test_app.py`: no LED commands for modes or warning, alarm red + 3 buzzes, red survives the firmware rewrite after the buzz, off after disarm, reconnect during an alarm restores red, leftover colour cleared at startup, and red is wiped without the re-send (shows the fake models the firmware).
  - Agreed shared files: `CLAUDE.md` (LED fact), `docs/PLAN.md` (demo script, seam 3, step table, new Open Question 24), `config/settings.example.json` (`flipper.led` is now `{"alarm": "red"}`; the dashboard keeps its own defaults for mode colours).
- **How it was tested:** fake Flipper and unit tests only (`python -m unittest discover`). **Not yet on the real Flipper**: the Join 1 retest is next.
- **What works:** full suite green; integration tests run three times without flakes.
- **What's broken or faked:** nothing known. Red can still drop out for about 2 s after the buzz, a send or a reconnect before it's re-sent.
- **Next step:** owner's Join 1 retest on the real Flipper: Night, wave (porch light on, LED stays off), wave again within 60 s (lamp on, LED red + 3 buzzes), passphrase (LED off).
- **For Dell (stale mentions of LED colours in Dell's files, not edited by NV):**
  - `docs/FORMATS.md` lines 17–18 (modes/alert levels list LED colours), 131, 157–158.
  - `dashboard/README.md` line 16 ("status colour matches the Flipper LED").
  - `docs/submission/pitch.md` lines 15, 44, 48, 52; `video_script.md` lines 7, 18, 20, 22; `setup_checklist.md` lines 39, 59, 61; `judge_qa.md` line 17; `docs/submission/README.md` line 15.
  - Suggested wording: "The Flipper LED is off except at alarm (red + buzzes). The dashboard shows the mode colour."
