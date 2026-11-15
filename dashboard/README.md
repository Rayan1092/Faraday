# dashboard/

**Owner: Dell laptop.**

The home's local status page. **Fully offline:** stdlib `http.server`, and every asset (`static/index.html`, `dashboard.css`, `dashboard.js`, two SVGs) is in this folder. No CDNs, no web fonts, no external JS. A test fails if a static file contains an `http(s)://` URL.

```python
from dashboard import Dashboard
board = Dashboard.from_settings(pipeline, settings).start()   # dashboard.host:port, 127.0.0.1:8080
print(board.url)
```

Developers can also start it from the simulator: `python -m sim --dashboard` (add `--offline` to run without LM Studio).

**What it shows** (one screen, no scrolling, readable across a room):
- **Mode** in huge letters, with the dashboard's own status colour: green Home, blue Away, purple Night, and an alert beats the mode (amber warning, red alarm). The Flipper's LED is separate: it stays off and only flashes red on an alarm.
- **Live camera in the centre:** integration's `GET /stream.mjpg` on the controls server (dashboard port + 1), with the object detector's boxes drawn in, and a small LIVE badge. While the stream is down (and before it's up) the latest photo shows instead. A stream image never reports a drop, so the page polls integration's `/api/status` every 3 s: no camera (or no server) brings the photo back, and the camera coming back restarts the stream. The stream pauses while the page is hidden, so the camera's detector rests.
- **Under the camera:** the latest threat label (a coloured pill: delivery, visitor, suspicious, nobody, or a dashed "unknown") with its reason; the objects the detector saw in that photo as small chips ("person", "2 × backpack"); "Recording saved" while the latest alarm has its recording (NV's `recording_saved`); then the photo's description and why it was taken.
- **Heard & said:** speech in, Faraday's speech out. The passphrase shows as `[passphrase]`. An owner request carries NV's voice check when there is one: "Owner's voice" or "Not the owner's voice" (display only, it never changes what Faraday does).
- **Activity:** newest first. Photos and alarm recordings carry a small thumbnail.
- **Lights:** porch light and lamp as real switches. A tap posts `/api/control` to integration's `board.control_hook`, which sends the code and logs an `outlet_on`/`outlet_off` with `source: dashboard`. The tile always shows the state from the log, never what the tap hoped for. Without the hook the tiles are display only. The spare outlet shows only while it's on.
- **Rules & instructions:** voice rules and the owner's standing instructions for visitors ("Delivery · ask them to leave it by the side door"), each with a delete button.
- **Messages:** plus the latest spoken summary.
- **Buttons:** Away and Night. Home asks for the passphrase.
- **Mic indicator:** a small green mic breathes next to "Live" while a listening window is open (integration's `board.listening_hook()`). There is no Listen button: "Hey Faraday" and the doorbell open the mic.
- **No demo controls:** no sensor or typed-speech buttons and no network badge. The product only acts on the real sensors and the mic, and a real home has Wi-Fi: Faraday just never uses the internet.
- **One big banner,** most important first: ALARM (with "Recording saved." once it is), DISARMED (20 s), DISARM REFUSED (while armed, 60 s), DOORBELL (15 s), WARNING, DOORBELL AT NIGHT (2 min).
- **Real photos:** the newest `.jpg`/`.png` in `captures/photos/` shows the moment it's saved, with "Looking at the photo..." until its `photo_described` arrives.
- **Tested** at 1280×720 and 1920×1080 (the Controls page also at 390 px phone width).

## Controls page (`/controls`)

The **Controls** button in the dock opens a phone-style page in the same glass look: one column on a phone, two on a laptop.
- **Live camera:** `<img>` on integration's `GET /stream.mjpg` (controls server, dashboard port + 1). It shows "The camera isn't connected" (from `/api/status`) with a Try again button.
- **Snapshot:** `POST /api/snapshot` makes `pipeline.owner_snapshot()` emit a `take_photo` with `source: dashboard`. The page waits for the photo whose `request` matches, then shows it with its description and threat label. Nobody at the door is greeted for an owner snapshot.
- **Talk to visitor:** a big hold button (pointer, touch or Space/Enter). Chrome's mic goes through `getUserMedia`, then 16-bit mono PCM chunks are POSTed to integration's `/api/intercom` with `X-Sample-Rate`, and `/api/intercom/end` on release. Integration mutes the door mic meanwhile. There's no text fallback. If the browser blocks the mic, the page says why: an untrusted address (with the `chrome://flags/#unsafely-treat-insecure-origin-as-secure` steps and the exact two origins to add), permission denied, no mic, or mic busy.
- **Lights:** all three outlets as big switches, same `/api/control` and log-based state as the dock.
- **Live captions:** everything heard and said at the door, in big text, newest at the bottom. It follows along unless you've scrolled up. For a deaf owner, it's the conversation as it happens.

The page finds the controls server at `location.hostname` plus `controls_port` from `/api/state` (settings `controls.port`, else dashboard port + 1), so it works from a phone at the Mac's address too.

**How it updates:** the page polls `GET /api/state` every second. That snapshot is built by `state.build_state()` from the log alone, so it's redacted, stateless and survives restarts.

| Route | Does |
|---|---|
| `GET /`, `/static/*` | the page and its assets |
| `GET /api/state` | live snapshot (JSON) |
| `GET /media/captures/...` | photos, `.jpg`/`.png` inside `captures/` only |
| `POST /api/mode {"mode": "away"\|"night"}` | arm. `home` is refused |
| `POST /api/home {"passphrase": ...}` | disarm through `modes/`. 5 misses → 30 s lockout. Never logged |
| `POST /api/rules/delete {"id": ...}` | remove a rule |
| `POST /api/instructions/delete {"id": ...}` | remove an owner instruction |
| `POST /api/control {"outlet": ..., "on": true\|false}` | light switch through `board.control_hook`. 400 for a bad outlet, 503 if the hook isn't set |
| `GET /controls` | the Controls page |
| `POST /api/snapshot` | owner snapshot: a `take_photo` with `source: dashboard`. Replies 202 with the request `id` |

POSTs need the header `X-Faraday: 1`, so another web page can't press the buttons.
