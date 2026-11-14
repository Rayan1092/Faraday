"""Settings loader for hardware/ (NV-owned).

Reads config/settings.json, or falls back to config/settings.example.json
with a warning so a fresh checkout still runs. Values keyed by "mac" /
"windows" are picked for the current OS by `for_os()`.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


def os_key() -> str:
    """'mac' on macOS, 'windows' on Windows, 'linux' elsewhere."""
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    if path is None:
        path = CONFIG_DIR / "settings.json"
        if not path.exists():
            log.warning("config/settings.json missing, using settings.example.json")
            path = CONFIG_DIR / "settings.example.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def for_os(value: Any) -> Any:
    """If value is {"mac": .., "windows": ..}, return this OS's entry."""
    if isinstance(value, dict) and ({"mac", "windows"} & value.keys()):
        return value.get(os_key())
    return value
