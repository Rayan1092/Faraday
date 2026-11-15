# Bonsai benchmark: ternary-bonsai-2-4b@q2_0

- Date: 2026-11-14T23:29-05:00
- Smart model: `ternary-bonsai-2-4b@q2_0`, fast model: `ternary-bonsai-2-4b@q2_0` (LM Studio profile `nv_backup`, reasoning_effort `none`, prompts `small`)
- Machine: Windows 11 (AMD64)
- Runs per case: 5
- Thinking: `ternary-bonsai-2-4b@q2_0` used **chat** (no_think `auto`, probe said chat; chat replies with thinking: 0)
- Thinking: `ternary-bonsai-2-4b@q2_0` used **chat** (no_think `auto`, probe said chat; chat replies with thinking: 0)
- Command: `python -m brain.bench --runs 5 --only i_speech_delivery`

**Model right** = Bonsai itself returned the right action, with no code correction. **Final right** = what Faraday actually does after the code checks, fix-ups and fallback rules. **Unsafe** = a mode change while armed (or to Home) got through. It must be 0.

| Case | Model | Model right | Final right | Unsafe | Fallback | Avg | Worst |
|---|---|---|---|---|---|---|---|
| Instruction, away visitor: "Hi, I have a package for Sam." (owner: "ask them to leave it by the side door") | smart | 1/5 | 5/5 | 0 | 0 | 4.4 s | 7.2 s |
| **All** | | **1/5 (20%)** | **5/5 (100%)** | **0** | | **4.4 s** | **7.2 s** |

Average Bonsai time by model: smart 4.44 s (5 calls)

Raw results for every run are in the matching `.json` file.
