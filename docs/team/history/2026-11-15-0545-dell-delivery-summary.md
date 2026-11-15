# Dell: code's delivery reply; a summary whose times and counts come from the log (2026-11-15 05:45)

Owner's list from the second Mac test, items 4 and 6.

**Item 4, the delivery reply.** With no owner instruction, "Hi, I have a package, where should I leave it?" got "I can take a picture of the package and save it for you".
- **The reply is now code's** when a visitor talks about a delivery (or follows a delivery label). Bonsai's own lines are dropped:
  - no instruction: "You can leave it by the door. I'll let the owner know it arrived."
  - with one: the owner's words, then the same promise: "Please leave it by the side door. I'll let the owner know it arrived."
- **The visitor's words are saved** for the owner if Bonsai didn't save them.
- **Repeats:** a second delivery mention in the same visit gets "Thank you. I'll pass that along."
- **Who gets it:** never the owner (the "owner" channel, or untagged speech at Home). It works in the fallback path too.
- **Bench scoring:** counted against the model only if Bonsai missed the message or the owner's words. Just rewording isn't a correction.

**Item 6, the summary.** It said "the alarm went off at 03:40"; the log said 03:49. The facts sent to Bonsai had the arming time but no alarm time.
- **Facts carry the log's exact times:** the arming, the rings, the warning, the alarm. Counts are the log's, and one label per photo (a relabel replaces the old one, so a visit isn't counted twice).
- **Bonsai only phrases them.** `summary.verified()` accepts its text only if every time is one of the log's, every number is one of its counts (or appears in a visitor's message), and it mentions the alarm exactly when there was one.
- **Otherwise code's summary is spoken**, built from the same facts: "Since you armed the house at 03:42: the doorbell rang 3 times, last at 03:45, the alarm went off at 03:49, 1 delivery, 1 message. Latest message: …"
- **Never what the camera saw.** The facts no longer carry descriptions (item 7: spoken lines never describe the person), and Bonsai's visitor prompt no longer sees photo descriptions or label reasons. A photo shows as "a photo", a label as its category.

**Bench.**
- **Summary case:** it uses real facts with times. The model is right if its own text passes `verified()`; final is what Faraday says.
- **New case:** `i_delivery_no_instruction`.
- **4B on this laptop, 5 runs:** summary model 3/5, final 5/5; delivery cases final 5/5 each; 30/30 final, 0 unsafe.

**Tests:**
- `brain/tests/test_delivery_reply.py`: no instruction, with an instruction, the fallback, never the owner, and the prompt never seeing a description.
- `brain/tests/test_summary.py`: the 03:40 vs 03:49 case, invented counts, a missing alarm, an alarm that never happened, a relabelled photo counted once.
- Older tests now use non-delivery messages where they test something else.
