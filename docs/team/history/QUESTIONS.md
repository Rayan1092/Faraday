# Open questions for the owner (written while you were away)

Each one has the default I used. Change it and I'll follow.

1. **Offline badge check.** It asks the OS for a route to the reserved test address 192.0.2.1 (and 2001:db8::1) with a UDP connect. That sends **no packets**. No route means OFFLINE, and any route means a red "ONLINE: turn Wi-Fi off". *Default:* this check. A LAN cable with no internet would still show ONLINE, which is the safe side for the demo.
2. **`describe_photo` added in `brain/vision.py`** (PLAN.md seam 4), because NV needs it for Join 2. It reads the optional `llm.vision_model` setting if it's present. *Default:* not added to `settings.example.json`, because config changes wait for you.
3. **Arming is shown to Bonsai as `arm_away` / `arm_night`** (no arguments) instead of `set_mode`. With an argument, the 4B wrote "home" 10 times out of 10. The action in the log and the formats is still `set_mode`. *Default:* keep it. FORMATS.md doesn't change.
4. **Code corrects an obvious arm mix-up.** If the owner says "heading out" and Bonsai picks night, code switches it to away. *Default:* on. The benchmark counts it as "model wrong", so the numbers stay honest.
5. **"I'll take that message" without saving.** If Bonsai promises to take a message and doesn't save it, code saves the visitor's words. *Default:* on, also counted as "model wrong" in the benchmark.
6. **Demo-mode files.** `python -m sim --demo` uses `logs/demo-events.jsonl` and `logs/demo-events-rules.json` and wipes them at the start of every run. *Default:* separate files, so the real log is never touched.
7. **Repeated porch-light sends.** The baseline sends `outlet_on porch_light` on every armed motion, even when it's already on. Each send makes the Flipper deaf for about 1.2 s (NV's numbers). *Default:* left as is, and the seams note tells NV they can skip repeats in `integration/`. Say if you'd rather the pipeline skipped them.
8. **PR #5 isn't merged yet.** I only read it, so the radio numbers in the README and the submission docs come from its description and NV's handoff note. *Default:* written as "tested on a Windows laptop", with the Mac mini still [TBD].
