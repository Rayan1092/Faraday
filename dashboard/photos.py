"""Newest photo in captures/photos/, so the dashboard shows it the moment the
camera saves it, before Bonsai's description arrives. Cached briefly, since
the page polls every second."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png")


class PhotoWatcher:
    def __init__(self, folder: Path, repo_root: Path, ttl_s: float = 0.5,
                 clock: Callable[[], float] = time.monotonic):
        self.folder, self.repo_root, self.ttl_s, self.clock = folder, repo_root, ttl_s, clock
        self._lock = threading.Lock()
        self._cached: Optional[Dict[str, object]] = None
        self._at = -1e9

    def newest(self) -> Optional[Dict[str, object]]:
        """{"path": "captures/photos/x.jpg", "taken": aware datetime} or None."""
        with self._lock:
            if self.clock() - self._at < self.ttl_s:
                return self._cached
            self._cached, self._at = self._scan(), self.clock()
            return self._cached

    def _scan(self) -> Optional[Dict[str, object]]:
        best, best_mtime = None, -1.0
        try:
            for p in self.folder.iterdir():
                if p.suffix.lower() in PHOTO_SUFFIXES and p.is_file():
                    m = p.stat().st_mtime
                    if m > best_mtime:
                        best, best_mtime = p, m
        except OSError:
            return None
        if best is None:
            return None
        try:
            rel = best.resolve().relative_to(self.repo_root.resolve()).as_posix()
        except ValueError:
            return None
        return {"path": rel, "taken": datetime.fromtimestamp(best_mtime).astimezone()}
