# sim/

**Owner: Dell laptop.**

Fake hardware for development. It sends any FORMATS.md event with `source: "sim"`, and other code must treat sim events exactly like real ones. It's useful on Windows laptops without the Flipper and as the stage backup. Sim runs never sign off a step; only real-hardware tests do.

```
python -m sim              # uses Bonsai from config/settings.json
python -m sim --offline    # rule-based fallback only, no LM Studio needed
python -m sim -v           # also print full JSON for every event and action
python -m sim --log x.jsonl  # write somewhere other than logs/events.jsonl (rules go to x-rules.json)
python -m sim --dashboard  # also serve the dashboard and open it in the browser
python -m sim --offline --dashboard --port 8081 --no-browser
python -m sim --demo --dashboard           # BACKUP DEMO: plays the PLAN.md script by itself
python -m sim --demo --dashboard --manual  # same, press Enter before each step
python -m sim --demo --offline --speed 3   # quick rehearsal without Bonsai
```

**Backup demo** (`sim/demo.py`): if a sensor or the Flipper fails on stage, stop the app and run this. It plays all 8 steps of the PLAN.md demo with realistic pauses, through the real pipeline and the real dashboard on the same port, so the page on the projector picks it up by itself.
- **Clean start.** It uses its own `logs/demo-events.jsonl` and rules file, wiped every run.
- **Passphrase.** It types the passphrase from settings, and never prints it.
- **Keeps going.** If Bonsai mishears a step, it switches the mode by hand and notes it at the end.
- **Honest log.** Events are `source: "sim"`, so say out loud that the sensors are simulated.

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
