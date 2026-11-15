# Faraday formats

This file is the source of truth for every message passed between modules. The matching Python is in `shared/` (`shared/events.py`, `shared/actions.py`). If they ever disagree, fix the code to match this file. Change either one only by agreement between both laptops.

## Common rules

- Every message is one JSON object. The log file `logs/events.jsonl` has one event or action per line, in the order they happened.
- `kind` is `"event"` (something happened) or `"action"` (something Faraday should do).
- `id` is a prefix plus 8 hex characters: `evt_3f9a1c2e`, `act_b71d04aa`. Use `shared.new_id()`.
- `timestamp` is local time in ISO 8601 with milliseconds and UTC offset: `2026-11-14T16:03:12.481-05:00`. Use `shared.now_iso()`.
- `source` says which module made the message. Allowed values: `flipper`, `speech`, `camera`, `vision`, `brain`, `modes`, `rules`, `dashboard`, `sim`, `system`.
- `caused_by` is the `id` of the message that led to this one, or `null`. It lets the dashboard and `summarize_log` follow a chain like doorbell → take_photo → photo_described → speak.
- Unknown keys are rejected. Missing optional keys take the defaults listed below.
- File paths are relative to the repo root and use forward slashes on every OS, for example `captures/photos/20261114-160312.jpg`.
- Sim events look exactly like real ones except `source: "sim"`. No code may treat a sim event differently.

Modes: `home` (disarmed), `away` (armed), `night` (armed). The dashboard shows each in its own colour: green, blue, purple.
Alert levels: `normal`, `warning`, `alarm`. On the dashboard an alert colour (amber, red) overrides the mode colour. The Flipper's LED is off at all times except `alarm`: red plus three buzzes, and off again at the passphrase disarm.
Outlets: `porch_light` (outlet 1), `living_room_lamp` (outlet 2), `spare` (outlet 3).

---

## Events

Envelope:

| key | type | notes |
|---|---|---|
| `kind` | `"event"` | |
| `id` | string | `evt_xxxxxxxx` |
| `type` | string | one of the eight types below |
| `timestamp` | string | ISO 8601, see above |
| `source` | string | see above |
| `caused_by` | string or null | |
| `data` | object | shape depends on `type` |

### doorbell

The doorbell sends Princeton 24-bit key `0x00DEC608`. The button has to be held for about 1 s, and all packets from one press become one event.

| data key | type | default | notes |
|---|---|---|---|
| `key` | string | required | `"0x00DEC608"` |
| `protocol` | string | `"Princeton"` | |
| `bits` | int | `24` | |
| `packets` | int | `1` | packets seen in this press |
| `duration_ms` | int | `0` | time from first packet to last |

```json
{
  "kind": "event",
  "id": "evt_3f9a1c2e",
  "type": "doorbell",
  "timestamp": "2026-11-14T16:03:12.481-05:00",
  "source": "flipper",
  "caused_by": null,
  "data": {"key": "0x00DEC608", "protocol": "Princeton", "bits": 24, "packets": 9, "duration_ms": 1040}
}
```

### motion

The motion sensor sends Princeton 24-bit key `0x00C5AFAE`, about 17 packets per trigger. All packets inside the merge window (`sensors.motion.merge_window_s` in settings) become one event.

| data key | type | default | notes |
|---|---|---|---|
| `key` | string | required | `"0x00C5AFAE"` |
| `protocol` | string | `"Princeton"` | |
| `bits` | int | `24` | |
| `packets` | int | `1` | packets merged into this event |
| `duration_ms` | int | `0` | time from first packet to last |

```json
{
  "kind": "event",
  "id": "evt_8c21d7f0",
  "type": "motion",
  "timestamp": "2026-11-14T23:41:05.112-05:00",
  "source": "flipper",
  "caused_by": null,
  "data": {"key": "0x00C5AFAE", "protocol": "Princeton", "bits": 24, "packets": 17, "duration_ms": 1830}
}
```

### speech_heard

