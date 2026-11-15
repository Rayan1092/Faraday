# NV: vision without thinking (2026-11-15 04:30)

Owner's Mac diagnosis: `ternary-bonsai-2-9b-mlx` ignores `reasoning_effort: "none"` for images. At max_tokens 768 it thought for 20 s (3245 chars), stopped with finish `length`, and gave an empty answer. The owner asked to turn thinking off instead of raising the cap, cap max_tokens at about 120, measure the 4B, aim for one factual sentence in under 5 s, give one command to verify, and check Dell's brain calls.

## What works, measured

This laptop's LM Studio has Bonsai 2 4B (`ternary-bonsai-2-4b@q2_0`, GGUF, takes images). Like the Mac's 9B, it thinks by default: with nothing switched off, it ran out of tokens at 120 with an empty answer. Each method, sent alone (`scratchpad nothink_experiment.py`):

| Method, alone | Result |
|---|---|
| `/no_think` in the system and user text | thinks |
| system instruction "answer at once, no reasoning" | thinks |
| `chat_template_kwargs: {"enable_thinking": false}` | ignored, thinks |
| top-level `enable_thinking: false` | ignored, thinks |
| `reasoning_effort: "none"` | works on GGUF; **ignored by the Mac's MLX** (owner) |
| **prefill**: an assistant turn holding the empty closed think block (`<think>`, a blank line, `</think>`) | **works: no thinking, "Nobody is there." in 0.2 s** |

LM Studio continues a trailing assistant message, so the model starts after its "thinking" with the answer. This works at the message and template level, not in the engine's reasoning setting, so it is the best bet for MLX. The Mac check below confirms it either way.

## Changes

- **`hardware/vision.py`**
  - Every request sends `reasoning_effort` "none", `enable_thinking` false and **max_tokens 120**.
  - Strategies, in order: `prefill`, `plain`, `no_think`, `system`. The probe tries only `prefill`, then `plain` (the two that can work). `no_think` and `system` can still be forced with `llm.vision_no_think`, and the check shows them.
  - The first strategy that answers without thinking is used and saved in `cache/vision_mode.json`, keyed by server and model, so later starts skip the probe. If none answers cleanly, it uses the fastest one that still answered within the cap.
  - If nothing answers:
    - photos never wait: the detector describes them at once
    - one console line gives the manual fix
    - a background probe retries every 5 minutes, so a fixed template comes back without a restart
  - Thinking now means real reasoning text. The empty closed block that a no-think template prints is not thinking. Any answer is used as the description.
  - Startup warm-up checks that a saved strategy still answers (it probes again if not) and prints `[vision] ready in X s; one description takes Y s (model, no-think: prefill)`.
- **`hardware/check.py vision`** (the one command):
  1. Warns if `vision_max_tokens` is over 200.
  2. Loads each model with an untimed request first.
  3. Times every strategy on the vision model, and on `ternary-bonsai-2-4b-mlx` if it's downloaded.
  4. Prints `USE: <model> with '<strategy>'`. It keeps the 9B unless the 9B misses 5 s and the 4B makes it; then it prints the `"vision_model"` line to add. It saves the pick.
  5. Runs Dell's `BonsaiClient` exactly as the app builds it, on the profile's `model` and `fast_model`, with a verdict for each: `OK: no thinking (chat)`, `OK: thinks by default; Dell's no-think prompt (/v1/completions) stops it`, or `PROBLEM: …` with the fix.
- **`integration/start_mac.sh`**: if `llm.vision_model` is set, it is loaded with the 9B and the 2B and counts as needed, so there's no eject warning.
- **Settings example:** `vision_max_tokens` 768 → 120; `vision_no_think` "auto".
- **Tests:**
  - FakeLMStudio can think unless the request turns thinking off in a given way. New tests cover each path.
  - The strategy cache goes to a temp file in every test, including the rehearsal, crash safety and app tests.
  - Applied Dell's `nv-test-tts-final-lines.patch` (PLAN Q38). Main had failed `test_dells_fixed_lines_are_found` since #61.

## Dell's brain calls

- **On the Mac, answered by the owner's benchmark (04:20, `python -m brain.bench --profile mac`):** the 9B thinks for text too, but Dell's raw `/v1/completions` no-think prompt works on MLX ("probe said raw; chat replies with thinking: 0", smart calls 1.41 s on average). The 2B doesn't think (chat, 0.38 s on average). So the brain is fine on both. Only images, which can't use the raw prompt, needed the prefill.
- **Text calls:** Dell's probe sends "Say OK." and switches to the raw `/v1/completions` no-think prompt if the model thinks. On this laptop the raw path answers "OK" in 0.3 s with no thinking, and the auto path picks chat because GGUF honours reasoning_effort. Not yet known on the Mac: whether LM Studio serves `/v1/completions` for MLX models. The check prints a verdict for the 9B and the 2B. If it says PROBLEM, the template line fixes chat for both.
- **`brain/vision.py` `Describer`:** sends images through chat with up to 300 tokens, so it would think on the Mac. Nothing in the product calls it; the product uses `hardware/vision.py`. Noted for Dell in PLAN Q39.

## On the Mac

- Settings: under `"llm"`, `"vision_max_tokens": 120,` (it was 768 in my earlier settings note). This is in MAC_TEST.md as edit 3.
- Command: `cd ~/Faraday && git pull && python3 -m hardware.check vision`
- If the 9B still thinks with every strategy: LM Studio → My Models → the model → gear → Prompt Template, add `{%- set enable_thinking = false -%}` as the first line, save, reload the model.
