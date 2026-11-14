"""Merge bursts of packets for the same key into one burst.

A burst ends when no packet for that key has arrived for `gap_s` seconds
(1.5 s by default). Pure logic with an injected clock, so tests can replay
captured samples without hardware.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .parser import Packet

DEFAULT_GAP_S = 1.5


@dataclass
class Burst:
    protocol: str
    bits: int
    key: str
    first_t: float
    last_t: float
    packets: int

    @property
    def duration_ms(self) -> int:
        return int(round((self.last_t - self.first_t) * 1000))


class BurstMerger:
    """`group(key)` decides which keys belong to the same burst (default: the
    key itself). The driver groups by sensor, so a doorbell that sends a
    different button nibble mid-press still makes one burst."""

    def __init__(self, gap_s: float = DEFAULT_GAP_S,
                 group: Optional[Callable[[str], str]] = None):
        self.gap_s = gap_s
        self.group = group or (lambda key: key)
        self._open: Dict[str, Burst] = {}

    def is_open(self, key: str) -> bool:
        return self.group(key) in self._open

    def add(self, packet: Packet) -> List[Burst]:
        """Add a packet. Returns bursts that ended before this packet arrived."""
        done = self.flush(packet.t)
        gid = self.group(packet.key)
        burst = self._open.get(gid)
        if burst is None:
            self._open[gid] = Burst(packet.protocol, packet.bits, packet.key,
                                    packet.t, packet.t, 1)
        else:
            burst.last_t = packet.t
            burst.packets += 1
        return done

    def flush(self, now: float, force: bool = False) -> List[Burst]:
        """Close bursts whose last packet is older than the gap (or all, if force)."""
        done = {gid: b for gid, b in self._open.items()
                if force or now - b.last_t >= self.gap_s}
        for gid in done:
            del self._open[gid]
        return sorted(done.values(), key=lambda b: b.first_t)

    @property
    def pending(self) -> int:
        return len(self._open)
