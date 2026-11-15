# Dell: one calm voice for every fixed line (2026-11-15 03:10)

Owner's list after the Mac test, item 5: "Rewrite every fixed spoken line in one tone: a calm, professional home security assistant. Not funny, not stiff, short."

**Where the lines live:** `brain/lines.py` has every fixed line. Modules import it: fallback, core (mode replies, confirmations), look, pipeline (refusals, "Checking the log.", forget lines). The doorbell greeting, the Home announcement, and the warning and alarm lines stay in `rules/baseline.py`, because `rules/` can't import `brain/`; `lines.py` lists them too. `python -m brain.lines [--json]` prints them all.

**The owner's lines, as given:**
- Away: "Away mode is on. I'll keep watch."
- Night: "Night mode is on. Good night."
- Disarm: "Welcome home. The system is disarmed."
- Doorbell: "Hello. No one can come to the door right now. I can take a message or help with a delivery."
- Message saved: "Thank you. I'll pass that along."
- Warning: "Please be aware this property is monitored and you are on camera."
- Alarm: "This property is protected. You are being recorded and the owner has been notified. Please leave now."
- Refusal: "I'm sorry, I can't do that. Only the owner can disarm the system."

**The rest, in the same tone:**
- "Someone is at the front door." (Home announcement)
- "Checking the camera.", "Checking the log."
- "Understood. The rule is saved."
- "Understood. If a delivery comes, I'll ask them to ..."
- "Understood. I've removed the delivery instruction." / "There's no instruction to remove."
- "I'm sorry, I can't help with that."
- "Please leave the package by the front door. Thank you." (default drop-off)
- "No one is there." (label words)
- the light lines ("The porch light is on.")
- code's confrontation fallback: "I can see ... Please leave now."

**Behaviour change:** while armed, a "Hey Faraday, turn off the alarm / disarm" request now gets the refusal line, not "Please say the passphrase to disarm." That's safer, because it never hints to whoever is at the door that a passphrase exists, and it's what video shot 7 shows.

**Prompts:** Bonsai's own lines are now asked for as "a calm, professional home security assistant: one short sentence, never jokes" (both prompt profiles).

**NV's voice cache:** `integration/fixed_lines.py` finds all 35 lines in `FIXED_LINES` and none of the old ones. The startup pre-render renders them, keyed by text. NV's `test_dells_fixed_lines_are_found` names three old lines, so a patch is ready: `docs/team/patches/nv-test-tts-final-lines.patch` (16/16 pass). PLAN Q35.

**Tests:** about 10 tests switched to the final wording. My suite is green; NV's one pinned test is pending their patch.
