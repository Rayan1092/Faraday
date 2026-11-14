"""Record raw Flipper output to hardware/flipper/samples/<name>.txt (not .log: the repo gitignores *.log).

Each line is "<seconds since start>\\t<raw line>", so the parser tests can
replay real timing. Ctrl+C here stops recording (and stops rx on the Flipper).

    python -m hardware.flipper.capture doorbell_hold --seconds 20
    python -m hardware.flipper.capture help --cmd "help" --seconds 3
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from hardware.flipper.connection import FlipperSerial
from hardware.settings import for_os, load_settings

SAMPLES = Path(__file__).resolve().parent / "samples"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="sample file name, without .txt")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--cmd", default=None, help="CLI command (default: settings rx_command)")
    args = ap.parse_args()

    settings = load_settings()["flipper"]
    cmd = args.cmd or settings["rx_command"]
    fl = FlipperSerial(for_os(settings["serial_port"]), settings.get("baud", 230400))
    if not fl.open():
        print("Flipper not found. Plug it in and close any app on it.", file=sys.stderr)
        return 1
    print(f"port {fl.port}")
    if not fl.get_prompt():
        print("no CLI prompt (is an app open on the Flipper?)", file=sys.stderr)
        return 1

    SAMPLES.mkdir(exist_ok=True)
    out = SAMPLES / f"{args.name}.txt"
    fl.write_line(cmd)
    t0 = time.monotonic()
    print(f"running '{cmd}' for {args.seconds:.0f}s -> {out}")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        try:
            while time.monotonic() - t0 < args.seconds:
                for line in fl.read_lines():
                    stamp = time.monotonic() - t0
                    f.write(f"{stamp:.3f}\t{line}\n")
                    f.flush()
                    print(f"{stamp:8.3f}  {line}")
        except KeyboardInterrupt:
            pass
        fl.ctrl_c()
        time.sleep(0.3)
        for line in fl.read_lines():
            f.write(f"{time.monotonic() - t0:.3f}\t{line}\n")
    fl.close()
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
