# Dell: the grace timer fires by itself, 15 s after the wave (2026-11-15 05:30)

Owner's list from the second Mac test, item 1: "Motion grace is not a timer. Log: motion at 03:48:52 gave no warning at 03:49:07; the warning came at 03:49:13 exactly when the next motion arrived."

**What really happened:** the timer did fire by itself, but late.
- NV's radio stamps each event with its first packet and reports it after the burst, 3 to 4 s later. The motion stamped 03:48:52 was handled at 03:48:55; its porch light is logged then.
- The grace counted from that moment, plus a 2.5 s doorbell margin: 03:48:55 + 17.5 s = 03:49:12.5. That's when the warning came. It lined up with the next wave by chance.

**Fix:**
- **From the first packet:** the grace counts from the motion's own `timestamp`. A report delay under 0.5 s or over 10 s counts as now (clock quirks, sim, tests).
- **No margin:** `doorbell_settle_s` now defaults to 0, and `alerts.doorbell_settle_s` can still add one. The warning comes 15 s after the wave. A ring pressed in the last 2 s or so can arrive after the warning; that visitor still never gets the alarm, because a doorbell after the warning blocks it.
- **Timer:** it is started with what's left of the grace.
- **Quiet time:** `visitor_quiet_s` drops from 120 to 60 s. It's also 60 in `config/settings.example.json`, CLAUDE.md, FORMATS.md and PLAN.md.
- **Console lines:** every motion prints why. `[motion] porch light on; warning in 15 s unless the doorbell rings`, `[motion] ignored: a visitor rang 20 s ago (motion stays quiet for 60 s after a doorbell)`, `[motion] ignored: already waiting for a doorbell (warning in 10 s)`, `[motion] ignored: the same wave, 3 s after the warning (...)`, `[motion] ignored: the alarm is already on`, `[motion] ignored: Home mode (...)`. Also `[motion] no doorbell within 15 s: warning`, and `[doorbell] a visitor: the pending warning is cancelled, motion stays quiet for 60 s`.

**On the Mac:** if `config/settings.json` has `"visitor_quiet_s": 120` under `alerts`, change it to 60 (or delete it). The file isn't in git, so the new default can't reach it.

**Tests:**
- `brain/tests/test_grace_timer.py` (real timer, real clock): the warning fires with no other event, counts from the first packet, a doorbell in time stops it, and ignored motion is printed with the reason.
- `modes/tests/test_modes.py`: 15 s from the first packet (the owner's 03:48:52 case), odd timestamps, 60 s quiet (waves at 80 and 117 s now count), and every reason.
- NV's `test_app` and rehearsal tests pass unchanged.
