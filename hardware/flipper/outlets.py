"""Outlet files on the Flipper: read them, compare on vs off, judge a guided test.

    python3 -m hardware.check outlets        (Faraday stopped: it holds the Flipper)

Owner 2026-11-15 (Mac test): the startup "off" turned the living room lamp ON,
and every dashboard press flipped it the other way. Either outlet2_on and
outlet2_off hold the same signal (the outlet then toggles on every send), or
they are swapped, or the outlet itself is paired as a toggle. The six .sub
files are read over the USB CLI (`storage read`), compared per outlet, and a
guided test (off twice, on twice, off once more) tells which it is.

Pure functions here (tested on Windows); the CLI glue is hardware/check.py.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

SUBGHZ_DIR = "/ext/subghz"


def parse_sub(lines: Sequence[str]) -> Dict[str, Any]:
    """The fields of a Flipper .sub file (Key File or RAW), from `storage read`
    output ("Size: N" first) or the file's own lines."""
    info: Dict[str, Any] = {"raw": []}
    for line in lines:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "RAW_Data":
            info["raw"] += [int(x) for x in value.split() if x.lstrip("-").isdigit()]
        elif key in ("Filetype", "Frequency", "Preset", "Protocol", "Bit", "Key", "TE"):
            info[key.lower()] = value
        elif key == "Storage error":
            info["error"] = value
    return info


