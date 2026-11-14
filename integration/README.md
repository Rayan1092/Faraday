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
- `r`: push-to-talk. Enter stops it and sends the recording to STT
- `t <text>`: typed speech
- `1` / `2` / `3`: Home (passphrase) / Away / Night
- `d` / `m`: fake doorbell / wave (fake only)
- `s`: status
- `q`: quit

**Tests:** `python -m unittest integration.tests.test_app` runs the whole flow on the fake Flipper with an offline brain and a temp log.

## On the Mac mini

```
integration/start_mac.sh            # checks LM Studio + Argmax answer, then python3 -m integration
integration/start_mac.sh --offline  # extra args go to the app
```

The script creates `config/settings.json` from the example with `llm.profile: "mac"` if it's missing. It **fails** if LM Studio doesn't answer or lacks the model, or if `argmax-cli serve` doesn't answer. It **warns** about Wi-Fi on, a missing `argmax-cli`, a missing Flipper port, or the example passphrase.

Env switches: `FARADAY_SKIP_CHECKS=1`, `FARADAY_DRY_RUN=1`, `PYTHON=...`.

Tests: `python -m unittest integration.tests.test_start_mac` (uses Git Bash on Windows).
