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

POSTs need the header `X-Faraday: 1`, so another web page can't press the buttons.
