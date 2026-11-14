# Faraday plan

Submissions close at **noon Sunday 2026-11-15**. Shapes of all messages: [FORMATS.md](FORMATS.md). Rules and ownership: [../CLAUDE.md](../CLAUDE.md).

## Goal

Faraday is a home guard that runs entirely on one Mac mini with Wi-Fi off. A Flipper Zero listens for a 433 MHz doorbell and motion sensor, and switches outlets for the porch light and living room lamp. A camera, mic and speaker let it see and talk. A small local model (Bonsai 2 in LM Studio) decides what to do, using a fixed menu of eight actions. It can never disarm the house: disarming takes a spoken or typed passphrase, checked in plain code. A big local dashboard on the projector shows the mode, the live event log, photos and saved messages.

What we want judges to remember: **useful, private, safe**. It's useful because it answers the door, controls lights, takes messages and learns rules by voice. It's private because nothing leaves the box, which we show by turning Wi-Fi off on stage. It's safe because the AI can't unlock the house.

## Demo script (about 3 minutes)

| # | Who does what | What Faraday does | Proves |
|---|---|---|---|
| 0 | Show Wi-Fi off on the Mac mini. Dashboard on the projector, mode **Home**, LED green | | Offline |
| 1 | Owner: "Faraday, I'm heading out." | `speech_heard` → brain → `set_mode away` → LED blue → speaks "Away mode on. I'll watch the door." | Voice to action |
| 2 | Visitor presses the **doorbell** | `doorbell` → `take_photo` → `photo_described` ("person holding a box") → speaks "Nobody can come to the door. Want to leave a message?" | Radio + vision |
| 3 | Visitor: "Package for Sam, I left it by the side door." | `speech_heard` → `save_message` → appears on the dashboard | Messages |
| 4 | Owner (remotely staged): switch to **Night** (LED purple). Wave at the **motion sensor** | `motion` → `outlet_on porch_light` (real lamp turns on) + `alert_changed warning` → LED yellow | Control |
| 5 | Owner: "From now on, when there's motion at night, turn on the living room lamp too." Wave again within 60 s | `add_rule` → rule shown on the dashboard. Both lights come on. Second motion within 60 s → `alert_changed alarm` → LED red | Learns rules |
| 6 | Someone says "Faraday, disarm" / "turn off the alarm" | Brain can't. Speaks "I can't disarm. Say the passphrase." | Safety |
| 7 | Owner says the passphrase | `modes/` checks it in code → `mode_changed home (passphrase)` + `alert_changed normal` → LED green | Safety |
| 8 | Owner: "What happened while I was out?" | `summarize_log` → spoken summary + dashboard card | Memory |

Backup: if the mic or STT fails on stage, the dashboard has a text box that sends the same `speech_heard` event. If the Flipper fails, `sim/` sends the same events and we say so out loud.

## Architecture in one picture

```
 hardware/flipper --doorbell/motion--+
 hardware/speech  --speech_heard-----+--> integration/ (bus, one process) --> log/ (logs/events.jsonl)
 brain/vision     --photo_described--+          |                       --> dashboard/ (reads log + state)
 sim/             --any event--------+          v
                                       brain.handle_event(event) -> [Action]
                                         (uses modes/, rules/, Bonsai)
                                                |
           hardware actions <-------------------+-------------> software actions
   outlet_on/off, speak, take_photo                  set_mode (modes/), save_message (log/),
   run by integration/ on hardware/                  summarize_log (brain/), add_rule (rules/)
                                                     modes/ emits mode_changed + alert_changed -> integration sets LED
```

### Seams (where the two laptops meet)

These are the only cross-team calls. Everything crossing them is a `shared.Event` or `shared.Action`.

