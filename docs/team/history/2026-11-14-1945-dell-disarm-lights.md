# Lights off on disarm (Dell, item 3)
- Laptop / branch / PR: Dell / `dell/disarm-lights` / PR "lights off on disarm"
- What changed: `rules/baseline.py` has a new `lights_off_on_disarm()`. On a passphrase `mode_changed`, it reads the log and sends `outlet_off` (source `rules`) for every outlet whose last command was an `outlet_on` from the rules or the brain. The pipeline emits those like any baseline action, so they're logged, NV's executor switches the lamps, and the dashboard follows. Lights the owner turned on from the dashboard stay on, and lights already off aren't resent. There's a note in the PLAN.md mode table.
- How it was tested: 340 tests pass. 4 new tests, including the full alarm → passphrase flow through the pipeline. There's no Bonsai in this step, so no bench case.
- Next: item 4 (real light switches on the dashboard).
