# modes/

**Owner: Dell laptop.**

Holds the Home / Away / Night state machine and the **passphrase check**, which is the only way to disarm. It's plain code with no AI involved. It handles `set_mode` (away/night only), redacts the passphrase from `speech_heard` text, and publishes a `mode_changed` event on every change.

It also holds the **alert level** (`normal` / `warning` / `alarm`). The first motion in Away or Night sets `warning`, and motion again within 60 s (`alerts.alarm_window_s`) sets `alarm`. Only the passphrase disarm returns it to `normal`. Every change publishes an `alert_changed` event. See [docs/FORMATS.md](../docs/FORMATS.md#alert_changed).