1. **NV → Dell:** `integration/` calls `brain.handle_event(event) -> list[Action]` for every event, including sim events. It also hands every event and action to `log/`.
2. **Dell → NV:** for each returned action in `ActionType.HARDWARE`, `integration/` runs it. Dell code runs the other actions itself.
3. **Dell → NV:** `modes/` publishes `mode_changed` and `alert_changed` through the bus. `integration/` sets the Flipper LED from them: the alert colour (yellow/red) while the alert is not `normal`, otherwise the mode colour (green/blue/purple).
4. **Camera → vision:** `take_photo` makes `hardware/camera` save a JPG. `integration/` passes the path to `brain.describe_photo(path, caused_by)`, which returns a `photo_described` event to put back on the bus.

Exact function names may change by agreement. Write the change here first.

## Build order

1. **Step 0 (done):** skeleton, plan, formats, `shared/`, settings example.
2. Each laptop builds against **fakes** first. Dell uses `sim/` events and a print-only hardware executor. NV uses a stub `brain.handle_event` that returns fixed actions.
3. Real parts replace fakes one at a time, at the three joins.
4. Polish (dashboard looks, prompt tuning) only after Join 3 passes.

## Step split

| Step | NV laptop (hardware/, integration/) | Dell laptop (brain/, modes/, log/, rules/, dashboard/, sim/) |
|---|---|---|
| 1 | Flipper client: open the port from settings, start rx, parse Princeton lines, doorbell detection (hold) and motion merging → `doorbell`/`motion` events | `sim/` CLI that sends any event. `log/` JSONL writer/reader. `modes/` state + passphrase check + `mode_changed`. Alert state (warning/alarm, 60 s window) + `alert_changed` |
| 2 | Flipper tx with pause rx → send → resume. LED control from `mode_changed` + `alert_changed`. Bus + `integration/` main loop with stub brain | `brain/` Bonsai client, short prompt, parse `{"actions": [...]}`, validate. `rules/` engine |
| **Join 1** | **Real radio into Dell code.** Real doorbell press and motion wave → events in `logs/events.jsonl` and `modes/` reacts. Target: **Sat 18:00** | |
| 3 | Mic record + STT client (Argmax on Mac, stand-in on Windows) → `speech_heard`. TTS `speak` + playback | `dashboard/` local page: mode, live log, photos, messages, text box fallback. Big fonts for the projector |
| 4 | Camera snapshot → `captures/photos/`. Hardware action executor (outlets, speak, take_photo) | `brain.describe_photo` (Bonsai vision). `summarize_log`. `save_message` |
| **Join 2** | **Real actions from real decisions.** Doorbell → photo → description → spoken greeting, and motion in Night → porch light on. Done on one laptop with both halves. Target: **Sat 23:00** | |
| 5 | Mac setup: argmax-cli serve, TTS command, serial port, LM Studio 9B. Startup script | Prompt tuning on the 9B, dashboard polish, `docs/submission/` |
| **Join 3** | **Full demo on the Mac mini, Wi-Fi off,** run start to finish twice with no fixes in between. Target: **Sun 08:00** | |
| End | Code freeze **Sun 10:30**. Record the backup video. Submit by **Sun 11:30** | |

A step is only signed off once it has been tested on real hardware. Sim and stand-in runs show progress but don't count as done.

## Collision rules

1. **Own branch.** Name it `nv/<task>` or `dell/<task>`. Never commit straight to `main` after Step 0.
2. **Small PRs.** One task per PR, merged within the hour if possible. The other laptop gives it a quick look.
3. **Pull first.** `git pull` on `main` before branching and before opening a PR. Rebase or merge `main` in if it moved.
4. **Stay in your folders.** `shared/`, `config/`, `docs/PLAN.md`, `docs/FORMATS.md` and `CLAUDE.md` change only by agreement. Say it out loud, put both names in the PR, and update FORMATS.md and `shared/` in the same PR.
5. **Only real-hardware tests sign off** a step or a join.
6. **New ideas go in PLAN.md first.** Add them to Open Questions or the step table and agree on them before building. Nothing gets built just because it would be cool.
7. **Handoff after every task:** a note in `docs/team/history/` (see the README there).
8. **Git identity:** commit as the configured user. No Claude attribution anywhere (see CLAUDE.md).

