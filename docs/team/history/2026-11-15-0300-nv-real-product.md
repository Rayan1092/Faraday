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