A transcript from Argmax STT on the Mac, or from the Windows stand-in. Before anything is logged or sent to the brain, `modes/` checks the text for the passphrase. If it matches, the passphrase is replaced with `[passphrase]` in `text`.

| data key | type | default | notes |
|---|---|---|---|
| `text` | string | required | not empty |
| `audio_path` | string or null | `null` | `captures/audio/....wav` |
| `duration_s` | number or null | `null` | length of the recording |
| `voices` | int or null | `null` | distinct voices in the recording, from `argmax-cli diarize` run alongside STT on visitor recordings (PLAN Q28). Present only if diarization finished in time; it never delays the reply |
| `owner_voice` | bool or null | `null` | owner requests only: `true` = the enrolled owner's voice, `false` = another voice, `null` = not checked or can't tell. From `argmax-cli diarize` on the enrollment plus the request (`hardware/owner_voice.py`). **Display only**: nothing is allowed or refused because of it (PLAN Q35) |
| `channel` | string or null | `null` | `"owner"`: said after the wake phrase "Hey Faraday" (or answering Faraday's question back to the owner). `"visitor"`: from a listening window (doorbell, Faraday's question to a visitor). `null` for typed text (PLAN Q29) |

```json
{
  "kind": "event",
  "id": "evt_51ab9e03",
  "type": "speech_heard",
  "timestamp": "2026-11-14T16:03:30.207-05:00",
  "source": "speech",
  "caused_by": "act_c40f2b19",
  "data": {"text": "Hi, I have a package for Sam.", "audio_path": "captures/audio/20261114-160326.wav", "duration_s": 3.4}
}
```

### photo_described

A camera snapshot plus a short description from Bonsai vision. `caused_by` is the `take_photo` action that asked for it.

| data key | type | default | notes |
|---|---|---|---|
| `photo_path` | string | required | `captures/photos/....jpg` |
| `description` | string | required | one or two short sentences |
| `model` | string or null | `null` | LM Studio model id used (only when `described_by` is `"bonsai"`) |
| `described_by` | string or null | `null` | `"bonsai"`: the vision LLM wrote `description`. `"detector"`: vision failed, so `description` comes from the object detector alone ("One person is at the door.", "Nobody is there."). `null`: no description ("Photo saved." / "Camera unavailable.", source `camera`) (PLAN Q30) |
| `people` | int or null | `null` | people the object detector (YOLOX-tiny, no LLM) counted in the photo |
| `faces` | int or null | `null` | faces visible to the camera (face detector). Fewer faces than close people = a face covered or turned away |
| `detections` | list or null | `null` | what the object detector saw: `[{"label": "person", "count": 1, "confidence": 0.91}, {"label": "backpack", "count": 1, "confidence": 0.55}]`, most confident first. `label` is a COCO class name, `count` >= 1, `confidence` 0 to 1 (the best box of that class) |

```json
{
  "kind": "event",
  "id": "evt_e6047a55",
  "type": "photo_described",
  "timestamp": "2026-11-14T16:03:16.930-05:00",
  "source": "vision",
  "caused_by": "act_9d3e61c8",
  "data": {"photo_path": "captures/photos/20261114-160313.jpg", "description": "A person in a dark jacket holding a cardboard box at the front door.", "model": "ternary-bonsai-2-4b@q2_0"}
}
```

### mode_changed

Sent by `modes/` after every mode change. The dashboard shows it. The Flipper LED ignores it: the LED only reacts to `alert_changed` (see below). Going from `away` or `night` to `home` is only valid with `changed_by: "passphrase"`. The one exception is `startup`, the first event after launch.

| data key | type | default | notes |
|---|---|---|---|
| `mode` | string | required | new mode |
| `previous` | string | required | old mode |
| `changed_by` | string | required | `passphrase`, `brain`, `rule`, `dashboard`, `startup` |
| `reason` | string | `""` | short text for the dashboard |

