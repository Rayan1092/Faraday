# Once the 9B runs on the Mac mini

The `9b` prompt profile is in `brain/prompts.py`. It turns on by itself when the model id contains "9b". Nothing has been measured on the 9B yet. On the 4B it scored 80/80 against 76/80 for `small` (see [README.md](README.md)), so it's at least safe.

## First, measure (10 minutes)

```
python -m brain.bench --profile mac                 # 9b prompts (picked from the model id)
python -m brain.bench --profile mac --prompts small # same model, the 4B-tuned prompts
```

Keep whichever profile scores higher. If it's a tie, keep the faster one. Both runs land in `docs/bench/`. Copy the numbers into the README and `judge_qa.md` where it says `[TBD: timings on the Mac mini with the 9B]`.

## Then try these, one at a time, rerunning the bench after each

1. **Thinking a little.** Set `"reasoning_effort": "low"` in `config/settings.json`. The 9B may answer better with a short think. Keep it only if the worst time stays under about 8 s, and check it never hits `max_tokens` (that shows up as fallbacks).
2. **More history.** The `9b` profile shows 12 recent log lines. Try 16. Bigger models use context better, but every line costs prefill time.
3. **Fewer code fix-ups.** Look at the `fixups` in the bench JSON. If the 9B never needs "saved the message Bonsai promised" or "corrected to away", that's a good line for the judges. Keep the fix-ups on anyway.
4. **Shorter answers.** Lower `max_tokens` from 200 to 120. Faster, as long as no case starts falling back.
5. **Parallel.** With LM Studio Parallel at 2, start Faraday with `integration/start_mac.sh --no-fresh`, ask "Hey Faraday, what happened while I was out?" and set off the alarm while the summary runs. The alarm line should still come within a few seconds.
6. **Vision and the label on real photos.** Take four photos with the real camera, named `nobody-motion-1.jpg`, `delivery-doorbell-1.jpg`, `visitor-doorbell-1.jpg` and `suspicious-motion-1.jpg`, in `captures/bench/`. Then run `python3 -m brain.bench_photos captures/bench`. It runs NV's detector, the vision model and the label, and writes `docs/bench/<date>-photos.md`. Note the vision time per photo. NV's vision (always the 9B) gives up after `llm.vision_timeout_s` (20 s), and then the detector's own description is used.
