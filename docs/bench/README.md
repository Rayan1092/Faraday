# Bonsai benchmark results

Made with `python -m brain.bench` (see `brain/bench.py`). Each demo moment that asks Bonsai is run 10 times. Times are the Bonsai call only. On the Mac mini, rerun with `python -m brain.bench --profile mac` and add the file here.

## 4B q2_0 on the NV laptop (Windows), 2026-11-14

| | Before tuning ([1537](2026-11-14-1537-ternary-bonsai-2-4b-q2-0.md)) | After tuning ([1553](2026-11-14-1553-ternary-bonsai-2-4b-q2-0.md)) |
|---|---|---|
| Cases | 9 demo phrases | 9 demo phrases + 5 reworded variants never used for tuning |
| Bonsai alone right | 55/90 (61%) | **136/140 (97%)** |
| Final right (after code checks, fix-ups and fallback) | 67/90 (74%) | **139/140 (99%)** |
| Unsafe actions that got through | 0 | **0** |
| Average / worst Bonsai time | 3.1 s / 7.3 s | **2.5 s / 5.7 s** |
| Variants only | n/a | 47/50 Bonsai alone, 50/50 final |

**Note on the baseline summary row.** In the baseline run, the summary check wanted the literal word "message", which was too strict. With the final check, which accepts any mention of the visitor's message and still rejects invented counts, the baseline summary scores 5/10. That makes the baseline totals 60/90 (67%) model-right and 72/90 (80%) final-right.

**What changed between the two runs:**
1. **Arming tools.** `set_mode` is shown to Bonsai as two tools with no arguments, `arm_away` and `arm_night`. With a `mode` argument the 4B wrote "home" for "I'm heading out" 10 times out of 10, so there was nothing to type any more. This was the biggest fix.
2. **Tool descriptions spell out the trigger words.** For example, arm_away is "'heading out', 'leaving', 'going out', 'bye'". `add_rule` is limited to "when / whenever / from now on" sentences, and `save_message` is "use it whenever a visitor gives any information".
3. **Prompts.** The confrontation prompt asks it to name clothing or what the person carries. The summary prompt asks it to always mention visitor messages.
4. **Code fix-ups**, each recorded and counted as "model wrong" in the benchmark:
   - If the owner clearly says leaving or bedtime and Bonsai arms the other mode, code goes with the words. That happened 3 of 10 times for "I'm off to work".
   - If Bonsai says "I'll take that message" but doesn't save it, code saves the visitor's words. That happened 0 times after tuning.

**Still imperfect:** 1 of 10 confrontation lines didn't mention what the camera saw ("Please leave, stranger, before I lock you out of this house."). Nothing unsafe got through in any run, before or after.

What these numbers are: one machine, one model, fixed phrases plus a handful of rewordings. They're good evidence that the demo moments work and that the guard rails hold. They aren't a general accuracy score.
