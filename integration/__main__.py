"""Start Faraday: py -m integration   (python3 -m integration on the Mac)

Starts the Flipper driver (or the fake one: --fake, or integration.fake_flipper
in settings), the event queue, Dell's pipeline (modes, log, rules, brain) and
the dashboard.

Console keys (then Enter):
  t <text>   typed speech (same as the dashboard text box)
  2 / 3      arm Away / Night        1   Home (asks for the passphrase)
  d / m      fake doorbell / fake motion wave (fake Flipper only)
  s          status                  q   quit
"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys
import webbrowser
from pathlib import Path
from typing import List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import Event, EventType, Mode, Source, SpeechHeardData, make_event  # noqa: E402

from hardware.settings import CONFIG_DIR, load_settings  # noqa: E402
from integration.app import App  # noqa: E402
from integration.standins import StandInCamera  # noqa: E402


def build_app(args: argparse.Namespace, settings) -> App:
    fake = args.fake or bool(settings.get("integration", {}).get("fake_flipper", False))
    if fake:
        from hardware.flipper.fake import FakeFlipper
        flipper = FakeFlipper(settings)
    else:
        from hardware.flipper import Flipper
        flipper = Flipper(settings)
    log, rules = None, None
    if args.log:  # keep test runs out of logs/events.jsonl
        from log import EventLog
        from rules import RuleStore
        log = EventLog(args.log, settings["security"]["passphrase"])
        rules = RuleStore(Path(args.log).with_name(Path(args.log).stem + "-rules.json"))
    camera = StandInCamera()
    return App(settings, flipper, camera=camera, use_llm=not args.offline, mode=args.mode,
               event_log=log, rules=rules, dashboard=not args.no_dashboard, port=args.port)


def console(app: App, fake: bool) -> None:
    modes = app.pipeline.modes
    while True:
        try:
            line = input(f"\n[{modes.mode}/{modes.alert}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        key, _, rest = line.partition(" ")
        key = key.lower()
        if key == "q":
            return
        if key == "t" and rest.strip():
            app.submit(make_event(EventType.SPEECH_HEARD, SpeechHeardData(text=rest.strip()),
                                  Source.DASHBOARD))
        elif key in ("2", "3"):
            if app.pipeline.owner_set_mode(Mode.AWAY if key == "2" else Mode.NIGHT) is None:
                print(f"  already {modes.mode}")
        elif key == "1":
            if not modes.armed:
                print("  already home")
                continue
            attempt = rest or getpass.getpass("  passphrase: ")
            if app.pipeline.owner_passphrase(attempt) is None:
                print("  wrong passphrase")
        elif key in ("d", "m"):
            if not fake:
                print("  d/m only work with the fake Flipper (--fake)")
            elif key == "d":
                app.flipper.device.press_doorbell()
            else:
                app.flipper.device.wave()
        elif key == "s":
            print(f"  mode {modes.mode}, alert {modes.alert}, LED {app.flipper.current_color()}, "
                  f"flipper {'connected' if app.flipper.connected.is_set() else 'NOT connected'}")
        elif key == "h" or line:
            print(__doc__.split("Console keys", 1)[1])


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(prog="py -m integration", description="Start Faraday")
    ap.add_argument("--fake", action="store_true", help="fake Flipper (no hardware)")
    ap.add_argument("--offline", action="store_true", help="skip Bonsai, rules-only brain")
    ap.add_argument("--mode", choices=sorted(Mode.ALL), default=Mode.HOME)
    ap.add_argument("--log", help="log file (default: logs/events.jsonl)")
    ap.add_argument("--port", type=int, help="dashboard port (default from settings)")
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="show driver details")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    settings = load_settings()
    app = build_app(args, settings)
    fake = type(app.flipper).__name__ == "FakeFlipper"
    print(f"Faraday. Settings: {'settings.json' if (CONFIG_DIR / 'settings.json').exists() else 'settings.example.json (public example passphrase!)'}")
    print(f"Flipper: {'FAKE' if fake else 'real (autodetect, then settings port)'}. "
          f"Bonsai: {'off' if args.offline else 'on'}. Log: {app.pipeline.log.path}")
    app.start()
    if app.board is not None:
        print(f"Dashboard: {app.board.url}")
        if not args.no_browser:
            webbrowser.open(app.board.url)
    print("Keys: t <text>, 1/2/3 home/away/night, d/m fake radio, s status, q quit")
    try:
        console(app, fake)
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
