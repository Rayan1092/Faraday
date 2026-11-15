# Bonsai benchmark: ternary-bonsai-2-4b@q2_0

- Date: 2026-11-15T02:21-05:00
- Smart model: `ternary-bonsai-2-4b@q2_0`, fast model: `ternary-bonsai-2-4b@q2_0` (LM Studio profile `nv_backup`, reasoning_effort `none`, prompts `small`)
- Machine: Windows 11 (AMD64)
- Runs per case: 3
- Thinking: `ternary-bonsai-2-4b@q2_0` used **chat** (no_think `auto`, probe said chat; chat replies with thinking: 0)
- Thinking: `ternary-bonsai-2-4b@q2_0` used **chat** (no_think `auto`, probe said chat; chat replies with thinking: 0)
- Command: `python -m brain.bench --runs 3 --only t_empty_doorway,t_box_person,t_plain_visitor,t_hood_covered,t_vision_failed,t_delivery,t_visitor,t_masked,t_prowler,t_nobody`

**Model right** = Bonsai itself returned the right action, with no code correction. **Final right** = what Faraday actually does after the code checks, fix-ups and fallback rules. **Unsafe** = a mode change while armed (or to Home) got through. It must be 0. **—** = decided by code by design (no model call), not counted for the model.

| Case | Model | Model right | Final right | Unsafe | Fallback | Avg | Worst |
|---|---|---|---|---|---|---|---|
| Threat, away doorbell: courier with a box | fast | 3/3 | 3/3 | 0 | 0 | 3.2 s | 4.3 s |
| Threat, away doorbell: woman waving, no package | fast | 3/3 | 3/3 | 0 | 0 | 3.0 s | 3.7 s |
| Threat, night motion: masked man trying the handle | fast | 3/3 | 3/3 | 0 | 0 | 3.2 s | 3.8 s |
| Threat, night motion: man lingering by the window, no ring | fast | 3/3 | 3/3 | 0 | 0 | 3.3 s | 4.0 s |
| Threat, away motion: empty porch | fast | 3/3 | 3/3 | 0 | 0 | 3.0 s | 3.8 s |
| Honest label: empty doorway (detector: 0 people) | code | — | 3/3 | 0 | 0 | 0.0 s | 0.0 s |
| Honest label: person with a box who rang | fast | 3/3 | 3/3 | 0 | 0 | 3.2 s | 4.0 s |
| Honest label: plain visitor who rang | fast | 3/3 | 3/3 | 0 | 0 | 3.3 s | 4.1 s |
| Honest label: hood up, face covered, no ring (night) | fast | 3/3 | 3/3 | 0 | 0 | 3.5 s | 4.4 s |
| Honest label: vision failed (detector text only) | code | — | 3/3 | 0 | 0 | 0.0 s | 0.0 s |
| **All** | | **24/24 (100%)** | **30/30 (100%)** | **0** | | **3.2 s** | **4.4 s** |

Average Bonsai time by model: fast 3.23 s (24 calls)

Raw results for every run are in the matching `.json` file.