```json
{
  "kind": "event",
  "id": "evt_07fd3c92",
  "type": "mode_changed",
  "timestamp": "2026-11-14T16:01:02.554-05:00",
  "source": "modes",
  "caused_by": "act_2a7710be",
  "data": {"mode": "away", "previous": "home", "changed_by": "brain", "reason": "Owner said they are leaving."}
}
```

### alert_changed

Sent by `modes/` when the alert level changes. `source` must be `modes`, and the validator rejects any other value. The AI and rules can't change the alert level, because no action exists for it.

The rule, implemented in `modes/`:
- Motion while the mode is `away` or `night` and the alert is `normal` starts a grace period (`alerts.motion_grace_s`, 15 s). The porch light goes on at once; no event yet.
- A `doorbell` within the grace period is a visitor: the pending warning is cancelled and the alert stays `normal`. After a doorbell, motion doesn't raise the alert for `alerts.visitor_quiet_s` (120 s).
- No doorbell by the end of the grace period moves the alert from `normal` to `warning` (amber on the dashboard, the LED stays off). `caused_by` is the motion that started it. A `suspicious` threat label while armed does the same at once.
- Motion at least `alerts.min_gap_s` (8 s) and at most `alerts.alarm_window_s` (60 s) after the warning, with no doorbell in between, moves it from `warning` to `alarm` (red on the dashboard, and the Flipper LED turns red and buzzes three times). Sooner is the same wave and is ignored. Later is a new walk-up: a new grace period, and if nobody rings, the warning steps run again, with no event because the level doesn't change. A doorbell press counts until about 2.5 s after the grace period, because the radio reports it when the burst ends.
- Only the passphrase disarm returns the alert to `normal`. That disarm sends `mode_changed` (to `home`, `changed_by: "passphrase"`) and then `alert_changed` (to `normal`), and both have the same `caused_by`. The Flipper LED goes off again.
- Motion in `home` mode and doorbell presses at home never change the alert level. Switching between `away` and `night` doesn't reset it, but ends a visitor's quiet period and a pending warning.

The validator allows only these steps: `normal→warning`, `warning→alarm`, `warning→normal`, `alarm→normal`. Because the event doesn't carry the passphrase, `modes/` is responsible for sending `→normal` only from the passphrase disarm.

| data key | type | default | notes |
|---|---|---|---|
| `level` | string | required | `normal`, `warning`, `alarm` |
| `previous` | string | required | the level before this change |
| `reason` | string | `""` | short text for the dashboard |

```json
{
  "kind": "event",
  "id": "evt_4d8a20bf",
  "type": "alert_changed",
  "timestamp": "2026-11-14T23:41:05.180-05:00",
  "source": "modes",
  "caused_by": "evt_8c21d7f0",
  "data": {"level": "warning", "previous": "normal", "reason": "Motion at the porch in night mode."}
}
```

### threat_assessed

Sent by `brain/` right after a `photo_described`. It says what the scene at the door looks like. `caused_by` is the `photo_described` it judges, and `source` must be `brain`.

