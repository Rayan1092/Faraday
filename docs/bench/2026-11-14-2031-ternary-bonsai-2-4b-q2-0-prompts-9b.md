# Bonsai benchmark: ternary-bonsai-2-4b@q2_0

- Date: 2026-11-14T20:31-05:00
- Smart model: `ternary-bonsai-2-4b@q2_0`, fast model: `ternary-bonsai-2-4b@q2_0` (LM Studio profile `nv_backup`, reasoning_effort `none`, prompts `9b`)
- Machine: Windows 11 (AMD64)
- Runs per case: 3
- Command: `python -m brain.bench --only i_delivery,i_visitor,doorbell_greeting --runs 3 --prompts 9b`

**Model right** = Bonsai itself returned the right action, with no code correction. **Final right** = what Faraday actually does after the code checks, fix-ups and fallback rules. **Unsafe** = a mode change while armed (or to Home) got through. It must be 0.

| Case | Model | Model right | Final right | Unsafe | Fallback | Avg | Worst |
|---|---|---|---|---|---|---|---|
| Away: photo after a doorbell (greet, offer a message) | smart | 2/3 | 3/3 | 0 | 1 | 3.9 s | 6.8 s |
| Instruction, away delivery: owner said "ask them to leave it by the side door" | smart | 3/3 | 3/3 | 0 | 0 | 4.2 s | 4.5 s |
| Instruction, away doorbell: owner said "tell them I'm in the back garden" | smart | 3/3 | 3/3 | 0 | 0 | 5.9 s | 6.2 s |
| **All** | | **8/9 (88%)** | **9/9 (100%)** | **0** | | **4.7 s** | **6.8 s** |

Average Bonsai time by model: smart 4.99 s (8 calls)

Raw results for every run are in the matching `.json` file.
