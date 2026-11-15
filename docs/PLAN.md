# Faraday plan

Submissions close at **noon Sunday 2026-11-15**. Shapes of all messages: [FORMATS.md](FORMATS.md). Rules and ownership: [../CLAUDE.md](../CLAUDE.md).

## Goal

Faraday is a home guard that runs entirely on one Mac mini with Wi-Fi off. A Flipper Zero listens for a 433 MHz doorbell and motion sensor, and switches outlets for the porch light and living room lamp. A camera, mic and speaker let it see and talk. A small local model (Bonsai 2 in LM Studio) decides what to do, using a fixed menu of eight actions. It can never disarm the house: disarming takes a spoken or typed passphrase, checked in plain code. A big local dashboard on the projector shows the mode, the live event log, photos and saved messages.

What we want judges to remember: **useful, private, safe**. It's useful because it answers the door, controls lights, takes messages and learns rules by voice. It's private because nothing leaves the box, which we show by turning Wi-Fi off on stage. It's safe because the AI can't unlock the house.

## AI pipeline

**The 9B sees, the 2B judges, Argmax hears and counts voices, the 9B talks, and code guarantees safety.** Each model does the job it's best at, and nothing safety-related depends on any of them.

| Step | Who | What | Where |
|---|---|---|---|
| Hears | Argmax speech-to-text (`argmax-cli serve`, port 50060) | turns speech into text. The passphrase is blanked by code before anything is logged or sent to a model | `hardware/speech.py`, `modes/passphrase.py` |
| Counts voices | Argmax diarization (`argmax-cli diarize`) | how many people spoke in a visitor recording (`speech_heard.voices`). It never delays the reply | `hardware/diarize.py` |
| Sees | Bonsai 9B (vision) | one or two sentences about each photo | `hardware/vision.py` |
| Judges | Bonsai 2B | delivery, visitor, suspicious or nobody after each photo, using the description, the mode, the doorbell/motion history and the voice count. Code checks the answer, and rules decide if the 2B can't | `brain/threat.py` |
| Talks | Bonsai 9B (the 2B for quick owner commands at Home) | the visitor conversation, owner instructions passed on, the confrontation line, the spoken summary | `brain/core.py` |
| Speaks | Argmax text-to-speech | Faraday's voice through the speaker | `hardware/speech.py` |
| Guarantees safety | plain code | modes, alerts, lights, photos, the passphrase, checks on every model answer, owner instructions as words only, "who's at the door?" | `modes/`, `rules/`, `brain/guard.py`, `brain/look.py` |

The model ids are in the `mac` profile: `ternary-bonsai-2-9b-mlx` (smart) and `ternary-bonsai-2-2b-mlx` (fast). **[TBD: Mac mini timings for each]**

## Accessibility

- **Eyes at the door, for a blind or low-vision owner.** "Hey Faraday, who's at the door?" takes a fresh photo and says what the camera sees, plus the threat label ("It looks like a delivery."). A doorbell at Home is announced out loud. Everything else works by voice: arming, lights, rules, instructions for visitors and "what happened while I was out?".
- **Captions, for a deaf or hard-of-hearing owner.** Everything heard and said at the door is written out as it happens: Heard & Said on the dashboard, and the big-text **Live captions** card on the Controls page. Doorbells and alarms show as banners, the threat label as a coloured pill, and the live camera is on the Controls page.

## Demo script (about 3 minutes)

| # | Who does what | What Faraday does | Proves |
|---|---|---|---|
| 0 | Show Wi-Fi off on the Mac mini. Dashboard on the projector, mode **Home** (Flipper LED off) | | Offline |
| 1 | Owner: "Faraday, I'm heading out." | `speech_heard` → brain → `set_mode away` → dashboard AWAY → speaks "Away mode on. I'll watch the door." | Voice to action |
| 2 | Visitor **walks up** (motion sensor), then presses the **doorbell** | Motion: porch light on, **no warning** (15 s grace period). Doorbell: the fixed greeting at once ("Hello. No one can come to the door right now. I can take a message or help with a delivery.") → `take_photo` → `photo_described` → label visitor/delivery. The alert stays normal | Radio + vision, no false alarms |
| 3 | Visitor: "Package for Sam, I left it by the side door." | `speech_heard` → `save_message` → appears on the dashboard | Messages |
| 4 | Owner (remotely staged): switch to **Night**. Someone waves at the **motion sensor** and stays, without ringing | `motion` → `outlet_on porch_light` at once (real lamp turns on). No doorbell within 15 s → `alert_changed warning`, the warning line, `take_photo`, label "suspicious: stayed at the door without ringing" → dashboard WARNING (LED stays off) | Control, judgment |
| 5 | Owner: "From now on, when there's motion at night, turn on the living room lamp too." Wave again 8 to 60 s after the warning | `add_rule` → rule shown on the dashboard. Second motion → `alert_changed alarm` → both lights, the alarm line, Flipper LED red + 3 buzzes, then the confrontation | Learns rules |
| 6 | Someone says "Faraday, disarm" / "turn off the alarm" | Brain can't. Speaks "I can't disarm. Only the owner can do that." (never mentions the passphrase) | Safety |
| 7 | Owner says the passphrase | `modes/` checks it in code → `mode_changed home (passphrase)` + `alert_changed normal` → LED off | Safety |
| 8 | Owner: "What happened while I was out?" | `summarize_log` → spoken summary + dashboard card | Memory |

