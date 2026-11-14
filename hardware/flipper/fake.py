"""Fake Flipper for development without hardware.

Only the USB serial port is faked: FakeDevice emulates the stock CLI (prompt,
echo, `subghz rx` output, `led`, `vibro`, `subghz tx_from_file`) as observed on
the real device (see samples/). Everything above the port (the driver's queue,
rx pause/resume, parser, bursts, LED logic) is the real code, so tests with the
fake exercise the same paths as the real Flipper.

    fl = FakeFlipper(settings, on_event=...)   # events have source "sim"
    fl.start()
    fl.device.press_doorbell()                 # or .wave(), .unplug(), .plug()
    fl.device.rgb, fl.device.tx, fl.device.vibro_count

Radio arriving while rx is paused (during a send or an LED change) is lost,
exactly like on the real device.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import serial

from shared import Event, Source

from .connection import FlipperSerial
from .device import Flipper

DOORBELL_KEY = "0x00DEC608"
MOTION_KEY = "0x00C5AFAE"
OUTLET_FILES = ("outlet1_on", "outlet1_off", "outlet2_on", "outlet2_off",
                "outlet3_on", "outlet3_off")
PROMPT = b"\r\n>: "


class FakeDevice:
    """State of the pretend Flipper. Survives fake unplug/replug."""

    def __init__(self, files=OUTLET_FILES, charge_poll_s: float = 1.0, tx_time_s: float = 0.0):
        self.files = {f.lower() for f in files}
        # Charge indicator, as seen on the real Flipper (battery full, on USB): it
        # owns the LED's red and green. Listening pauses charging, and the power
        # service, polling every charge_poll_s, rewrites red/green on each change:
        # charged = green on, not charging = both off. 0 turns the emulation off.
        self.charge_poll_s = charge_poll_s
        # Tests set this so their own few-millisecond LED pauses are never noticed
        # (on the real device a ~0.35 s LED pause is noticed only some of the time).
        self.charge_min_off_s = 0.0
        self._rx_off_at = time.monotonic()
        self.tx_time_s = tx_time_s            # how long a tx_from_file takes (real: ~0.9 s)
        self.charge_state = "charged"
        self.charge_writes = 0
        self._next_poll = time.monotonic() + charge_poll_s
        self.rx = False
        self.rgb: List[int] = [0, 0, 0]
        self.vibro = False
        self.vibro_count = 0
        self.tx: List[str] = []           # file names sent, in order
        # The real outlets toggle on every code they receive (a second outlet1_on
        # turned the porch light OFF in Join 1), so the fake models that too.
        self.outlet_lit: Dict[int, bool] = {1: False, 2: False, 3: False}
        self.commands: List[str] = []     # every command line received
        self.packets_sent = 0             # radio packets the Flipper actually "heard"
        self.plugged = True
        self._out: List[Tuple[float, bytes, bool]] = []   # (due, data, is_radio)
        self._line = b""
        self._lock = threading.Lock()

    # ---- radio (what a remote would do) ------------------------------------
    def press_doorbell(self, key: str = DOORBELL_KEY) -> None:
        """One held press: the real doorbell decodes as a single packet."""
        self._radio([key], gap_s=0.0, te=203, btn=key[-1])

    def wave(self, packets: int = 17, gap_s: float = 0.122, key: str = MOTION_KEY) -> None:
        """One motion trigger: ~17 packets 122 ms apart, like the real sensor."""
        self._radio([key] * packets, gap_s=gap_s, te=477, btn=key[-1])

    def _radio(self, keys: List[str], gap_s: float, te: int, btn: str) -> None:
        now = time.monotonic()
        with self._lock:
            for i, key in enumerate(keys):
                block = (f"Princeton 24bit\r\nKey:{key}\r\nYek:0x00000000\r\n"
                         f"Sn:0x{key[4:9]} Btn:{btn} (4b)\r\nTe:{te}us  GT:Te*30\r\n").encode()
                self._out.append((now + i * gap_s, block, True))

    # ---- cable ---------------------------------------------------------------
    def unplug(self) -> None:
        self.plugged = False
        self.rx = False
        self._rx_off_at = time.monotonic()

    def plug(self) -> None:
        self.plugged = True

    # ---- serial side -----------------------------------------------------------
    def _say(self, data: bytes, delay_s: float = 0.0) -> None:
        self._out.append((time.monotonic() + delay_s, data, False))

    def _power_tick(self) -> None:
        now = time.monotonic()
        if self.charge_poll_s <= 0 or now < self._next_poll:
            return
        while self._next_poll <= now:
            self._next_poll += self.charge_poll_s
        resumed = not self.rx and now - self._rx_off_at >= self.charge_min_off_s
        state = "charged" if resumed else "not_charging"
        if not self.rx and not resumed:
            state = self.charge_state           # too short to tell: no change yet
        if state != self.charge_state:
            self.charge_state = state
            self.rgb[0], self.rgb[1] = 0, (255 if state == "charged" else 0)
            self.charge_writes += 1

    def take_due(self) -> bytes:
        self._power_tick()
        now = time.monotonic()
        with self._lock:
            due = [o for o in self._out if o[0] <= now]
            self._out = [o for o in self._out if o[0] > now]
        data = b""
        for _, chunk, is_radio in due:
            if is_radio:
                if not self.rx:
                    continue                  # deaf while rx is paused
                self.packets_sent += 1
            data += chunk
        return data

    def pending(self) -> int:
        now = time.monotonic()
        with self._lock:
            return sum(len(o[1]) for o in self._out if o[0] <= now)

    def receive(self, data: bytes) -> None:
        self._power_tick()
        with self._lock:
            for byte in data:
                ch = bytes([byte])
                if ch == b"\x03":
                    self._line = b""
                    if self.rx:
                        self.rx = False
                        self._rx_off_at = time.monotonic()
                        self._say(b"\r\nPackets received 0\r\n" + PROMPT)
                    else:
                        self._say(PROMPT)
                elif ch in (b"\r", b"\n"):
                    line, self._line = self._line.decode(errors="replace").strip(), b""
                    if ch == b"\r" or line:
                        self._command(line)
                else:
                    self._line += ch

    def _command(self, line: str) -> None:
        if not line:
            if not self.rx:
                self._say(PROMPT)
            return
        self.commands.append(line)
        self._say(line.encode() + b"\r\n")                       # echo
        parts = line.split()
        if parts[:2] == ["subghz", "rx"]:
            self.rx = True
            self._say(b"Load_keystore keeloq_mfcodes OK\r\n"
                      b"Listening at frequency: 433919830 device: 0. Press CTRL+C to stop\r\n")
            return
        if parts[:2] == ["subghz", "tx_from_file"] and len(parts) >= 3:
            name = parts[2].rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            if name in self.files:
                self.tx.append(name)
                n = int(name[6]) if name[6:7].isdigit() else 0
                if n in self.outlet_lit:
                    self.outlet_lit[n] = not self.outlet_lit[n]
                self._say(f"Listening at {parts[2]}. Frequency=315000000, Protocol=Princeton\r\n"
                          "Press CTRL+C to stop\r\n.\r\n".encode())
                self._say(PROMPT, self.tx_time_s)       # the send takes a while
                return
            else:
                self._say(f"subghz tx_from_file: Error open file {parts[2]}\r\n".encode())
        elif parts[:1] == ["led"] and len(parts) == 3 and parts[1] in "rgb":
            self.rgb["rgb".index(parts[1])] = int(parts[2])
        elif parts[:1] == ["vibro"] and len(parts) == 2:
            self.vibro = parts[1] == "1"
            self.vibro_count += self.vibro
        self._say(PROMPT)


class _FakePort:
    """Just enough of serial.Serial for FlipperSerial."""

    def __init__(self, device: FakeDevice):
        self.device = device
        self.is_open = True

    def _check(self) -> None:
        if not self.device.plugged:
            self.is_open = False
            raise serial.SerialException("fake cable unplugged")

    @property
    def in_waiting(self) -> int:
        self._check()
        return self.device.pending()

    def read(self, n: int = 1) -> bytes:
        self._check()
        data = self.device.take_due()
        if not data:
            time.sleep(0.02)
        return data

    def write(self, data: bytes) -> int:
        self._check()
        self.device.receive(data)
        return len(data)

    def close(self) -> None:
        self.is_open = False


class FakeSerial(FlipperSerial):
    def __init__(self, device: FakeDevice):
        super().__init__(port_setting="FAKE")
        self.device = device

    def open(self) -> bool:
        if not self.device.plugged:
            return False
        self._ser = _FakePort(self.device)   # type: ignore[assignment]
        self.port, self._buf = "FAKE", ""
        return True


class FakeFlipper(Flipper):
    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 on_event: Optional[Callable[[Event], None]] = None,
                 source: str = Source.SIM, charge_poll_s: float = 1.0, tx_time_s: float = 0.0):
        super().__init__(settings, on_event, source=source)
        self.device = FakeDevice(charge_poll_s=charge_poll_s, tx_time_s=tx_time_s)
        self.conn = FakeSerial(self.device)
