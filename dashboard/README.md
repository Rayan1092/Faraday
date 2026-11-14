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
- **Mode** in huge letters. The status colour matches the Flipper LED, and an alert beats the mode: yellow warning, red alarm.
- An **Offline** badge, and a banner for an alarm or a quiet doorbell at night.
- **Camera:** the latest photo and its description, plus why it was taken.
- **Heard & said:** speech in, Faraday's speech out. The passphrase shows as `[passphrase]`.
- **Live events:** feed, newest first.
- **Lights:** porch light and lamp, ON/OFF.
- **Rules:** with a delete button.
- **Messages:** plus the latest spoken summary.
- **Buttons:** Away and Night. Home asks for the passphrase.
- **Backup text box:** sends a `speech_heard` event.
- **Stage backup buttons:** "Ring doorbell" and "Motion" send the same events the Flipper would, marked `source: "dashboard"` in the log.
- **Real offline check:** the badge asks the OS for a route to a reserved test address (192.0.2.1 / 2001:db8::1) with a UDP connect, which sends no packets. No route shows a green OFFLINE. Any network shows a red, blinking "ONLINE: turn Wi-Fi off".
- **One big banner for the audience,** most important first: ALARM, DISARMED BY PASSPHRASE (20 s), THE AI CAN'T DISARM (while armed, 60 s), DOORBELL PRESSED (15 s), WARNING, DOORBELL AT NIGHT (2 min).
- **Real photos:** the newest `.jpg`/`.png` in `captures/photos/` shows the moment it's saved, with "Looking at the photo..." until its `photo_described` arrives.
- **Tested** at 1280×720 and 1920×1080.

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
| `POST /api/sensor {"type": "doorbell"\|"motion"}` | stage backup: the same event the Flipper would send |

POSTs need the header `X-Faraday: 1`, so another web page can't press the buttons.