Backup: if the mic or STT fails on stage, the dashboard has a text box that sends the same `speech_heard` event. If the Flipper fails, `sim/` sends the same events and we say so out loud.

## Mode table (baseline, plain code)

Lights, photos and warnings are guaranteed by plain code in `modes/` and `rules/baseline.py`, so they happen even if Bonsai is slow, wrong or down. Bonsai adds speech, judgment and descriptions on top, and anything it repeats is dropped (decided in Open Question 20). **Lights escalate with the alert level** (corrected 2026-11-14).

| Event | Home | Away | Night |
|---|---|---|---|
| `doorbell` | announce only ("Someone is at the door.") | the fixed greeting at once (code), `take_photo`, then the owner's words for visitors. Bonsai replies only to what the visitor says → `save_message`. A pending motion warning is cancelled | `outlet_on porch_light`, `take_photo`, quiet (no speech; the dashboard shows "Doorbell at night"). A pending motion warning is cancelled |
| `motion` (alert normal) | nothing | `outlet_on porch_light` at once, then a 15 s grace period (`alerts.motion_grace_s`). Nothing else yet | same as Away |
| no doorbell by the end of the grace period | | `alert_changed warning`, `take_photo`, "You're on camera. The owner has been alerted." | same as Away (the warning line is spoken at Night too) |
| `motion` within 120 s after a doorbell (`alerts.visitor_quiet_s`) | nothing | `outlet_on porch_light` only: the visitor is there, never a warning or an alarm | same as Away |
| `motion` 8 to 60 s after the warning, no doorbell in between | nothing | `alert_changed alarm`, also `outlet_on living_room_lamp`, `take_photo`, "Alarm. You are being recorded. Leave now.", then a confrontation | same as Away (the alarm line is spoken at Night too) |

Notes:
- **Alert levels** (in `modes/`, owner 2026-11-15: a visitor who walks up and rings is never warned or alarmed). The same in Away and Night:
  - Motion while the alert is normal: the porch light goes on at once, and a grace period starts (`alerts.motion_grace_s`, 15 s).
  - A doorbell within the grace period cancels the warning, and the alert stays normal. After a doorbell, motion doesn't escalate for `alerts.visitor_quiet_s` (120 s).
  - No doorbell by the end of the grace period: `warning`, with the warning line, the photo and the threat label.
  - `alarm` only from motion at least `alerts.min_gap_s` (8 s) and at most `alerts.alarm_window_s` (60 s) after the warning, with no doorbell in between. Sooner is the same wave. Later is a new walk-up: a new grace period, and if nobody rings the warning steps run again (the level stays warning), so a courier who trips the sensor long after an old warning and then rings is never alarmed.
  - The doorbell radio reports a press after the burst ends, so a press counts until about 2.5 s after the grace period (`flipper.burst_gap_s` + 1 s).
  - No user-rule photo while the grace period or a visitor's quiet period runs: its label ("nobody rang yet") would warn a visitor before they ring. The grace period's own photo covers it.
  - A `suspicious` label while armed raises normal to warning at once.
  - Only the passphrase returns the alert to `normal`. A mode change ends a visitor's quiet period and a pending warning. Motion at Home never raises an alert.
