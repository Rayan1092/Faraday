# Open questions from Dell (written while the owner was away)

Named `QUESTIONS-dell.md`, because NV's open PRs add their own `QUESTIONS.md`. That way neither merge conflicts.

**All 11 approved by the owner on 2026-11-14.** Each one has the default I used.

1. ✅ **Offline badge check.** It asks the OS for a route to the reserved test address 192.0.2.1 (and 2001:db8::1) with a UDP connect. That sends **no packets**. No route means OFFLINE, and any route means a red "ONLINE: turn Wi-Fi off". *Default:* this check. A LAN cable with no internet would still show ONLINE, which is the safe side for the demo.
2. ✅ **`describe_photo` added in `brain/vision.py`** (PLAN.md seam 4), because NV needs it for Join 2. It reads the optional `llm.vision_model` setting if it's present. *Default:* not added to `settings.example.json`, because config changes wait for you.
3. ✅ **Arming is shown to Bonsai as `arm_away` / `arm_night`** (no arguments) instead of `set_mode`. With an argument, the 4B wrote "home" 10 times out of 10. The action in the log and the formats is still `set_mode`. *Default:* keep it. FORMATS.md doesn't change.
4. ✅ **Code corrects an obvious arm mix-up.** If the owner says "heading out" and Bonsai picks night, code switches it to away. *Default:* on. The benchmark counts it as "model wrong", so the numbers stay honest.
5. ✅ **"I'll take that message" without saving.** If Bonsai promises to take a message and doesn't save it, code saves the visitor's words. *Default:* on, also counted as "model wrong" in the benchmark.
6. ✅ **Demo-mode files.** `python -m sim --demo` uses `logs/demo-events.jsonl` and `logs/demo-events-rules.json` and wipes them at the start of every run. *Default:* separate files, so the real log is never touched.
7. ✅ **Repeated porch-light sends.** The baseline sends `outlet_on porch_light` on every armed motion, even when it's already on. Each send makes the Flipper deaf for about 1.2 s (NV's numbers). *Default:* left as is, and the seams note tells NV they can skip repeats in `integration/`. Say if you'd rather the pipeline skipped them.
8. ✅ **PR #5 (NV's Flipper driver) was merged while I worked.** I merged main into `dell/polish`. NV's 28 tests pass here with a stand-in `serial` module, because pyserial isn't installed on this laptop. The radio numbers in the README and submission docs come from PR #5 and NV's handoff note. *Default:* written as "tested on a Windows laptop", with the Mac mini still [TBD].
9. ✅ **OpenCV wasn't installed on this laptop, and installing it was blocked** (the auto-mode classifier denied the pip install). `describe_photo` shrinks photos with OpenCV only if it's there. That path is tested with a stand-in module but hasn't been run for real. *Default:* fine, because `requirements.txt` already lists `opencv-python`. Just run `pip install -r requirements.txt` on the Mac.
10. ✅ **Vision is built twice.** NV's PR #7 does camera → Bonsai vision → `photo_described` in `hardware/vision.py` (shrinks to 512 px, live-tested at 3.3 s), already wired into `py -m integration`. I wrote the same seam as `brain/vision.py` (PLAN.md seam 4) before seeing that. *Default:* integration uses NV's. `brain/vision.py` stays as an unused duplicate for now, and one of them gets deleted after the demo. The seams note says so.
11. ✅ **Push-to-talk button (NV's question 3).** Added a "Talk" button to the dashboard. It calls `board.talk_hook()` if integration sets one, and stays hidden otherwise. *Default:* one press calls the hook, and integration decides whether that starts or stops recording.


## Round 2: after the Mac full-system test (2026-11-14 night, owner away)

Each one has the default I used. Answer inline and I'll adjust.

12. **What may an owner ask for while the house is armed?** You said wake requests change nothing except through the passphrase. *Default:*
    - At **Night**, "who's at the door?" still answers, because it changes nothing and the owner may be inside.
    - At **Away**, nothing does: anyone at the door can say "Hey Faraday", so not even the summary (it would read the house log to a stranger).
    - Everything else gets "The system is armed. Say the passphrase to make changes." "Disarm" gets "Please say the passphrase to disarm."
13. **Typed text (`channel: null`)** keeps the old meaning: the owner at Home, a visitor while armed. *Default:* yes, so the dashboard text box and the sim behave as before.
14. **"If a delivery comes, leave it by the side door"** (no "ask them to") is now accepted as an instruction, the same as "ask them to leave it...". A request that stops on a dangling word ("tell visitors that") is refused and the owner is asked to repeat. *Default:* on.

15. **A courier who says nothing.** The doorbell greeting offers to help with a delivery, but the owner's delivery instruction ("leave it by the side door") is said only once the visitor speaks. You said "if nobody answers, stay quiet". *Default:* quiet. The other choice is to say the delivery instruction when the photo's label is `delivery` and nobody has spoken after 8 s.
16. **Doorbell greeting, not always.** No friendly greeting while an alert is on (warning or alarm), and no second greeting for another press within 60 s. The photo still happens every time. *Default:* on.

17. **Night warning is spoken now.** Your walk-up rule says "speak the warning" when no doorbell comes; before, a Night warning was silent (porch light + photo). *Default:* spoken in both modes, like the alarm line (item 8).
18. **A mode change starts a new visit.** A doorbell in Away followed by switching to Night no longer keeps Night motion quiet for 120 s, and a pending warning is dropped (the alert itself stays: only the passphrase lowers it). *Default:* on.
19. **Motion during an alarm** turns on both lights again but doesn't repeat the alarm line or take another photo. *Default:* on (before, every motion during an alarm repeated the alarm line).

20. **A courier who doesn't ring while armed.** Following your rule ("stayed at the door without ringing = suspicious"), someone with a box who never rings gets the warning after the grace period and the label suspicious. One who rings is a delivery. *Default:* as you wrote it.
