# log/

**Owner: Dell laptop.**

This is the code that writes and reads the event/action log: one JSON object per line in `logs/events.jsonl`. It also stores `save_message` messages and gives `summarize_log` and the dashboard recent entries.

Note that `log/` is code and is committed. `logs/` is runtime data and is gitignored.

```python
from log import EventLog
log = EventLog.from_settings(settings)   # <paths.logs>/events.jsonl
log.write(event_or_action)               # returns the stored (redacted) dict
log.recent(10)                           # last 10 entries, oldest first (brain prompt, dashboard)
log.messages()                           # save_message actions
```

Every string in an entry is checked for the passphrase and replaced with `[passphrase]` before it's written or kept in memory. Speech is already redacted upstream in `modes/`; this is the second layer. Writes are thread-safe. On start, the tail of an existing file is loaded, so `recent()` survives a restart, and a torn last line is skipped.

**Checking a test run:** `python -m log.check logs/join1-events.jsonl [--last 30]` prints:
- counts per event type and source (for example `doorbell flipper 3`),
- likely duplicates (the same sensor event less than 3 s apart),
- whether the passphrase from settings appears anywhere (it must be 0),
- the last entries, one line each.
