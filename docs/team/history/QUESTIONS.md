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
   - **Update 16:10:** Dell's PR #10 adds a Talk button when `board.talk_hook` is set. That's wired now (`app.board.talk_hook = voice.toggle`), so the button shows once #10 is merged.
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

## Added 2026-11-15 (owner away; items 1-9 built on Windows)

13. **Faraday's voice.**
    - Default: `tts.mac.voice` = seed 7, temperature 0.6, top-k 50, model 0.6b.
    - Run `python3 -m hardware.tts_audition` on the Mac and pick a take by ear, then paste the line `--pick N` prints.
    - The 1.7B isn't used until its latency is known; the audition prints it.
14. **Lights off at every start.**
    - Default: on (`integration.lights_off_at_start`).
    - Unknown: whether an *off* code toggles a lamp that's already off. If a lamp turns ON at start, set it to false.
15. **Vision timeout.**
    - Default: 20 s (`llm.vision_timeout_s`), inside Dell's 25 s "who's at the door" wait.
    - If the 9B needs longer even with thinking off, raise both together.
16. **Detection model in the repo.**
    - Default: YOLOX-tiny (20 MB, Apache-2.0) and YuNet (0.2 MB, MIT) are committed in `hardware/models/`, so `git pull` brings them and nothing downloads at run time.
    - Say if you'd rather keep binaries out of git; then there would be a download step instead.
17. **Item 10 (owner voice check).**
    - Not started: your brief says only after 1-9 pass on the Mac.
18. **Owner follow-up rule.**
    - Default: a question opens an *owner* window only if the speak action's `caused_by` is an owner speech_heard (Dell's brain sets it). This replaces the 30 s timer and answers Dell's PLAN Q31.
19. **Vision model: the 9B or the 4B** (owner 2026-11-15: "measure the 4B too").
    - Default: the 9B stays. `python3 -m hardware.check vision` recommends the 4B only if the 9B misses the 5 s target and the 4B makes it. It then prints the `"vision_model"` line to add, and `start_mac.sh` loads that model too.
20. **People stay sensitive** (detector item, 2026-11-15).
    - Default: as you said. A person box at 0.35 counts at once, with no 3-of-5 wait. So a coat or a shadow could still be taken for a person.
    - If ghost people show up on the Mac, the same steadiness rule can apply to people (for example 2 of 5 frames).
21. **Going out of view when close.**
    - Default: camera stays at 1280x720 (16:9). The stream is already the whole frame; Dell is fixing the page's crop.
    - If you still go out of view with Dell's fix, the camera itself may crop its sensor for 16:9. `python3 -m hardware.check camera` now prints the frame size. Try `"width": 1280, "height": 960` under `"camera"`.
22. **Launcher Wi-Fi warning** (Dell's PLAN Q39 (1); Dell left the wording to you).
    - Default: `Wi-Fi is ON (en0). Turn it off: the event rule is Wi-Fi off, and Faraday never uses the internet.` It no longer says "for the demo".
23. **A photo now takes about 0.4 s longer** (detector item).
    - Default: yes. The photo is checked together with the next 4 frames, so a one-frame "cell phone" can't be reported. Vision starts after that, so a description takes about 0.4 s more.
24. **"unknown object" on photo_described.**
    - Default: never. Only people and steady objects at 0.6 or more go on the event, into the vision hint and into the detector's description. The live stream shows unsure steady things as "unknown object".
25. **Naming needs the same label at the threshold in 3 of 5 frames.**
    - Default: strict, as written. A backpack seen in 5 of 5 frames at 0.62, 0.58, 0.61, 0.59 and 0.57 shows as "unknown object" and is left off the photo, because only two frames reach 0.6.
    - Alternative: name it when it's seen in 3 of 5 and its best confidence reaches the threshold. Lowering `camera.detector.object_score` to 0.55 also names it.
