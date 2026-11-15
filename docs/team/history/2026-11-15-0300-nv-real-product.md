# NV: the real product only (2026-11-15 03:00)

Owner decision: "I present the real product, not a demo build."

## Removed from the product run (`integration/__main__.py`)

- **Console keys:** `d` and `m` (the fake doorbell and fake motion wave) and `t <text>` (typed speech). The console keeps `r`, `1`/`2`/`3`, `s` and `q`.
- **Start options:** `--fake` (fake Flipper), `--standin-camera`, `--print-speech` and `--offline` (rules-only brain), plus the `integration.fake_flipper` setting.
- `camera.enabled: false` now means no camera at all, not a stand-in camera with a fixed description.

The product always uses the real Flipper, camera and vision, mic, Argmax and Bonsai. `start_mac.sh` starts only that; its extra-arguments example is updated in the launcher PR (#54).

## Where the stand-ins live now

Only in the automated tests and the demo rehearsal. They call `build_app(args, settings, parts={...})`, which swaps a hardware piece for a stand-in without changing any wiring ("flipper", "capture_factory", "speaker", "stream_factory", "stt", "chime"). The product never passes `parts`. `integration/standins.py` is marked tests-only.

## Tests

`integration/tests/test_voice.py` `BuiltApp`:
- the product start command rejects `--fake`, `--standin-camera`, `--print-speech` and `--offline`
- the console help has no `t`/`d`/`m`
- `__main__.py` has no stand-in import
- the dashboard hook test now passes its fakes through `parts`

## For Dell (PLAN)

The root README still says `py -m integration --fake --offline`, which no longer runs. The dashboard's typed-speech box and `sim/` are in Dell's folders; the owner decides there.

## Review (fresh reviewer agent) and fixes

- **Camera disabled:** with `camera.enabled: false` there is no camera at all, so a `take_photo` was dropped silently. "Who's at the door?" waited 65 s, and the alarm confrontation never came. Now it is answered at once with "Camera unavailable." (source camera, like a failed snapshot).
- **Intercom:** it muted the door mic before opening the speaker stream. A missing speaker then left the mic deaf until a restart. Now it mutes only after the stream really opened, and a failed open drops the chunk and logs an error.
- **Launcher:** the product needs Argmax speech. `start_mac.sh` FAILs on `tts.mac.kind` other than "argmax", a missing TTS binary or no STT. Before, it warned and fell back to printed speech.
- **`build_app`:** `parts` is a closed list (unknown keys raise), a stand-in STT is used even if it's falsy, and argparse takes no abbreviations, so no prefix can bring an old flag back. The test checks `__main__.py` for every stand-in name.
- **Small ones:** no "use t <text>" hints left.
- **From the rehearsal:** a controls server that never started no longer hangs on `stop()`, and the voice's console lines go through the app's printer.
