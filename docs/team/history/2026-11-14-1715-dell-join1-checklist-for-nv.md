# Join 1 checklist for NV (from Dell)

- **For:** NV, who runs all real-hardware tests (the Flipper is on the NV laptop). Dell won't run these.
- **Goal (PLAN.md Join 1):** real radio into Dell's code. Real doorbell presses and motion waves become events in the log, and `modes/` reacts. This also covers lights, LED and rules by voice.
- **How to check each step:** the dashboard, plus `python -m log.check logs/join1-events.jsonl` (new in this PR). It prints counts per type and source, likely duplicates (same sensor event less than 3 s apart), whether the passphrase leaked into the log (must be 0), and the last entries.
- **Please write results** (pass/fail per step, doorbell hit rate) into a Join 1 handoff note.

## Setup
1. Close qFlipper. Leave the Flipper on its main screen with no app open, and plug it in by USB.
2. Lamps go into outlet 1 (porch light) and outlet 2 (living room lamp), with each lamp's own switch ON. Doorbell (new battery) and motion sensor go close to the Flipper.
3. Start with a separate log, a stand-in camera, and speech printed instead of spoken. That keeps Join 1 about the radio:
   `py -m integration --standin-camera --no-mic --print-speech --log logs/join1-events.jsonl`
   **Pass:** the dashboard opens showing HOME in green, and the Flipper LED is green.

## Radio into the software (the core of Join 1)
4. **Hold the doorbell 1 to 2 s, three times, about 5 s apart.**
   **Pass:**
   - exactly 3 `doorbell` events from `flipper` (`log.check` counts),
   - "Someone is at the door." printed each time,
   - an orange "DOORBELL PRESSED" banner.

   Write down the hit rate (x of 3).
5. **Wave once at the motion sensor.**
   **Pass:** exactly 1 `motion` event (about 17 packets), no alert, no lights. Home ignores motion.

## Modes react, lights and LED
6. Click **Away** on the dashboard. **Pass:** LED blue.
7. **Wave once.**
   **Pass:**
   - warning (LED and dashboard yellow),
   - the **real porch lamp turns on**,
   - stand-in photo,
   - "You're on camera. The owner has been alerted." printed.
8. **Wave again within 60 s.**
   **Pass:**
   - alarm (LED red, 3 buzzes),
   - the **living room lamp turns on**,
   - the alarm line printed,
   - then one Bonsai confrontation line a few seconds later (if LM Studio is up).
9. **Type the passphrase in the dashboard's Home box.**
   **Pass:** LED green, alert back to normal, "DISARMED BY PASSPHRASE" banner.
10. In the app console: `t turn off the porch light`, then `t turn off the living room lamp`.
    **Pass:** both lamps go off. That tests switching outlets off.

## Night and rules by voice
11. Click **Night**, then `t When there's motion at night, turn on the living room lamp too.`
    **Pass:** the rule appears on the dashboard.
12. **Wave once.**
    **Pass:** porch lamp **and** living room lamp on (the second one from the rule), LED yellow, no speech.
13. **Hold the doorbell.**
    **Pass:** porch light on, stand-in photo, no speech, "DOORBELL PRESSED" then "DOORBELL AT NIGHT".

## Robustness
14. **Unplug the Flipper USB for 5 s, then plug it back in.**
    **Pass:** reconnects within about 10 s, and the LED colour comes back.
15. **Wave once more.** **Pass:** the motion event still arrives after the reconnect.
16. **Press "Ring doorbell" on the dashboard** (the stage backup).
    **Pass:** the same flow as a real press, logged as `source: dashboard`.

## At the end
- `python -m log.check logs/join1-events.jsonl --last 40`
- Doorbell and motion counts should match what you did. Duplicates should be 0. The passphrase in the log must be 0.
- Clear the test rule on the dashboard (✕) before the next run.