**What the label may do.** A photo itself is never answered (the doorbell greeting is fixed and spoken at the ring); the label shapes what Faraday says to a visitor afterwards (a delivery hears the owner's drop-off words). Walked up and rang with no warning signs is a visitor or a delivery; stayed at the door without ringing while armed is `suspicious`. It's also shown on the dashboard and included in the summary. `suspicious` while armed may *escalate*: plain code in `modes/` raises the alert from `normal` to `warning`, and the confrontation line comes early.

**What it can never do:** lower an alert, turn lights off, change the mode or disarm. Code still guarantees the lights and alerts from the mode table. If Bonsai's answer is invalid, plain-code rules decide (`via: "rules"`).

| data key | type | default | notes |
|---|---|---|---|
| `category` | string | required | `delivery`, `visitor`, `suspicious`, `nobody` |
| `reason` | string | `""` | one short line, at most 200 characters |
| `via` | string | `"rules"` | `bonsai` or `rules` |
| `model` | string or null | `null` | LM Studio model id when `via` is `bonsai` |
| `voices` | int or null | `null` | voice count from diarization, if known |

```json
{
  "kind": "event",
  "id": "evt_5c1d9a7e",
  "type": "threat_assessed",
  "timestamp": "2026-11-14T16:03:18.402-05:00",
  "source": "brain",
  "caused_by": "evt_e6047a55",
  "data": {"category": "delivery", "reason": "Holding a box and rang the doorbell.", "via": "bonsai", "model": "ternary-bonsai-2-2b-mlx", "voices": 1}
}
```

### recording_saved

About 10 s of camera frames saved when the alert reaches `alarm` (`camera.alarm_record_s`). `integration/` writes it straight to the log after the recording ends; it doesn't go through the pipeline, so nothing decides anything on it. The dashboard shows it as "Recording saved" (PLAN Q33). `caused_by` is the `alert_changed` event.

| data key | type | default | notes |
|---|---|---|---|
| `path` | string | required | the folder, `captures/alarm-<time>/`, with `frame-0001.jpg`, `frame-0002.jpg`, ... |
| `frames` | int | required | frames saved (about 10 per second) |
| `duration_s` | number | required | first to last frame |
| `video_path` | string or null | `null` | `captures/alarm-<time>/clip.avi` (MJPG), if OpenCV could write it |
| `reason` | string | `""` | `"alarm"` |

```json
{
  "kind": "event",
  "id": "evt_7a3c19d0",
  "type": "recording_saved",
  "timestamp": "2026-11-14T23:41:12.204-05:00",
  "source": "camera",
  "caused_by": "evt_2f8e6b14",
  "data": {"path": "captures/alarm-20261114-234102/", "frames": 98, "duration_s": 9.9, "video_path": "captures/alarm-20261114-234102/clip.avi", "reason": "alarm"}
}
```

---

## Actions

Envelope:

| key | type | notes |
|---|---|---|
| `kind` | `"action"` | |
| `id` | string | `act_xxxxxxxx` |
| `type` | string | one of the eight types below |
| `timestamp` | string | ISO 8601 |
| `source` | string | usually `brain`, `rules` or `dashboard` |
| `caused_by` | string or null | the event that led to it |
| `args` | object | shape depends on `type` |

**The AI can only choose these eight actions. None of them disarms.** Disarming means going to `home` from an armed mode, and only the passphrase check in `modes/` can do it. `set_mode` rejects `home` for every source. A dashboard Home button has to go through the passphrase check too.

Who runs each action:
- `outlet_on`, `outlet_off`, `speak` and `take_photo` touch hardware, so `integration/` runs them (NV laptop).
- `set_mode`, `save_message`, `summarize_log` and `add_rule` are handled by `modes/`, `log/`, `brain/` and `rules/` (Dell laptop).

### set_mode

| args key | type | default | notes |
|---|---|---|---|
| `mode` | string | required | `away` or `night` only |

```json
{
  "kind": "action",
  "id": "act_2a7710be",
  "type": "set_mode",
  "timestamp": "2026-11-14T16:01:02.390-05:00",
  "source": "brain",
  "caused_by": "evt_a90c4d11",
  "args": {"mode": "away"}
}
```

### outlet_on

| args key | type | default | notes |
|---|---|---|---|
| `outlet` | string | required | `porch_light`, `living_room_lamp`, `spare` |

`integration/` maps the outlet name to `outletN_on` and runs `subghz tx_from_file /ext/subghz/outletN_on.sub 10 0`. It pauses rx first and starts it again afterwards.

```json
{
  "kind": "action",
  "id": "act_5be2f8a0",
  "type": "outlet_on",
  "timestamp": "2026-11-14T23:41:05.640-05:00",
  "source": "rules",
  "caused_by": "evt_8c21d7f0",
  "args": {"outlet": "porch_light"}
}
```

### outlet_off

Same args as `outlet_on`.

```json
{
  "kind": "action",
  "id": "act_f13a9c77",
  "type": "outlet_off",
  "timestamp": "2026-11-14T23:46:05.640-05:00",
  "source": "brain",
  "caused_by": null,
  "args": {"outlet": "porch_light"}
}
```

### speak

| args key | type | default | notes |
|---|---|---|---|
| `text` | string | required | 1 to 240 characters |

```json
{
  "kind": "action",
  "id": "act_c40f2b19",
  "type": "speak",
  "timestamp": "2026-11-14T16:03:17.402-05:00",
  "source": "brain",
  "caused_by": "evt_e6047a55",
  "args": {"text": "Hello. Nobody can come to the door right now. Would you like to leave a message?"}
}
```

### take_photo

| args key | type | default | notes |
|---|---|---|---|
| `reason` | string | `""` | short, shown on the dashboard |

The result arrives later as a `photo_described` event whose `caused_by` is this action's id.

```json
{
  "kind": "action",
  "id": "act_9d3e61c8",
  "type": "take_photo",
  "timestamp": "2026-11-14T16:03:12.733-05:00",
  "source": "brain",
  "caused_by": "evt_3f9a1c2e",
  "args": {"reason": "doorbell"}
}
```

### save_message

| args key | type | default | notes |
|---|---|---|---|
| `text` | string | required | 1 to 500 characters |
| `sender` | string | `"visitor"` | |

```json
{
  "kind": "action",
  "id": "act_7e0b5d26",
  "type": "save_message",
  "timestamp": "2026-11-14T16:03:31.015-05:00",
  "source": "brain",
  "caused_by": "evt_51ab9e03",
  "args": {"text": "Delivery driver has a package for Sam and left it by the side door.", "sender": "visitor"}
}
```

### summarize_log

| args key | type | default | notes |
|---|---|---|---|
| `minutes` | int | `60` | how far back to look, 1 to 1440 |

The brain turns the summary into a `speak` action and also shows it on the dashboard.

```json
{
  "kind": "action",
  "id": "act_33c8e4f1",
  "type": "summarize_log",
  "timestamp": "2026-11-14T18:30:44.820-05:00",
  "source": "brain",
  "caused_by": "evt_d2b6a0e8",
  "args": {"minutes": 180}
}
```

### add_rule

| args key | type | default | notes |
|---|---|---|---|
| `when` | object | required | `{"event": <event type>, "modes": [<mode>, ...]}`. An empty or missing `modes` list means any mode |
| `then` | list | required | 1 to 3 items of `{"type": <action type>, "args": {...}}`. Each item is checked like a normal action. No `add_rule` inside a rule, and `set_mode` still cannot be `home` |
| `description` | string | `""` | the user's own words, for the dashboard |

When a rule fires, `rules/` sends each `then` item as an action with `source: "rules"` and `caused_by` set to the event that matched.

```json
{
  "kind": "action",
  "id": "act_6a19fd02",
  "type": "add_rule",
  "timestamp": "2026-11-14T21:12:09.300-05:00",
  "source": "brain",
  "caused_by": "evt_0e4c7b35",
  "args": {
    "when": {"event": "motion", "modes": ["night"]},
    "then": [{"type": "outlet_on", "args": {"outlet": "porch_light"}}],
    "description": "When there's motion at night, turn on the porch light."
  }
}
```

---

## What the LLM returns

Keep the prompt short. Bonsai sees the current mode, the new event, and a few recent log lines.

**Main path: tool calling.** `brain/` offers Bonsai one OpenAI-style tool per allowed action through LM Studio, and reads its tool calls. Arming is shown as two tools with no arguments (`arm_away`, `arm_night`), which `brain/` turns back into `set_mode`. Each tool call becomes one `{"type", "args"}` item, the same as below.

**Also accepted:** the same list written as JSON in the reply text, because small models sometimes do that:

```json
{"actions": [{"type": "take_photo", "args": {"reason": "doorbell"}}, {"type": "outlet_on", "args": {"outlet": "porch_light"}}]}
```

`brain/` wraps each item in the action envelope (`kind`, `id`, `timestamp`, `source: "brain"`, `caused_by`) and checks it with `shared.validate_action()`. Invalid items are dropped and logged, never retried in a loop. An empty list `{"actions": []}` means do nothing.
