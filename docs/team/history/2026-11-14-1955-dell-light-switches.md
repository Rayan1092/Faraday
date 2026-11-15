# Real light switches (Dell, item 4)
- Laptop / branch / PR: Dell / `dell/light-switches` / PR "real light switches"
- What changed:
  - **Server:** `dashboard/server.py` has `board.control_hook` (NV sets it to `App.control`, already in `integration/__main__.py`) and `POST /api/control {"outlet", "on"}`. It returns 400 for a bad outlet or non-bool `on`, 503 without the hook, otherwise `{"ok": true, "changed": <hook result>}`. `/api/state` gets `control_available`.
  - **Page:** the dock's light tiles are real toggle buttons (`aria-pressed`, keyboard focus, "Switching…" while in flight, a short error note on a failure). They're updated in place, so the 1 s poll doesn't steal focus. The state always comes from the log.
  - **Sim:** `sim/controls.py` `SimControls` has the same contract as NV's hook (logs a `source: dashboard` action, False if already in that state), so `python -m sim --dashboard` has working switches.
  - **Docs:** a PLAN.md Cross-laptop row (Q26 control half accepted) and the dashboard/README routes.
- How it was tested: `python -m unittest discover` passes. 8 new tests: route, validation, 403 without the header, a hook exception gives a 500, panel state from the log, and an owner-switched lamp surviving the passphrase disarm while Faraday's porch light goes off. Screenshots at 1280×720 and 1920×1080 (Home with the owner's lamp on, alarm with both lights on). There's no Bonsai in this step, so no bench case.
- For NV: nothing to change, `app.board.control_hook = app.control` already matches. Please tap the tiles once on the Mac during the hardware run.
- Next: item 5 (Controls page).