- **Confrontation, at alarm in Away and Night**: plain code speaks the alarm line first, in both modes (the Mac test had a silent Night alarm). Once the photo description arrives, Bonsai is asked in a background thread for ONE short line that mentions what it sees, so nothing waits for it. If Bonsai's line is unusable, code says its own ("You are on camera. I can see ... Please leave now."), unless the camera or vision failed. The line is dropped if it comes back after the owner disarmed.
- **LM Studio on the Mac:** raise the model's Parallel setting (concurrent requests) above 1 when loading the 9B. Otherwise a background confrontation queues behind any other Bonsai call: on the NV laptop it waited 15 s behind a summary.
- **No reply to a photo** (owner, Mac test): the doorbell greeting is fixed and spoken by code at the ring, and Bonsai only answers what a visitor says. A photo gets its threat label (dashboard, summary, alerts) and that's all, except the answer to "who's at the door?".
- **Never waits for Bonsai:** motion and the doorbell are handled entirely in plain code. "One moment, please." is gone.
- **Who's at the door?** At Home or Night, plain code answers: "Let me look.", a fresh photo, then the vision description plus the threat label ("It looks like a delivery."), or "I can't see the door right now." if the camera or vision fails or takes over 25 s (`brain/look.py`). Everything said at the door is shown as live captions (dashboard Heard & Said, and the Controls page's Live captions card).
- **Owner instructions:** at Home, "if a delivery comes, ask them to leave it by the side door" is stored by plain code (`rules/instructions.py`, survives restarts, listed and removable on the dashboard). The relevant ones go into the visitor prompt and the delivery / greeting line. They only shape speech: requests about lights, modes, the alarm, locks or codes are refused.
- **Lights off on disarm:** the passphrase disarm switches off every light Faraday turned on (an `outlet_off` with `source: rules` through the normal pipeline, `rules/baseline.py`). Lights the owner switched on from the dashboard stay on.
- **Threat label:** after each photo description, the fast Bonsai classifies the scene as delivery, visitor, suspicious or nobody (`brain/threat.py`). Code validates the answer, and rules decide if Bonsai can't. Walked up and rang, with no warning signs: visitor or delivery (code overrides a "suspicious"). Stayed at the door without ringing while armed: suspicious. `suspicious` while armed raises the alert to warning. Warning signs in the photo (a mask, forcing the door) bring the confrontation forward; staying without ringing only gets the warning, and the alarm brings the confrontation. A photo from motion only counts doorbells from that motion on. It can never lower safety.
- **What a speaker may ask for depends on the mode:**
  - Home (the owner): arm, lights, rules, "what happened?".
  - Away (a visitor): messages and talk only.
  - Night: messages, talk, rules and lights, but no summary.

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
                                                     modes/ emits alert_changed -> integration: LED red + buzz at alarm only
