# sim/

**Owner: Dell laptop.**

Fake hardware for development and tests only: it isn't part of the product and nothing in the product UI reaches it. It sends any FORMATS.md event with `source: "sim"`, and other code must treat sim events exactly like real ones. It's useful on Windows laptops without the Flipper. Sim runs never sign off a step; only real-hardware tests do.

```
python -m sim              # uses Bonsai from config/settings.json
python -m sim --offline    # rule-based fallback only, no LM Studio needed
python -m sim -v           # also print full JSON for every event and action
python -m sim --log x.jsonl  # write somewhere other than logs/events.jsonl (rules go to x-rules.json)
python -m sim --dashboard  # also serve the dashboard and open it in the browser
python -m sim --offline --dashboard --port 8090 --no-browser
```

Type a key, then Enter:

| Key | Does |
|---|---|
| `d` | doorbell |
| `m` | motion (one merged event). Armed: porch light, then a 15 s wait for a doorbell |
| `w` | end that wait now: no doorbell came, so the warning goes up |
| `t <text>` | speech, as if STT heard it. Speech that contains the passphrase disarms (it is redacted first) |
| `1` / `2` / `3` | Home / Away / Night. Home from an armed mode asks for the passphrase |
| `s` | status: mode, alert and the last 10 log entries |
| `q` | quit |

Every event goes through the real `brain/pipeline.py`: `modes/` (passphrase, alerts), `log/` (written to `logs/events.jsonl`), the `rules/` baseline, then Bonsai. It prints every event and action with its source (`rules` or `brain`), and how long each Bonsai call took. Hardware actions only print. `take_photo` gets a fake `photo_described` reply. The prompt shows `[mode/alert]`. Keys can be piped in too: `printf "d\nm\nq\n" | python -m sim --offline`.
