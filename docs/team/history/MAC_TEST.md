# Mac test list (read this on the Mac)

Everything since the last Mac session (vision, object detection, hands-free windows, the wake phrase, owner/visitor channel, the voice, fresh start, alarm recording) was built on Windows with fakes. This is the one list to check it on the real hardware, in order.

`argmax-cli` below means `/Users/captain/clueless/vendor/argmax-oss-swift/.build/release/argmax-cli`.

## Two edits in `config/settings.json`

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

Every other new setting already has the right default in the code. The full file is in `2026-11-14-1900-nv-mac-settings.md`.

## With Wi-Fi ON (downloads)

1. `cd ~/Faraday && git pull`. This also brings the two detection model files (about 20 MB, in `hardware/models/`).
2. Make the two settings edits above.
3. Pick the voice:
   - `python3 -m hardware.tts_audition`. Take 10, the first 1.7B take, downloads that model.
   - Listen to the numbered takes. The 1.7B latency is **take 11's** time (take 10's includes the download); the 0.6B takes are 1-9.
   - `python3 -m hardware.tts_audition --pick N`, then **replace** the `"voice"` line from edit 2 with the line it prints (don't keep both).
4. Download the diarization models: `argmax-cli diarize --audio-path captures/tts_audition/take01.wav`, once.
5. `python3 -c 'import cv2; print(cv2.__version__)'`. If it is older than 4.7, run `pip3 install -U opencv-python` now, while Wi-Fi is still on (object detection needs 4.7).

## With Wi-Fi OFF

6. In LM Studio, load only the 9B and the 2B. Start `argmax-cli serve`.
7. `python3 -m hardware.check camera`, then `python3 -m hardware.check vision`.
   - **Expect:** the warm-up time, the detector's line, and a factual description. With nobody in view, it says "Nobody is there."
   - **If it says FAILED:** send me the output.
8. Look at the lamps first, then run `integration/start_mac.sh --fresh`.
   - **Startup output:** every line OK, including "object detection model present" and "OpenCV".
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
