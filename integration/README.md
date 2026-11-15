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
| `alert_changed` → Flipper | `Flipper.apply_event()`: alarm = red LED + 3 buzzes, anything else = LED off. `mode_changed` is passed too but lights nothing |
| `speak` | speech worker, one line at a time |
| `take_photo` | camera worker → `photo_described` back onto the queue |
| Dashboard | `Dashboard.from_settings(pipeline, settings)`, started with the app |

Nothing slow runs inside the pipeline callbacks: `on_action` can come from dashboard and confrontation threads (PLAN Q22). The pipeline logs every event and action itself (Q21).

**Console:**
- `r`: open one listening window now (it ends when you stop talking)
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

## Hands-free voice

There is no push-to-talk. The mic opens only in **listening windows**:
- after a doorbell (once Faraday has greeted, or after 4 s),
- after every line Faraday says that ends with "?",
- from the dashboard Listen button (`board.listen_hook`) or console `r`,
- for the **wake listener**: while idle, short windows run back to back, and a transcript counts only if it starts with "hey faraday". Plain "Faraday" does nothing, and that audio is deleted. Off on Windows (no STT).

A window records up to 6 s and ends about 0.8 s after the speaker stops (energy VAD), or after 3 s if nobody spoke. The mic is always muted while Faraday speaks. Settings: `audio.listen`, `audio.wake`. Tests: `python -m unittest hardware.tests.test_listen integration.tests.test_voice`.

## Dashboard hooks

Set on the `Dashboard` object by `integration/__main__.py` (PLAN Q26):
- `board.listen_hook()` opens one listening window (`talk_hook` does the same, for the existing Talk button).
- `board.control_hook(outlet, on)` switches an outlet through the normal dispatcher, with the same never-resend rule, and logs it as a `dashboard` action.

## Controls server (camera, intercom, lights)

Started with the app on **dashboard port + 1** (`http://127.0.0.1:8081`, `--controls-port`, `--no-controls`). It carries what the JSON-only dashboard server can't (PLAN Q27):

| Route | Does |
|---|---|
| `GET /` | reference Controls page: live camera, hold-to-talk intercom, lights, Listen |
| `GET /stream.mjpg`, `GET /frame.jpg` | live camera (MJPEG) / one frame. The webcam has one owner (`CameraFeed`), shared with photos |
| `POST /api/intercom` | raw int16 mono PCM chunk, header `X-Sample-Rate`. Plays live on the door speaker; the door mic is muted meanwhile |
| `POST /api/intercom/end` | owner released the button |
| `POST /api/control {"outlet", "on"}` | outlet through the dispatcher (never resends) |
| `POST /api/listen` | open one listening window |
| `GET /api/status` | camera / intercom / listen available, outlet states |

POSTs need `X-Faraday: 1`. CORS only for pages on the dashboard's port or this one.

**Chrome mic trust step:** none on the Mac itself (`127.0.0.1` is a secure context). From another device: `dashboard.host` = `0.0.0.0`, and add `http://<mac-ip>:8080,http://<mac-ip>:8081` to `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.

**Tests:** `python -m unittest integration.tests.test_controls hardware.tests.test_media`; the real page in headless Chrome with a fake mic: `FARADAY_BROWSER=1 python -m unittest integration.tests.test_browser_intercom`.
