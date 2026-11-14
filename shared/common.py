"""Small pieces every module shares: timestamps, ids, modes, sources, outlets.

Stdlib only, Python 3.9+, works the same on macOS and Windows.
Changes here need agreement from both laptops (see docs/PLAN.md).
"""
from __future__ import annotations

import uuid
from datetime import datetime


def now_iso() -> str:
    """Local time, ISO 8601, millisecond precision, with UTC offset.

    Example: "2026-11-14T16:03:12.481-05:00"
    """
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    """Short unique id, e.g. "evt_3f9a1c2e" or "act_b71d04aa"."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Mode:
    HOME = "home"    # disarmed. Flipper LED green
    AWAY = "away"    # armed, nobody home. LED blue
    NIGHT = "night"  # armed, people asleep. LED: see Open Questions in PLAN.md
    ALL = frozenset({HOME, AWAY, NIGHT})
    # Modes the AI (and rules) may switch to. HOME is missing on purpose:
    # going to HOME is disarming, and only the passphrase code in modes/ can do it.
    ARMED = frozenset({AWAY, NIGHT})


class Source:
    """Who produced an event or action. Never branch on source for logic,
    except the disarm check: sim events must behave exactly like real ones."""
    FLIPPER = "flipper"      # hardware/flipper
    SPEECH = "speech"        # hardware/speech (STT client)
    CAMERA = "camera"        # hardware/camera
    VISION = "vision"        # brain/ (Bonsai vision description)
    BRAIN = "brain"          # brain/ (LLM decisions)
    MODES = "modes"          # modes/ (state machine + passphrase)
    RULES = "rules"          # rules/ (user rules firing)
    DASHBOARD = "dashboard"  # dashboard/ (buttons on the web page)
    SIM = "sim"              # sim/ (fake events for development)
    SYSTEM = "system"        # integration/ (startup, shutdown, errors)
    ALL = frozenset({FLIPPER, SPEECH, CAMERA, VISION, BRAIN, MODES,
                     RULES, DASHBOARD, SIM, SYSTEM})


class Outlet:
    """Names the AI uses. config/settings.json maps each to its .sub files."""
    PORCH_LIGHT = "porch_light"            # outlet 1
    LIVING_ROOM_LAMP = "living_room_lamp"  # outlet 2
    SPARE = "spare"                        # outlet 3
    ALL = frozenset({PORCH_LIGHT, LIVING_ROOM_LAMP, SPARE})
