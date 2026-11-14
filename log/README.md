# log/

**Owner: Dell laptop.**

This is the code that writes and reads the event/action log: one JSON object per line in `logs/events.jsonl`. It also stores `save_message` messages and gives `summarize_log` and the dashboard recent entries.

Note that `log/` is code and is committed. `logs/` is runtime data and is gitignored.
