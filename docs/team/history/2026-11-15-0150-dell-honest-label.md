# Dell: honest, grounded threat label (2026-11-15 01:50)

Owner's list after the Mac test, item 6. It uses NV's #43 fields on `photo_described`: `people`, `faces`, `detections`, `described_by` (PLAN Q30, now confirmed).

**Rules** (`brain/threat.py`):
- **Zero people from the detector:** always `nobody` ("No person detected", plus any objects in view). No model call.
- **Vision failed** (`described_by` "detector", or a bare `camera` photo "Photo saved."): `unknown`, "Couldn't analyse the photo", plus what the detector saw. No model call and no guess. `unknown` is a new `threat_assessed` category in `shared/events.py` and FORMATS.md, as the owner asked. The 2B can't pick it.
- **Otherwise** the 2B judges the 9B's factual description plus "Object detector: 1 person, backpack; faces visible: 0", the mode, the time and the doorbell/motion history. Code then checks it:
  - warning signs in the description escalate;
  - "nobody" while a person is detected or described is rejected;
  - rang with no warning signs means visitor or delivery;
  - stayed without ringing while armed means suspicious.
- **Reasons:** a reason must name what was seen ("1 person, holding a box, rang the doorbell", "1 person, backpack, face covered") or the context ("stayed at the door without ringing while armed"). If the 2B's reason doesn't, code writes it from the facts, and the bench counts that as a correction.
- **Faces:** the face count is in the prompt but never used alone to call a face covered. NV counts faces only reliably close up; the 9B's description says "face covered" when it is.

**Bench:** 5 new cases built the way NV's camera reports (empty doorway, person with a box, plain visitor, hood up with face covered, vision failed). 4B: 15/15. The 2B's own reasons were grounded every time; nobody and unknown are decided by code. The 5 old threat cases still pass.

**Real photos: [TBD on the Mac].** New `python3 -m brain.bench_photos captures/bench` takes photos named `<expected>-<doorbell|motion>-<n>.jpg` through NV's detector, the vision model and the label, and writes `docs/bench/<date>-photos.md`.

**Dashboard:** "Unknown" is a label name. The pill colour comes with item 9.

**Tests:** `brain/tests/test_threat.py` (`Honest`, 7 tests), `brain/tests/test_bench_photos.py` (3).