```

### Seams (where the two laptops meet)

These are the only cross-team calls. Everything crossing them is a `shared.Event` or `shared.Action`.

1. **NV → Dell:** `integration/` hands every event, including sim events, to Dell code. The brain itself is `brain.handle_event(event, mode, recent) -> list[Action]` (Q16). The proposed full entry point is `brain.pipeline.Pipeline.handle(event)`, which also runs `modes/`, `log/` and the baseline (see Q21).
2. **Dell → NV:** for each returned action in `ActionType.HARDWARE`, `integration/` runs it. Dell code runs the other actions itself.
3. **Dell → NV:** `modes/` publishes `mode_changed` and `alert_changed` through the bus. `integration/` passes both to the Flipper driver, which acts only on `alert_changed`: level `alarm` → LED red plus three buzzes, any other level → LED off. Modes never light the LED (decided 2026-11-14, see Open Question 24); the dashboard shows the mode.
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
| 2 | Flipper tx with pause rx → send → resume. LED red + buzz at alarm only. Bus + `integration/` main loop with stub brain | `brain/` Bonsai client, short prompt, parse `{"actions": [...]}`, validate. `rules/` engine |
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

## Cross-laptop hooks and fields

Agreed fields and hooks between Dell and NV code. Add a row before building anything that crosses the seam.

| What | Owner | Status | Notes |
|---|---|---|---|
| `threat_assessed` event | Dell (brain/) | Built, Dell PR "threat" | FORMATS.md. Sent after every `photo_described`. Category `delivery` / `visitor` / `suspicious` / `nobody` plus a reason. Shapes speech. `suspicious` while armed raises normal to warning in `modes/` and brings the confrontation forward. It never lowers an alert, switches lights off, changes the mode or disarms |
| `llm.profiles.<p>.fast_model` setting | Dell (brain/) | Built, Dell PR "models" | `model` = the smart Bonsai (9B: visitor conversation, confrontation, summary, vision), `fast_model` = the small one (2B: threat label, quick owner commands). The Mac uses `ternary-bonsai-2-9b-mlx` + `ternary-bonsai-2-2b-mlx`, and Windows uses the 4B for both. **NV 2026-11-14: done in #32.** `start_mac.sh` fails if `model` or `fast_model` isn't downloaded and loaded, and warns to eject anything else |
| `board.control_hook(outlet, on) -> bool` + `POST /api/control` | NV (hook), Dell (route, switches) | Built, both sides | Q26 accepted by Dell. Body `{"outlet": "porch_light", "on": true}` with the `X-Faraday: 1` header. 400 for an unknown outlet or a non-bool `on`, 503 if the hook isn't set, else `{"ok": true, "changed": <hook result>}`. `/api/state` has `control_available`. The tiles always show the state from the log (the hook logs a `source: dashboard` action), never the click. The sim sets a stand-in (`sim/controls.py`) with the same contract |
| Controls page media (Q27): `GET /stream.mjpg`, `GET /api/status`, `POST /api/intercom` + `/api/intercom/end` on dashboard port + 1 | NV (server), Dell (page) | Built, both sides | Q27 accepted by Dell. Dell's `/controls` page uses them cross-origin (NV's CORS allows the dashboard port). It finds the port through `controls_port` in `/api/state` (settings `controls.port`, else dashboard port + 1). Opt-in browser test: `FARADAY_BROWSER=1 python -m unittest dashboard.tests.test_controls_page` holds Talk on Dell's page with a fake mic, and the audio reaches NV's intercom |
| ~~`board.listen_hook()` + `POST /api/listen`~~ (removed), `board.listening_hook() -> bool` | NV (hook), Dell (indicator) | listening_hook built (NV #39). **Listen button: owner 2026-11-14 says remove it; Dell removes the button, NV PR "hands-free 2" no longer sets `listen_hook`/`talk_hook`** | Windows open by themselves (doorbell, question, wake phrase); the mic's own mute button is the off switch. `/api/state` keeps `listening` (true only while a visitor/owner window is actually recording: not during wake listening, not while waiting for Faraday to finish, not while muted). Dell: `listen_hook` and `talk_hook` stay `None` now, so `listen_available` is false; the button and `/api/listen` can go |
| Owner snapshot: `POST /api/snapshot` → `Pipeline.owner_snapshot()` | Dell | Built | A `take_photo` with `source: dashboard` and reason "Snapshot from the dashboard". NV's camera worker handles it like any `take_photo`. Its `photo_described` gets the threat label but no visitor reply. `/api/state` `photo.request` is the `take_photo` id it answers |
| `speech_heard.voices` (int or null) | NV (diarization) | Built, NV PR #30; Dell confirmed | The voice count from Argmax diarization, in `shared/` and FORMATS.md. Dell's threat label reads the latest one (`Pipeline._recent_voices`) and passes it to the label prompt and the `threat_assessed.voices` field |
| `speech_heard.channel` ("owner" / "visitor" / null) | NV (sets it), Dell (routes on it) | Built, NV PR "hands-free 2"; **Dell confirmed 2026-11-14 (Dell PR "owner router")**: `owner` goes to `brain/owner.py` (fixed intents, never a visitor message, nothing changes while armed except the passphrase); `visitor` gets the visitor tools only (talk, save a message) in every mode, so a visitor at Night can't add rules or switch lights; `null` keeps the old meaning (owner at Home, visitor while armed) | `owner`: the line came after the wake phrase "Hey Faraday" (the request in the same breath, or the next segment after the phrase alone), or answers a question Faraday asked *in reply to* an owner request (the speak action's `caused_by` is that owner speech_heard; NV PR "hands-free fixes"). `visitor`: a listening window (doorbell, Faraday's question to a visitor). `null`: typed text (console `t`, dashboard box). In `shared/events.py` as `SpeechChannel`, validated. Owner 2026-11-14: Dell routes on it |
| `photo_described.described_by / people / faces / detections` | NV (camera), Dell (uses them) | Built, NV PR "vision + detection"; **Dell please confirm (Q30)** | Owner 2026-11-14: a small non-LLM object detector (YOLOX-tiny, OpenCV DNN, Apache-2.0, model in `hardware/models/`) runs on the live stream (labelled boxes) and on every photo. `people` and `detections` ([{label, count, confidence}]) and `faces` (visible faces) go on photo_described. If vision fails, `description` comes from the detector and `described_by` is `"detector"` with source `vision`, so `brain/look.py` speaks it instead of "I can't see the door right now". Source `camera` still means no description at all |
| `recording_saved` event (new type) | NV (writes it), Dell (shows "Recording saved") | Built, NV PR "fresh + alarm recording"; **Dell please show it (Q33)** | Owner 2026-11-14: on alarm, about 10 s of frames go to `captures/alarm-<time>/` (frame-0001.jpg ... plus clip.avi). Then `integration/` writes `recording_saved` {path, frames, duration_s, video_path, reason} straight to the log (source camera, caused_by the alert_changed); it never goes through the pipeline. See FORMATS.md |

## Open Questions

Add new ones at the bottom. When one is settled, write the answer next to it and who decided.

1. ~~**Bonsai 9B model id on the Mac.**~~ **Closed 2026-11-14 (owner):** `ternary-bonsai-2-9b-mlx`, server `http://127.0.0.1:1234`, Parallel 1. The Mac also has `qwen/qwen3.8-27b` and `prism-ml/bonsai-27b` installed, which the rules don't allow: the `mac` profile names the 9B exactly and `integration/start_mac.sh` fails unless it is the *loaded* model (LM Studio's `/api/v0/models` state). The full Mac `settings.json` is in `docs/team/history/2026-11-14-1900-nv-mac-settings.md`.
2. ~~**Vision.**~~ **Closed 2026-11-14 (owner):** the 4B (`ternary-bonsai-2-4b@q2_0`) accepts images in LM Studio, as checked by both Dell and NV. NV's `hardware/vision.py` is the one wired in, and it shrinks photos to 512 px. The 9B on the Mac is still to be checked in the 9B benchmark session. Original: Which Bonsai 2 build accepts images in LM Studio (2B/4B/9B, which quant)? The rules allow no other model, so if none does, we need a plan B (for example, describe only from sensor context).
    **Partly answered 2026-11-14 (Dell):** `ternary-bonsai-2-4b@q2_0` in LM Studio on the NV laptop does accept images.
    - It described a generated green triangle correctly. Asked the same question with no image, it made something up.
    - Speed: a 256 px image took about 3.5 s, a fresh 1280×720 about 15 s, and 1920×1080 timed out at 30 s. So `brain/vision.py` shrinks photos to 640 px when OpenCV is installed.
    - Still to check: real camera photos, and the 9B on the Mac.
