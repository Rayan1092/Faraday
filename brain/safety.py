"""Spotting disarm requests, so the brain answers them in plain code.

The brain can't disarm. Mode state and the passphrase check live in modes/,
and the brain never gets a reference to them, only the current mode string.
"""
from __future__ import annotations

import re

# Someone asking to disarm. Answered by plain code with the refusal line, never via the AI.
DISARM_REQUEST = re.compile(
    r"\b(disarm\w*|unlock\w*|deactivate\w*|unarm\w*"
    r"|(turn|switch|shut) off (the )?(alarm|security|guard|system|faraday)"
    r"|stop (the )?(alarm|guard)|cancel (the )?alarm"
    r"|(go|switch|change|set)( back)?( it)? to home|home mode"
    r"|i'?m home|i am home|i'?m back|let me in|open the door)\b",
    re.IGNORECASE)


def is_disarm_request(text: str) -> bool:
    return bool(DISARM_REQUEST.search(text.replace("’", "'")))
