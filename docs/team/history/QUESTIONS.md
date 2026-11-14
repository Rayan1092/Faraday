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
7. **Argmax STT request.**
   - Default: multipart POST to `stt.<os>.url` with `file`, `language`, `response_format=json`, and `model` only if `stt.<os>.model` is set. The text is read from `{"text": ...}`.
   - Tested against a fake server only. Confirm on the Mac with `argmax-cli serve`, and tell me whether it needs a `model` field.
8. **Argmax TTS command (PLAN Open Question 3, still open).**
   - Default: run `tts.mac.command` from settings (a list; `{text}` and `{out_wav}` are filled in), then play the wav. The command in `settings.example.json` is still the Step 0 guess.
   - If it fails, the line is printed, with no fallback voice. That keeps the Mac Argmax-only. Should macOS `say` be a fallback?
9. **Windows TTS.**
   - Default: pyttsx3 (SAPI5, offline) when `tts.windows.kind` is `standin` and pyttsx3 is installed, otherwise printed.
   - Added `pyttsx3; sys_platform == "win32"` to `requirements.txt`.
10. **Mic mute while speaking.**
    - Default: the mic drops audio while TTS talks, plus 0.4 s after. A recording can't start while Faraday is talking.
11. **Mac profile.**
    - Default: the `mac` profile in `settings.example.json` stays `http://localhost:1234` with the placeholder `bonsai-2-9b` (PLAN Open Question 1).
    - `start_mac.sh` fails with the exact fix if LM Studio lists a different id. Put the real id in `config/settings.json` on the Mac.
12. **start_mac.sh location.**
    - Default: `integration/start_mac.sh` (inside NV's folder) rather than the repo root. Move it?
