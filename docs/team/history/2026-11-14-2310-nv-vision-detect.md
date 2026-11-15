# NV: vision robust + factual, live object detection (2026-11-14 23:10)

Owner items 1 and 2. Built and tested on Windows with stand-ins (no camera, no LM Studio); the Mac confirms.

## Vision (`hardware/vision.py`)

- **The model:** the profile's `model` (the 9B on the Mac). Never the 2B.
- **Factual prompt:** how many people (or exactly "Nobody is there."), what each holds, face visible or covered (mask, hood, helmet, turned away), what they're doing, and a package on the ground. Up to two short sentences. The object detector's counts are added as a hint.
- **Robust to a model that thinks before answering:**
  - Thinking is switched off two ways: `reasoning_effort: "none"`, plus `chat_template_kwargs: {"enable_thinking": false}`. If LM Studio refuses the second field (HTTP 400), the request is retried without it.
  - `max_tokens` is 768 (setting `llm.vision_max_tokens`). A model that doesn't think stops after about 40 tokens anyway. If the answer is cut off (finish_reason `length`), there is one retry with 2048.
  - Reasoning is stripped: `<think>…</think>`, a reply that has only the closing `</think>`, an unfinished `<think>`, and a separate `reasoning_content` field.
  - **Timeout:** one clear timeout per photo, `llm.vision_timeout_s`, default 20 s. That is inside Dell's 25 s "who's at the door" wait, so the detector's description still arrives in time. It is named in the log and in `hardware.check vision`. The 2048 retry runs only if there's time for it.
- **Warm-up:** the app sends one tiny image at startup, so LM Studio loads the vision part before the first doorbell.
- **`python3 -m hardware.check vision [photo]`** is still the one-command Mac test. It now also prints the warm-up time, the detector's line, the attempts and the token budget.

## Object detection (`hardware/detect.py`, models in `hardware/models/`)

- **Objects:** YOLOX-tiny (Megvii, Apache-2.0), 80 COCO classes, through OpenCV DNN (no new package), about 21 ms per frame on the NV laptop.
  - Fallback: OpenCV's HOG people detector, on OpenCV 4 only (OpenCV 5 dropped it).
  - Picked over YOLOX-nano after measuring both here: tiny found 4 people against nano's 2 on the reference image, for 8 ms more per frame. The Ultralytics YOLO models were ruled out (AGPL).
- **Faces:** YuNet (OpenCV `FaceDetectorYN`, MIT). OpenCV 5 dropped the Haar cascades, so Haar is the fallback on OpenCV 4 only.
- **Live stream:** labelled boxes, at about 5 detections a second while someone watches. Saved photos stay clean.
- **Photos:** `photo_described` gets `people`, `faces` and `detections` ([{label, count, confidence}]).
  - If vision fails, the description comes from the detector ("One person is at the door. No face is visible: covered or turned away.", "Nobody is there."). It goes out with `described_by: "detector"` and source `vision`, so Dell's look line speaks it instead of "I can't see the door right now".
  - Faces are mentioned only for people close to the camera.
- **Check:** `python3 -m hardware.detect <photo>`.
- Both model files are committed (20 MB + 0.2 MB, with their licences and SHA-256 in `hardware/models/README.md`), so `git pull` is the download.

## Shared (owner-requested; PLAN Q30, FORMATS.md)

`photo_described`: `described_by` ("bonsai" / "detector" / null), `people`, `faces`, `detections`. All are optional and validated.

## Settings (all optional: the code defaults are the same)

Under `"llm"`: `"vision_timeout_s": 20, "vision_max_tokens": 768`.
Under `"camera"`: `"detector": {"enabled": true, "model": "hardware/models/yolox_tiny.onnx", "score": 0.35, "fps": 5}`.
Flag `--no-detect` turns detection off.

## Tests

- `hardware/tests/test_detect.py`: decoding maths with a stub network, duplicate merging, summary, hint and fallback sentences, the real committed models on a blank frame, boxes on the stream but not on photos, and event field validation.
- `hardware/tests/test_camera.py`: two sentences, reasoning stripping, the retry with 2048 tokens, the server refusing the thinking switch, the hint in the prompt, the warm-up, and the detector fallback description.

## Review (fresh reviewer agent) and fixes

- **HIGH, fixed:** plain-text thinking cut off before `</think>` was taken as the description. Now a cut-off reply, or one that starts like reasoning ("Thinking Process…"), is no answer: retry, then the detector fallback.
- **Description length:** the two-sentence cap dropped facts such as "face covered", which Dell's threat label uses. Now every whole sentence that fits in 280 characters is kept.
- **Thinking switch:** any HTTP 400 used to turn it off for good (for example "No models loaded" at the warm-up). Now it's turned off only if the retry without it succeeds.
- **Failed detector:** it looked like "nobody there". Now `Detector.detect`/`analyse` return None on failure, so the photo says "Photo saved." and no hint is sent. The warning is logged once a minute. The hint is sent only when the detector saw something, so a missed person can't become Bonsai's "Nobody is there.".
- **Timing:** vision could outlast Dell's 25 s look timeout. The timeout is now 20 s and the retry is time-aware (PLAN Q30 note).
- **Small ones:** boxes are clamped on all sides (empty ones dropped), and the warm-up prints "failed" instead of "None s".
