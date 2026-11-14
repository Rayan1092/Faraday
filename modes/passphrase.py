"""Passphrase matching and redaction. Plain code, no AI.

Speech-to-text output varies, so both the passphrase and the heard text are
reduced to the same canonical words before comparing:
- case and punctuation are ignored ("Silver, otter... LANTERN!" matches),
- hyphens split words ("twenty-one" -> "twenty one"),
- digits become words ("17" -> "seventeen", "1,250" -> "one thousand two hundred fifty"),
- "and" inside a spoken number is dropped ("one hundred and five" == "105").
The passphrase words must appear in order, next to each other.
An empty passphrase never matches, so a missing setting can't disarm.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

REDACTED = "[passphrase]"

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_NUMBER_WORDS = set(_ONES) | set(_TENS[2:]) | {"hundred", "thousand"}
_TOKEN = re.compile(r"\d+(?:,\d{3})*|[a-z]+(?:'[a-z]+)*")

Word = Tuple[str, int, int]  # (canonical word, start, end) in the original text


def number_words(digits: str) -> List[str]:
    """'17' -> ['seventeen'], '105' -> ['one', 'hundred', 'five']. Odd shapes go digit by digit."""
    if len(digits) > 1 and digits.startswith("0") or len(digits) > 6:
        return [_ONES[int(d)] for d in digits]
    return _say(int(digits))


def _say(n: int) -> List[str]:
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10]] + (_say(n % 10) if n % 10 else [])
    if n < 1000:
        return _say(n // 100) + ["hundred"] + (_say(n % 100) if n % 100 else [])
    return _say(n // 1000) + ["thousand"] + (_say(n % 1000) if n % 1000 else [])


def canonical_words(text: str) -> List[Word]:
    lowered = text.lower().replace("’", "'").replace("-", " ")
    words: List[Word] = []
    for m in _TOKEN.finditer(lowered):
        token, start, end = m.group(), m.start(), m.end()
        if token[0].isdigit():
            words += [(w, start, end) for w in number_words(token.replace(",", ""))]
        else:
            words.append((token.replace("'", ""), start, end))
    # "one hundred and five": drop "and" between number words.
    return [w for i, w in enumerate(words)
            if not (w[0] == "and" and 0 < i < len(words) - 1
                    and words[i - 1][0] in ("hundred", "thousand")
                    and words[i + 1][0] in _NUMBER_WORDS)]


def find_all(text: str, passphrase: str) -> List[Tuple[int, int]]:
    """Non-overlapping (start, end) character spans of the passphrase in text."""
    target = [w for w, _, _ in canonical_words(passphrase)]
    if not target:
        return []
    words = canonical_words(text)
    n, i, spans = len(target), 0, []
    while i <= len(words) - n:
        if [w for w, _, _ in words[i:i + n]] == target:
            start, end = words[i][1], words[i + n - 1][2]
            if spans and start < spans[-1][1]:  # two matches inside one number like "100100"
                spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
            else:
                spans.append((start, end))
            i += n
        else:
            i += 1
    return spans


def find_passphrase(text: str, passphrase: str) -> Optional[Tuple[int, int]]:
    """(start, end) character span of the first match, or None."""
    spans = find_all(text, passphrase)
    return spans[0] if spans else None


def redact(text: str, passphrase: str) -> Tuple[str, bool]:
    """Replace every occurrence of the passphrase with [passphrase]. Returns (text, matched)."""
    spans = find_all(text, passphrase)
    for start, end in reversed(spans):
        text = text[:start] + REDACTED + text[end:]
    return text, bool(spans)
