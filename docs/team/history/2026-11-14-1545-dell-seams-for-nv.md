# Seams for NV: how integration/ calls Dell's side

- Laptop / branch / PR: Dell / `dell/polish` / Step 5 PR
- For: NV wiring Join 1 and Join 2 in `integration/`. Everything below exists on `dell/polish` and is covered by tests. `describe_photo` is new in this PR.

## The calls

| What | Call | Notes |
|---|---|---|
| Build everything | `pipeline = Pipeline.from_settings(settings, on_event=..., on_action=...)` | `from brain.pipeline import Pipeline`. Loads Bonsai, modes, `logs/events.jsonl` and `logs/rules.json` from settings |
| Announce the start | `pipeline.start()` | Sends `mode_changed` (`changed_by: startup`), so the LED gets set |
| Every incoming event | `pipeline.handle(event)` | Doorbell, motion, speech, `photo_described`. Thread-safe (one lock). Returns `Result(events, actions)`, but you don't need it if you use the callbacks |
| Dashboard Away/Night | `pipeline.owner_set_mode("away")` | The dashboard calls this itself. Only use it for a physical button |
| Typed passphrase | `pipeline.owner_passphrase(text)` | Returns `None` if wrong. The dashboard already does this |
| Photo description | `describe_photo(path, caused_by=action.id, describer=describer)` | `from brain import Describer, describe_photo`. Build once with `describer = Describer.from_settings(settings)`. Always returns a valid `photo_described`; feed it to `pipeline.handle()` |
| Log | `pipeline.log.recent(n)`, `pipeline.log.messages()` | **Don't write to the log yourself.** The pipeline already logs every event and action, redacted |
| Dashboard | `board = Dashboard.from_settings(pipeline, settings).start()` | `from dashboard import Dashboard`. Serves `dashboard.host:port` (127.0.0.1:8080) |
| Shutdown | `pipeline.wait_background(5); board.stop()` | Waits for a pending alarm confrontation |

## The two callbacks

- **`on_action(action)`** is called for **every** action, the moment it exists: baseline first, then Bonsai's.
  - Run the ones in `ActionType.HARDWARE`:
    - `outlet_on` / `outlet_off` → `fl.execute(action)`
    - `speak` → TTS
    - `take_photo` → camera, then `describe_photo`, then `pipeline.handle(...)`
  - Ignore the rest: `set_mode`, `save_message`, `add_rule` and `summarize_log` are already done by the time you see them.
  - **It can be called from different threads.** That includes the main pipeline thread, the dashboard's request threads, and the background thread for the alarm confrontation. So push actions onto a `queue.Queue` and run them on one worker thread.
- **`on_event(event)`** is called for every event the pipeline handles (already redacted). For the LED, pass it to `fl.apply_event(event)`. The driver uses `mode_changed` and `alert_changed`, and should ignore the rest.

## Example

```python
import queue, threading
from brain import Describer, describe_photo
from brain.pipeline import Pipeline
from brain.settings import load_settings
from dashboard import Dashboard
from hardware.flipper import Flipper
from shared import ActionType, EventType, Source, make_event

settings = load_settings()
actions = queue.Queue()
incoming = queue.Queue()

fl = Flipper(on_event=incoming.put)          # radio events -> queue
pipeline = Pipeline.from_settings(settings,
                                  on_event=fl.apply_event,      # LED from mode/alert
                                  on_action=actions.put)        # hardware runs on our worker
describer = Describer.from_settings(settings)
board = Dashboard.from_settings(pipeline, settings).start()

def run_actions():
    while True:
        a = actions.get()
        if a.type in (ActionType.OUTLET_ON, ActionType.OUTLET_OFF):
            fl.execute(a)
        elif a.type == ActionType.SPEAK:
            tts_say(a.args["text"])                              # hardware/speech
        elif a.type == ActionType.TAKE_PHOTO:
            path = camera_snapshot()                             # hardware/camera -> captures/photos/...jpg
            incoming.put(describe_photo(path, a.id, describer))  # back through the pipeline

def run_events():
    while True:
        pipeline.handle(incoming.get())       # one at a time; Bonsai can take seconds

def on_transcript(text):                      # hardware/speech STT
    # Don't print or log `text`: the pipeline blanks the passphrase out of it.
    incoming.put(make_event(EventType.SPEECH_HEARD, {"text": text}, Source.SPEECH))

threading.Thread(target=run_actions, daemon=True).start()
threading.Thread(target=run_events, daemon=True).start()
fl.start()
pipeline.start()
```

## Things that bite

- **Never log or print the raw STT text** before it reaches `pipeline.handle()`. That's where the passphrase gets redacted.
- **Photos.** Save them under `captures/` as `.jpg`. Otherwise the dashboard's `/media` won't serve them, and `photo_path` should be relative (`describe_photo` handles that).
- **Repeated outlet sends.** The baseline sends `outlet_on porch_light` on every armed motion, even if it's already on. Your rx is blind for about 1.2 s per send (your PR #5 numbers), and at alarm that's porch light plus lamp plus the buzz. If that hurts motion detection, skip an `outlet_on` for an outlet your driver already turned on in the last minute. That's an integration choice; tell me if you'd rather the pipeline did it.
- **Sim events.** `source: "sim"` events from `python -m sim --demo` look exactly like real ones. Don't run the sim and the real app on the same dashboard port at the same time.
- **Bonsai on the Mac.** Set LM Studio's Parallel above 1, so the alarm confrontation doesn't queue behind other calls.

## Quick self-check

`python -m unittest discover` from the repo root. Then `python -m sim --offline --dashboard` should show the whole flow with no hardware.