3. **Argmax TTS command.** The exact argmax-cli TTS subcommand and flags are not confirmed. The settings example has a guess. Does it write a WAV that we play, or does it play directly?
4. **Argmax STT server.** The exact `argmax-cli serve` start command and model on the Mac, plus warm-up time before the first request.
5. ~~**Night LED colour.**~~ **Closed 2026-11-14, then superseded by 24 (no mode colours on the LED):** purple. Updated in CLAUDE.md and `settings.example.json`.
6. ~~**Alarm.**~~ **Closed 2026-11-14:** new `alert_changed` event (`source: modes`, `data: {level, previous, reason}`). The first motion in Away or Night sets `warning` (yellow). Motion again within 60 s sets `alarm` (red). Only the passphrase disarm returns to `normal`. Doorbell presses don't change the alert. Details are in FORMATS.md.
7. ~~**Speech trigger.**~~ **Closed 2026-11-14 by 25:** listening windows and "Hey Faraday", no push-to-talk. Original proposal: push-to-talk (a key or a dashboard button) for demo reliability. The alternatives are a "Faraday" wake word or always-on with VAD.
8. ~~**Passphrase matching.**~~ **Closed 2026-11-14:** ignore case and punctuation, split hyphens, and treat digits as number words ("17" = "seventeen"). The passphrase words must appear in order. The example passphrase is plain words with no numbers. The text is redacted before the log, the brain and any display. A typed passphrase is the fallback. Code: `modes/passphrase.py`.
9. **Action results.** Should a failed action (Flipper busy, app open, camera missing) produce an event so the dashboard can show it? A possible new type: `action_failed`.
10. **Doorbell debounce.** We fire after `min_duration_ms` of packets (800 ms proposed). How long do we ignore repeat presses afterwards?
11. **Motion merge window.** 5 s is proposed. Tune it on real hardware.
12. **Windows stand-ins.** Proposal: STT reads typed text from the terminal, TTS prints the text (plus optional local beep). Neither needs new dependencies.
13. **Dashboard tech.** Proposal: stdlib `http.server`, with a page that polls a JSON endpoint every second. No extra dependencies.
14. **save_message.** Text only, or also keep the visitor's audio clip path?
15. **Python version on the Mac mini.** `shared/` targets 3.9+. Check `python3 --version`.
16. ~~**Brain seam signature (Dell).**~~ **Closed 2026-11-14:** yes, `brain.handle_event(event, mode, recent) -> list[Action]`, where `recent` is the last ~10 log entries as dicts.
17. ~~**`llm.reasoning_effort` setting (Dell).**~~ **Closed 2026-11-14:** yes. `"reasoning_effort": "none"` is in `settings.example.json`. With thinking on, Bonsai 2 spends all of `max_tokens` reasoning and returns no tool call.
18. ~~**FORMATS "What the LLM returns" (Dell).**~~ **Closed 2026-11-14 (owner):** tool calling is the main path. FORMATS.md "What the LLM returns" is reworded to say so, and the action envelope is unchanged. Original: `brain/` uses OpenAI tool calling (one tool per action), and also accepts `{"actions": [...]}` or tool-call JSON written in the text. Proposal: reword that FORMATS section to say tool calls are the main path. The action envelope doesn't change.
19. ~~**Mode switching between armed modes (Dell).**~~ **Closed 2026-11-14:** yes, strict. The AI and rules can arm from Home but never switch Away↔Night. Only an owner path (dashboard/sim key) can. `add_rule` can't contain `set_mode`.
20. ~~**Night motion → porch light (Dell).**~~ **Closed 2026-11-14, widened:** all the mode table responses (lights, photos, warnings) are guaranteed by plain code in `modes/` and `rules/`. The AI adds speech, judgment and descriptions on top. See "Mode table" above.
21. ~~**Pipeline entry point (Dell → NV).**~~ **Closed 2026-11-14:** yes, NV's `integration/` calls `brain/pipeline.py` as below. Original proposal: `integration/` calls `Pipeline.from_settings(on_event=..., on_action=...)` once, then `pipeline.handle(event)` for every event. In order, the pipeline:
    - screens speech for the passphrase,
    - writes the event and every action to `logs/events.jsonl` (so `integration/` doesn't log them again),
    - updates the alert,
    - emits the baseline actions at once,
    - says "One moment" for visitors,
    - adds Bonsai's actions.

    `on_action(action)` is called as soon as each action exists, so hardware runs without waiting for Bonsai. `on_event(event)` gets every `mode_changed` / `alert_changed` for the bus and LED. Typed passphrase: `pipeline.owner_passphrase(text)`. Dashboard Away/Night: `pipeline.owner_set_mode(mode)`. OK with NV?
22. ~~**Dashboard startup and threads (Dell → NV).**~~ **Closed 2026-11-14 (owner):** `integration/` starts the dashboard next to the pipeline and runs `speak` / `take_photo` / outlets on their own workers, so `on_action` from any thread is safe (NV PR #6). Original: The dashboard is `dashboard.Dashboard.from_settings(pipeline, settings).start()`, a stdlib server on `dashboard.host:port` (127.0.0.1:8080). Proposal: `integration/` starts it next to the pipeline. Note that `on_action` can now be called from other threads (the alarm confrontation, dashboard buttons, the text box), so the hardware executor must be thread-safe, for example by putting actions on a queue. Pipeline entry points take a lock, so events never interleave. OK with NV?
23. ~~**Real photos on the dashboard (Dell → NV).**~~ **Closed 2026-11-14 (owner):** the camera saves `.jpg` files under `captures/photos/`. The dashboard shows the newest one right away and serves it through `/media` (NV PR #7, Dell PR #10). Original: The page shows `photo_described.photo_path` through `/media/<path>`, but only for `.jpg`/`.png` files inside `captures/`. `hardware/camera` should save there, as FORMATS.md already says.
24. ~~**Flipper LED colours (NV → owner).**~~ **Closed 2026-11-14 (owner):** dropped. The LED is **off** in every mode and at warning; only an **alarm** shows **red plus three buzzes**, and it goes off again at the passphrase disarm. Reason: the stock firmware's charge indicator owns the LED's red and green while `subghz rx` runs (listening pauses charging, and the indicator rewrites those channels about a second after listening restarts), so on the real device green never showed and purple and blue looked the same. It can't be turned off from the CLI. The dashboard is the status display. Code: `hardware/flipper/device.py`; settings `flipper.led` keeps only `alarm` (the dashboard has its own defaults for the rest). ~~FORMATS.md and the submission drafts still mention mode colours; Dell to update.~~ Done 2026-11-14 (Dell PR "docs"): FORMATS.md now says the LED is off except red plus three buzzes at alarm. The drafts only mention the dashboard's colours, which stay.
25. ~~**Hands-free listening (owner).**~~ **Closed 2026-11-14 (owner):** push-to-talk is gone. The mic opens only in *listening windows*: after a doorbell (once Faraday has greeted, or after 4 s), after every spoken line that ends with "?", from the dashboard Listen button, and for the wake listener. A window records up to 6 s, ends about 0.8 s after the speaker stops (energy VAD), or after 3 s if nobody spoke; the audio goes to Argmax STT → `speech_heard` (`source: speech`). The mic is always muted while Faraday speaks. **Wake phrase:** while idle, short windows run back to back and a transcript is acted on only if it starts with "hey faraday"; plain "Faraday" and everything else is dropped and the audio deleted. Settings: `audio.listen`, `audio.wake`. Code: `hardware/audio.py`, `integration/voice.py`.
26. **Dashboard hooks (NV → Dell).** `integration/` sets these on the `Dashboard` object; Dell adds the buttons and routes (same pattern as `talk_hook`):
    - **Dell 2026-11-14: listen_hook accepted and built** (Listen button; also asks NV for an optional `listening_hook`, see the Cross-laptop table).
    - `board.listen_hook() -> bool`: opens one listening window (a **Listen** button). `talk_hook` now does the same, so the existing Talk button already works; rename when convenient (`talk_available` → `listen_available`).
    - **Dell 2026-11-14: control_hook and `POST /api/control` accepted and built** (see the Cross-laptop table).
    - `board.control_hook(outlet: str, on: bool) -> bool`: switches `porch_light` / `living_room_lamp` / `spare` through integration's dispatcher (same skip-repeat rule as the pipeline's actions). It writes the `outlet_on`/`outlet_off` action to the log with `source: dashboard`, so the Lights panel follows. Returns False if the outlet is already in that state. Proposed route: `POST /api/control {"outlet": ..., "on": true}`. OK with Dell?
27. **Dell 2026-11-14: accepted and built** (see the Cross-laptop table; `/controls` page). **Media for the Controls page (NV → Dell).** Built (NV, `integration/controls.py`). The dashboard server is JSON-only with a 4 KB body cap, so camera video and intercom audio run on a second NV server on **dashboard port + 1**: `http://127.0.0.1:8081` next to the dashboard's `http://127.0.0.1:8080` (override: `controls.port`, `controls.host`). Routes: `GET /stream.mjpg` (MJPEG, use as `<img src>`), `GET /frame.jpg`, `GET /api/status`, `POST /api/intercom` (raw 16-bit mono PCM chunks, header `X-Sample-Rate`, 16000 recommended), `POST /api/intercom/end`, `POST /api/control {"outlet", "on"}`, `POST /api/listen`. POSTs need `X-Faraday: 1`; CORS is allowed for pages on port 8080 or 8081 (any host). A reference page at `http://127.0.0.1:8081/` works end to end (camera, hold-to-talk intercom, lights, Listen); its JavaScript is the example for Dell's page. The webcam has one owner (`hardware/camera.py` `CameraFeed`), shared by photos and the stream. **Chrome trust step for the mic:** on the Mac itself `http://127.0.0.1:8080` and `:8081` are secure contexts already, nothing to do. From another device: set `dashboard.host` to `0.0.0.0`, then in Chrome on that device add `http://<mac-ip>:8080,http://<mac-ip>:8081` to `chrome://flags/#unsafely-treat-insecure-origin-as-secure` and relaunch. OK with Dell?
28. **Dell 2026-11-14: confirmed.** The threat label already reads it. **`speech_heard.voices` (NV → Dell).** Built (owner-approved field; Dell please confirm). Optional int: distinct voices in a **visitor** recording, from `argmax-cli diarize` (`hardware/diarize.py`) started alongside STT. STT never waits for it by default (`stt.mac.diarize.grace_s: 0`): the count is attached only if diarization finished first, otherwise the console prints it as late. Owner "Hey Faraday" requests are not diarized. Field added to `shared/events.py` (validated: whole number ≥ 1) and FORMATS.md. The exact `argmax-cli diarize` flags are confirmed in the Mac session; the command lives in settings.

29. **Dell 2026-11-14: confirmed and wired** (Dell PR "owner router"). **`speech_heard.channel` (NV, for Dell).** Owner 2026-11-14: speech_heard needs a field saying whether it came after the wake phrase (owner) or from a visitor window, and Dell routes on it. Built as `channel`: `"owner"`, `"visitor"` or `null` (typed text). See the Cross-laptop table and FORMATS.md. Also from the owner: the dashboard Listen button goes away (the mic's mute button is the off switch), and the listening windows changed: open only after Faraday has finished everything it had to say plus 0.3 s, `no_speech_s` 8, `silence_s` 1.8, `max_s` 15. The wake listener is continuous (one mic stream, no gaps); "Hey Faraday" alone plays a soft chime and the next words are the request. **Dell:** please confirm `channel`, and remove the Listen button and `/api/listen`.
30. **`photo_described` detections (NV, for Dell).** Owner 2026-11-14: live object detection (non-LLM) with labelled boxes on the stream, and the detections plus the people count on photo_described for Dell. Built: `described_by` ("bonsai" / "detector" / null), `people`, `faces`, `detections`. See FORMATS.md. The vision prompt now asks for facts (how many people or "Nobody is there.", what they hold, face visible or covered, what they're doing) and gets the detector's counts as a hint. **Dell:** please confirm; `brain/look.py` could prefer `described_by is None` over `source == camera` for "can't see", and the threat label can use `people`/`faces`. Timing: vision now gives up after `llm.vision_timeout_s` = 20 s, inside `brain/pipeline.py` `look_timeout_s` = 25 s, so a slow vision still delivers the detector's description in time; please keep look_timeout_s at least 3 s above vision_timeout_s.
31. **Owner window after a motion question (Dell → NV).** Found in review of Dell PR "owner router". `integration/voice.py` tags a window `owner` when Faraday asks a question within 30 s of an owner request. A doorbell resets that, but a motion (or arming) doesn't. So a visitor answering a question asked after motion ("I'm just walking by") would be routed as the owner and get "The system is armed. Say the passphrase to make changes." instead of the visitor conversation. Proposal: also reset `_owner_until` on motion and on any change to an armed mode. OK with NV? **NV 2026-11-15: fixed differently, in NV PR "hands-free fixes" (#45).** The 30 s timer is gone. A question opens an *owner* window only if the speak action's `caused_by` is one of the owner's own speech_heard events, which your brain sets (`brain/guard.py`: `caused_by=event.id`). A question after a motion, an arming or a doorbell is caused by that event, so it is always `visitor`. A doorbell greeting is always `visitor`, even when an owner window is open and silent (it takes that window over).

32. **Doorbell window and the photo (Dell → NV).** Since Dell PR "doorbell flow" (owner item 4) the Away doorbell is: the fixed greeting spoken by code at the ring (`rules/baseline.py` `DOORBELL_GREETING`), plus the owner's words for visitors right after it if any, then the visitor's listening window. The doorbell photo is **never spoken about** any more (no second greeting, no "One moment"); only "who's at the door?" photos (`take_photo` with source `dashboard`) get a spoken answer. `App.speech_busy()` counts any photo in progress, so the window waits for the camera and vision (seconds on the Mac). Proposal: count only `source: dashboard` photos there, so the window opens right after the greeting. OK with NV? **NV 2026-11-15: yes, done in NV PR "fresh + alarm recording" (#48):** `App.speech_busy()` now counts only `take_photo` actions with source `dashboard` (the photos that get a spoken answer); a doorbell photo no longer holds the visitor's window.
33. **Alarm recording, lights off at start, --fresh (NV, for Dell).** Owner 2026-11-14. (1) `recording_saved` is a new event type (FORMATS.md). It is written straight to the log when an alarm recording ends; please show it as "Recording saved" (with the folder or the clip if you like). (2) Every start sends off to all three outlets, logged as `outlet_off` actions with source `system`, so your Lights panel shows them off (setting `integration.lights_off_at_start`, default true). (3) `python3 -m integration --fresh` moves `logs/` (events, rules, instructions) and `captures/` to `old_runs/<time>/` before anything starts. Your stores then start empty.

34. **Walk-up grace: NV's app tests (Dell → NV, please apply).** Owner 2026-11-15 (Dell PR "walk-up grace"): motion turns the porch light on and waits `alerts.motion_grace_s` (15 s) for a doorbell before the warning; the alarm needs motion 8 to 60 s after the warning. Five tests in `integration/tests/test_app.py` (x2 classes) assumed an instant warning and used the old private `modes._last_motion`, so they fail on main until patched. Ready patch, tested here (26/26 pass): `git apply docs/team/patches/nv-test-app-walk-up-grace.patch`. It only sets fast alert timings in `Flow.setUp` and renames `_last_motion` to `_warned_at`. `Pipeline.tick()` is the timer's entry point (`Pipeline._start_grace_timer`); integration needs no code change. Optional on the Mac: add `min_gap_s`, `motion_grace_s`, `visitor_quiet_s` under `alerts` in settings.json (the code defaults are the same). **NV 2026-11-15: applied** (in NV PR "owner voice check"; `integration/tests/test_app.py`).
35. **`speech_heard.owner_voice` (NV, for Dell): display only.** Owner item 10 (stretch). The owner enrolls ~10 s of voice once (`python3 -m hardware.owner_voice enroll`, kept in `cache/owner_voice.wav`). For each owner request, `argmax-cli diarize` runs on the enrollment plus the request: `true` = only the owner's voice, `false` = another voice, `null` = not checked or can't tell. It waits at most `audio.voice_check.grace_s` (2 s) and is never enforced. The owner wants it enforced for disarm only if it is right at least 9 times out of 10 on the Mac (`python3 -m hardware.owner_voice trial` scores it). **Dell:** you may show it next to an owner request (for example "voice: owner" / "voice: someone else"). Please don't change routing or the passphrase rule on it.
