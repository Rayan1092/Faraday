"""Parse `subghz rx` CLI output into packets.

Real output, captured on stock firmware (see samples/doorbell_hold.log):

    Princeton 24bit
    Key:0x00DEC608
    Yek:0x0010637B
    Sn:0xDEC60 Btn:8 (4b)
    Te:203us  GT:Te*30

A packet is emitted on the Key line (protocol and bits come from the header
line before it), so protocols with different trailing lines still parse.
Echoed commands, "Listening at ...", keystore and other lines are ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_HEADER = re.compile(r"^(?P<protocol>[A-Za-z0-9][A-Za-z0-9 _.\-]*?)\s+(?P<bits>\d+)\s*bit\b")
_KEY = re.compile(r"^Key:\s*0x(?P<key>[0-9A-Fa-f]+)")
_TE = re.compile(r"^Te:\s*(?P<te>\d+)\s*us")


@dataclass
class Packet:
    protocol: str
    bits: int
    key: str           # normalised "0x" + uppercase hex, at least 8 digits
    t: float           # seconds (monotonic clock, or sample time in tests)
    te_us: Optional[int] = None


def normalise_key(hex_digits: str) -> str:
    value = int(hex_digits, 16)
    return f"0x{value:08X}"


class RxParser:
    """Feed lines one at a time; `feed` returns a Packet when one completes."""

    def __init__(self) -> None:
        self._protocol: Optional[str] = None
        self._bits: Optional[int] = None
        self._last: Optional[Packet] = None

    def feed(self, line: str, t: float) -> Optional[Packet]:
        line = line.strip()
        if not line:
            return None
        m = _HEADER.match(line)
        if m and not line.startswith(("Key:", "Yek:", "Sn:", "Te:")):
            self._protocol, self._bits = m.group("protocol").strip(), int(m.group("bits"))
            return None
        m = _KEY.match(line)
        if m and self._protocol is not None:
            packet = Packet(self._protocol, self._bits or 0, normalise_key(m.group("key")), t)
            self._protocol, self._bits = None, None
            self._last = packet
            return packet
        m = _TE.match(line)
        if m and self._last is not None and self._last.te_us is None:
            self._last.te_us = int(m.group("te"))   # info only; packet already emitted
        return None
