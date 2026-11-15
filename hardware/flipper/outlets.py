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


GAP_US = 4000                 # a low this long ends a frame (PT2262/EV1527 sync: ~31 T, 9-12 ms)
PULSE_US = (80, 3000)         # a data pulse is within this; noise usually isn't
MIN_BITS, MIN_REPEATS = 20, 3


def raw_code(durations: Sequence[int]) -> Optional[str]:
    """The frame a RAW recording repeats, as bits, to compare two recordings.
    OOK pulse-width codes (Princeton/PT2262, EV1527): each bit is a high pulse
    then a low one, the longer of the two gives 1 or 0, and frames end at a long
    low gap (the sync). A frame with a pulse out of range or out of step is
    noise. The code is the most common clean frame of MIN_BITS+ bits, seen
    MIN_REPEATS+ times; None otherwise (the guided test decides then)."""
    vals = [int(d) for d in durations if d]
    frames: List[str] = []
    bits: List[str] = []
    clean = True
    i = 0
    while i < len(vals):
        v = vals[i]
        nxt = vals[i + 1] if i + 1 < len(vals) else 0
        if v < 0 and -v > GAP_US:                 # the gap: the frame ends
            if clean and len(bits) >= MIN_BITS:
                frames.append("".join(bits))
            bits, clean = [], True
            i += 1
        elif v > 0 and nxt < 0 and -nxt <= GAP_US:
            low = -nxt
            if not (PULSE_US[0] <= v <= PULSE_US[1] and PULSE_US[0] <= low <= PULSE_US[1]):
                clean = False
            bits.append("1" if v > low else "0")
            i += 2
        elif v > 0 and nxt < 0:                   # the sync's high pulse, right before the gap
            i += 1
        else:                                     # two of a kind in a row: out of step
            clean = False
            i += 1
    if clean and len(bits) >= MIN_BITS:
        frames.append("".join(bits))
    if not frames:
        return None
    code, count = Counter(frames).most_common(1)[0]
    return code if count >= MIN_REPEATS else None


def signal_key(info: Dict[str, Any]) -> Optional[Tuple[str, int, int]]:
    """(format, bits, value) to compare two files of the same kind, or None:
    "key" for decoded files with a Key line, "raw" for a decoded recording."""
    proto = info.get("protocol")
    if proto == "RAW":
        code = raw_code(info.get("raw", []))
        return ("raw", len(code), int(code, 2)) if code else None
    key = (info.get("key") or "").replace(" ", "")
    if not proto or not key:
        return None                          # e.g. BinRAW: no Key line to compare
    try:
        return "key", int(str(info.get("bit", "0")).strip() or 0), int(key, 16)
    except ValueError:
        return None


def signal_of(info: Dict[str, Any]) -> Optional[str]:
    """What a .sub file sends, for the table: "Princeton 24bit 0x95D5D4", or
    "RAW 24 bits 0x..." for a recording. None if it can't be told."""
    sig = signal_key(info)
    if sig is None:
        return None
    kind, bits, value = sig
    return f"RAW {bits} bits {value:#x}" if kind == "raw" else f"{info.get('protocol')} {bits}bit 0x{value:X}"


def compare(on: Dict[str, Any], off: Dict[str, Any]) -> Tuple[str, str]:
    """(kind, text) for one outlet's two files. kind: "same", "different",
    "missing", "unknown" (can't decode, or one is a recording and one a key)."""
    if on.get("error") or not on.get("protocol"):
        return "missing", "the ON file is missing or unreadable"
    if off.get("error") or not off.get("protocol"):
        return "missing", "the OFF file is missing or unreadable"
    a, b = signal_key(on), signal_key(off)
    if a is None or b is None or a[0] != b[0]:
        return "unknown", "can't compare these two files; the guided test tells"
    if a == b:
        return "same", "SAME SIGNAL: the outlet can't tell on from off, so every send flips it"
    return "different", "different signals (good)"


# The guided test: these sends, in order; after each the owner says if the lamp is on.
STEPS = ("off", "off", "on", "on", "off")


def verdict(before: bool, after: Sequence[bool]) -> Tuple[str, str]:
    """(kind, text) from the lamp's state before the test and after each of
    STEPS. kind: works, missed, toggles, swapped, deaf, mixed, incomplete."""
    if len(after) < len(STEPS):
        return "incomplete", "the test was stopped before the end"
    states = [before] + list(after)
    wanted = [s == "on" for s in STEPS]
    changed = [states[i + 1] != states[i] for i in range(len(STEPS))]
    # a send that moved the lamp the other way than it says: never a missed send
    wrong_way = [changed[i] and after[i] != wanted[i] for i in range(len(STEPS))]
    offs = [after[i] for i, s in enumerate(STEPS) if s == "off"]
    ons = [after[i] for i, s in enumerate(STEPS) if s == "on"]
    if not any(changed):
        return "deaf", ("the lamp never reacted: the outlet doesn't hear these files (unplugged, "
                        "too far from the Flipper, or the files hold another code)")
    if not any(offs) and all(ons):
        return "works", "OFF turns it off and ON turns it on, every time, also when repeated"
    if not any(wrong_way):
        missed = [f"{STEPS[i].upper()} (send {i + 1})" for i in range(len(STEPS)) if after[i] != wanted[i]]
        return "missed", ("it always switched the right way, but " + ", ".join(missed)
                          + " didn't reach it")
    if all(changed):
        return "toggles", "every send flips the lamp, ON and OFF alike (a repeat flips it back)"
    if all(offs) and not any(ons):
        return "swapped", "OFF turns it on and ON turns it off: the two files are swapped"
    flips = [name for name, a, b in (("OFF", 0, 1), ("ON", 2, 3))
             if after[a] == wanted[a] and wrong_way[b]]       # right first, a repeat undid it
    if flips:
        return "toggles", f"sending {' and '.join(flips)} twice flips the lamp back: it toggles"
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
