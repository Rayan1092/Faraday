"""Is this machine offline? Checked without sending a single packet.

A UDP socket "connect" only asks the operating system which route it would use;
nothing is transmitted. We ask for a route to a reserved documentation address
(192.0.2.1, and 2001:db8::1 for IPv6). These are never used on the real
internet. If the OS has no route there (no Wi-Fi, no Ethernet, no default
gateway), it refuses, and we're offline. If it picks a non-loopback local
address, some network is up, and the dashboard says so in red.

Same behaviour on macOS and Windows. Cached for a few seconds so the dashboard
can poll it every second for free.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Dict, Optional, Tuple

PROBES: Tuple[Tuple[int, str], ...] = ((socket.AF_INET, "192.0.2.1"),
                                       (socket.AF_INET6, "2001:db8::1"))


def route_address(family: int, target: str) -> Optional[str]:
    """Local address the OS would use to reach target, or None if there's no route."""
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect((target, 9))  # UDP: no packet is sent by connect()
            local = s.getsockname()[0]
    except OSError:
        return None
    if not local or local.startswith("127.") or local in ("0.0.0.0", "::", "::1"):
        return None
    return local


class NetCheck:
    def __init__(self, ttl_s: float = 3.0, probe: Callable[[int, str], Optional[str]] = route_address,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl_s, self.probe, self.clock = ttl_s, probe, clock
        self._lock = threading.Lock()
        self._cached: Optional[Dict[str, object]] = None
        self._at = -1e9

    def status(self) -> Dict[str, object]:
        """{"offline": bool, "detail": short text for the badge tooltip}."""
        with self._lock:
            if self._cached is not None and self.clock() - self._at < self.ttl_s:
                return dict(self._cached)
            routes = [addr for family, target in PROBES if (addr := self.probe(family, target))]
            if routes:
                result = {"offline": False, "detail": "A network is connected. Turn Wi-Fi off."}
            else:
                result = {"offline": True, "detail": "No route to the internet."}
            self._cached, self._at = result, self.clock()
            return dict(result)
