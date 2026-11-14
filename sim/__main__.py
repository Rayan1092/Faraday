"""Keyboard simulator: python -m sim

Sends fake events through the real pipeline (modes/ -> log/ -> rules/ baseline
-> brain) and prints every event and action. Hardware actions are print-only.
take_photo is answered with a fake photo_described event. Everything is written
to logs/events.jsonl like a real run. --dashboard serves the dashboard too,
so the whole thing can be tried without hardware.

Keys (then Enter):
  d            doorbell
  m            motion (one merged event)
  t [text]     speech, as if STT heard it (asks for text if none given)
  1 / 2 / 3    Home / Away / Night. Home from an armed mode asks for the passphrase
  s            status (mode, alert and recent log)
  h            help
  q            quit
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional

if __package__ in (None, ""):  # allow `python sim/__main__.py` as well as `python -m sim`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import Action, ActionType, Event, Mode  # noqa: E402

from brain.pipeline import Pipeline  # noqa: E402
from brain.settings import llm_config, load_settings, settings_path  # noqa: E402
from dashboard import Dashboard  # noqa: E402
from log import EventLog  # noqa: E402
from rules import RuleStore  # noqa: E402
from sim import events  # noqa: E402


class Sim:
    def __init__(self, pipeline: Pipeline, verbose: bool = False,
                 photo_text: str = events.FAKE_DESCRIPTION):
        self.pipeline = pipeline
        self.verbose = verbose
        self.photo_text = photo_text
        self._pending: List[Event] = []  # fake camera replies, sent after the current event
        pipeline.on_event = self.show_event
        pipeline.on_action = self.show_action

    @property
    def modes(self):
        return self.pipeline.modes

    # ---- inputs ------------------------------------------------------------

    def command(self, line: str) -> bool:
        """Handle one typed line. Returns False to quit."""
        line = line.strip()
        if not line:
            return True
        key, _, rest = line.partition(" ")
        key = key.lower()
        if key == "q":
            return False
        if key == "h":
            print(__doc__.split("Keys", 1)[1].split("\n", 1)[1])
        elif key == "s":
            self.status()
        elif key == "d":
            self.send(events.doorbell())
        elif key == "m":
            self.send(events.motion())
        elif key == "t":
            text = (rest or input("  heard> ")).strip()
            if text:
                self.send(events.speech(text))
            else:
                print("  (nothing heard)")
        elif key == "1":
            self.go_home(rest)
        elif key in ("2", "3"):
            target = Mode.AWAY if key == "2" else Mode.NIGHT
            if self.pipeline.owner_set_mode(target) is None:
                print(f"  already {self.modes.mode}")
            self._drain()
        else:
            print(f"  unknown key {key!r}, h for help")
        return True

    def go_home(self, typed: str) -> None:
        if not self.modes.armed:
            print("  already home")
            return
        attempt = typed or (getpass.getpass("  passphrase: ") if sys.stdin.isatty()
                            else input("  passphrase: "))
        if self.pipeline.owner_passphrase(attempt) is None:
            print(f"  wrong passphrase, still {self.modes.mode}")
        self._drain()

    def send(self, event: Event) -> None:
        self.pipeline.handle(event)
        self._drain()

    def _drain(self) -> None:
        while self._pending:
            self.pipeline.handle(self._pending.pop(0))

    # ---- outputs -----------------------------------------------------------

    def show_event(self, event: Event) -> None:
        print(f"\nEVENT  {event.type:<15} {_brief(event.data)}  [{event.id}, source {event.source}]")
        if self.verbose:
            print("       " + event.to_json())

    def show_action(self, action: Action) -> None:
        print(f"  ACTION {action.type:<13} {json.dumps(action.args, ensure_ascii=False)}"
              f"  [{action.source}, {action.id} <- {action.caused_by}]")
        if self.verbose:
            print("       " + action.to_json())
        if action.type == ActionType.TAKE_PHOTO:  # stand-in camera + vision
            photo = events.photo_described(action.id, self.photo_text)
            if threading.current_thread() is threading.main_thread():
                self._pending.append(photo)  # sent right after the current event
            else:  # asked for from the dashboard thread: answer from a thread too
                threading.Thread(target=self.pipeline.handle, args=(photo,), daemon=True).start()

    def status(self) -> None:
        print(f"  mode: {self.modes.mode}, alert: {self.modes.alert}, log: {self.pipeline.log.path}")
        for entry in self.pipeline.log.recent(10):
            body = entry.get("data") or entry.get("args")
            print(f"  {entry['timestamp'][11:19]} {entry['kind']:<6} {entry['type']:<15} {_brief(body)}")


def _brief(d: Optional[Dict]) -> str:
    if not d:
        return ""
    text = json.dumps(d, ensure_ascii=False)
    return text if len(text) <= 90 else text[:87] + "..."


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # Windows consoles and odd model output
        except AttributeError:
            pass
    parser = argparse.ArgumentParser(prog="python -m sim", description="Faraday keyboard simulator")
    parser.add_argument("--offline", action="store_true", help="skip Bonsai, rule-based fallback only")
    parser.add_argument("--mode", choices=sorted(Mode.ALL), default=Mode.HOME, help="starting mode")
    parser.add_argument("--photo", default=events.FAKE_DESCRIPTION, help="fake camera description")
    parser.add_argument("--log", help="log file (default: logs/events.jsonl from settings)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print full JSON messages")
    parser.add_argument("--dashboard", action="store_true", help="serve the dashboard and open it")
    parser.add_argument("--port", type=int, help="dashboard port (default: dashboard.port in settings)")
    parser.add_argument("--no-browser", action="store_true", help="with --dashboard: don't open a browser")
    args = parser.parse_args(argv)

    settings = load_settings()
    cfg = llm_config(settings)
    log, rules = None, None
    if args.log:  # keep the rules next to a custom log, so test runs don't touch logs/
        log = EventLog(args.log, settings["security"]["passphrase"])
        rules = RuleStore(Path(args.log).with_name(Path(args.log).stem + "-rules.json"))
    pipeline = Pipeline.from_settings(settings, use_llm=not args.offline, log=log, mode=args.mode,
                                      rules=rules)
    sim = Sim(pipeline, args.verbose, args.photo)

    print(f"Faraday sim. Settings: {settings_path().name}. Log: {pipeline.log.path}")
    print("Bonsai: off (rules only)" if args.offline
          else f"Bonsai: {cfg['model']} at {cfg['base_url']} (timeout {cfg['timeout_s']:.0f}s)")
    if settings_path().name == "settings.example.json":
        print("Note: using settings.example.json, so the passphrase is the public example.")
    print(f"Rules: {pipeline.rules.path} ({len(pipeline.rules.all())} stored)")
    print("Keys: d doorbell, m motion, t <text> speech, 1/2/3 home/away/night, s status, h help, q quit")
    board = None
    if args.dashboard:
        board = Dashboard.from_settings(pipeline, settings, args.port).start()
        print(f"Dashboard: {board.url}")
        if not args.no_browser:
            webbrowser.open(board.url)
    pipeline.start()

    while True:
        try:
            line = input(f"\n[{sim.modes.mode}/{sim.modes.alert}]> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not sim.command(line):
            break
    pipeline.wait_background(20)
    if board:
        board.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
