"""Local dashboard server. Stdlib only (http.server), fully offline.

Serves the page and its assets from dashboard/static/, the live state as JSON,
and photos from captures/. Buttons post back to the pipeline:
- Away / Night  -> pipeline.owner_set_mode()
- Home          -> pipeline.owner_passphrase(), the only way to disarm
- text box      -> a speech_heard event (backup if the mic or STT fails)
- light switch  -> integration's control_hook (outlet_on/off, source dashboard)
- Snapshot      -> pipeline.owner_snapshot() (take_photo, source dashboard)
- Listen        -> integration's listen_hook (opens one listening window)

/controls is the phone-style Controls page. Its live camera and intercom come from
integration's controls server on dashboard port + 1 (PLAN Q27); `controls_port` in
/api/state tells the page where that is.

Safety:
- POSTs must carry the header "X-Faraday: 1". Browsers won't send a custom header
  cross-site without a CORS preflight, which this server never approves, so
  another web page can't press the buttons.
- Wrong passphrases are rate-limited: after 5 misses, 30 s of lockout.
- The typed passphrase is never logged or echoed.
- /media only serves .jpg/.jpeg/.png files inside captures/.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from shared import EventType, Mode, Outlet, Source, make_event

from brain.pipeline import snapshot_action
from rules import instructions as owner_instructions

from .netcheck import NetCheck
from .photos import PhotoWatcher
from .state import build_state

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
MEDIA_DIR = REPO_ROOT / "captures"
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
# Explicit, because the Windows registry can map .js to text/plain.
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
                ".png": "image/png", ".ico": "image/x-icon"}
MAX_BODY = 4096


class PassphraseLimiter:
    def __init__(self, max_misses: int = 5, lockout_s: float = 30, clock=time.monotonic):
        self.max_misses, self.lockout_s, self.clock = max_misses, lockout_s, clock
        self.misses, self.locked_until = 0, 0.0
        self._lock = threading.Lock()

    def wait_s(self) -> float:
        with self._lock:
            return max(0.0, self.locked_until - self.clock())

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.misses = 0
                return
            self.misses += 1
            if self.misses >= self.max_misses:
                self.misses = 0
                self.locked_until = self.clock() + self.lockout_s


class Dashboard:
    """Owns the HTTP server thread. `pipeline` is a brain.pipeline.Pipeline."""

    def __init__(self, pipeline: Any, host: str = "127.0.0.1", port: int = 8080,
                 colors: Optional[Dict[str, str]] = None):
        self.pipeline = pipeline
        self.colors = colors
        self.limiter = PassphraseLimiter()
        self.netcheck = NetCheck()
        self.photos = PhotoWatcher(MEDIA_DIR / "photos", REPO_ROOT)
        self.sensor_keys = {"doorbell": "0x00DEC608", "motion": "0x00C5AFAE"}
        # integration/ sets these (PLAN Q26). listen_hook() -> bool opens one listening
        # window (the Listen button); talk_hook is the old name for the same thing.
        # listening_hook() -> bool, optional, says whether the mic is open right now.
        self.listen_hook: Optional[Any] = None
        self.talk_hook: Optional[Any] = None
        self.listening_hook: Optional[Any] = None
        # integration/ sets this (PLAN Q26): control_hook(outlet, on) -> bool switches a
        # light through its dispatcher and logs it with source "dashboard".
        self.control_hook: Optional[Any] = None
        self._controls_port: Optional[int] = None   # settings controls.port, else port + 1
        self.httpd = ThreadingHTTPServer((host, port), _make_handler(self))
        self.httpd.daemon_threads = True
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def from_settings(cls, pipeline: Any, settings: Dict[str, Any],
                      port: Optional[int] = None) -> "Dashboard":
        cfg = settings.get("dashboard", {})
        board = cls(pipeline, cfg.get("host", "127.0.0.1"),
                    cfg.get("port", 8080) if port is None else port)
        if settings.get("controls", {}).get("port"):
            board._controls_port = int(settings["controls"]["port"])
        for name in board.sensor_keys:
            key = settings.get("sensors", {}).get(name, {}).get("key")
            if key:
                board.sensor_keys[name] = key
        return board

    @property
    def controls_port(self) -> int:
        """Where integration's controls server (camera stream, intercom) listens."""
        return self._controls_port or self.httpd.server_address[1] + 1

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> "Dashboard":
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        name="faraday-dashboard", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # ---- what the routes do ------------------------------------------------

    def state(self) -> Dict[str, Any]:
        p = self.pipeline
        rules = p.rules.all() if getattr(p, "rules", None) is not None else []
        state = build_state(p.log.recent(500), p.modes.mode, p.modes.alert, rules, self.colors,
                            network=self.netcheck.status(), newest_photo=self.photos.newest())
        listen = self._listen_fn()
        state["listen_available"] = state["talk_available"] = listen is not None
        state["listening"] = self._listening()
        state["control_available"] = self.control_hook is not None
        state["controls_port"] = self.controls_port
        state["instructions"] = self.instructions()
        return state

    def instructions(self) -> list:
        """The owner's standing instructions for visitors, for the Rules card."""
        store = getattr(self.pipeline, "instructions", None)
        if store is None:
            return []
        return [{"id": i["id"], "who": owner_instructions.NAMES[i["audience"]],
                 "text": owner_instructions.owner_words(i), "said": i.get("said", "")}
                for i in store.all()]

    def delete_instruction(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        store = getattr(self.pipeline, "instructions", None)
        ok = store is not None and store.remove(str(body.get("id", "")))
        return (200 if ok else 404), {"ok": bool(ok)}

    def snapshot(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Snapshot button: a take_photo with source dashboard. Answers at once with the
        request id; the page waits for the photo whose `request` matches."""
        action = snapshot_action()
        threading.Thread(target=self.pipeline.owner_snapshot, kwargs={"action": action},
                         daemon=True).start()
        return 202, {"ok": True, "id": action.id}

    def control(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """A light switch on the page: {"outlet": "porch_light", "on": true}. Integration
        sends it and logs it; the Lights panel then follows the log, so it shows the
        real state, not what the button hoped for."""
        outlet, on = body.get("outlet"), body.get("on")
        if outlet not in Outlet.ALL or not isinstance(on, bool):
            return 400, {"ok": False, "error": "Send an outlet name and on: true or false."}
        if self.control_hook is None:
            return 503, {"ok": False, "error": "The light switches aren't connected."}
        changed = bool(self.control_hook(outlet, on))
        return 200, {"ok": True, "changed": changed}

    def _listen_fn(self) -> Optional[Any]:
        return self.listen_hook or self.talk_hook

    def _listening(self) -> Optional[bool]:
        """True/False from integration's listening_hook, None if it isn't set."""
        if self.listening_hook is None:
            return None
        try:
            return bool(self.listening_hook())
        except Exception:
            return None

    def listen(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Listen button: one listening window. integration's hook returns at once
        (False if a window is already open)."""
        hook = self._listen_fn()
        if hook is None:
            return 503, {"ok": False, "error": "The microphone isn't connected."}
        opened = hook()
        return 200, {"ok": True, "opened": True if opened is None else bool(opened)}

    def set_mode(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        mode = body.get("mode")
        if mode not in Mode.ARMED:
            return 400, {"ok": False, "error": "Use the Home button and the passphrase to disarm."}
        changed = self.pipeline.owner_set_mode(mode) is not None
        return 200, {"ok": True, "changed": changed, "mode": self.pipeline.modes.mode}

    def go_home(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        wait = self.limiter.wait_s()
        if wait > 0:
            return 429, {"ok": False, "error": f"Too many tries. Wait {wait:.0f} s."}
        if not self.pipeline.modes.armed:
            return 200, {"ok": True, "mode": self.pipeline.modes.mode}
        ok = self.pipeline.owner_passphrase(str(body.get("passphrase", ""))) is not None
        self.limiter.record(ok)
        if not ok:
            return 403, {"ok": False, "error": "Wrong passphrase."}
        return 200, {"ok": True, "mode": self.pipeline.modes.mode}

    def say(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        text = str(body.get("text", "")).strip()[:500]
        if not text:
            return 400, {"ok": False, "error": "Type something first."}
        event = make_event(EventType.SPEECH_HEARD, {"text": text}, Source.DASHBOARD)
        # Bonsai can take seconds: answer the browser now, the feed shows the result.
        threading.Thread(target=self.pipeline.handle, args=(event,), daemon=True).start()
        return 202, {"ok": True}

    def sensor(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Stage backup: the same doorbell/motion event the Flipper would send, marked
        source "dashboard" in the log so nobody is fooled."""
        kind = body.get("type")
        if kind not in self.sensor_keys:
            return 400, {"ok": False, "error": "type must be doorbell or motion"}
        data = {"key": self.sensor_keys[kind]}
        if kind == "motion":
            data.update(packets=17, duration_ms=1960)
        event = make_event(kind, data, Source.DASHBOARD)
        threading.Thread(target=self.pipeline.handle, args=(event,), daemon=True).start()
        return 202, {"ok": True}

    def delete_rule(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        rules = getattr(self.pipeline, "rules", None)
        ok = rules is not None and rules.remove(str(body.get("id", "")))
        return (200 if ok else 404), {"ok": bool(ok)}


def _make_handler(app: Dashboard):
    posts = {"/api/mode": app.set_mode, "/api/home": app.go_home,
             "/api/say": app.say, "/api/rules/delete": app.delete_rule,
             "/api/sensor": app.sensor, "/api/listen": app.listen,
             "/api/talk": app.listen,   # old name for Listen
             "/api/control": app.control,
             "/api/snapshot": app.snapshot, "/api/instructions/delete": app.delete_instruction}

    class Handler(BaseHTTPRequestHandler):
        server_version = "Faraday"

        def log_message(self, *args: Any) -> None:  # keep the console for Faraday's own output
            pass

        def do_GET(self) -> None:
            path = unquote(urlparse(self.path).path)
            if path in ("/", "/index.html"):
                return self._file(STATIC_DIR / "index.html", STATIC_DIR, STATIC_TYPES)
            if path in ("/controls", "/controls/"):
                return self._file(STATIC_DIR / "controls.html", STATIC_DIR, STATIC_TYPES)
            if path.startswith("/static/"):
                return self._file(STATIC_DIR / path[len("/static/"):], STATIC_DIR, STATIC_TYPES)
            if path == "/api/state":
                return self._json(200, app.state())
            if path.startswith("/media/"):
                rel = path[len("/media/"):]
                if not rel.startswith("captures/"):
                    return self._json(404, {"error": "not found"})
                return self._file(REPO_ROOT / rel, MEDIA_DIR, MEDIA_TYPES)
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.close_connection = True
                return self._json(413, {"error": "too large"})
            # Read the body before any reply: answering early with unread data makes
            # Windows abort the connection on the client side.
            raw = self.rfile.read(length) if length else b""
            handler = posts.get(path)
            if handler is None:
                return self._json(404, {"error": "not found"})
            if self.headers.get("X-Faraday") != "1":
                return self._json(403, {"error": "missing X-Faraday header"})
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "send a JSON object"})
            try:
                code, reply = handler(body)
            except Exception as e:  # never take the server down
                code, reply = 500, {"ok": False, "error": type(e).__name__}
            self._json(code, reply)

        # ---- helpers -------------------------------------------------------

        def _json(self, code: int, data: Any) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _file(self, path: Path, root: Path, types: Dict[str, str]) -> None:
            try:
                real = path.resolve()
                inside = real.is_relative_to(root.resolve())
            except (OSError, ValueError):
                inside = False
            suffix = real.suffix.lower() if inside else ""
            if not inside or not real.is_file() or suffix not in types:
                return self._json(404, {"error": "not found"})
            ctype = types[suffix]
            raw = real.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

    return Handler
