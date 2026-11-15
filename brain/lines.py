"""Every fixed line Faraday speaks, in one place and one tone: a calm, professional
home security assistant. Short, never funny, never stiff (owner, 2026-11-15).

Modules that speak import their lines from here (rules/ keeps the doorbell and
warning lines in rules/baseline.py, which can't import brain/; they're listed here
too). NV pre-renders them with Argmax TTS: `python -m brain.lines` prints every
line, `--json` gives {name: text}, and the literal FIXED_LINES list below is what
integration/fixed_lines.py picks up by itself.
"""
from __future__ import annotations

from typing import Dict, List

from rules.baseline import ALARM_LINE, ANNOUNCE_DOORBELL, DOORBELL_GREETING, WARNING_LINE

# ---- modes -----------------------------------------------------------------------
AWAY_ON = "Away mode is on. I'll keep watch."
NIGHT_ON = "Night mode is on. Good night."
DISARMED = "Welcome home. The system is disarmed."
ALREADY_HOME = "The system is already disarmed."

# ---- visitors --------------------------------------------------------------------
MESSAGE_SAVED = "Thank you. I'll pass that along."
# A visitor with a delivery and no owner instruction (owner, Mac test): code's reply.
DELIVERY_DEFAULT = "You can leave it by the door. I'll let the owner know it arrived."
CANT_HELP = "I'm sorry, I can't help with that."
CANT_DISARM = "I'm sorry, I can't do that. Only the owner can disarm the system."

# ---- the owner ("Hey Faraday, ...") ----------------------------------------------
SORRY = "Sorry, I didn't catch that."
REPEAT = "Sorry, I missed the end of that. Please say it again."
ARMED_NO_CHANGE = "The system is armed. I can't make changes right now."   # never hints a passphrase
RULE_SAVED = "Understood. The rule is saved."
LOOKING = "Checking the camera."
CANT_SEE = "I can't see the door right now."
CHECKING = "Checking the log."
NOTHING_HAPPENED = "Nothing happened while you were out."
INSTRUCTION_REFUSED = "I can only pass messages to visitors. I can't change lights, modes or the alarm."
NOTHING_TO_FORGET = "There's no instruction to remove."
ALL_FORGOTTEN = "Understood. I've removed all your instructions."

# ---- what the camera sees (after the description, for "who's at the door?") ------
LABEL_WORDS = {
    "delivery": "It looks like a delivery.",
    "visitor": "It looks like a visitor.",
    "suspicious": "This looks suspicious.",
    "nobody": "No one is there.",
    "unknown": "",          # "who's at the door?": the detector's own sentence says enough
}

OUTLET_NAMES = {"porch_light": "porch light", "living_room_lamp": "living room lamp",
                "spare": "spare outlet"}
AUDIENCE_NAMES = {"delivery": "delivery instruction", "visitor": "visitor instruction",
                  "anyone": "instruction for anyone"}


def delivery_line(owner_words: str) -> str:
    """The owner's delivery instruction, then the same promise as DELIVERY_DEFAULT."""
    return f"{owner_words} I'll let the owner know it arrived."


def light_line(outlet: str, on: bool) -> str:
    return f"The {OUTLET_NAMES.get(outlet, 'light')} is {'on' if on else 'off'}."


def forgot_line(audience: str) -> str:
    return f"Understood. I've removed the {AUDIENCE_NAMES.get(audience, audience + ' instruction')}."


def confront_line(description: str) -> str:
    """Code's own confrontation line when Bonsai's is unusable: what the camera sees,
    then leave. The alarm line itself was already spoken by code."""
    seen = " ".join(str(description or "").split()).rstrip(".")
    if not seen:
        return "Please leave the property now."
    seen = seen[0].lower() + seen[1:]
    if len(seen) > 150:
        seen = seen[:150].rsplit(" ", 1)[0]
    return f"I can see {seen}. Please leave now."


# Literal on purpose: integration/fixed_lines.py reads it with `ast` for pre-rendering.
FIXED_LINES = [
    "Hello. No one can come to the door right now. I can take a message or help with a delivery.",
    "Someone is at the front door.",
    "Please be aware this property is monitored and you are on camera.",
    "This property is protected. You are being recorded and the owner has been notified. Please leave now.",
    "Away mode is on. I'll keep watch.",
    "Night mode is on. Good night.",
    "Welcome home. The system is disarmed.",
    "The system is already disarmed.",
    "Thank you. I'll pass that along.",
    "You can leave it by the door. I'll let the owner know it arrived.",
    "I'm sorry, I can't help with that.",
    "I'm sorry, I can't do that. Only the owner can disarm the system.",
    "Sorry, I didn't catch that.",
    "Sorry, I missed the end of that. Please say it again.",
    "The system is armed. I can't make changes right now.",
    "Understood. The rule is saved.",
    "Checking the camera.",
    "I can't see the door right now.",
    "Checking the log.",
    "Nothing happened while you were out.",
    "I can only pass messages to visitors. I can't change lights, modes or the alarm.",
    "There's no instruction to remove.",
    "Understood. I've removed all your instructions.",
    "Understood. I've removed the delivery instruction.",
    "Understood. I've removed the visitor instruction.",
    "Understood. I've removed the instruction for anyone.",
    "The porch light is on.",
    "The porch light is off.",
    "The living room lamp is on.",
    "The living room lamp is off.",
    "The spare outlet is on.",
    "The spare outlet is off.",
]


def all_lines() -> Dict[str, str]:
    """{name: text} for every fixed line (for NV's pre-rendering and the tests)."""
    out = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, str)}
    out.update(DOORBELL_GREETING=DOORBELL_GREETING, ANNOUNCE_DOORBELL=ANNOUNCE_DOORBELL,
               WARNING_LINE=WARNING_LINE, ALARM_LINE=ALARM_LINE)
    for cat, text in LABEL_WORDS.items():
        if text:                       # "unknown" adds no words
            out[f"LABEL_{cat.upper()}"] = text
    for outlet in OUTLET_NAMES:
        for on in (True, False):
            out[f"LIGHT_{outlet.upper()}_{'ON' if on else 'OFF'}"] = light_line(outlet, on)
    for audience in AUDIENCE_NAMES:
        out[f"FORGOT_{audience.upper()}"] = forgot_line(audience)
    return out


def fixed_texts() -> List[str]:
    return list(dict.fromkeys(all_lines().values()))


if __name__ == "__main__":
    import json
    import sys
    lines = all_lines()
    if "--json" in sys.argv:
        print(json.dumps(lines, indent=1))
    else:
        for name, text in lines.items():
            print(f"{name:<28} {text}")
