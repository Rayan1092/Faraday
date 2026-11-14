"""Keyboard simulator: python -m sim

Sends fake events to the brain and prints every action it returns.
Hardware actions are print-only. take_photo is answered with a fake
photo_described event, and set_mode goes through the same ModeState the
passphrase uses, so the whole chain runs without any hardware.

Keys (then Enter):
  d            doorbell
  m            motion (one merged event)
  t [text]     speech, as if STT heard it (asks for text if none given)
  1 / 2 / 3    Home / Away / Night. Home from an armed mode asks for the passphrase
  s            status (mode and recent log)
  h            help
  q            quit
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

if __package__ in (None, ""):  # allow `python sim/__main__.py` as well as `python -m sim`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import Action, ActionType, ChangedBy, Event, EventType, Mode  # noqa: E402

from brain import Brain  # noqa: E402
from brain.safety import ModeState, redact  # noqa: E402
from brain.settings import llm_config, load_settings, passphrase, settings_path  # noqa: E402
from sim import events  # noqa: E402

MAX_CHAIN = 4  # event -> action -> follow-up event depth, so nothing can loop


class Sim:
    def __init__(self, brain: Brain, modes: ModeState, verbose: bool = False,
                 photo_text: str = events.FAKE_DESCRIPTION):
        self.brain = brain
        self.modes = modes
        self.verbose = verbose
        self.photo_text = photo_text
        self.log: Deque[Dict] = deque(maxlen=50)

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
            self.say(rest or input("  heard> "))
        elif key == "1":
            self.go_home(rest)
        elif key in ("2", "3"):
            target = Mode.AWAY if key == "2" else Mode.NIGHT
            change = self.modes.arm(target, ChangedBy.DASHBOARD, "Set from the sim keyboard.")
            if change:
                self.send(change)
            else:
                print(f"  already {self.modes.mode}")
        else:
            print(f"  unknown key {key!r}, h for help")
        return True

    def say(self, text: str) -> None:
        text = text.strip()
        if not text:
            print("  (nothing heard)")
            return
        # Passphrase check happens before anything is logged or sent to the brain.
        clean, matched = redact(text, self.brain.passphrase)
        heard = events.speech(clean)
        self.send(heard)
        if matched:
            change = self.modes.disarm_with_passphrase(text, caused_by=heard.id)
            if change:
                self.send(change)

    def go_home(self, typed: str) -> None:
        if not self.modes.armed:
            print("  already home")
            return
        attempt = typed or (getpass.getpass("  passphrase: ") if sys.stdin.isatty()
                            else input("  passphrase: "))
        change = self.modes.disarm_with_passphrase(attempt)
        if change:
            self.send(change)
        else:
            print(f"  wrong passphrase, still {self.modes.mode}")

    # ---- event -> brain -> actions -----------------------------------------

    def send(self, event: Event, depth: int = 0) -> None:
        print(f"\nEVENT  {event.type:<15} {_brief(event.data)}  [{event.id}, source {event.source}]")
        if self.verbose:
            print("       " + event.to_json())
        recent = list(self.log)
        self.log.append(event.to_dict())
        decision = self.brain.decide(event, self.modes.mode, recent)
        took = f", Bonsai {decision.elapsed_s:.2f}s" if decision.elapsed_s is not None else ""
        print(f"  brain -> {len(decision.actions)} action(s) via {decision.via}{took}")
        for action in decision.actions:
            print(f"  ACTION {action.type:<13} {json.dumps(action.args, ensure_ascii=False)}"
                  f"  [{action.id} <- {action.caused_by}]")
            if self.verbose:
                print("       " + action.to_json())
            self.log.append(action.to_dict())
        for action in decision.actions:
            self.run(action, depth)

    def run(self, action: Action, depth: int) -> None:
        """Stand-in executor: software actions update state, hardware is print-only."""
        if depth >= MAX_CHAIN:
            return
        if action.type == ActionType.SET_MODE:
            change = self.modes.arm(action.args["mode"], ChangedBy.BRAIN,
                                    "Brain chose set_mode.", caused_by=action.id)
            if change:
                self.send(change, depth + 1)
        elif action.type == ActionType.TAKE_PHOTO:
            self.send(events.photo_described(action.id, self.photo_text), depth + 1)

    def status(self) -> None:
        print(f"  mode: {self.modes.mode}")
        for entry in list(self.log)[-10:]:
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
    parser.add_argument("-v", "--verbose", action="store_true", help="print full JSON messages")
    args = parser.parse_args(argv)

    settings = load_settings()
    cfg = llm_config(settings)
    secret = passphrase(settings)
    brain = Brain.from_settings(settings, use_llm=not args.offline)
    sim = Sim(brain, ModeState(secret, args.mode), args.verbose, args.photo)

    print(f"Faraday sim. Settings: {settings_path().name}. Mode: {args.mode}.")
    print("Bonsai: off (rules only)" if args.offline
          else f"Bonsai: {cfg['model']} at {cfg['base_url']} (timeout {cfg['timeout_s']:.0f}s)")
    if not secret or secret == "CHANGE_ME_NOT_IN_GIT":
        print("Warning: passphrase is not set in config/settings.json.")
    print("Keys: d doorbell, m motion, t <text> speech, 1/2/3 home/away/night, s status, h help, q quit")

    while True:
        try:
            line = input(f"\n[{sim.modes.mode}]> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not sim.command(line):
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
