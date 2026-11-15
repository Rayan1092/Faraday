# Mac test list (read this on the Mac)

Everything since the last Mac session (vision, object detection, hands-free windows, the wake phrase, owner/visitor channel, the voice, fresh start, alarm recording) was built on Windows with fakes. This is the one list to check it on the real hardware, in order.

`argmax-cli` below means `/Users/captain/clueless/vendor/argmax-oss-swift/.build/release/argmax-cli`.

## Retest first: everything from the night of 2026-11-15, in this order

All of this was built and tested on Windows with fakes and the Bonsai 2 4B; only the Mac can confirm it. NV and Dell items are in one list. The older full list is further down, for reference.

**Settings first** (`config/settings.json`; it isn't in git, so new defaults can't reach it):
- Under `"llm"`: `"vision_max_tokens": 120,` (it was 768). If you raised `"vision_timeout_s"`, set it back to `20`.
- Under `"camera"`, the detector line becomes:
  `"detector": {"enabled": true, "model": "hardware/models/yolox_tiny.onnx", "score": 0.35, "object_score": 0.6, "fps": 5},`
- Under `"alerts"`: if it has `"visitor_quiet_s": 120`, change it to `60` (Dell's grace timer).

1. **Pull, with Wi-Fi on:** `cd ~/Faraday && git pull`. Then turn Wi-Fi off.
2. **Bring everything up:** open LM Studio, then `FARADAY_DRY_RUN=1 integration/start_mac.sh`. Expect all OK and READY.
3. **Vision without thinking:** `python3 -m hardware.check vision` (under a minute).
   - On the 9B, `plain` says THINKS. The **`prefill`** row should be `ok` with a factual sentence ("Nobody is there." with nobody in view), under 5 s.
   - Then `USE: …`, and `brain …: OK` for the 9B and the 2B.
   - **If it says `NO strategy stops the thinking`:** in LM Studio go to My Models → the 9B → gear → Prompt Template. Add `{%- set enable_thinking = false -%}` as the first line, save, reload the model, and run it again.
   - If `USE` names the 4B, add the `"vision_model"` line it prints.
4. **Voice cache, before starting Faraday:** `python3 -m integration.fixed_lines --render --fresh`. It deletes the old takes and renders Dell's new lines; about a minute. Expect `… of … are cached` with both numbers equal.
5. **Camera and detector:** `python3 -m hardware.check camera`.
   - A `frame: WxH (16:9)` line.
   - `objects named at 0.60 or more`.
   - The detector line names only things really in view.
6. **Start Faraday:** `integration/start_mac.sh`. Watch for `[vision] ready in X s; one description takes Y s (…, no-think: prefill)`.
7. **Live view:** the dashboard, and the Controls page at `http://127.0.0.1:8081/`.
   - Walk up close: you stay in view, the frame isn't cropped and the boxes aren't cut.
   - Random things no longer flash as "cell phone". Unsure steady things show a grey "unknown object".
   - A phone at your ear (0.4-0.5) shows as "unknown object". To name it, set `"object_score": 0.4` and restart.
8. **Doorbell in Away:**
   - The greeting comes, then `photo_described` a few seconds after the photo (it took 20-47 s last night). The description is one factual sentence.
   - `grep '"photo_described"' logs/events.jsonl | tail -n 3`: the detections hold only people and real objects.
   - Say "Hi, I have a package, where should I leave it?". Expected: "You can leave it by the door. I'll let the owner know it arrived.", and the dashboard label turns to delivery (Dell).
9. **Walk-up, then ring:** no warning.
10. **Motion alone:** the warning comes 15 s after the wave, and the console prints `[motion] …` lines saying why (Dell).
11. **Motion again: the alarm.**
    - Only the fixed line is spoken: "This property is protected. You are being recorded and the owner has been notified. Please leave now." No description and no phantom bag (Dell).
    - The recording is saved.
12. **Disarm:**
    - A wrong phrase gets "The system is armed. I can't make changes right now."
    - The passphrase gets "Welcome home. The system is disarmed."
    - Nothing says "pass phase".
13. **"Hey Faraday, who's at the door?"** The answer is facts only, for example "One person is at the front door." The description stays on the dashboard (Dell).
14. **"Hey Faraday, what happened while I was out?"** The times and counts match the log (Dell).
15. **"Hey Faraday, if a delivery comes, I want you to tell him to leave the package by the side door."** It's accepted, not "please say it again" (Dell).

---

## Three edits in `config/settings.json`

1. Under `"audio"`, replace the `listen` and `wake` lines. The old values override the new defaults:

   ```json
   "listen": {"max_s": 15, "silence_s": 1.8, "no_speech_s": 8, "after_speech_s": 0.3},
   "wake": {"enabled": true, "phrase": "hey faraday", "silence_s": 0.7, "max_s": 10}
   ```

2. Under `"tts"` → `"mac"`, put a comma after the command's closing `]`, then add these lines. Without them, the seed stays random and the voice drifts:

   ```json
   "voice": {"seed": 7, "temperature": 0.6, "top_k": 50, "model": "0.6b", "instruction": null},
   "cache": true,
   "prerender": true
   ```

3. Under `"llm"`, change the vision token cap from 768 to 120. One sentence needs about 30 tokens, and 768 only let the 9B think for 20 s:

   ```json
   "vision_max_tokens": 120,
   ```

   If you raised `"vision_timeout_s"` for the diagnosis, set it back to `20`.

Every other new setting already has the right default in the code. The full file is in `2026-11-14-1900-nv-mac-settings.md`.

## With Wi-Fi ON (downloads)

1. `cd ~/Faraday && git pull`. This also brings the two detection model files (about 20 MB, in `hardware/models/`).
2. Make the three settings edits above.
3. Pick the voice:
   - `python3 -m hardware.tts_audition`. Take 10, the first 1.7B take, downloads that model.
   - Listen to the numbered takes. The 1.7B latency is **take 11's** time (take 10's includes the download); the 0.6B takes are 1-9.
   - `python3 -m hardware.tts_audition --pick N`, then **replace** the `"voice"` line from edit 2 with the line it prints (don't keep both).
4. Download the diarization models: `argmax-cli diarize --audio-path captures/tts_audition/take01.wav`, once.
5. `python3 -c 'import cv2; print(cv2.__version__)'`. If it is older than 4.7, run `pip3 install -U opencv-python` now, while Wi-Fi is still on (object detection needs 4.7).

## With Wi-Fi OFF

6. Open LM Studio, then bring everything up without starting Faraday: **`FARADAY_DRY_RUN=1 integration/start_mac.sh`**. It starts `argmax-cli serve` (it keeps running), loads the 9B and the 2B (and `llm.vision_model` if you set one) if the `lms` command is installed, and checks everything. If `lms` isn't installed, load only the 9B and the 2B by hand (plus `llm.vision_model` if you set one). Eject anything else.
7. `python3 -m hardware.check camera`, then **`python3 -m hardware.check vision`** (under a minute; it also loads and times `ternary-bonsai-2-4b-mlx` if that is downloaded).
   - **Expect:** a table, one row per model and no-think method. On the 9B, `plain` says THINKS (it ignores reasoning_effort). The row that matters is **`prefill`**: `ok`, a factual sentence ("Nobody is there." with nobody in view), under 5 s. Then a `USE: …` line (the app picks it up from its next start) and two `brain …: OK` lines (Dell's 9B and 2B calls).
   - **If `USE` names the 4B:** add the line it prints under `"llm"` in `config/settings.json`. `start_mac.sh` then loads the 4B as well.
   - **If it says `NO strategy stops the thinking`, or a `brain` line says PROBLEM:** in LM Studio go to My Models → the model → gear → Prompt Template. Add `{%- set enable_thinking = false -%}` as the first line, save, reload the model, and run the check again. Send me the output either way.
8. Look at the lamps first, then run **`integration/start_mac.sh`** (one command; it starts with `--fresh` by default, `--no-fresh` keeps the old log).
   - **Startup output:** big `[ OK ]` / `[WARN]` / `[FAIL]` lines, then **READY** (it starts Faraday) or **NOT READY** (nothing started, with the list to fix). Expect OK for LM Studio and both models, Argmax STT ("started and answering" the first time), the object detection model, OpenCV, mic, speaker and camera.
   - The launcher starts plain `argmax-cli serve`. If it needs different flags on this Mac, put the exact command in `config/settings.json` under `"stt"` → `"mac"` as `"serve_command": [...]`, stop the old one (`pkill -f "argmax-cli serve"`), and run the launcher again. Its log is `cache/argmax-serve.log`.
   - **Lamps:** lamps that were off must stay off. If one turns ON, set `"lights_off_at_start": false` under `"integration"`.
9. Open the Controls page at `http://127.0.0.1:8081/` and walk into view. You should see a labelled "person" box.
10. Arm **Away** (console key `2`), keep away from the motion sensor, then ring the doorbell. (In Home the doorbell only says "Someone is at the door." and takes no photo; the greeting, the photo and the visitor window are Away.)
    - "[mic] listening (doorbell)" appears only after the greeting has finished.
    - Answer with a 1-second pause mid-sentence: one transcript, not two.
    - Another time, stay silent: the window closes after about 8 s.
11. The wake phrase (works while armed too):
    - "Hey Faraday, what happened while I was out?" in one breath: a reply, no chime.
    - "Hey Faraday." then wait: a chime, then say your request.
    - Plain "Faraday": nothing happens.
12. `grep -E '"(speech_heard|photo_described)"' logs/events.jsonl | tail -n 10` (whole lines; `log.check` cuts them at 80 characters). Check:
    - your requests show `"channel": "owner"`
    - the visitor's replies show `"channel": "visitor"`
    - the doorbell photo shows `people`, `faces` and `detections`
13. The voice across several lines: the same Aiden every time, not rushed. Fixed lines are instant: watch for "[tts] … fixed line(s) rendered" at startup.
14. Hold to talk on the Controls page, then release. "[mic] listening (intercom)" appears, and the visitor's reply is heard.
15. Switch to Night and wave twice. Expect the alarm, both lights on and the buzz. After about 10 s, "Recording saved: captures/alarm-…/" prints, and that folder has the frames plus `clip.avi`.
16. Disarm with the passphrase: the lights go off. Then do the full demo once.

## Owner voice check (display only)

17. `python3 -m hardware.owner_voice enroll`: read anything aloud for 10 s.
18. Restart with `integration/start_mac.sh` (it prints "owner voice enrolled"). Say a few "Hey Faraday, …" requests: the console prints `[voice] the owner's voice` (or "unknown voice").
19. Score it: `python3 -m hardware.owner_voice trial` (you, 10 tries), then `python3 -m hardware.owner_voice trial --other` with someone else speaking. Enforce it for disarm only if both are 9/10 or better.

## Crash safety (optional, takes 2 minutes)

20. While Faraday runs, unplug the Flipper: the console says `[health] Flipper DOWN: …`. Plug it back in: `[health] Flipper back`, and the lights work again.
21. Quit `argmax-cli serve` (`pkill -f "argmax-cli serve"`): `[health] Argmax STT DOWN`. Run `integration/start_mac.sh` in a second Terminal tab with `FARADAY_DRY_RUN=1` to start it again: `[health] Argmax STT back`.
22. Stop LM Studio's server: `[health] Bonsai (LM Studio) DOWN`. Faraday still answers from plain code (photos are described by the detector). Start it again: `… back`. Every DOWN/back line is also in `logs/health.log`.