def raw_code(durations: Sequence[int]) -> Optional[str]:
    """The frame a RAW recording repeats, as bits, to compare two recordings.
    OOK pulse-width codes (Princeton/PT2262, EV1527): each bit is a high pulse
    then a low one, the longer of the two gives 1 or 0, and frames end at a long
    low gap. Noise around the presses doesn't repeat, so the most common frame
    is the code. None if no frame of 8+ bits repeats."""
    pulses = [int(d) for d in durations if d]
    if len(pulses) < 16:
        return None
    short = sorted(abs(d) for d in pulses)[len(pulses) // 4] or 1     # a short pulse
    frames: List[str] = []
    bits: List[str] = []
    i = 0
    while i < len(pulses) - 1:
        high, low = pulses[i], pulses[i + 1]
        if high <= 0 or low >= 0:                 # out of step: move on one pulse
            i += 1
            continue
        if -low > 8 * short:                      # the gap: the frame ends (sync dropped)
            if len(bits) >= 8:
                frames.append("".join(bits))
            bits = []
        else:
            bits.append("1" if high > -low else "0")
        i += 2
    if len(bits) >= 8:
        frames.append("".join(bits))
    if not frames:
        return None
    code, count = Counter(frames).most_common(1)[0]
    return code if count >= 2 else None


def signal_of(info: Dict[str, Any]) -> Optional[str]:
    """What a .sub file sends, comparable between files: "Princeton 24bit
    0x95D5D4", or "RAW <bits>" for a recording. None if it can't be told."""
    proto = info.get("protocol")
    if not proto:
        return None
    if proto == "RAW":
        code = raw_code(info.get("raw", []))
        return f"RAW {len(code)} bits {int(code, 2):#x}" if code else None
    key = (info.get("key") or "").replace(" ", "").upper().lstrip("0") or "0"
    return f"{proto} {info.get('bit', '?')}bit 0x{key}"


def compare(on: Dict[str, Any], off: Dict[str, Any]) -> Tuple[str, str]:
    """(kind, text) for one outlet's two files. kind: "same", "different",
    "missing", "unknown" (two RAW recordings that couldn't be decoded)."""
    if on.get("error") or not on.get("protocol"):
        return "missing", "the ON file is missing or unreadable"
    if off.get("error") or not off.get("protocol"):
        return "missing", "the OFF file is missing or unreadable"
    a, b = signal_of(on), signal_of(off)
    if a is None or b is None:
        return "unknown", "can't decode the recording; the guided test tells"
    if a == b:
        return "same", "SAME SIGNAL: the outlet can't tell on from off, so every send flips it"
    return "different", "different signals (good)"


# The guided test: these sends, in order; after each the owner says if the lamp is on.
STEPS = ("off", "off", "on", "on", "off")


def verdict(before: bool, after: Sequence[bool]) -> Tuple[str, str]:
    """(kind, text) from the lamp's state before the test and after each of
    STEPS. kind: works, toggles, swapped, deaf, mixed, incomplete."""
    if len(after) < len(STEPS):
        return "incomplete", "the test was stopped before the end"
    states = [before] + list(after)
    changed = [states[i + 1] != states[i] for i in range(len(STEPS))]
    offs = [after[i] for i, s in enumerate(STEPS) if s == "off"]
    ons = [after[i] for i, s in enumerate(STEPS) if s == "on"]
    if not any(changed):
        return "deaf", ("the lamp never reacted: the outlet doesn't hear these files (unplugged, "
                        "too far from the Flipper, or the files hold another code)")
    if not any(offs) and all(ons):
        return "works", "OFF turns it off and ON turns it on, every time, also when repeated"
    if all(changed):
        return "toggles", "every send flips the lamp, ON and OFF alike (a repeat flips it back)"
    if all(offs) and not any(ons):
        return "swapped", "OFF turns it on and ON turns it off: the two files are swapped"
    if changed[1] or changed[3]:
        which = " and ".join(n for n, c in (("OFF", changed[1]), ("ON", changed[3])) if c)
        return "toggles", f"sending {which} twice flips the lamp back: it toggles"
    return "mixed", ("OFF left it " + ", ".join("on" if s else "off" for s in offs)
                     + "; ON left it " + ", ".join("on" if s else "off" for s in ons))


def rerecord_steps(number: int, on_name: str, off_name: str) -> List[str]:
    """Stock firmware, checked against flipperzero-firmware (subghz scenes and the
    name validator): a name that exists is refused ("This name exists!"), so the
    old files are renamed first."""
    return [
        f"Re-record outlet {number} with its keyfob (stock Flipper firmware, about 3 minutes):",
        "  1. Quit Faraday (q): it holds the Flipper's radio.",
        f"  2. On the Flipper: Sub-GHz > Saved > {on_name} > Rename, type {on_name}_old, Save.",
        f"     Same for {off_name} > Rename > {off_name}_old. (A new file can't take a name that",
        '     exists: the Flipper says "This name exists!")',
        "  3. Sub-GHz > Read. Press LEFT (Config) once to check: Frequency 433.92, Hopping OFF,",
        "     Modulation AM650 (the defaults). BACK to the scanning screen.",
        f"  4. Hold the keyfob next to the Flipper and press outlet {number}'s ON button for about a second.",
        "     A line appears (for example \"Princeton 24bit\").",
        f"  5. Highlight it, press OK, then RIGHT (Save). Clear the suggested name, type {on_name},",
        "     press Save. BACK to the list.",
        f"  6. Press outlet {number}'s OFF button. A NEW line must appear: highlight the new one, OK,",
        f"     RIGHT (Save), name it {off_name}, Save.",
        "     If no new line appears (only the first line's count goes up), the keyfob sends the",
        "     same code for ON and OFF: that outlet is a toggle and can't be told on from off.",
        "  7. Run python3 -m hardware.check outlets again: this outlet should say \"different\",",
        "     and the guided test \"works\".",
    ]


def swap_steps(on_name: str, off_name: str) -> List[str]:
    return [
        "Swap the two files on the Flipper (Faraday stopped):",
        f"  Sub-GHz > Saved > {on_name} > Rename > {on_name}_tmp, Save;",
        f"  {off_name} > Rename > {on_name}, Save;  {on_name}_tmp > Rename > {off_name}, Save.",
        "  Then run python3 -m hardware.check outlets again.",
    ]
