# dashboard/

**Owner: Dell laptop.**

The local page for the projector. **Fully offline:** stdlib `http.server`, and every asset (`static/index.html`, `dashboard.css`, `dashboard.js`, two SVGs) is in this folder. No CDNs, no web fonts, no external JS. A test fails if a static file contains an `http(s)://` URL.

```python
from dashboard import Dashboard
board = Dashboard.from_settings(pipeline, settings).start()   # dashboard.host:port, 127.0.0.1:8080
print(board.url)
```

Or from the sim: `python -m sim --dashboard` (add `--offline` to run without LM Studio).

**What it shows** (one screen, no scrolling, readable from the back of a room):
- **Mode** in huge letters, with the dashboard's own status colour: green Home, blue Away, purple Night, and an alert beats the mode (amber warning, red alarm). The Flipper's LED is separate: it stays off and only flashes red on an alarm.
- An **Offline** badge, and a banner for an alarm or a quiet doorbell at night.
- **Camera:** the latest photo and its description, plus why it was taken.
- **Heard & said:** speech in, Faraday's speech out. The passphrase shows as `[passphrase]`.
- **Live events:** feed, newest first.
- **Lights:** porch light and lamp as real switches. A tap posts `/api/control` to integration's `board.control_hook`, which sends the code and logs an `outlet_on`/`outlet_off` with `source: dashboard`. The tile always shows the state from the log, never what the tap hoped for. Without the hook the tiles are display only. The spare outlet shows only while it's on.
- **Rules & instructions:** voice rules and the owner's standing instructions for visitors ("Delivery · ask them to leave it by the side door"), each with a delete button.
- **Messages:** plus the latest spoken summary.
- **Buttons:** Away and Night. Home asks for the passphrase.
- **Listen:** a small tap button (push-to-talk is gone). One tap opens one listening window through integration's `board.listen_hook`, like saying "Hey Faraday". While the mic is open the button reads "Listening…" and a small green mic breathes next to "Live". That follows `board.listening_hook()` if integration sets it, otherwise it shows for 8 s after a tap.
- **Backup text box:** sends a `speech_heard` event.
- **Stage backup buttons:** "Ring doorbell" and "Motion" send the same events the Flipper would, marked `source: "dashboard"` in the log.
- **Real offline check:** the badge asks the OS for a route to a reserved test address (192.0.2.1 / 2001:db8::1) with a UDP connect, which sends no packets. No route shows a green OFFLINE. Any network shows a red, blinking "ONLINE: turn Wi-Fi off".
- **One big banner for the audience,** most important first: ALARM, DISARMED BY PASSPHRASE (20 s), THE AI CAN'T DISARM (while armed, 60 s), DOORBELL PRESSED (15 s), WARNING, DOORBELL AT NIGHT (2 min).
- **Real photos:** the newest `.jpg`/`.png` in `captures/photos/` shows the moment it's saved, with "Looking at the photo..." until its `photo_described` arrives.
- **Tested** at 1280×720 and 1920×1080 (the Controls page also at 390 px phone width).

## Controls page (`/controls`)

The **Controls** button in the dock opens a phone-style page in the same glass look: one column on a phone, two on a laptop.
- **Live camera:** `<img>` on integration's `GET /stream.mjpg` (controls server, dashboard port + 1). It shows "The camera isn't connected" (from `/api/status`) with a Try again button.
- **Snapshot:** `POST /api/snapshot` makes `pipeline.owner_snapshot()` emit a `take_photo` with `source: dashboard`. The page waits for the photo whose `request` matches, then shows it with its description and threat label. Nobody at the door is greeted for an owner snapshot.
- **Talk to visitor:** a big hold button (pointer, touch or Space/Enter). Chrome's mic goes through `getUserMedia`, then 16-bit mono PCM chunks are POSTed to integration's `/api/intercom` with `X-Sample-Rate`, and `/api/intercom/end` on release. Integration mutes the door mic meanwhile. There's no text fallback. If the browser blocks the mic, the page says why: an untrusted address (with the `chrome://flags/#unsafely-treat-insecure-origin-as-secure` steps and the exact two origins to add), permission denied, no mic, or mic busy.
- **Lights:** all three outlets as big switches, same `/api/control` and log-based state as the dock.

The page finds the controls server at `location.hostname` plus `controls_port` from `/api/state` (settings `controls.port`, else dashboard port + 1), so it works from a phone at the Mac's address too.

**How it updates:** the page polls `GET /api/state` every second. That snapshot is built by `state.build_state()` from the log alone, so it's redacted, stateless and survives restarts.

| Route | Does |
|---|---|
| `GET /`, `/static/*` | the page and its assets |
| `GET /api/state` | live snapshot (JSON) |
| `GET /media/captures/...` | photos, `.jpg`/`.png` inside `captures/` only |
| `POST /api/mode {"mode": "away"\|"night"}` | arm. `home` is refused |
| `POST /api/home {"passphrase": ...}` | disarm through `modes/`. 5 misses → 30 s lockout. Never logged |
| `POST /api/say {"text": ...}` | backup for the mic |
| `POST /api/rules/delete {"id": ...}` | remove a rule |
| `POST /api/instructions/delete {"id": ...}` | remove an owner instruction |
| `POST /api/sensor {"type": "doorbell"\|"motion"}` | stage backup: the same event the Flipper would send |
| `POST /api/control {"outlet": ..., "on": true\|false}` | light switch through `board.control_hook`. 400 for a bad outlet, 503 if the hook isn't set |
| `POST /api/listen` | Listen button: one listening window through `board.listen_hook` (`talk_hook` also works). Replies `{"opened": false}` if one is already open |
| `POST /api/talk` | old name for `/api/listen` |
| `GET /controls` | the Controls page |
| `POST /api/snapshot` | owner snapshot: a `take_photo` with `source: dashboard`. Replies 202 with the request `id` |

POSTs need the header `X-Faraday: 1`, so another web page can't press the buttons.
