"""The event/action log: one JSON object per line in logs/events.jsonl.

Every string in an entry is checked for the passphrase and redacted before it
is written or kept in memory, so nothing read back from here (brain prompt,
dashboard, summaries) can show it. Redaction happens upstream in modes/ too;
this is the second layer.

Thread-safe, stdlib only, same on Windows and macOS.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Union

from shared import Action, ActionType, Event

from modes.passphrase import redact

Entry = Dict[str, Any]
REPO_ROOT = Path(__file__).resolve().parents[1]


class EventLog:
    def __init__(self, path: Union[str, Path], passphrase: str = "", keep: int = 500):
        """path: the .jsonl file. Relative paths are taken from the repo root."""
        path = Path(path)
        self.path = path if path.is_absolute() else REPO_ROOT / path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._passphrase = passphrase
        self._lock = threading.Lock()
        self._entries: Deque[Entry] = deque(maxlen=keep)
        self._load_tail()

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "EventLog":
        logs_dir = settings.get("paths", {}).get("logs", "logs")
        secret = str(settings.get("security", {}).get("passphrase", ""))
        return cls(Path(logs_dir) / "events.jsonl", secret)

    def write(self, message: Union[Event, Action, Entry]) -> Entry:
        """Append one event or action. Returns the redacted dict that was stored."""
        entry = message.to_dict() if hasattr(message, "to_dict") else dict(message)
        entry = self._redact(entry)
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
            self._entries.append(entry)
        return json.loads(line)

    def recent(self, n: int = 10) -> List[Entry]:
        """The last n entries, oldest first, as fresh copies."""
        with self._lock:
            items = list(self._entries)[-n:] if n > 0 else []
        return [json.loads(json.dumps(e)) for e in items]

    def messages(self, n: int = 20) -> List[Entry]:
        """The last n save_message actions (for the dashboard)."""
        with self._lock:
            found = [e for e in self._entries
                     if e.get("kind") == "action" and e.get("type") == ActionType.SAVE_MESSAGE]
        return [json.loads(json.dumps(e)) for e in found[-n:]]

    # ---- helpers -----------------------------------------------------------

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact(value, self._passphrase)[0]
        if isinstance(value, dict):
            return {k: self._redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact(v) for v in value]
        return value

    def _load_tail(self) -> None:
        """Keep the end of an existing log so a restart doesn't lose recent context."""
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue  # a torn last line after a crash
                if isinstance(entry, dict):
                    self._entries.append(self._redact(entry))
