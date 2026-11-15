# Dell: walk-up grace, alarm gap, alarm line at Night (2026-11-15 00:45)

Owner's required logic fix: a visitor who walks up (motion) and then rings must never be warned or alarmed. It also covers item 8 of the list after the Mac test.

**Rules** (`modes/state.py`, the same in Away and Night):
- Motion while the alert is normal: porch light at once (`rules/baseline.py`), then a grace period (`alerts.motion_grace_s`, 15 s).
- A doorbell within it: a visitor. The pending warning is cancelled, the alert stays normal, and the normal doorbell flow runs.
- No doorbell by the end: `Pipeline.tick()` (a timer, `Pipeline._start_grace_timer`) raises the warning, speaks the warning line, takes the photo (reason "motion while away, no doorbell") and runs the threat label.
- After a doorbell, motion doesn't escalate for `alerts.visitor_quiet_s` (120 s).
- Alarm only from motion `min_gap_s` (8 s) to `alarm_window_s` (60 s) after the warning, with no doorbell in between. Sooner is the same wave (the Mac's "one long wave jumped to alarm"); later restarts the window.
- A mode change ends a visitor's quiet period and a pending warning. A suspicious label still raises the warning at once. Only the passphrase lowers the alert.
- `ModeState.last_outcome` (`MotionOutcome`: grace, pending, quiet, ignored, alarm, none) tells the baseline which steps to run.

**Alarm line at Night** (item 8): the alarm steps are the same in both modes: both lights, photo, "Alarm. You are being recorded. Leave now." If Bonsai's confrontation is unusable, code says "You are on camera. I can see <description>. Please leave now." (`brain/lines.py` `confront_line`), unless the camera or vision failed.

**Threat label:** walked up and rang with no warning signs means visitor or delivery (code overrides a 2B "suspicious"). Stayed without ringing while armed means suspicious ("Stayed at the door without ringing while the house is armed.").

**Sim and demo:**
- sim key `w`: wait out the grace period now.
- Backup demo step 2: the visitor walks up and then rings, and is never warned.
- Step 4: stays without ringing, warning after the grace period.
- Step 5: motion 8 to 60 s later, alarm. The demo waits the real 8 s at any speed.

**Docs:** PLAN.md demo script, mode table and notes. FORMATS.md `alert_changed`. CLAUDE.md alert rule. `settings.example.json` `alerts` (the keys you named).

**Tests:** new `modes` tests (walk up then ring, walk up no ring, lingering after the warning, boundaries, a doorbell after the warning, quiet ending, a mode change as a new visit). Pipeline tests for the same three scenarios. About 15 tests updated to drive time with `tick()` and a fake clock. My suite is all green.

**Review fixes** (fresh reviewer agent):
- A user rule's photo during the grace period was labelled "suspicious" (nobody rang yet) and warned the visitor before they rang. Rule photos are now dropped while the grace period or a visitor's quiet period runs.
- The label counted doorbells from the last 3 minutes, so someone who stayed without ringing was "a visitor who rang" after an earlier visitor (this happened in the backup demo). A photo from motion now counts only doorbells from that motion on. "Stayed without ringing while armed" now comes before "carrying a box".
- Motion long after an old warning restarted the alarm window silently, so a courier who tripped the sensor twice before ringing got the alarm. Late motion is now a new walk-up: a new grace period, then the warning steps again if nobody rings.
- The doorbell radio reports a press about 1.5 s after the burst ends, so a press near 15 s arrived too late. Fix: a 2.5 s margin (`flipper.burst_gap_s` + 1).
- The timer now re-arms if it wakes early, and any event ends a grace period that's overdue (a missed timer).
- "Stayed without ringing" is now always suspicious, which would have brought the early confrontation forward at every warning. The early confrontation now needs warning signs in the photo (mask, forcing the door); otherwise the alarm brings the confrontation, as you laid out.
- Tests that passed for the wrong reason were fixed: the QUIET checks now assert the outcome, the baseline merge test matches what the pipeline does, and demo step 2 now checks that the doorbell cancelled the grace period.

**NV:** 5 tests in `integration/tests/test_app.py` assumed an instant warning. The patch is ready: `docs/team/patches/nv-test-app-walk-up-grace.patch` (26/26 pass here). PLAN Q34.
