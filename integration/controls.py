"""Controls server: live camera, intercom and outlets, on dashboard port + 1.

Dell's dashboard server is JSON-only (4 KB body cap) and lives in Dell's folder,
so the binary media runs here, next to it (PLAN Q27):

    GET  /                  reference Controls page (works on its own, end to end)
    GET  /stream.mjpg       live camera, MJPEG (use as <img src=...>)
    GET  /frame.jpg         one current frame
    GET  /api/status        {"camera": bool, "intercom": bool, "outlets": {...}, "health": {...}}
    POST /api/intercom      raw int16 mono PCM chunk, header X-Sample-Rate (default 16000)
    POST /api/intercom/end  the owner released the talk button
    POST /api/control       {"outlet": "porch_light", "on": true}

POSTs need the header "X-Faraday: 1" (same rule as the dashboard). CORS is
allowed only for pages served on the dashboard's port or this one, so Dell's
page at :8080 can call these routes and other sites can't.

Browser mic (Chrome) needs a secure context: http://127.0.0.1 and
http://localhost already are. From another device, add both origins
(http://<mac-ip>:8080 and http://<mac-ip>:8081) to
chrome://flags/#unsafely-treat-insecure-origin-as-secure and set
dashboard.host to 0.0.0.0.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger("integration.controls")

MAX_JSON = 4096
MAX_PCM = 256 * 1024          # ~8 s of 16 kHz audio per chunk, far more than a chunk needs
BOUNDARY = "faradayframe"


class _QuietServer(ThreadingHTTPServer):
    """A viewer closing the tab mid-stream is normal, not an error to print."""
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        log.exception("controls request from %s failed", client_address)


class ControlsServer:
    def __init__(self, host: str, port: int, *, feed: Any = None, intercom: Any = None,
                 control: Optional[Callable[[str, bool], bool]] = None,
                 outlet_state: Optional[Callable[[], Dict[str, str]]] = None,
                 dashboard_port: Optional[int] = None,
                 health: Optional[Callable[[], Dict[str, Any]]] = None):
        self.feed, self.intercom = feed, intercom
        self.control_fn, self.outlet_state = control, outlet_state
        self.health_fn = health
        self.dashboard_port = dashboard_port
        self.httpd = _QuietServer((host, port), _make_handler(self))
        self._thread: Optional[threading.Thread] = None
        self.streams_served = 0

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/"

    def allowed_origin(self, origin: Optional[str]) -> Optional[str]:
        """Pages on the dashboard's port or this port may call us (any host, so
        http://<mac-ip>:8080 works from a phone); nothing else."""
        if not origin:
            return None
        try:
            parsed = urlparse(origin)
        except ValueError:
            return None
        if parsed.scheme not in ("http", "https") or parsed.port is None:
            return None
        return origin if parsed.port in (self.port, self.dashboard_port) else None

    def start(self) -> "ControlsServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        name="faraday-controls", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._thread is not None:     # shutdown() without serve_forever() running hangs
            self.httpd.shutdown()
            self._thread = None
        self.httpd.server_close()
        if self.intercom is not None:
            self.intercom.stop()

    # ---- routes ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return {"camera": self.feed is not None and not getattr(self.feed, "failed", False),
                "intercom": self.intercom is not None,
                "intercom_active": bool(self.intercom is not None and self.intercom.active),
                "outlets": self.outlet_state() if self.outlet_state else {},
                # part health: {"stt": {"ok": false, "detail": "...", "since": 1731650000.0}, ...}
                "health": self.health_fn() if self.health_fn else {}}

    def do_control(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        if self.control_fn is None:
            return 503, {"ok": False, "error": "outlet control isn't connected"}
        outlet, on = body.get("outlet"), body.get("on")
        if not isinstance(outlet, str) or not isinstance(on, bool):
            return 400, {"ok": False, "error": 'send {"outlet": "...", "on": true|false}'}
        changed = self.control_fn(outlet, on)
        return 200, {"ok": True, "changed": bool(changed)}


def _make_handler(app: ControlsServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FaradayControls"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        # ---- CORS --------------------------------------------------------------
        def _cors(self) -> None:
            origin = app.allowed_origin(self.headers.get("Origin"))
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def do_OPTIONS(self) -> None:
            origin = app.allowed_origin(self.headers.get("Origin"))
            self.send_response(204 if origin else 403)
            if origin:
                self._cors()
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers",
                                 "Content-Type, X-Faraday, X-Sample-Rate")
                self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ---- GET -----------------------------------------------------------------
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            if path == "/api/status":
                return self._json(200, app.status())
            if path == "/frame.jpg":
                data = app.feed.jpeg() if app.feed is not None else None
                if data is None:
                    return self._json(503, {"error": "camera unavailable"})
                return self._send(200, "image/jpeg", data)
            if path == "/stream.mjpg":
                return self._stream()
            self._json(404, {"error": "not found"})

        def _stream(self) -> None:
            if app.feed is None:
                return self._json(503, {"error": "camera unavailable"})
            app.streams_served += 1
            self.close_connection = True
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            last = 0.0
            try:
                with app.feed.viewer():
                    while True:
                        data, last = app.feed.next_jpeg(last, timeout=5.0)
                        if data is None:
                            if getattr(app.feed, "failed", False):
                                break
                            continue
                        self.wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                                         f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
                        self.wfile.write(data)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass            # the viewer closed the page

        # ---- POST ----------------------------------------------------------------
        def do_POST(self) -> None:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            limit = MAX_PCM if path == "/api/intercom" else MAX_JSON
            if length > limit:
                self.close_connection = True
                return self._json(413, {"error": "too large"})
            raw = self.rfile.read(length) if length else b""
            if self.headers.get("X-Faraday") != "1":
                return self._json(403, {"error": "missing X-Faraday header"})
            if path == "/api/intercom":
                return self._intercom(raw)
            if path == "/api/intercom/end":
                if app.intercom is not None:
                    app.intercom.end()
                return self._json(200, {"ok": True})
            handlers = {"/api/control": app.do_control}
            handler = handlers.get(path)
            if handler is None:
                return self._json(404, {"error": "not found"})
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "send a JSON object"})
            try:
                code, reply = handler(body)
            except Exception as e:
                log.exception("controls %s failed", path)
                code, reply = 500, {"ok": False, "error": type(e).__name__}
            self._json(code, reply)

        def _intercom(self, raw: bytes) -> None:
            if app.intercom is None:
                return self._json(503, {"ok": False, "error": "no speaker"})
            try:
                rate = int(self.headers.get("X-Sample-Rate") or 16000)
            except ValueError:
                return self._json(400, {"error": "bad X-Sample-Rate"})
            if not 8000 <= rate <= 48000:
                return self._json(400, {"error": "X-Sample-Rate must be 8000..48000"})
            try:
                app.intercom.feed(raw, rate)
            except Exception as e:
                log.exception("intercom playback failed")
                return self._json(500, {"ok": False, "error": type(e).__name__})
            self._json(200, {"ok": True, "samples": len(raw) // 2})

        # ---- helpers -------------------------------------------------------------
        def _send(self, code: int, ctype: str, data: bytes) -> None:
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, data: Any) -> None:
            self._send(code, "application/json; charset=utf-8",
                       json.dumps(data, ensure_ascii=False).encode("utf-8"))

    return Handler


# Reference page: plain, offline, no external assets. Dell's Controls page can
# replace it; the routes above are the contract.
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Faraday controls</title>
<style>
:root { --bg: #f4f5f7; --fg: #15171a; --muted: #5d636b; --card: #fff; --line: #d9dce1;
        --accent: #2457d6; --on: #1a7f37; --warn: #b42318; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #111316; --fg: #eceef1; --muted: #9aa1aa; --card: #1b1e22; --line: #2c3138;
          --accent: #6f97ff; --on: #4ac26b; --warn: #ff6b5e; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.4 system-ui, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 16px; display: grid; gap: 16px;
       grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
section { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
h1 { font-size: 20px; margin: 16px 16px 0; } h2 { font-size: 16px; margin: 0 0 12px; }
img { width: 100%; border-radius: 8px; background: #000; aspect-ratio: 16 / 9; object-fit: cover; }
button { font: inherit; padding: 12px 16px; border-radius: 10px; border: 1px solid var(--line);
         background: var(--bg); color: var(--fg); cursor: pointer; min-height: 44px; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.live { background: var(--warn); border-color: var(--warn); color: #fff; }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 8px 0; }
.name { flex: 1; min-width: 120px; } .muted { color: var(--muted); font-size: 14px; }
#talk { width: 100%; padding: 20px; font-size: 18px; user-select: none; touch-action: none; }
</style></head><body>
<h1>Faraday controls</h1>
<main>
  <section><h2>Camera</h2><img id="cam" alt="Live camera"><p class="muted" id="camnote">Live view</p></section>
  <section><h2>Intercom</h2>
    <button id="talk" class="primary">Hold to talk to the door</button>
    <p class="muted" id="talknote">The door mic is muted while you talk. Faraday listens for the reply by itself.</p>
  </section>
  <section><h2>Lights</h2><div id="outlets"></div></section>
</main>
<script>
"use strict";
const $ = (id) => document.getElementById(id);
const H = { "X-Faraday": "1" };
const post = (path, body) => fetch(path, { method: "POST",
  headers: Object.assign({ "Content-Type": "application/json" }, H), body: JSON.stringify(body || {}) })
  .then((r) => r.json().catch(() => ({})));

$("cam").src = "/stream.mjpg";
$("cam").onerror = () => { $("camnote").textContent = "Camera unavailable"; };

const NAMES = { porch_light: "Porch light", living_room_lamp: "Living room lamp", spare: "Spare outlet" };
function drawOutlets(state) {
  const box = $("outlets"); box.textContent = "";
  for (const id of Object.keys(NAMES)) {
    const row = document.createElement("div"); row.className = "row";
    const name = document.createElement("span"); name.className = "name";
    name.textContent = NAMES[id] + (state[id] ? " (" + state[id].replace("outlet_", "") + ")" : "");
    row.appendChild(name);
    for (const on of [true, false]) {
      const b = document.createElement("button"); b.textContent = on ? "On" : "Off";
      b.onclick = () => post("/api/control", { outlet: id, on }).then(refresh);
      row.appendChild(b);
    }
    box.appendChild(row);
  }
}
function refresh() {
  fetch("/api/status", { cache: "no-store" }).then((r) => r.json()).then((s) => {
    drawOutlets(s.outlets || {});
    $("talk").disabled = !s.intercom;
    if (!s.intercom) $("talknote").textContent = "No speaker connected.";
  }).catch(() => {});
}
refresh(); setInterval(refresh, 3000);
// ---- intercom: browser mic -> 16 kHz int16 chunks -> POST /api/intercom ----
const RATE = 16000;
let ctx = null, src = null, node = null, stream = null, sending = false, queue = Promise.resolve();
function toInt16(f32) {
  const out = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i])); out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}
async function startTalk() {
  if (sending) return;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio:
      { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
  } catch (e) {
    $("talknote").textContent = "Microphone blocked: " + e.name + ". See the Chrome trust note.";
    return;
  }
  ctx = new AudioContext({ sampleRate: RATE });
  src = ctx.createMediaStreamSource(stream);
  node = ctx.createScriptProcessor(2048, 1, 1);
  node.onaudioprocess = (ev) => {
    if (!sending) return;
    const pcm = toInt16(ev.inputBuffer.getChannelData(0));
    const rate = String(ctx.sampleRate);
    queue = queue.then(() => fetch("/api/intercom", { method: "POST", body: pcm.buffer,
      headers: Object.assign({ "Content-Type": "application/octet-stream", "X-Sample-Rate": rate }, H) })
      .catch(() => {}));
  };
  src.connect(node); node.connect(ctx.destination);
  sending = true;
  $("talk").classList.add("live"); $("talk").textContent = "Talking... release to stop";
}
function stopTalk() {
  if (!sending) return;
  sending = false;
  queue = queue.then(() => post("/api/intercom/end"));
  try { node.disconnect(); src.disconnect(); ctx.close(); } catch (e) {}
  if (stream) stream.getTracks().forEach((t) => t.stop());
  $("talk").classList.remove("live"); $("talk").textContent = "Hold to talk to the door";
}
const talk = $("talk");
talk.addEventListener("pointerdown", (e) => { e.preventDefault(); talk.setPointerCapture(e.pointerId); startTalk(); });
talk.addEventListener("pointerup", stopTalk);
talk.addEventListener("pointercancel", stopTalk);
talk.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) startTalk(); });
talk.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") stopTalk(); });
// ?autotalk=N holds the talk button for N seconds on load (automated browser test)
const auto = Number(new URLSearchParams(location.search).get("autotalk") || 0);
if (auto > 0) startTalk().then(() => setTimeout(stopTalk, auto * 1000));
</script></body></html>
"""
