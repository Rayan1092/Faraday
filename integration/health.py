"""Part health: when a part fails mid-demo, Faraday keeps running, says so clearly
once, and says so again when the part is back (owner 2026-11-15).

Parts and how each is watched:
- flipper: the driver's own connected flag (it reconnects by itself)
- camera:  the camera feed's failed flag (it reopens by itself on the next use)
- stt:     an HTTP probe of the Argmax server (`argmax-cli serve`); any answer = up
- bonsai:  an HTTP probe of LM Studio's /v1/models (vision and the brain fall back
           to plain code while it's down, and use it again when it answers)
- tts:     reported by the app's speech worker after every line

A transition prints one console line, e.g.
    [health] Argmax STT DOWN: not answering at http://127.0.0.1:50060. Faraday keeps running and retries.
    [health] Argmax STT back
and logs a warning (down) or info (back). status() is on the controls server's
/api/status as "health", for the dashboard.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger("integration.health")

LABELS = {"flipper": "Flipper", "camera": "Camera", "stt": "Argmax STT", "tts": "Argmax TTS",
          "bonsai": "Bonsai (LM Studio)"}
Check = Callable[[], Tuple[bool, str]]


def http_check(url: str, what: str, ok_status: Optional[int] = None, timeout_s: float = 2.0) -> Check:
    """Up if `url` answers (any HTTP status, or exactly ok_status if given)."""
    def check() -> Tuple[bool, str]:
        import requests
        try:
            r = requests.get(url, timeout=timeout_s)
        except Exception:
            return False, f"not answering at {url}{what}"
        if ok_status is not None and r.status_code != ok_status:
            return False, f"answers HTTP {r.status_code} at {url}{what}"
        return True, ""
    return check


class Health:
    def __init__(self, out: Callable[[str], None] = print, interval_s: float = 5.0,
                 grace_s: float = 10.0):
        self.out = out
        self.interval_s, self.grace_s = interval_s, grace_s
        self.checks: Dict[str, Check] = {}
        self._state: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at = 0.0

    def add(self, part: str, check: Check) -> None:
        self.checks[part] = check

    def report(self, part: str, ok: bool, detail: str = "") -> None:
        """Record a part's state; prints only when it changes (and not during the
        start-up grace, when parts are still coming up)."""
        now = time.monotonic()
        with self._lock:
            old = self._state.get(part)
            self._state[part] = {"ok": ok, "detail": "" if ok else detail, "since": time.time()}
            changed = old is None and not ok or (old is not None and old["ok"] != ok)
            in_grace = now - self._started_at < self.grace_s and old is None
        if not changed or (in_grace and not ok):
            if in_grace and not ok:
                with self._lock:
                    self._state.pop(part, None)      # decide after the grace period
            return
        label = LABELS.get(part, part)
        if ok:
            self.out(f"  [health] {label} back")
            log.info("%s back", label)
        else:
            self.out(f"  [health] {label} DOWN: {detail}. Faraday keeps running and retries.")
            log.warning("%s down: %s", label, detail)

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
