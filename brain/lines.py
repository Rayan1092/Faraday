"""Every fixed line Faraday speaks, in one place and one tone: a calm, professional
home security assistant. Short, never funny, never stiff.

NV pre-renders these with Argmax TTS: `python -m brain.lines` prints them, and
`python -m brain.lines --json` gives {name: text}.
"""
from __future__ import annotations

# ---- modes ---------------------------------------------------------------------
AWAY_ON = "Away mode is on. I'll keep watch."
NIGHT_ON = "Night mode is on. Good night."
RULE_SAVED = "Understood. The rule is saved."

# ---- owner requests ("Hey Faraday, ...") -------------------------------------
SORRY = "Sorry, I didn't catch that."
REPEAT = "Sorry, I missed the end of that. Please say it again."
ARMED_NO_CHANGE = "The system is armed. Say the passphrase to make changes."
SAY_PASSPHRASE = "Please say the passphrase to disarm."
ALREADY_HOME = "The system is already disarmed."
OUTLET_NAMES = {"porch_light": "porch light", "living_room_lamp": "living room lamp",
                "spare": "spare outlet"}


def light_line(outlet: str, on: bool) -> str:
    return f"The {OUTLET_NAMES.get(outlet, 'light')} is {'on' if on else 'off'}."


def all_lines() -> dict:
    """{name: text} for every fixed line (for NV's pre-rendering)."""
    out = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, str)}
    for outlet in OUTLET_NAMES:
        for on in (True, False):
            out[f"LIGHT_{outlet.upper()}_{'ON' if on else 'OFF'}"] = light_line(outlet, on)
    return out


if __name__ == "__main__":
    import json
    import sys
    lines = all_lines()
    if "--json" in sys.argv:
        print(json.dumps(lines, indent=1))
    else:
        for name, text in lines.items():
            print(f"{name:<28} {text}")
