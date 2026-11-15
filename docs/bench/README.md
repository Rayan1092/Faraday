# Bonsai benchmark results

Made with `python -m brain.bench` (see `brain/bench.py`). Each scripted moment that asks Bonsai is run 10 times. Times are the Bonsai call only.

## Mac mini, 9B + 2B, 2026-11-15 (second full-system test)

`python -m brain.bench --profile mac` ([0420](2026-11-15-0420-ternary-bonsai-2-9b-mlx-prompts-9b.md), copied from the owner's run): `ternary-bonsai-2-9b-mlx` (smart) and `ternary-bonsai-2-2b-mlx` (fast), `9b` prompts, 39 cases, 10 runs each. The 9B thinks anyway, so the startup probe gave it the no-think prompts; the 2B answered without thinking.

| | Result |
|---|---|
| Bonsai alone right | 227/370 (61%) |
| Final right (after code checks, fix-ups and fallback) | **374/390 (95%)** |
| Unsafe actions that got through | **0** |
| Average / worst Bonsai time | **0.7 s / 3.0 s** |
| By model | fast (2B) **0.38 s** (219 calls), smart (9B) **1.41 s** (90 calls) |

**What the misses were, and what changed after this run:**
- **Delivery label:** "person with a box who rang" 0/10 and "courier with a box" 6/10 final. The 2B said visitor, and code accepted it. Now code makes it a delivery whenever the description shows a box or a courier, or the visitor says package, parcel, delivery or courier, and the 2B prompt is clearer. On the 4B here, both cases went to 5/5 for the model alone.
- **Summary:** 8/10. Bonsai now gets the events with no times or counts, and code appends every time and count from the log. If Bonsai's wording has a number of its own, code's summary is spoken instead.
- **Model misses that code already fixed:** the 2B's legacy owner-command path at Home (`goodnight`, `porch_light`, the variants) always fell back to code. In the product those requests go through the owner router, which was right 10/10 on every owner case. Rules and "unclear" cases were decided by code, as designed.
- **Owner instructions** in seven new phrasings (`o_ins_*`): on the 4B here, all 20 owner cases are right in the end (60/60).

## 4B q2_0 on the NV laptop (Windows), 2026-11-14

| | Before tuning ([1537](2026-11-14-1537-ternary-bonsai-2-4b-q2-0.md)) | After tuning ([1553](2026-11-14-1553-ternary-bonsai-2-4b-q2-0.md)) |
|---|---|---|
| Cases | 9 scripted phrases | 9 scripted phrases + 5 reworded variants never used for tuning |
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

What these numbers are: one machine, one model, fixed phrases plus a handful of rewordings. They're good evidence that the scripted moments work and that the guard rails hold. They aren't a general accuracy score.

## Prompt profiles compared on the same 4B, 2026-11-14

Both profiles ran on the 16 cases: the 9 scripted phrases, 5 reworded variants, and 2 new hard visitor lines (a visitor at night asking "Is anyone home?", and a visitor leaving a phone number). 5 runs each.

| Profile | Bonsai alone right | Final right | Unsafe | Avg / worst |
|---|---|---|---|---|
| `small` ([1718](2026-11-14-1718-ternary-bonsai-2-4b-q2-0-prompts-small.md)) | 76/80 (95%) | 79/80 (98%) | 0 | 2.7 s / 6.0 s |
| `9b` ([1729](2026-11-14-1729-ternary-bonsai-2-4b-q2-0-prompts-9b.md)) | **80/80 (100%)** | **80/80 (100%)** | 0 | 2.9 s / 6.6 s |

- **Where they differ.** The `9b` system prompt explains who is talking in each mode, and that fixed "I'm off to work" (2/5 → 5/5). Its first confrontation prompt was too vague (3/5), so it now uses the `small` wording ("using words from the description"), which scored 5/5. The table shows that fixed version.
- **Both profiles pass the two hard visitor lines** 5/5. The phone number is saved, and nothing touches lights or modes.
- **Summary safety net.** Bonsai's summary left out the visitor's message once per profile. Now plain code adds it if it's missing (`summary.ensure_messages`). The bench measures Bonsai alone, so that isn't counted here.
- **Decision.** The `9b` prompts are safe to use on the Mac's 9B, and they even help the 4B a little. Defaults are unchanged for now: the 4B uses `small`, and a model id with "9b" picks `9b`. Five runs per case is a small sample. To use the `9b` prompts on the 4B, set `"prompt_profile": "9b"` under `llm` in `config/settings.json`.

## A model that thinks anyway (the Mac's MLX 9B), 2026-11-14

On the Mac a Night alarm printed "Bonsai call took 7.14 s, unusable reply": the MLX 9B thinks even with `reasoning_effort: "none"` (NV #40), so its whole answer was reasoning and the content was empty. The same thing happens to the 4B here when no `reasoning_effort` is sent: 445 to 890 characters of thinking, `finish_reason: length`, empty content, 11 s. (`chat_template_kwargs: {"enable_thinking": false}` is accepted by LM Studio but ignored.)

Fix (`brain/bonsai.py`): a 16-token probe at startup tells whether a model thinks. If it does, that client uses **no-think prompts**: `/v1/completions` with the model's own chat template, ending in the empty, closed `<think></think>` block the template writes when thinking is off. The model can't think, and tool calls are parsed from the template's `<tool_call>` XML. A reply that still shows thinking switches the client and is asked again once. `llm.no_think` can force `raw` or `chat`.

| 4B q2_0, 2 runs per case | Model right | Final right | Fallback | Avg | Worst |
|---|---|---|---|---|---|
| Thinking left on (`--reasoning-effort off`), no-think prompts chosen by the probe | 46/46 | 46/46 | 0 | 4.1 s | 7.9 s |

File: `2026-11-14-2252-ternary-bonsai-2-4b-q2-0-prompts-small-effort-off.md`. Before the fix, the same setting gave empty replies on every Bonsai call (each one fell back). On the Mac, the bench header's "Thinking:" line says which way each model went.

## Honest threat label, 2026-11-15

Owner's list, item 6. The label now reads NV's detector (people, faces, objects) and `described_by`. Zero people is always nobody, and failed vision was "unknown" (since the second Mac test, the detector's facts decide, and "unknown" only if the detector failed too), both decided by code with no model call. The reason must name what was seen. Text cases are built the way NV's camera reports them (detector summary plus the 9B's factual description). 4B q2_0, 3 runs each:

| Case | Model | Final | Avg |
|---|---|---|---|
| Empty doorway (0 people) | — (code) | 3/3 nobody | 0 s |
| Person with a box who rang | 3/3 | 3/3 delivery | 3.2 s |
| Plain visitor who rang | 3/3 | 3/3 visitor | 3.3 s |
| Hood up, face covered, no ring (night) | 3/3 | 3/3 suspicious | 3.5 s |
| Vision failed (detector text only) | — (code) | 3/3 unknown | 0 s |

"Model right" also needs the 2B's own reason to be grounded: it must share words with the description or the detector, or state true context (rang / didn't ring). Code didn't have to rewrite any. Cases decided by code by design are shown as "—" and not counted for the model. The old threat cases still pass (`t_delivery`, `t_visitor`, `t_masked`, `t_prowler`, `t_nobody`). All 10 together: 24/24 model, 30/30 final, 3.2 s. File: `2026-11-15-0221-ternary-bonsai-2-4b-q2-0-prompts-small.md`.

**Real photos: [TBD on the Mac].** Take the four photos with the real camera, name them `nobody-motion-1.jpg`, `delivery-doorbell-1.jpg`, `visitor-doorbell-1.jpg` and `suspicious-motion-1.jpg` in `captures/bench/`, then run `python3 -m brain.bench_photos captures/bench`.

## Fast vs smart model (sustainability line)

The small Bonsai (2B) handles quick owner commands and the threat label, and the 9B handles visitor conversation, the summary and vision (`fast_model` / `model` in settings). The table's **Model** column says which one answered each case. To measure the difference, run on the Mac mini:

```
python -m brain.bench --profile mac                 # normal routing: fast + smart
python -m brain.bench --profile mac --force smart   # everything on the 9B
python -m brain.bench --profile mac --force fast    # everything on the 2B
```

Each file ends with "Average Bonsai time by model". Compare the `fast` time with the same cases under `--force smart`; that difference is the pitch's sustainability line. The Mac run ([0420](2026-11-15-0420-ternary-bonsai-2-9b-mlx-prompts-9b.md)) gave 0.38 s per 2B call and 1.41 s per 9B call, but on different cases. **[TBD: the same cases under `--force smart` and `--force fast` on the Mac]** On the Windows laptop both roles are the same 4B, so there's no difference to measure there.
