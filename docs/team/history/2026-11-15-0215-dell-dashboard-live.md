# Dell: dashboard, live camera in the centre (2026-11-15 02:15)

Owner's list after the Mac test, item 9, plus the dashboard part of "the real product, not a demo build".

**What's new**
- **Centre:** NV's live stream (`/stream.mjpg` on the controls port, with the detector's boxes) and a LIVE badge. The latest photo shows until the stream is up and whenever it drops; the stream is retried every 8 s.
- **Under the camera:**
  - the latest threat label pill (plus a dashed "unknown" for the honest-label PR);
  - the detector's objects as chips;
  - "Recording saved · time" while the latest alarm has its recording (NV's `recording_saved`, PLAN Q33);
  - the description.
- **Activity:** photos and alarm recordings get thumbnails (a recording uses its `frame-0001.jpg`). The recording entry reads "Recording saved (10 s)" in red.
- **Heard & said:** NV's `owner_voice` (#51) shows as "Owner's voice" or "Not the owner's voice", display only.
- **ALARM banner:** "Someone stayed after the warning. Both lights on. Recording saved." (or "Recording." until it's saved).

**Removed**
- **Listen button:** gone (owner). `/api/listen` and `/api/talk` return 404. The mic indicator now follows `listening_hook` only, never a guess. `board.listen_hook` stays as an unused `None`, because NV's `test_voice` checks it.
- **Demo-only parts** (owner's product decision): the ONLINE/OFFLINE badge (`dashboard/netcheck.py` deleted), the "Stage backup" menu with Ring doorbell, Motion and the typed-speech box, and their routes `/api/sensor` and `/api/say`.
- **Banner wording:** for the owner, not an audience ("DISARMED", "DISARM REFUSED", "DOORBELL").

**Tests:**
- new `dashboard/tests/test_live_camera.py`;
- `test_listen.py` rewritten for the mic indicator, and for proving Listen is gone;
- `test_photos_buttons.py` now checks the demo buttons and routes are gone;
- banner tests moved to `test_banners.py`.

Screenshots were checked at 1280×720 and 1920×1080 with sample events.

**Still to do (demo removal, next PR):** `sim --demo`, and the stage-fallback notes in the pitch, video script and setup checklist.