## Open Questions

Add new ones at the bottom. When one is settled, write the answer next to it and who decided.

1. **Bonsai 9B model id on the Mac.** `bonsai-2-9b` in the settings example is a placeholder. Confirm with `curl http://localhost:1234/v1/models` on the Mac mini.
2. **Vision.** Which Bonsai 2 build accepts images in LM Studio (2B/4B/9B, which quant)? The rules allow no other model, so if none does, we need a plan B (for example, describe only from sensor context).
3. **Argmax TTS command.** The exact argmax-cli TTS subcommand and flags are not confirmed. The settings example has a guess. Does it write a WAV that we play, or does it play directly?
4. **Argmax STT server.** The exact `argmax-cli serve` start command and model on the Mac, plus warm-up time before the first request.
5. ~~**Night LED colour.**~~ **Closed 2026-11-14:** purple. Updated in CLAUDE.md and `settings.example.json`.
6. ~~**Alarm.**~~ **Closed 2026-11-14:** new `alert_changed` event (`source: modes`, `data: {level, previous, reason}`). The first motion in Away or Night sets `warning` (yellow). Motion again within 60 s sets `alarm` (red). Only the passphrase disarm returns to `normal`. Doorbell presses don't change the alert. Details are in FORMATS.md.
7. **Speech trigger.** Proposal: push-to-talk (a key or a dashboard button) for demo reliability. The alternatives are a "Faraday" wake word or always-on with VAD.
8. **Passphrase matching.** How tolerant should it be of STT errors? Proposal: lowercase, strip punctuation, exact match. The text is redacted before log and brain. A typed passphrase in the dashboard is the fallback.
9. **Action results.** Should a failed action (Flipper busy, app open, camera missing) produce an event so the dashboard can show it? A possible new type: `action_failed`.
10. **Doorbell debounce.** We fire after `min_duration_ms` of packets (800 ms proposed). How long do we ignore repeat presses afterwards?
11. **Motion merge window.** 5 s is proposed. Tune it on real hardware.
12. **Windows stand-ins.** Proposal: STT reads typed text from the terminal, TTS prints the text (plus optional local beep). Neither needs new dependencies.
13. **Dashboard tech.** Proposal: stdlib `http.server`, with a page that polls a JSON endpoint every second. No extra dependencies.
14. **save_message.** Text only, or also keep the visitor's audio clip path?
15. **Python version on the Mac mini.** `shared/` targets 3.9+. Check `python3 --version`.
16. **Brain seam signature (Dell).** The brain needs the current mode and recent log, so the call is `brain.handle_event(event, mode, recent) -> list[Action]`, where `recent` is the last ~10 log entries as dicts. `integration/` should pass both. OK with NV?
17. **`llm.reasoning_effort` setting (Dell).** Bonsai 2 is a thinking model. With thinking on, it spends all 200 `max_tokens` reasoning and returns no tool call (`finish_reason: length`). `brain/` sends `reasoning_effort: "none"` by default and reads an optional `llm.reasoning_effort`. Proposal: add `"reasoning_effort": "none"` to `settings.example.json`.
18. **FORMATS "What the LLM returns" (Dell).** `brain/` uses OpenAI tool calling (one tool per action), and also accepts `{"actions": [...]}` or tool-call JSON written in the text. Proposal: reword that FORMATS section to say tool calls are the main path. The action envelope doesn't change.
19. **Mode switching between armed modes (Dell).** The AI and rules can arm from Home, but can't switch Away↔Night (that counts as "leaving Away/Night"). Only an owner path (dashboard/sim key) can switch between armed modes. Also, `add_rule` can't contain `set_mode` at all. OK?
20. **Night motion → porch light (Dell).** The 4B q2_0 model often picks `take_photo` instead of the porch light. Proposal: `rules/` starts with a built-in rule "motion at night → `outlet_on porch_light`", so demo step 4 doesn't depend on the model.
