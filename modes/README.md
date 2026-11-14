# modes/

**Owner: Dell laptop.**

Holds the Home / Away / Night state machine and the **passphrase check**, which is the only way to disarm. It's plain code with no AI involved. It handles `set_mode` (away/night only), redacts the passphrase from `speech_heard` text, and publishes a `mode_changed` event on every change.

It also holds the **alert level** (`normal` / `warning` / `alarm`). The first motion in Away or Night sets `warning`, and motion again within 60 s (`alerts.alarm_window_s`) sets `alarm`. Only the passphrase disarm returns it to `normal`. Every change publishes an `alert_changed` event. See [docs/FORMATS.md](../docs/FORMATS.md#alert_changed).

| File | What it does |
|---|---|
| `state.py` | `ModeState`. `on_event(motion)` updates alerts. `screen_speech(event)` redacts and maybe disarms. `arm(mode, changed_by)` refuses home, and refuses brain/rule changes while armed. `disarm_with_passphrase(text)` sends `mode_changed`, then `alert_changed normal` with the same `caused_by`. `startup()` |
| `passphrase.py` | `find_passphrase` / `redact`. Ignores case and punctuation, splits hyphens, and treats digits as number words ("Blue pelican 17." matches "blue pelican seventeen"). An empty passphrase never matches |

Methods return the events they produced. `ModeState` does no I/O; `brain/pipeline.py` logs the events and passes them on.
