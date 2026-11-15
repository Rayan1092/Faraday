# Dell: no spoken line describes anyone; help line; full camera frame (2026-11-15 05:20)

Owner's list from the second Mac test, items 7 to 11. The Mac test ran main from before "calm voice lines" (#61), so its log still had the old lines ("Alarm. You are being recorded. Leave now.", "Please say the passphrase to disarm.", "Welcome home. Faraday is disarmed."). #61 had already replaced them.

**Item 7, spoken lines never describe the person.**
- **Alarm:** the alarm's Bonsai line is gone (`Pipeline._confront_later`, `Brain.confront`, the `confront` prompts, `lines.confront_line`, the bench case). So is the early one for a suspicious label. The fixed line is the only thing said: "This property is protected. You are being recorded and the owner has been notified. Please leave now." It never says "Alarm".
- **"Who's at the door?":** it answers from facts only, the detector's count plus the label: "One person is at the front door. It looks like a delivery.", "No one is at the front door.", or "Someone is at the front door. …" when there's no count. The description stays on the dashboard and in the log. QUESTIONS #23 has the alternative.

**Item 8.** "Please say the passphrase to disarm" had already gone in #61, and nothing spoken mentions a passphrase. A test checks every fixed line for "passphrase" and "pass phrase".

**Item 9.** Unknown owner requests get "Sorry, I can't help with that. You can ask who's at the door, what happened, or arm the house." While armed, an unclear request, which includes a wrong passphrase, gets the neutral "The system is armed. I can't make changes right now." (QUESTIONS #24).

**Item 10.** The camera on the dashboard and on `/controls` now uses `object-fit: contain` (full frame, letterboxed on black), and so do the thumbnails. NV's own reference page on :8081 still crops; that's PLAN Q41.

**Item 11.** `FIXED_LINES` has the new lines. NV: re-render, and apply `docs/team/patches/nv-test-rehearsal-facts-only.patch` (PLAN Q41).

**Review fixes:**
- **Suspicious label:** a suspicious label that raises the warning (a mask, trying the handle) now speaks the fixed warning line, as when nobody rang. Before, that warning was silent once the early confrontation went.
- **System prompts:** both now say "Never describe the person, their clothes or what they carry."
- **Two more description paths:** the summary's facts ("The camera saw: …") and photo descriptions in the visitor prompt's history. Both are fixed in Dell PR "delivery + summary" (#69).
- **Optional, not done:** the "who's at the door?" answers aren't pre-rendered, so the first one waits for Argmax TTS.

**Tests:**
- **Alarm:** says only the fixed line (Away and Night), and Bonsai is never asked about the photo.
- **Suspicious label:** says nothing about the person.
- **"Who's at the door?":** facts only, never the clothing or the box.
- **Lines:** the new help line, nothing about a passphrase, no prompt left for a confrontation.
- **NV's rehearsal:** passes with the patch.
