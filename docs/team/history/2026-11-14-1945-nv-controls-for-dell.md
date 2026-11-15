# For Dell: Controls page backend (camera stream, intercom, lights, Listen)

- **From:** NV, branch `nv/controls`. Owner's items 4–6. Built and tested on Windows with fakes; real devices in the Mac session.

## Exact URLs
| What | URL |
|---|---|
| Dashboard (yours) | `http://127.0.0.1:8080/` |
| NV controls server | `http://127.0.0.1:8081/` (always dashboard port + 1) |
| Live camera | `<img src="http://127.0.0.1:8081/stream.mjpg">` (MJPEG, ~10 fps) |
| One frame | `GET http://127.0.0.1:8081/frame.jpg` |
| Availability | `GET http://127.0.0.1:8081/api/status` → `{"camera", "intercom", "intercom_active", "listen", "outlets"}` |
| Intercom chunk | `POST http://127.0.0.1:8081/api/intercom`, body = raw int16 mono PCM, headers `X-Faraday: 1`, `X-Sample-Rate: 16000`, `Content-Type: application/octet-stream` |
| Intercom end | `POST http://127.0.0.1:8081/api/intercom/end` (header `X-Faraday: 1`) |
| Outlet | `POST http://127.0.0.1:8081/api/control {"outlet": "porch_light", "on": true}` → `{"ok", "changed"}` |
| Listen | `POST http://127.0.0.1:8081/api/listen` → `{"ok", "opened"}` |

CORS allows pages on port 8080 or 8081 from any host, so your page at `:8080` can `fetch()` these directly. Build the URL from `location.hostname` + `:8081`, so it also works from a phone.

**Same calls in-process, if you'd rather proxy from your server:** `board.control_hook(outlet, on)` and `board.listen_hook()` are set on your `Dashboard` object (PLAN Q26). The intercom and stream need the 8081 routes; your server's 4 KB JSON cap can't carry them.

## Intercom from your page
Copy the JavaScript from the reference page (`GET http://127.0.0.1:8081/`, at the bottom of `integration/controls.py`):
1. `getUserMedia({audio: {echoCancellation: true}})`.
2. `new AudioContext({sampleRate: 16000})`, then a `ScriptProcessor(2048)`.
3. Float32 → Int16.
4. POST each block to `/api/intercom` in order.
5. On release, POST `/api/intercom/end`.

The door speaker plays it live, and the door mic is muted while it plays. There's no text "say" fallback (owner's rule). Proven end to end: headless Chrome with a fake mic sent 1.9 s of a 2 s hold at 16 kHz through this exact code (`integration/tests/test_browser_intercom.py`).

## Chrome trust step (mic needs a secure context)
- **On the Mac itself:** nothing. `http://127.0.0.1:8080` and `:8081` are secure contexts already.
- **From another device (phone, laptop):**
  1. Set `dashboard.host` to `0.0.0.0` in the Mac's `config/settings.json`.
  2. In Chrome on that device, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
  3. Add `http://<mac-ip>:8080,http://<mac-ip>:8081`.
  4. Relaunch.

## Camera
One owner (`CameraFeed` in `hardware/camera.py`). Photos for `take_photo` and the live stream share the same capture, so they never fight over the webcam. It opens on first use and closes 15 s after the last viewer or photo.

## Open for you
PLAN Q26 and Q27: OK with the hooks and routes? The Listen button can use either `board.listen_hook` (in-process) or `POST :8081/api/listen`.
