"""Mode state and the passphrase check: the only way to disarm.

This is plain code with no AI involved. The brain never gets a ModeState, only
the current mode as a string, so nothing the model says can reach disarm().
It lives in brain/ for Step 1 and moves to modes/ when that folder is built.

Passphrase matching (PLAN.md Open Question 8 proposal): lowercase, strip
punctuation, and the passphrase's words must appear in order in the text.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from shared import ChangedBy, Event, EventType, Mode, Source, make_event

REDACTED = "[passphrase]"
_WORD = re.compile(r"[a-z0-9']+")


def _words(text: str):
    return _WORD.findall(text.lower().replace("’", "'"))


def find_passphrase(text: str, passphrase: str) -> Optional[Tuple[int, int]]:
    """(start, end) character span of the passphrase in text, or None.

    An empty passphrase never matches, so a missing setting can't disarm.
    """
    target = _words(passphrase)
    if not target:
        return None
    spans = [(m.start(), m.end(), m.group()) for m in
             _WORD.finditer(text.lower().replace("’", "'"))]
    n = len(target)
    for i in range(len(spans) - n + 1):
        if [w for _, _, w in spans[i:i + n]] == target:
            return spans[i][0], spans[i + n - 1][1]
    return None


def redact(text: str, passphrase: str) -> Tuple[str, bool]:
    """Replace the passphrase with [passphrase]. Returns (text, matched)."""
    span = find_passphrase(text, passphrase)
    if span is None:
        return text, False
    start, end = span
    return text[:start] + REDACTED + text[end:], True


class ModeState:
    """Home / Away / Night. Only disarm_with_passphrase() can go back to home."""

    def __init__(self, passphrase: str, mode: str = Mode.HOME):
        if mode not in Mode.ALL:
            raise ValueError(f"unknown mode {mode!r}")
        self._passphrase = passphrase
        self._mode = mode

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def armed(self) -> bool:
        return self._mode in Mode.ARMED

    def arm(self, mode: str, changed_by: str, reason: str = "",
            caused_by: Optional[str] = None) -> Optional[Event]:
        """Switch to away or night. Returns mode_changed, or None if refused."""
        if mode not in Mode.ARMED or mode == self._mode:
            return None
        if changed_by in (ChangedBy.BRAIN, ChangedBy.RULE) and self.armed:
            return None  # AI and rules can arm from home but never leave away/night
        return self._change(mode, changed_by, reason, caused_by)

    def disarm_with_passphrase(self, attempt: str,
                               caused_by: Optional[str] = None) -> Optional[Event]:
        """Go to home if attempt contains the passphrase. Returns mode_changed or None."""
        if not self.armed or find_passphrase(attempt, self._passphrase) is None:
            return None
        return self._change(Mode.HOME, ChangedBy.PASSPHRASE, "Passphrase accepted.", caused_by)

    def _change(self, mode: str, changed_by: str, reason: str,
                caused_by: Optional[str]) -> Event:
        previous, self._mode = self._mode, mode
        return make_event(EventType.MODE_CHANGED,
                          {"mode": mode, "previous": previous,
                           "changed_by": changed_by, "reason": reason},
                          Source.MODES, caused_by=caused_by)


# Someone asking to disarm. The brain answers this in plain code, never via the AI.
DISARM_REQUEST = re.compile(
    r"\b(disarm\w*|unlock\w*|deactivate\w*|unarm\w*"
    r"|(turn|switch|shut) off (the )?(alarm|security|guard|system|faraday)"
    r"|stop (the )?(alarm|guard)|cancel (the )?alarm"
    r"|(go|switch|change|set)( back)?( it)? to home|home mode"
    r"|i'?m home|i am home|i'?m back|let me in|open the door)\b",
    re.IGNORECASE)


def is_disarm_request(text: str) -> bool:
    return bool(DISARM_REQUEST.search(text.replace("’", "'")))
