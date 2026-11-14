"""Flipper driver: real radio -> shared events, shared actions -> radio + LED.

One background thread owns the serial port. It keeps `subghz rx` running,
parses packets, merges bursts and emits shared Events (source "flipper").
Commands (outlet sends, LED, vibration) go through a queue; the thread drains
it in batches: stop rx (Ctrl+C) -> wait for prompt -> run commands -> restart
rx. So two sends never overlap and listening resumes right after.
The CLI is busy while rx runs, so LED changes need the same pause.

Interface for integration/:
    fl = Flipper(on_event=callback)      # or read fl.events (queue.Queue)
    fl.start()
    fl.execute(action)                   # outlet_on / outlet_off -> Future
    fl.apply_event(event)                # mode_changed / alert_changed -> LED
    fl.stop()
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from shared import (Action, ActionType, AlertLevel, DoorbellData, Event, EventType,
                    Mode, MotionData, Source, make_event)

from hardware.settings import for_os, load_settings
from .bursts import DEFAULT_GAP_S, Burst, BurstMerger
from .connection import FlipperDisconnected, FlipperSerial
from .parser import RxParser, normalise_key

log = logging.getLogger("hardware.flipper")

# RGB for the colour names used in settings flipper.led. Purple and yellow
# were picked by eye on the real Flipper LED (r255 b255 looked pink).
COLORS: Dict[str, Tuple[int, int, int]] = {
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "purple": (140, 0, 255),
    "yellow": (255, 140, 0),
    "red": (255, 0, 0),
    "white": (255, 255, 255),
    "off": (0, 0, 0),
}
DATA_FOR_TYPE = {EventType.DOORBELL: DoorbellData, EventType.MOTION: MotionData}
ERROR_WORDS = ("error", "failed", "fail ", "can't", "cannot", "not found", "close it")
TX_TIMEOUT_S = 15.0
ALARM_PULSES, PULSE_ON_S, PULSE_OFF_S = 3, 0.4, 0.25   # buzz-buzz-buzz, ~2 s
# The firmware's notification service turns our LED off by itself (seen after
# vibro and after a few idle minutes), so the colour is re-sent this often.
# Each refresh pauses rx ~0.35 s; it never runs while a burst is in progress.
DEFAULT_LED_REFRESH_S = 15.0
POST_VIBRO_REFRESH_S = 1.2


@dataclass
class _Command:
    steps: List[Tuple[str, Any]]             # ("cmd", "led r 255") or ("sleep", 0.5)
    future: Future = field(default_factory=Future)
    check_errors: bool = True
    quiet: bool = False                       # log at debug (periodic LED refresh)


class Flipper:
    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 on_event: Optional[Callable[[Event], None]] = None,
                 source: str = Source.FLIPPER):
        settings = settings or load_settings()
        self.source = source                 # FakeFlipper passes Source.SIM
        fs = settings["flipper"]
        self.rx_command: str = fs["rx_command"]
        self.tx_template: str = fs["tx_command"]
        self.led_colors: Dict[str, str] = fs["led"]
        self.gap_s: float = float(fs.get("burst_gap_s", DEFAULT_GAP_S))
        self.led_refresh_s: float = float(fs.get("led_refresh_s", DEFAULT_LED_REFRESH_S))
        self.outlets: Dict[str, Dict[str, Any]] = settings["outlets"]
        # key -> event type, from settings sensors {"doorbell": {"key": ..}, "motion": ..}
        self.sensor_keys: Dict[str, str] = {}
        # Princeton 24-bit: top 20 bits = remote serial, low 4 bits = button.
        # The real doorbell sent button 8 and later button 1 (0x00DEC608 and
        # 0x00DEC601), so these sensors match on the serial, ignoring the button.
        self.sensor_serials: Dict[int, str] = {}
        for name, cfg in settings["sensors"].items():
            if name not in DATA_FOR_TYPE:
                continue
            key = normalise_key(cfg["key"].replace("0x", ""))
            self.sensor_keys[key] = name
            if cfg.get("protocol", "Princeton") == "Princeton" and cfg.get("bits", 24) == 24:
                self.sensor_serials[int(key, 16) >> 4] = name

        self.conn = FlipperSerial(for_os(fs["serial_port"]), fs.get("baud", 230400))
        self.events: "queue.Queue[Event]" = queue.Queue()
        self.on_event = on_event
        # called with (burst, sensor name or None) for every burst; runs on the
        # driver thread, so keep it quick (manual_test's learn mode prints)
        self.on_burst: Optional[Callable[[Burst, Optional[str]], None]] = None
        self.connected = threading.Event()

        self._cmds: "queue.Queue[_Command]" = queue.Queue()
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._parser = RxParser()
        self._merger = BurstMerger(self.gap_s, group=lambda k: self.sensor_for(k) or k)
        self._burst_wall: Dict[str, datetime] = {}
        self._lock = threading.Lock()
        self._mode = Mode.HOME
        self._alert = AlertLevel.NORMAL
        self._was_connected = False
        self._rgb: Optional[Tuple[int, int, int]] = None   # last LED sent; None = unknown
        self._led_at = 0.0                                  # monotonic time LED last queued
        self._refresh_due: Optional[float] = None           # one-off refresh (after vibro)

    # ---- public interface ----------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._threads = [threading.Thread(target=self._run, name="flipper", daemon=True)]
        if self.on_event:
            self._threads.append(threading.Thread(target=self._dispatch,
                                                  name="flipper-events", daemon=True))
        for t in self._threads:
            t.start()
        self._queue_led()   # show the current mode colour once connected

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)
        self._threads = []

    def execute(self, action: Action) -> Future:
        """Run outlet_on / outlet_off. Returns a Future -> (ok, output lines)."""
        if action.type not in (ActionType.OUTLET_ON, ActionType.OUTLET_OFF):
            raise ValueError(f"Flipper can't execute '{action.type}'")
        outlet = self.outlets.get(action.args.get("outlet"))
        if outlet is None:
            raise ValueError(f"unknown outlet '{action.args.get('outlet')}'")
        name = outlet["on" if action.type == ActionType.OUTLET_ON else "off"]
        return self.send_file(name)

    def send_file(self, name: str) -> Future:
        """Transmit /ext/subghz/<name>.sub (template from settings)."""
        cmd = self.tx_template.format(name=name)
        return self._enqueue([("cmd", cmd, TX_TIMEOUT_S)])

    def apply_event(self, event: Event) -> Optional[Future]:
        """Feed mode_changed / alert_changed; updates the LED (and vibrates on alarm).
        Other event types are ignored. Returns a Future or None."""
        vibrate = False
        with self._lock:
            if event.type == EventType.MODE_CHANGED:
                self._mode = event.data["mode"]
            elif event.type == EventType.ALERT_CHANGED:
                vibrate = (event.data["level"] == AlertLevel.ALARM
                           and self._alert != AlertLevel.ALARM)
                self._alert = event.data["level"]
            else:
                return None
        fut = self._queue_led()
        if vibrate:     # vibrate() re-sends the (new) colour around every buzz
            fut = self.vibrate()
        return fut

    def sensor_for(self, key: str) -> Optional[str]:
        """Event type for a received key: exact match, else Princeton serial match."""
        name = self.sensor_keys.get(key)
        if name is None and len(key) == 10:          # 0x + 8 hex digits = 24-bit range
            value = int(key, 16)
            if value < (1 << 24):
                name = self.sensor_serials.get(value >> 4)
        return name

    def current_color(self) -> str:
        with self._lock:
            key = self._alert if self._alert != AlertLevel.NORMAL else self._mode
        return self.led_colors.get(key, "white")

    def set_color(self, color: str, quiet: bool = False) -> Future:
        """Low level: set the LED to a colour name from COLORS.

        All three channels are always sent: the firmware's notification
        service can reset the LED on its own (seen after `vibro`), so the last
        colour we sent can't be trusted. The CLI sets one channel per command,
        so channels going down are sent first: no bright in-between flashes."""
        target = COLORS[color]
        with self._lock:
            old, self._rgb = self._rgb, target
        order = list(zip("rgb", target))
        if old is not None:
            order.sort(key=lambda cv: cv[1] - old["rgb".index(cv[0])])  # down, same, up
        self._led_at = time.monotonic()
        return self._enqueue([("cmd", f"led {ch} {v}", 3.0) for ch, v in order], quiet)

    def vibrate(self, pulses: int = ALARM_PULSES, on_s: float = PULSE_ON_S,
                off_s: float = PULSE_OFF_S) -> Future:
        """Buzz in pulses. Every `vibro` command makes the firmware put its own
        LED colour back (green while charging), so the current colour is
        re-sent right after each one."""
        r, g, b = COLORS[self.current_color()]
        led = [("cmd", f"led r {r}", 3.0), ("cmd", f"led g {g}", 3.0), ("cmd", f"led b {b}", 3.0)]
        led_s = 0.14                       # three led commands take ~0.14 s
        steps: List[Tuple] = []
        for i in range(pulses):
            steps += [("cmd", "vibro 1", 3.0), *led, ("sleep", max(0.0, on_s - led_s)),
                      ("cmd", "vibro 0", 3.0), *led]
            if i < pulses - 1:
                steps.append(("sleep", max(0.0, off_s - led_s)))
        # the firmware wipes the LED ~1 s *after* vibro stops: re-send it then
        self._refresh_due = (time.monotonic() + pulses * (on_s + off_s)
                             + POST_VIBRO_REFRESH_S)
        return self._enqueue(steps)

    # ---- internals: queue ----------------------------------------------------
    def _enqueue(self, steps: List[Tuple], quiet: bool = False) -> Future:
        cmd = _Command(steps, quiet=quiet)
        if not steps:                       # nothing to do: don't pause rx for it
            cmd.future.set_result((True, []))
        else:
            self._cmds.put(cmd)
        return cmd.future

    def _queue_led(self, quiet: bool = False) -> Future:
        return self.set_color(self.current_color(), quiet)

    def _maybe_refresh_led(self, now: float) -> None:
        due = self._refresh_due is not None and now >= self._refresh_due
        periodic = self.led_refresh_s > 0 and now - self._led_at >= self.led_refresh_s
        if (due or periodic) and self._merger.pending == 0 and self._cmds.empty():
            self._refresh_due = None
            self._queue_led(quiet=True)

    def _dispatch(self) -> None:
        while not self._stop.is_set():
            try:
                event = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.on_event(event)
            except Exception:  # never let a bad callback kill the driver
                log.exception("on_event callback failed")

    # ---- internals: device thread --------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.conn.is_open and not self._connect():
                self._stop.wait(1.0)
                continue
            try:
                if not self._cmds.empty():
                    self._run_batch()
                now = time.monotonic()
                for line in self.conn.read_lines():
                    self._feed(line, time.monotonic())
                for burst in self._merger.flush(now):
                    self._emit(burst)
                self._maybe_refresh_led(now)
            except FlipperDisconnected as e:
                self._on_disconnect(e)
        # shutdown: stop rx politely, flush what we have
        for burst in self._merger.flush(time.monotonic(), force=True):
            self._emit(burst)
        if self.conn.is_open:
            try:
                self.conn.ctrl_c()
            except FlipperDisconnected:
                pass
            self.conn.close()
        self.connected.clear()

    def _connect(self) -> bool:
        if not self.conn.open():
            return False
        try:
            if not self.conn.get_prompt():
                log.error("Flipper on %s gave no CLI prompt. Close any app on the Flipper.",
                          self.conn.port)
                self.conn.close()
                self._stop.wait(2.0)
                return False
            if not self._start_rx():
                self.conn.close()
                self._stop.wait(2.0)
                return False
        except FlipperDisconnected as e:
            self._on_disconnect(e)
            return False
        if self._was_connected:
            with self._lock:
                self._rgb = None    # Flipper may have rebooted: resend all channels
            self._queue_led()   # re-apply LED after a reconnect
        self._was_connected = True
        self.connected.set()
        return True

    def _on_disconnect(self, err: Exception) -> None:
        if self.connected.is_set():
            log.warning("Flipper disconnected (%s); reconnecting", err)
        self.connected.clear()
        self.conn.close()
        self._parser = RxParser()
        for burst in self._merger.flush(time.monotonic(), force=True):
            self._emit(burst)

    def _start_rx(self) -> bool:
        self.conn.write_line(self.rx_command)
        end = time.monotonic() + 4.0
        seen: List[str] = []
        while time.monotonic() < end:
            for line in self.conn.read_lines():
                seen.append(line)
                if line.startswith("Listening at"):
                    return True
                if any(w in line.lower() for w in ERROR_WORDS):
                    log.error("rx failed to start: %s", line)
                    return False
        log.error("rx did not start; output: %s", seen[-5:])
        return False

    def _stop_rx(self) -> bool:
        self.conn.ctrl_c()
        ok, lines = self.conn.wait_prompt(3.0)
        for line in lines:          # packets that arrived just before Ctrl+C
            self._feed(line, time.monotonic())
        return ok

    def _run_batch(self) -> None:
        batch: List[_Command] = []
        while True:
            try:
                batch.append(self._cmds.get_nowait())
            except queue.Empty:
                break
        paused_at = time.monotonic()
        if not self._stop_rx():
            log.warning("no prompt after stopping rx; trying anyway")
        try:
            for cmd in batch:
                cmd.future.set_result(self._run_steps(cmd))
        except FlipperDisconnected as e:
            for cmd in batch:
                if not cmd.future.done():
                    cmd.future.set_exception(e)
            raise
        if self._start_rx():
            level = logging.DEBUG if all(c.quiet for c in batch) else logging.INFO
            log.log(level, "rx resumed after %.2fs pause", time.monotonic() - paused_at)
        else:
            log.error("rx did not restart; reconnecting")
            self.conn.close()

    def _run_steps(self, cmd: _Command) -> Tuple[bool, List[str]]:
        ok_all, out_all = True, []
        for step in cmd.steps:
            if step[0] == "sleep":
                time.sleep(step[1])
                continue
            _, text, timeout = step
            t0 = time.monotonic()
            ok, out = self.conn.run(text, timeout)
            failed = cmd.check_errors and any(w in ln.lower() for ln in out for w in ERROR_WORDS)
            ok = ok and not failed
            # failures always show (WARNING); successes are INFO (-v), refreshes DEBUG
            level = logging.WARNING if not ok else (logging.DEBUG if cmd.quiet else logging.INFO)
            log.log(level, "ran '%s' in %.2fs: %s %s", text, time.monotonic() - t0,
                    "ok" if ok else "FAILED", out[-2:] if out else "")
            ok_all &= ok
            out_all += out
        return ok_all, out_all

    def _feed(self, line: str, t: float) -> None:
        packet = self._parser.feed(line, t)
        if packet is None:
            return
        for burst in self._merger.flush(packet.t):      # bursts that ended before this packet
            self._emit(burst)
        if not self._merger.is_open(packet.key):
            # first packet of a burst: its wall-clock time becomes the event timestamp
            self._burst_wall[self._merger.group(packet.key)] = datetime.now().astimezone()
        self._merger.add(packet)

    def _emit(self, burst: Burst) -> None:
        started = self._burst_wall.pop(self._merger.group(burst.key), None)
        name = self.sensor_for(burst.key)
        if self.on_burst is not None:       # learn mode: sees every burst, known or not
            try:
                self.on_burst(burst, name)
            except Exception:
                log.exception("on_burst callback failed")
        if name is None:
            log.info("ignoring unknown key %s (%s %dbit, %d packets)",
                     burst.key, burst.protocol, burst.bits, burst.packets)
            return
        if burst.key not in self.sensor_keys:
            log.info("%s matched by serial: key %s (button %s)", name, burst.key, burst.key[-1])
        data = DATA_FOR_TYPE[name](key=burst.key, protocol=burst.protocol, bits=burst.bits,
                                   packets=burst.packets, duration_ms=burst.duration_ms)
        event = make_event(name, data, self.source)
        if started is not None:
            event.timestamp = started.isoformat(timespec="milliseconds")
        log.info("event %s %s packets=%d duration_ms=%d", event.type, burst.key,
                 burst.packets, burst.duration_ms)
        self.events.put(event)
