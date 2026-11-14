# brain/

**Owner: Dell laptop.**

Decides what to do. `handle_event(event, mode, recent) -> list[Action]` asks Bonsai 2 (LM Studio, OpenAI-compatible tool calling) with a **short** prompt and checks the reply in plain code. The brain can never disarm.

```python
from brain import Brain
brain = Brain.from_settings()            # config/settings.json, else settings.example.json
decision = brain.decide(event, "away", recent_log_entries)
decision.actions    # list[shared.Action], all valid
decision.via        # "bonsai", "fallback" or "code"
decision.elapsed_s  # Bonsai call time (also printed)
```

`pipeline.py` wires everything for one event: `modes/` (passphrase, alerts) → `log/` → `rules/` baseline (emitted at once) → "One moment, please." for visitors → Bonsai's actions on top, minus repeats. Then `set_mode` goes back through `modes/`. `on_action` / `on_event` callbacks fire as soon as each thing exists. See PLAN.md Q21.

| File | What it does |
|---|---|
| `core.py` | `Brain.decide()`: plain-code cases first, then Bonsai, then guard rails, then fallback. Builds the prompt |
| `tools.py` | One tool per action. Each (event, mode) pair is offered only a few tools, and `set_mode` is never offered while armed |
| `guard.py` | Parses tool calls (or JSON in the text) and drops unknown actions, tools that weren't offered, bad args, `set_mode` while armed, and rules that change mode |
| `fallback.py` | Keyword rules used when Bonsai errors, times out, returns garbage or nothing usable |
| `bonsai.py` | Stdlib HTTP client for LM Studio. Sends `reasoning_effort: "none"`, because with thinking on Bonsai spends `max_tokens` reasoning and never calls a tool |
| `pipeline.py` | `Pipeline.handle(event)`, the Dell-side entry point (PLAN.md Q21) |
| `safety.py` | Spots disarm requests so they're answered in code. The passphrase check and mode state are in `modes/`, and the brain never gets them |
| `settings.py` | Loads settings and picks the `llm.profile` |

Answered in plain code without calling Bonsai: `mode_changed`, disarm requests ("disarm", "turn off the alarm", ...) → "I can't disarm. Say the passphrase.", any speech that contains the passphrase, `alert_changed`, doorbell (the baseline takes the photo; the greeting comes on `photo_described`) and motion at Home. For `photo_described`, the brain is told why the photo was taken (from the `take_photo` reason in the log): doorbell → greeting, motion while armed → "You are on camera."

Tests (no LM Studio needed): `python -m unittest discover` from the repo root.

The model's reply format is described in [docs/FORMATS.md](../docs/FORMATS.md#what-the-llm-returns).
