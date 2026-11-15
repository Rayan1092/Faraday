"""Low-level Flipper Zero USB serial CLI connection.

Finds the port (USB VID/PID first, then the setting), reads lines, detects the
">: " prompt, runs commands. Raises FlipperDisconnected when the cable drops;
the caller (device.py) handles reconnecting. Works on macOS and Windows.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import time
from typing import List, Optional, Tuple

import serial
from serial.tools import list_ports

log = logging.getLogger(__name__)

FLIPPER_VID = 0x0483   # STMicroelectronics (Flipper uses ST's USB VCP ids)
FLIPPER_PID = 0x5740
PROMPT = ">: "
CTRL_C = b"\x03"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class FlipperDisconnected(Exception):
    pass


def find_port(port_setting: Optional[str]) -> Optional[str]:
    """Auto-detect by VID/PID (preferring ports that say Flipper), else the
    setting. The setting may be a glob like /dev/cu.usbmodemflip_*."""
    ports = list(list_ports.comports())
    candidates = [p for p in ports if p.vid == FLIPPER_VID and p.pid == FLIPPER_PID]
    flipperish = [p for p in candidates
                  if "flip" in f"{p.manufacturer} {p.product} {p.serial_number} {p.device}".lower()]
    for group in (flipperish, candidates):
        cu = [p.device for p in group if not p.device.startswith("/dev/tty.")]
        if cu or group:
            return sorted(cu or [p.device for p in group])[0]
    if port_setting:
        if any(ch in port_setting for ch in "*?["):
            matches = sorted(glob.glob(port_setting))
            return matches[0] if matches else None
        if port_setting in {p.device for p in ports}:
            return port_setting
    return None


class FlipperSerial:
    def __init__(self, port_setting: Optional[str], baud: int = 230400):
        self.port_setting = port_setting
        self.baud = baud
        self.port: Optional[str] = None
        self._ser: Optional[serial.Serial] = None
        self._buf = ""

    # ---- open / close ------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def open(self) -> bool:
        """Try once to find and open the Flipper. Returns True on success."""
        port = find_port(self.port_setting)
        if not port:
            return False
        # exclusive (POSIX): macOS lets a second process open the port, and two talkers
        # stop each other's commands (a check run beside Faraday stopped its rx)
        extra = {"exclusive": True} if os.name == "posix" else {}
        try:
            try:
                self._ser = serial.Serial(port, self.baud, timeout=0.05, write_timeout=2, **extra)
            except (TypeError, ValueError):                  # a pyserial without exclusive
                self._ser = serial.Serial(port, self.baud, timeout=0.05, write_timeout=2)
        except (serial.SerialException, OSError) as e:
            log.debug("open %s failed: %s", port, e)
            self._ser = None
            return False
        self.port, self._buf = port, ""
        log.info("Flipper connected on %s", port)
        return True

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except (serial.SerialException, OSError):
                pass
        self._ser = None

    # ---- raw io --------------------------------------------------------------
    def write(self, data: bytes) -> None:
        if not self.is_open:
            raise FlipperDisconnected("not open")
        try:
            self._ser.write(data)
        except (serial.SerialException, OSError) as e:
            self.close()
            raise FlipperDisconnected(str(e)) from e

    def write_line(self, cmd: str) -> None:
        self.write(cmd.encode("ascii") + b"\r\n")

    def ctrl_c(self) -> None:
        self.write(CTRL_C)

    def read_lines(self) -> List[str]:
        """Complete lines received so far (waits up to the 50 ms read timeout).
        ANSI codes and \\r stripped. The unfinished tail stays buffered."""
        if not self.is_open:
            raise FlipperDisconnected("not open")
        try:
            chunk = self._ser.read(max(1, self._ser.in_waiting))
        except (serial.SerialException, OSError, TypeError) as e:
            # TypeError: pyserial on Windows when the device vanishes mid-read
            self.close()
            raise FlipperDisconnected(str(e)) from e
        if not chunk:
            return []
        self._buf += _ANSI.sub("", chunk.decode("utf-8", errors="replace"))
        *lines, self._buf = self._buf.split("\n")
        return [line.rstrip("\r") for line in lines]

    @property
    def at_prompt(self) -> bool:
        return self._buf.rstrip("\r").endswith(PROMPT) or self._buf.strip() == ">:"

    def wait_prompt(self, timeout: float) -> Tuple[bool, List[str]]:
        """Read until the CLI prompt shows. Returns (seen, lines read)."""
        lines: List[str] = []
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            lines += self.read_lines()
            if self.at_prompt:
                self._buf = ""
                return True, lines
        return False, lines

    def run(self, cmd: str, timeout: float = 3.0) -> Tuple[bool, List[str]]:
        """Run one CLI command at the prompt. Returns (finished, output lines
        without the echoed command)."""
        self.write_line(cmd)
        ok, lines = self.wait_prompt(timeout)
        return ok, [ln for ln in lines if ln.strip() and ln.strip() != cmd]

    def get_prompt(self, timeout: float = 3.0) -> bool:
        """Get to a clean prompt from any state (stops a running command)."""
        self.ctrl_c()
        time.sleep(0.05)
        self.write(b"\r\n")
        ok, _ = self.wait_prompt(timeout)
        return ok
