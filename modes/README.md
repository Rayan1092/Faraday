# modes/

**Owner: Dell laptop.**

Holds the Home / Away / Night state machine and the **passphrase check**, which is the only way to disarm. It's plain code with no AI involved. It handles `set_mode` (away/night only), redacts the passphrase from `speech_heard` text, and publishes a `mode_changed` event on every change.
