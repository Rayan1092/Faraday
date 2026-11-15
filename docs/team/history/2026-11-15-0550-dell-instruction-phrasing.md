# Dell: owner instructions in any phrasing (2026-11-15 05:50)

Owner's list from the second Mac test, item 5: "If a delivery comes, I want you to tell him to leave the package by the side door" got "Sorry, I missed the end of that". "Let the 2B extract it from natural speech, code validates. Add varied phrasings to the benchmark."

**Why it failed:** the keyword parser wanted "ask/tell" right after the comma. When it failed, the router marked the request as cut off, and the owner heard "Sorry, I missed the end of that".

**Fix, in two layers:**
1. **Keywords.** They now accept a lead-in before the verb: "I want you to", "I'd like you to", "can/could/would/will you (please)", "make sure to", "please". A trailing "?" no longer ends up in the request. The Mac test sentence parses with no model call: "Please leave the package by the side door."
2. **The 2B.** When the keywords can't read it, and the 2B or the keywords say it's an instruction, and the owner names who it's for or says tell/ask, and it isn't cut off, the 2B pulls it out with a forced `instruction(audience, request)` tool. Code checks the answer (`rules/instructions.from_model`):
   - the audience must be backed by the owner's words; otherwise it's the one the words name, and with no audience word at all it's refused;
   - the request is complete (3 to 120 characters, not ending in "to" or "the");
   - at least 75% of its words come from what the owner said, so the model can't invent an instruction;
   - the store still refuses anything beyond words (lights, modes, the alarm, locks, codes).
   - "A piece of paper." can never become an instruction, and a real cut-off still gets "please say it again".
- **Pipeline:** it stores what the router read, instead of parsing again.

**Bench:** 7 new owner cases in varied phrasings (`o_ins_*`). Each checks the stored audience and the words of the request.

**4B on this laptop, 3 runs, all 20 owner cases:** 60/60 final (100%), 54/60 by the model alone, 0 unsafe, 3.0 s average. All 7 phrasings are right in the end. The bench also caught a router bug: when the words clearly said add_instruction and the 2B agreed, a second weak-keyword check still rejected it (it missed plurals like "couriers"). Now an agreement with the plain words is accepted. The weak words also cover plurals, Amazon/FedEx/UPS and drivers.

**Tests:** `brain/tests/test_instruction_phrasing.py`: the Mac sentence, lead-ins, still cut off and still refused, the 2B's answer checked, invented or bad answers refused, the owner's words decide the audience, the router asking the 2B, never from a random sentence, a real cut-off asked again.
