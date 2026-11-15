# NV: steady detections and the whole camera frame (2026-11-15 05:00)

Two owner items from the Mac run on 2026-11-15.

## 1. Detector false positives

Owner: "random objects show as 'cell phone', and a bag was reported that didn't exist. Only name an object at 0.6 confidence or higher, show 'unknown object' below that, and require it in at least 3 of the last 5 frames. Keep person detection sensitive. Only confident detections go on photo_described."

The bag ended up in the alarm line ("Leave now with your black hooded jacket and bag."). The detector's unsure `handbag`/`backpack` boxes (it kept anything at 0.35 or more) went into the vision hint and the photo's `detections`.

New in `hardware/detect.py`:
- **People:** unchanged and sensitive. Any person box at 0.35 counts at once, with no waiting.
- **Every other object:**
  - **Steady:** seen in at least 3 of the last 5 frames, else it isn't shown at all. Boxes in two frames are the same object when they overlap (IoU 0.3), whatever the label, so a phone flickering between "cell phone" and "remote" is one thing.
  - **Named:** only if one label reached 0.6 in at least 3 of those frames. Otherwise the live stream shows "unknown object" (grey, no percentage).
- **Photos:**
  - `CameraFeed.snapshot_burst(5)` saves the photo, then takes the next 4 frames (about 0.4 s at 10 fps).
  - `Detector.analyse_frames(frames)` takes people, close people and faces from the photo, plus the objects that are steady and named across the 5 frames.
  - **Only those go on `photo_described.detections`**, into the vision hint and into the detector's own description. Unknown objects never do.
  - A single image (`hardware.check camera`, `python -m hardware.detect`) needs 0.6 only. There's nothing to compare it with.
- **Live stream:** `StreamFilter` applies the same rules to the 5-per-second detection frames. After a pause of over 2 s the old frames don't count.
- `python -m hardware.detect photo.jpg` also lists what wasn't reported (under 0.6).

### The threshold is a setting (owner, 2026-11-15)

"A phone at my ear scores about 0.4-0.5 and would show as 'unknown object'." The threshold is `camera.detector.object_score`, default 0.6. In `config/settings.json`, the detector line under `"camera"` becomes:

```json
"detector": {"enabled": true, "model": "hardware/models/yolox_tiny.onnx", "score": 0.35, "object_score": 0.6, "fps": 5},
```

Set it to 0.4 to name the phone at your ear. The object still has to be steady (3 of 5 frames at that confidence). `score` (0.35) is the lowest box the detector keeps, so an `object_score` below it does nothing. `python3 -m hardware.check camera` prints the threshold in use.

### Review fixes (fresh reviewer, before merging)

- One object seen as two labels in a frame ("cell phone" 0.65 and "remote" 0.40 on one phone) is one thing. The second box only votes for a label.
- The best overlaps are matched first, so something new next to a steady object can't take its box.
- **A photo reports only what is in the photo itself**, and steady across the frames, at its box in the photo. Before this, an object seen only in the next frames could be reported for a photo that didn't show it.
- The live view draws no grey "unknown object" on top of a named one. It still draws one next to a person (the phone at the ear).
- A low detection rate (`fps` 0.5) no longer clears the window before every update.
- The label clamp test now fails without the clamp.

Second review:
- A weapon label that qualifies always wins the name. A knife can't hide behind "cell phone"; Dell's threat label puts weapons first.
- A track matches against all its boxes from its latest frame, votes too, so an object seen only as a second box in the photo is still followed.
- A photo reports each object at the photo box of its winning label.
- `object_score` outside 0-1 (60, NaN, true) falls back to 0.6 with a warning.
- Tests now cover the vote path, the setting through the live stream, and the slow-fps window.

The setting is in the shared settings example at the owner's direct request (2026-11-15); Dell FYI.

Left for the owner (QUESTIONS 25): naming needs the same label at the threshold in 3 of the 5 frames. So a backpack at 0.62, 0.58, 0.61, 0.59, 0.57 is "unknown object". Lowering `object_score` to 0.55 would name it.

## 2. Camera framing

Owner: "the Controls page crops the frame, so I go out of view when close and boxes get cut. Make sure the stream sends the full frame and boxes match (Dell fixes the page side)."

- **Checked:** `/stream.mjpg` and `/frame.jpg` were already the camera's whole frame, never resized or cropped. The YOLOX boxes are mapped back to full-frame pixels and clipped to it, and they're burned into the stream, so they match at any display size. The crop is the pages' CSS: Dell's `.cam-frame` is `aspect-ratio: 4 / 3` with `object-fit: cover`, and that crops the sides of a 16:9 frame.
- **New:** `/api/status` has `"frame": {"width": 1280, "height": 720}` (null before the first frame), so a page can use the real shape. Use CSS `aspect-ratio: width / height`, or `object-fit: contain`.
- My reference Controls page (`:8081/`) now uses `object-fit: contain` and sets its aspect ratio from `frame`.
- A box at the right edge keeps its label inside the frame.
- `python3 -m hardware.check camera` prints the frame size and shape. If it says 16:9 and you still go out of view when close with Dell's page fixed, the camera may crop its sensor to give 16:9. Try `"width": 1280, "height": 960` under `"camera"` (QUESTIONS 21).

## Tests

- `hardware/tests/test_detect.py` `Steadiness`:
  - a flicker is never shown
  - a steady, sure object is named from the third frame
  - unsure, or a flickering label, gives "unknown object"
  - old frames drop out
  - a person shows at once
  - two objects in one frame stay two
  - a photo burst reports only people and steady, sure objects, and the hint and description leave the rest out
  - the live feed shows "unknown object", and a photo analyses 5 frames
- `integration/tests/test_controls.py`: the stream is the whole frame, `/api/status` gives its size, and the reference page never crops.
