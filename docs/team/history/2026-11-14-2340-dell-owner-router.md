# Dell: owner vs visitor routing (2026-11-14 23:40)

Owner's list after the Mac test, item 2. It uses NV's `speech_heard.channel` from #41 (Q29, now confirmed).

**Owner requests** (`channel: "owner"`, or untagged speech at Home) go to `brain/owner.py`. The visitor conversation never sees them.
- **Intents:** `arm_away`, `arm_night`, `passphrase`, `summary`, `look`, `add_rule`, `add_instruction`, `light`, `unclear`.
- **Who decides:** the 2B picks one through a forced-choice `route` tool. Code then checks it:
  - a clear keyword match wins;
  - an intent with nothing in the words to back it is refused;
  - a light needs to know which one and on or off.
- **Doing it:** `Pipeline._owner_request` runs the intent in plain code and answers with a fixed line from `brain/lines.py`.
- **Unclear:** "Sorry, I didn't catch that." A cut-off request ("if a delivery comes, ask them to"), or an instruction or rule that can't be built, gets "Sorry, I missed the end of that. Please say it again."
- **Armed:** nothing changes. At Night, "who's at the door?" still answers. Everything else gets "The system is armed. Say the passphrase to make changes." The passphrase itself is handled earlier by `modes/` as before.
- **Never a visitor message:** owner speech never reaches `save_message`. In `decide()` the owner channel has no `save_message` tool, and the fallback never says "message saved" unless a message really was saved.

**Visitor speech** (`channel: "visitor"`) gets the visitor tools (talk, save a message) in every mode. A visitor at Night can no longer add rules or switch lights, and a visitor at Home no longer gets the owner's tools.

**Instructions:** "If a delivery comes, leave it by the side door" (no "ask them to") is accepted now. Cut-off requests are refused.

**Bench:** 13 owner cases (`o_*`), 4B with 3 runs each: 36/39 picked right by the 2B alone, 39/39 final, 2.7 s average. The only misses: the 4B calls the cut-off sentence an instruction, and code turns it into "please repeat".

**Also in this PR:** review fixes for #42 in `brain/bonsai.py`:
- inline `<think>` is now seen;
- a call waits for a probe that's still running;
- a slow retry keeps raw;
- tool values are typed by the schema;
- a finished answer is kept;
- `IncompleteRead` is caught;
- a null `no_think` means auto.

**Questions:** `QUESTIONS-dell.md` items 12 to 14.
