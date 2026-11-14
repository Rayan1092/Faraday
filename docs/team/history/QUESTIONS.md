# Open questions from NV (written while the owner was away)

Each has the default I used, so work continued. Answer inline and I'll adjust.

1. **Fake Flipper event source.**
   - Default: events from the fake Flipper carry `source: "sim"`, not `"flipper"`, so logs never pass fake radio off as real.
   - The fake only replaces the serial port. The real driver code runs.
2. **`integration.fake_flipper` setting.**
   - Default: added to `settings.example.json` as `false`. `py -m integration --fake` also turns it on.
3. **Push-to-talk trigger.**
   - Default: in the `py -m integration` console, type `r` and Enter to start recording, then Enter to stop. Recording also stops by itself after `audio.max_record_s`.
   - A big dashboard button would be nicer on stage, but `dashboard/` is Dell's. Should we ask Dell to add one that calls an integration hook?
4. **`sensors.motion.merge_window_s` (5) is unused.**
   - The driver merges bursts with `flipper.burst_gap_s` (1.5).
   - Default: left in the file, since removing it wasn't in the PR #5 decisions. Remove it?
5. **Vision model (PLAN Open Question 2).**
   - Found: `ternary-bonsai-2-4b@q2_0` accepts images in LM Studio. It described a saved test image correctly ("A brown box labeled PACKAGE…") in 3–7 s.
   - Default: vision uses the same `llm` profile as the brain, with `llm.vision_timeout_s` (falls back to `timeout_s`).
   - Please confirm on the Mac that the 9B accepts images too.
6. **Camera on by default.**
   - Default: `py -m integration` uses the real webcam plus vision. Use `--standin-camera`, or `"camera": {"enabled": false}`, for a fixed fake description.
   - If vision fails, the photo is kept and the description is "Photo saved." (`source: "camera"`).
