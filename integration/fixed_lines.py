"""Faraday's fixed spoken lines, for TTS pre-rendering at startup.

Dell's code says some lines word for word every time ("Night mode on. Sleep
well.", the alarm line, the greetings). Rendering them once at startup with
the same voice settings makes them instant and identical (owner 2026-11-14).

The lines are read from Dell's source with `ast` (read-only, nothing is
imported or run): module-level UPPERCASE string constants that read like a
sentence, and literal `_speak("...")` calls. If Dell adds
`FIXED_LINES = [...]` to any of those modules, it is picked up the same way.
Lines the scan misses are still cached after their first use.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable, List

from hardware.settings import REPO_ROOT

PACKAGES = ("brain", "modes", "rules")
SKIP_NAMES = re.compile(r"(PROMPT|REASON|NOTE|REDACTED|PATTERN|_RE|TEMPLATE|SYSTEM|KEY|URL|PATH)$")
SENTENCE = re.compile(r"^[A-Z][^{}%<>\[\]\n]{2,220}[.!?]$")


def _strings(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            yield from _strings(elt)


def lines_in(source: str) -> List[str]:
    found: List[str] = []
    tree = ast.parse(source)
    for node in tree.body:                                   # module level only
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.isupper() and (name == "FIXED_LINES" or not SKIP_NAMES.search(name)):
                found += list(_strings(node.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "_speak":
            for arg in node.args[:1]:
                found += list(_strings(arg))
    return [s for s in found if SENTENCE.match(" ".join(s.split())) and len(s.split()) >= 2]


def fixed_lines(root: Path = REPO_ROOT, packages: Iterable[str] = PACKAGES) -> List[str]:
    out: List[str] = []
    for pkg in packages:
        for path in sorted((root / pkg).glob("*.py")):
            if path.name.startswith(("bench", "test")):      # benchmark data, not spoken
                continue
            try:
                out += lines_in(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
    seen, unique = set(), []
    for line in (" ".join(s.split()) for s in out):
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return unique


if __name__ == "__main__":
    for line in fixed_lines():
        print(line)
