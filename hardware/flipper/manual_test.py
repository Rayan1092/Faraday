"""Manual hardware test for the Flipper driver.

    python -m hardware.flipper.manual_test

Prints live events. Keys (no Enter needed):
  1 / 2 / 3   toggle outlet 1 / 2 / 3 (porch light, living room lamp, spare)
  c           cycle LED colours (green, blue, purple, yellow, red)
  h / a / n   pretend mode_changed -> home / away / night (LED follows)
  w / x / r   pretend alert_changed -> warning / alarm (+vibrate) / normal
  v           vibrate
  q           quit
"""
from __future__ import annotations

import logging
import sys
import threading
import time

from shared import Action, ActionType, Event, EventType, Source

from hardware.flipper.device import Flipper

OUTLET_KEYS = {"1": "porch_light", "2": "living_room_lamp", "3": "spare"}
CYCLE = ["green", "blue", "purple", "yellow", "red"]


def getch() -> str:
    """Read one key without Enter, on Windows and macOS/Linux."""
    try:
        import msvcrt  # Windows
        return msvcrt.getwch()
    except ImportError:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def report(label: str, fut, t0: float) -> None:
    def done(f):
        try:
            ok, out = f.result()
            print(f"  -> {label}: {'OK' if ok else 'FAILED'} in {time.monotonic() - t0:.2f}s "
                  f"{out[-1] if out else ''}")
        except Exception as e:
            print(f"  -> {label}: ERROR {e}")
    fut.add_done_callback(done)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                        datefmt="%H:%M:%S")

    def on_event(ev: Event) -> None:
        print(f"\n*** EVENT {ev.type.upper()}  packets={ev.data['packets']} "
              f"duration_ms={ev.data['duration_ms']}\n    {ev.to_json()}")

    fl = Flipper(on_event=on_event)
    fl.start()
    print(__doc__)
    if not fl.connected.wait(10):
        print("Waiting for the Flipper (plug it in, close apps on it)...")

    outlet_on = {name: False for name in OUTLET_KEYS.values()}
    color_i = -1
    mode, alert = "home", "normal"
    try:
        while True:
            key = getch().lower()
            t0 = time.monotonic()
            if key == "q":
                break
            if key in OUTLET_KEYS:
                name = OUTLET_KEYS[key]
                outlet_on[name] = not outlet_on[name]
                kind = ActionType.OUTLET_ON if outlet_on[name] else ActionType.OUTLET_OFF
                print(f"> {kind} {name}")
                report(f"{kind} {name}", fl.execute(Action(kind, "dashboard", {"outlet": name})), t0)
            elif key == "c":
                color_i = (color_i + 1) % len(CYCLE)
                print(f"> LED {CYCLE[color_i]}")
                report(f"LED {CYCLE[color_i]}", fl.set_color(CYCLE[color_i]), t0)
            elif key in "han":
                prev, mode = mode, {"h": "home", "a": "away", "n": "night"}[key]
                ev = Event(EventType.MODE_CHANGED, Source.MODES,
                           {"mode": mode, "previous": prev, "changed_by": "dashboard", "reason": "test"})
                fut = fl.apply_event(ev)
                print(f"> mode {mode} (alert {alert}) -> LED {fl.current_color()}")
                report("LED", fut, t0)
            elif key in "wxr":
                prev, alert = alert, {"w": "warning", "x": "alarm", "r": "normal"}[key]
                ev = Event(EventType.ALERT_CHANGED, Source.MODES,
                           {"level": alert, "previous": prev, "reason": "test"})
                fut = fl.apply_event(ev)
                print(f"> alert {alert} (mode {mode}) -> LED {fl.current_color()}")
                report("LED/vibro", fut, t0)
            elif key == "v":
                report("vibrate", fl.vibrate(), t0)
    except KeyboardInterrupt:
        pass
    finally:
        fl.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
