"""Quick look at an event log after a test run (for Join 1 and stage checks).

    python -m log.check logs/join1-events.jsonl
    python -m log.check logs/join1-events.jsonl --last 30

Prints:
- counts per type and source (e.g. doorbell from flipper: 3),
- likely duplicates (same sensor event less than 3 s apart),
- whether the passphrase from settings shows up anywhere (it must not),
- the last N entries, one line each.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from modes.passphrase import find_passphrase

REPO_ROOT = Path(__file__).resolve().parents[1]
DUPLICATE_S = 3.0


def load(path: Path) -> List[Dict[str, Any]]:
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _when(entry: Dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(entry.get("timestamp", "")))
    except ValueError:
        return None


def duplicates(entries: List[Dict[str, Any]]) -> List[str]:
    """Sensor events of the same type and source closer than DUPLICATE_S."""
    out, last = [], {}
    for e in entries:
        if e.get("type") not in ("doorbell", "motion"):
            continue
        key, t = (e["type"], e.get("source")), _when(e)
        prev = last.get(key)
        if prev is not None and t is not None and (t - prev).total_seconds() < DUPLICATE_S:
            out.append(f"{e['type']} from {e.get('source')} at {str(e.get('timestamp'))[11:19]} "
                       f"({(t - prev).total_seconds():.1f} s after the previous one)")
        last[key] = t
    return out


def passphrase_leaks(entries: List[Dict[str, Any]], secret: str) -> int:
    return sum(1 for e in entries if find_passphrase(json.dumps(e, ensure_ascii=False), secret))


def one_line(e: Dict[str, Any]) -> str:
    info = e.get("data") if e.get("kind") == "event" else e.get("args")
    detail = json.dumps(info, ensure_ascii=False) if info else ""
    if len(detail) > 80:
        detail = detail[:77] + "..."
    return f"{str(e.get('timestamp', ''))[11:19]} {e.get('kind', '?'):<6} {e.get('type', '?'):<15} " \
           f"{e.get('source', '?'):<9} {detail}"


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    parser = argparse.ArgumentParser(prog="python -m log.check", description=__doc__.split("\n")[0])
    parser.add_argument("file", nargs="?", default="logs/events.jsonl")
    parser.add_argument("--last", type=int, default=15)
    args = parser.parse_args(argv)
    path = Path(args.file)
    path = path if path.is_absolute() else REPO_ROOT / path
    if not path.exists():
        print(f"No log at {path}")
        return 1
    entries = load(path)
    print(f"{path.name}: {len(entries)} entries")

    counts = Counter((e.get("type"), e.get("source")) for e in entries)
    print("\nCounts (type, source):")
    for (typ, src), n in sorted(counts.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        print(f"  {typ:<16} {src:<10} {n}")

    dups = duplicates(entries)
    print(f"\nLikely duplicates (< {DUPLICATE_S:.0f} s apart): {len(dups)}")
    for d in dups:
        print(f"  {d}")

    try:
        from brain.settings import load_settings, passphrase
        secret = passphrase(load_settings())
    except Exception:
        secret = ""
    if secret:
        leaks = passphrase_leaks(entries, secret)
        print(f"\nPassphrase found in the log: {leaks} " + ("(OK)" if leaks == 0 else "(PROBLEM: must be 0)"))
    else:
        print("\nPassphrase check skipped (no passphrase in settings)")

    print(f"\nLast {min(args.last, len(entries))}:")
    for e in entries[-args.last:]:
        print("  " + one_line(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
