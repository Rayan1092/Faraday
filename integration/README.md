# integration/

**Owner: NV laptop.** This is the main app. It wires the hardware to Dell's pipeline.

```
py -m integration                 # real Flipper (autodetect by USB id, then the settings port)
py -m integration --fake          # fake Flipper, no hardware (or set integration.fake_flipper)
py -m integration --fake --offline --no-browser --log tmp.jsonl   # quick try, no LM Studio
```

| Flow | How |
|---|---|
| Flipper events → `Pipeline.handle()` | thread-safe queue, one worker |
| `outlet_on` / `outlet_off` → Flipper | `Flipper.execute()`, queued inside the driver (rx pause, send, resume) |
| `mode_changed` / `alert_changed` → LED | `Flipper.apply_event()`, alert beats mode |
| `speak` | speech worker, one line at a time |
| `take_photo` | camera worker → `photo_described` back onto the queue |
| Dashboard | `Dashboard.from_settings(pipeline, settings)`, started with the app |

Nothing slow runs inside the pipeline callbacks: `on_action` can come from dashboard and confrontation threads (PLAN Q22). The pipeline logs every event and action itself (Q21).

**Console:**
- `t <text>`: typed speech
- `1` / `2` / `3`: Home (passphrase) / Away / Night
- `d` / `m`: fake doorbell / wave (fake only)
- `s`: status
- `q`: quit

**Tests:** `python -m unittest integration.tests.test_app` runs the whole flow on the fake Flipper with an offline brain and a temp log.
