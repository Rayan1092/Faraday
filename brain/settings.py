"""Load config/settings.json, falling back to config/settings.example.json.

No addresses are hardcoded anywhere in brain/: the Bonsai URL and model come
from the `llm` section of the settings file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


def settings_path() -> Path:
    real = CONFIG_DIR / "settings.json"
    return real if real.exists() else CONFIG_DIR / "settings.example.json"


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    with open(path or settings_path(), encoding="utf-8") as f:
        return json.load(f)


def os_key() -> str:
    """'mac' or 'windows', the keys settings.json uses for OS-specific values."""
    return "mac" if sys.platform == "darwin" else "windows"


def llm_config(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten the llm section: the chosen profile's base_url/model plus the shared knobs."""
    llm = settings.get("llm", {})
    profiles = llm.get("profiles", {})
    profile_name = llm.get("profile")
    if profile_name not in profiles:
        raise ValueError(f"llm.profile '{profile_name}' not found in llm.profiles")
    profile = profiles[profile_name]
    return {
        "profile": profile_name,
        "base_url": profile["base_url"],
        "model": profile["model"],
        # Optional: the small, fast Bonsai for the threat label and quick decisions.
        "fast_model": profile.get("fast_model"),
        "max_tokens": int(llm.get("max_tokens", 200)),
        "temperature": float(llm.get("temperature", 0.2)),
        "timeout_s": float(llm.get("timeout_s", 30)),
        "recent_log_lines": int(llm.get("recent_log_lines", 10)),
        # "none" turns Bonsai thinking off (PLAN.md Q17).
        "reasoning_effort": llm.get("reasoning_effort", "none"),
    }


def passphrase(settings: Dict[str, Any]) -> str:
    return str(settings.get("security", {}).get("passphrase", ""))
