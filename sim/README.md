# sim/

**Owner: Dell laptop.**

Fake hardware for development. It sends any FORMATS.md event with `source: "sim"`, and other code must treat sim events exactly like real ones. It's useful on Windows laptops without the Flipper and as the stage backup. Sim runs never sign off a step; only real-hardware tests do.

```
python -m sim              # uses Bonsai from config/settings.json
python -m sim --offline    # rule-based fallback only, no LM Studio needed
python -m sim -v           # also print full JSON for every event and action
python -m sim --log x.jsonl  # write somewhere other than logs/events.jsonl
```

Type a key, then Enter:

| Key | Does |
|---|---|
| `d` | doorbell |
| `m` | motion (one merged event) |
| `t <text>` | speech, as if STT heard it. Speech that contains the passphrase disarms (it is redacted first) |
| `1` / `2` / `3` | Home / Away / Night. Home from an armed mode asks for the passphrase |
| `s` | status: mode, alert and the last 10 log entries |
| `q` | quit |

Every event goes through the real `brain/pipeline.py`: `modes/` (passphrase, alerts), `log/` (written to `logs/events.jsonl`), the `rules/` baseline, then Bonsai. It prints every event and action with its source (`rules` or `brain`), and how long each Bonsai call took. Hardware actions only print. `take_photo` gets a fake `photo_described` reply. The prompt shows `[mode/alert]`. Keys can be piped in too: `printf "d\nm\nq\n" | python -m sim --offline`.
