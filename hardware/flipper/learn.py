"""Learn mode helpers: turn a received burst into the exact settings line.

Used by `python -m hardware.flipper.manual_test --learn` (or key `l`) to pick up
a new doorbell or sensor: press it, copy the printed line into the "sensors"
section of config/settings.json, restart.
"""
from __future__ import annotations

import json
from typing import Optional

from .bursts import Burst


def config_line(name: str, burst: Burst) -> str:
    """'"doorbell": {"protocol": "Princeton", "bits": 24, "key": "0x00ABCDE1"},'"""
    body = json.dumps({"protocol": burst.protocol, "bits": burst.bits, "key": burst.key})
    return f'"{name}": {body},'


def report(burst: Burst, known_as: Optional[str], name: str = "doorbell") -> str:
    """What learn mode prints for one burst."""
    head = (f"{burst.protocol} {burst.bits}bit key {burst.key}  "
            f"({burst.packets} packets, {burst.duration_ms} ms)")
    if known_as:
        return f"known   {head}  -> already configured as '{known_as}'"
    return (f"NEW KEY {head}\n"
            f"        paste into \"sensors\" in config/settings.json (replace the old entry):\n"
            f"        {config_line(name, burst)}")
