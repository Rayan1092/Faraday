"""--fresh: start with an empty log and no old captures.

Moves logs/ (events.jsonl, rules.json, instructions.json, ...) and captures/
(photos, audio, alarm recordings) into old_runs/<time>/, so a demo starts clean
and nothing is deleted. old_runs/ is gitignored. The TTS cache (cache/) and
the models stay where they are.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from hardware.settings import REPO_ROOT


def archive_run(settings: Dict[str, Any], root: Path = REPO_ROOT,
                now: Optional[datetime] = None) -> Optional[Path]:
    """Move the run's data folders to old_runs/<YYYYmmdd-HHMMSS>/. Returns that
    folder, or None if there was nothing to move. Empty folders are recreated."""
    paths = settings.get("paths", {})
    names = [paths.get("logs", "logs"), paths.get("captures", "captures")]
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    dest = root / "old_runs" / stamp
    n = 1
    while dest.exists():                      # two --fresh starts in the same second
        n += 1
        dest = root / "old_runs" / f"{stamp}-{n}"
    moved = False
    for name in names:
        src = (root / name)
        if not src.is_dir() or not any(src.iterdir()):
            continue
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest / Path(name).name))
        src.mkdir(parents=True, exist_ok=True)
        moved = True
    return dest if moved else None
