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

`pipeline.py` wires everything for one event:
1. `modes/` screens the passphrase and sets alerts.
2. `log/` writes the event.
3. The `rules/` baseline goes out at once, escalating with the alert.
4. Stored user rules fire.
5. "One moment, please." is said for visitors.
6. Bonsai's actions are added on top, minus repeats.

Then the software actions run: `set_mode` goes back through `modes/`, `add_rule` is stored in `rules/`, and `summarize_log` is spoken. At an alarm (Away or Night), a background thread asks Bonsai for one confrontation line once the photo description arrives. It's dropped if the owner disarmed meanwhile. `on_action` / `on_event` fire as soon as each thing exists, and one lock serializes the app, the dashboard and background threads. See PLAN.md Q21/Q22.

| File | What it does |
|---|---|
| `core.py` | `Brain.decide()`: plain-code cases first, then Bonsai, then guard rails, then fallback. Builds the prompt |
| `tools.py` | One tool per action. Each (event, mode) pair is offered only a few tools. `set_mode` is shown as `arm_away` / `arm_night` with no arguments (with a `mode` argument the 4B kept writing "home"), and it's never offered while armed. In Away (a visitor) speech gets only `save_message` and `speak`: no rules, lights or summary |
| `guard.py` | Parses tool calls (or JSON in the text) and drops unknown actions, tools that weren't offered, bad args, `set_mode` while armed, and rules that change mode |
| `fallback.py` | Keyword rules used when Bonsai errors, times out, returns garbage or nothing usable. Includes `parse_rule()` for rules by voice |
| `summary.py` | `summarize_log`. The window is "since the house was last armed". Plain code collects the facts, `Brain.summarize()` turns them into speech, and `spoken_list()` is the fallback |
| `bonsai.py` | Stdlib HTTP client for LM Studio. Sends `reasoning_effort: "none"`, because with thinking on Bonsai spends `max_tokens` reasoning and never calls a tool |
| `pipeline.py` | `Pipeline.handle(event)`, the Dell-side entry point (PLAN.md Q21) |
| `safety.py` | Spots disarm requests so they're answered in code. The passphrase check and mode state are in `modes/`, and the brain never gets them |
| `settings.py` | Loads settings and picks the `llm.profile` |
| `prompts.py` | Prompt profiles. `small` is for 2B/4B and tuned with the bench. `9b` is for the Mac mini. Picked from the model id, or `llm.prompt_profile` |
| `vision.py` | `describe_photo(path, caused_by, describer)` gives a `photo_described` event (PLAN.md seam 4). It tries Bonsai vision, and if that fails it still returns a valid event saying "No description available" |
| `bench.py` | `python -m brain.bench [--profile mac] [--prompts 9b]`: every demo moment 10 times against Bonsai, plus reworded variants and two hard visitor lines. Writes `docs/bench/<date>-<model>-prompts-<profile>.md` |

Answered in plain code without calling Bonsai: `mode_changed`, disarm requests ("disarm", "turn off the alarm", ...) → "I can't disarm. Only the owner can do that." (it never mentions the passphrase), any speech that contains the passphrase, `alert_changed`, doorbell and all motion (the baseline handles those), and "what happened while I was out?" at Home (straight to `summarize_log`, saving one model round trip).

Code fix-ups on top of Bonsai's answer, all recorded in `Decision.fixups`, so the benchmark can count them as "model wrong":
- It promised "I'll take that message" but didn't save it: code saves the visitor's words.
- The owner clearly said "heading out" or "goodnight" and Bonsai armed the other mode: code goes with the words.
- It armed the house, saved a message or stored a rule without saying anything: code adds a spoken confirmation.
- A doorbell greeting with no offer to take a message: code adds the offer.
- The visitor replied to "Would you like to leave a message?" and Bonsai answered with something else: code saves the reply as the message.
- The spoken summary left out the visitor's message: code adds it (`summary.ensure_messages`).

Free-text Bonsai calls (no tools): `Brain.summarize(facts)` and `Brain.confront(description)`. Each returns one clean line, or `None` when there's no model or the call fails.

`photo_described` is traced back through `caused_by` (`photo_described` → `take_photo` → event, see `photo_cause()`):
- **Doorbell photo (Away):** Bonsai is asked to greet and offer to take a message. If its reply never mentions a message, code adds the offer. The fallback does the same.
- **Motion photos and anything at Night:** nothing. The warning lines are spoken by the baseline at motion time, and Night stays quiet.

Tests (no LM Studio needed): `python -m unittest discover` from the repo root.

The model's reply format is described in [docs/FORMATS.md](../docs/FORMATS.md#what-the-llm-returns).
