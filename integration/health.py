"""Part health: when a part fails mid-demo, Faraday keeps running, says so clearly
once, and says so again when the part is back (owner 2026-11-15).

Parts and how each is watched:
- flipper: the driver's own connected flag (it reconnects by itself)
- camera:  real frames. Ok while frames arrive; down when the camera fails to open.
           A dead camera is retried every 20 s, so "back" means frames really came.
- stt:     a TCP connect to `argmax-cli serve` (a crashed server refuses; a busy
           one still accepts, so a long transcription never looks like a failure)
- bonsai:  a TCP connect to LM Studio, and every 30 s its /api/v0/models: the
           profile's model must be loaded (an ejected or crashed 9B is down too).
           Vision and the brain fall back to plain code meanwhile.
- tts:     reported by the app's speech worker after every line

A timeout counts only after two polls in a row; a refused connection at once.
A change prints one console line, e.g.
    [health] Argmax STT DOWN: not answering at 127.0.0.1:50060 (start it: argmax-cli serve). Faraday keeps running and retries.
    [health] Argmax STT back
and appends it to logs/health.log. status() is on the controls server's
/api/status as "health", for the dashboard.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger("integration.health")

LABELS = {"flipper": "Flipper", "camera": "Camera", "stt": "Argmax STT", "tts": "Argmax TTS",
          "bonsai": "Bonsai (LM Studio)"}
Check = Callable[[], Tuple[Optional[bool], str]]     # None = can't tell this time: keep the state


def tcp_check(url: str, what: str = "", timeout_s: float = 1.0) -> Check:
    """Up if something accepts a TCP connection at the URL's host:port. Refused =
    down at once; a timeout counts only on the second poll in a row."""
    u = urlparse(url)
    host, port = u.hostname or "127.0.0.1", u.port or (443 if u.scheme == "https" else 80)
    misses = {"n": 0}

    def check() -> Tuple[Optional[bool], str]:
        try:
            with socket.create_connection((host, port), timeout=timeout_s):
                pass
        except socket.timeout:
            misses["n"] += 1
            if misses["n"] < 2:
                return None, ""
            return False, f"not answering at {host}:{port}{what}"
        except OSError:
            misses["n"] = 0
            return False, f"not answering at {host}:{port}{what}"
        misses["n"] = 0
        return True, ""
    return check


def lm_studio_check(base_url: str, model: str, every_s: float = 30.0) -> Check:
    """LM Studio is up (TCP) and, checked every `every_s`, `model` is loaded."""
    alive = tcp_check(base_url, "  (start LM Studio's server)")
    state = {"at": 0.0, "loaded": True}

    def check() -> Tuple[Optional[bool], str]:
        ok, detail = alive()
        if ok is not True:
            return ok, detail
        if model and time.monotonic() - state["at"] >= every_s:
            state["at"] = time.monotonic()
            try:
                import requests
                data = requests.get(base_url.rstrip("/") + "/api/v0/models", timeout=3).json().get("data", [])
                state["loaded"] = any(m.get("id") == model and str(m.get("state", "")).lower() == "loaded"
                                      for m in data)
            except Exception:
                pass                    # busy with a long call, or an older LM Studio: keep the last answer
        if not state["loaded"]:
            return False, f"{model} isn't loaded (ejected, or it crashed): load it in LM Studio"
        return True, ""
    return check


class Health:
    def __init__(self, out: Callable[[str], None] = print, interval_s: float = 5.0,
                 grace_s: float = 10.0, log_path: Optional[Path] = None):
        self.out = out
        self.interval_s, self.grace_s = interval_s, grace_s
        self.log_path = log_path
        self.checks: Dict[str, Check] = {}
        self._state: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at = time.monotonic()

    def add(self, part: str, check: Check) -> None:
        self.checks[part] = check

    def report(self, part: str, ok: Optional[bool], detail: str = "") -> None:
        """Record a part's state; prints only when it changes, and not during the
        start-up grace while parts are still coming up. Never raises."""
        if ok is None:
            return                                   # can't tell this time
        try:
            now = time.monotonic()
            with self._lock:
                old = self._state.get(part)
                in_grace = now - self._started_at < self.grace_s and old is None
                if in_grace and not ok:
                    return                           # decide after the grace period
                if old is not None and old["ok"] == ok:
                    if not ok:
                        old["detail"] = detail
                    return                           # no change: keep "since"
                self._state[part] = {"ok": ok, "detail": "" if ok else detail, "since": time.time()}
                changed = (old is None and not ok) or old is not None
            if changed:
                self._announce(part, ok, detail)
        except Exception:
            log.exception("health report failed")

    def _announce(self, part: str, ok: bool, detail: str) -> None:
        label = LABELS.get(part, part)
        line = (f"  [health] {label} back" if ok
                else f"  [health] {label} DOWN: {detail}. Faraday keeps running and retries.")
        self.out(line)
        log.info(line.strip())
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now().isoformat(timespec='seconds')} {line.strip()}\n")
            except OSError:
                pass

    def status(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._state.items()}

    def check_now(self) -> None:
        for part, check in list(self.checks.items()):
            try:
                ok, detail = check()
            except Exception as e:                  # a broken check must not stop the others
                ok, detail = False, f"check failed ({e!r})"
            self.report(part, ok, detail)

    def start(self) -> "Health":
        self._started_at = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="faraday-health", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(3)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.check_now()


def camera_check(feed: Any, retry_s: float = 20.0) -> Check:
    """Ok while real frames arrive; down when the camera fails to open. While it's
    down and idle, open it again every `retry_s`, so "back" means frames came."""
    state = {"ok": None, "tried": 0.0}

    def check() -> Tuple[Optional[bool], str]:
        age = feed.frame_age()
        if age is not None and age < 5.0:
            state["ok"] = True
        elif feed.failed:
            state["ok"] = False
            if not feed.running and time.monotonic() - state["tried"] >= retry_s:
                state["tried"] = time.monotonic()
                feed._touch()                        # try again (the frames decide)
        return state["ok"], "the camera doesn't open (unplugged, or no camera permission)"
    return check
