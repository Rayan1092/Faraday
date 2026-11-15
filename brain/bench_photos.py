"""Photo bench for the honest threat label: real photos, on the Mac.

    python3 -m brain.bench_photos captures/bench        # every .jpg/.png in the folder

Name each photo <expected>-<cause>-<n>.jpg:
- expected: nobody, delivery, visitor or suspicious
- cause: doorbell (they rang) or motion (they didn't)

The four from the owner's list:
    nobody-motion-1.jpg          empty doorway
    delivery-doorbell-1.jpg      person with a box who rang
    visitor-doorbell-1.jpg       plain visitor who rang
    suspicious-motion-1.jpg      hood up, face covered, didn't ring

Each photo goes through what the app does with a real photo: NV's object detector
(people, faces, detections), the vision model's description (hardware/vision.py),
then the label (brain/threat.py, the small Bonsai). The mode is Away. Writes
docs/bench/<date>-photos.md with the accuracy and every answer.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from shared import EventType, Mode, ThreatCategory

from . import threat
from .settings import REPO_ROOT, load_settings

EXPECTED = ThreatCategory.ALL - {ThreatCategory.UNKNOWN}
CAUSES = (EventType.DOORBELL, EventType.MOTION)
IMAGE_TYPES = (".jpg", ".jpeg", ".png")


def parse_name(path: Path) -> Optional[Tuple[str, str]]:
    """(expected label, cause) from "<expected>-<cause>-<n>.jpg", or None."""
    parts = path.stem.lower().split("-")
    if len(parts) >= 2 and parts[0] in EXPECTED and parts[1] in CAUSES:
        return parts[0], parts[1]
    return None


WHEN = datetime(2026, 11, 15, 15, 0)   # a fixed afternoon, so the bench doesn't depend on the clock


def run(photos: Sequence[Path], analyse: Callable[[Path], Optional[Dict[str, Any]]],
        describe: Callable[..., Optional[str]], assessor: Any,
        from_detector: Callable[[Dict[str, Any]], str] = lambda info: "",
        mode: str = Mode.AWAY, hint: Optional[Callable[[Dict[str, Any]], str]] = None) -> List[Dict[str, Any]]:
    """One row per photo. analyse: NV's detector summary or None; describe: the vision
    model's sentence or None (failed); from_detector: NV's description from detections."""
    rows = []
    for path in photos:
        named = parse_name(path)
        if named is None:
            continue
        expected, cause = named
        started = time.perf_counter()
        info = analyse(path)
        # as hardware/camera.py does: the detector's counts as a hint, only when it saw something
        line = hint(info) if (hint is not None and info and info.get("detections")) else None
        text = describe(path, line) if line else describe(path)
        vision_ok = bool(text)
        description = text or (from_detector(info) if info else "") or "Photo saved."
        ctx = threat.Context(description, mode, cause=cause, doorbells=1 if cause == EventType.DOORBELL else 0,
                             motions=1, when=WHEN, people=None if info is None else int(info.get("people") or 0),
                             faces=None if info is None or "faces" not in info else int(info["faces"]),
                             detections=None if info is None else list(info.get("detections") or []),
                             vision_ok=vision_ok)
        a = assessor.assess(ctx)
        rows.append({"photo": path.name, "expected": expected, "cause": cause, "label": a.category,
                     "right": a.category == expected, "reason": a.reason, "via": a.via, "note": a.note,
                     "people": ctx.people, "faces": ctx.faces, "description": description,
                     "vision_ok": vision_ok, "seconds": round(time.perf_counter() - started, 1)})
    return rows


def to_markdown(rows: List[Dict[str, Any]], meta: Dict[str, str]) -> str:
    right = sum(r["right"] for r in rows)
    lines = [f"# Photo bench: honest threat label ({meta.get('model', '?')})", "",
             f"- Date: {meta.get('date', '')}", f"- Photos: {len(rows)}, right: **{right}/{len(rows)}**"
             + (f" ({100 * right // len(rows)}%)" if rows else ""),
             f"- Command: `{meta.get('command', '')}`", "",
             "| Photo | Expected | Label | Reason | People | Faces | Vision | Time |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['photo']} | {r['expected']} | {'✅' if r['right'] else '❌'} {r['label']} "
                     f"| {r['reason']} | {r['people']} | {r['faces']} | "
                     f"{'ok' if r['vision_ok'] else 'failed'} | {r['seconds']} s |")
    lines += ["", "Descriptions:", ""] + [f"- {r['photo']}: {r['description']}" for r in rows] + [""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m brain.bench_photos", description=__doc__.split("\n")[0])
    parser.add_argument("folder", help="folder of <expected>-<cause>-<n>.jpg photos")
    parser.add_argument("--out", default="docs/bench")
    args = parser.parse_args(argv)
    folder = Path(args.folder)
    photos = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_TYPES) if folder.is_dir() else []
    if not any(parse_name(p) for p in photos):
        print(f"No photos named <expected>-<cause>-<n>.jpg in {folder} (see python -m brain.bench_photos -h).")
        return 2
    try:   # NV's camera side: needs OpenCV and the detector models (the Mac has them)
        import cv2
        from hardware.detect import Detector, describe_from, hint
        from hardware.vision import Vision
    except Exception as e:
        print(f"Needs OpenCV and NV's detector (hardware/detect.py): {e!r}. Run it on the Mac.")
        return 2
    settings = load_settings()
    detector = Detector.from_settings(settings)
    vision = Vision.from_settings(settings)
    assessor = threat.ThreatAssessor.from_settings(settings, log=lambda _m: None)

    def analyse(path: Path) -> Optional[Dict[str, Any]]:
        frame = cv2.imread(str(path))
        return detector.analyse(frame) if (detector is not None and frame is not None) else None

    rows = run(photos, analyse, lambda p, h=None: vision.describe(p, hint=h), assessor, describe_from,
               hint=hint)
    meta = {"model": str(assessor.model), "date": datetime.now().astimezone().isoformat(timespec="minutes"),
            "command": "python -m brain.bench_photos " + " ".join(argv or sys.argv[1:])}
    md = to_markdown(rows, meta)
    print(md)
    out = Path(args.out)
    out = out if out.is_absolute() else REPO_ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{datetime.now():%Y-%m-%d-%H%M}-photos.md"
    path.write_text(md, encoding="utf-8")
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
