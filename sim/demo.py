"""Backup demo: plays the PLAN.md demo script by itself, through the real pipeline.

    python -m sim --demo --dashboard            # plays on its own with realistic pauses
    python -m sim --demo --dashboard --manual   # press Enter before each step
    python -m sim --demo --offline --speed 3    # fast rehearsal without Bonsai

If a sensor or the Flipper fails on stage, stop the app, start this, and keep
talking: the dashboard on the projector reconnects by itself and shows every
step. Events are marked source "sim" in the log, and we say so out loud.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from shared import Mode

from sim import events

Say = Callable[[str], None]


@dataclass
class Step:
    number: int
    title: str
    lines: List[str]                # what happens, printed for the presenter
    run: Callable[["DemoRunner"], None]
    pause_after: float = 5.0        # seconds before the next step, at speed 1


class DemoRunner:
    def __init__(self, sim, passphrase: str, speed: float = 1.0, manual: bool = False,
                 sleep: Callable[[float], None] = time.sleep,
                 wait_enter: Callable[[str], str] = input, say: Say = print):
        self.sim = sim
        self.passphrase = passphrase
        self.speed = max(0.1, speed)
        self.manual = manual
        self.sleep = sleep
        self.wait_enter = wait_enter
        self.say = say
        self.notes: List[str] = []
        self._motion_at: Optional[float] = None

    @property
    def pipeline(self):
        return self.sim.pipeline

    def pause(self, seconds: float) -> None:
        self.sleep(seconds / self.speed)

    # ---- the moves ---------------------------------------------------------

    def speak(self, text: str, who: str = "Owner") -> None:
        self.say(f"\n    {who}: \"{text}\"")
        self.pause(1.5)  # a person takes a moment to say it
        self.sim.send(events.speech(text))

    def ensure_mode(self, mode: str, why: str) -> None:
        """Keep the demo going even if Bonsai misheard: switch like the owner would."""
        if self.pipeline.modes.mode != mode:
            self.notes.append(f"{why}: Bonsai didn't switch to {mode}, owner switched it")
            self.say(f"    (note: switching to {mode} by hand, {why})")
            self.pipeline.owner_set_mode(mode)
            self.sim._drain()

    def motion(self) -> None:
        self.say("\n    Someone walks past the motion sensor")
        self.pause(1.0)
        window = self.pipeline.modes.alarm_window_s
        if self._motion_at is not None and time.monotonic() - self._motion_at > window:
            self.notes.append("second motion came after the alarm window; no alarm")
        self._motion_at = time.monotonic()
        self.sim.send(events.motion())


def steps() -> List[Step]:
    def leaving(d: DemoRunner) -> None:
        d.speak("Faraday, I'm heading out.")
        d.ensure_mode(Mode.AWAY, "step 1")

    def doorbell(d: DemoRunner) -> None:
        d.say("\n    Visitor presses the doorbell")
        d.pause(1.0)
        d.sim.send(events.doorbell())

    def message(d: DemoRunner) -> None:
        d.speak("Package for Sam, I left it by the side door.", who="Visitor")

    def night(d: DemoRunner) -> None:
        d.say("\n    Owner switches to Night on the dashboard")
        d.pipeline.owner_set_mode(Mode.NIGHT)
        d.sim._drain()
        d.pause(3.0)
        d.motion()

    def rule(d: DemoRunner) -> None:
        d.speak("When there's motion at night, turn on the living room lamp too.")
        d.pause(3.0)
        d.motion()
        d.say("    (waiting for Bonsai's line about what it sees)")
        d.pipeline.wait_background(25)

    def disarm(d: DemoRunner) -> None:
        d.speak("Faraday, turn off the alarm.", who="Someone")

    def passphrase(d: DemoRunner) -> None:
        d.say("\n    Owner types the passphrase on the dashboard (hidden)")
        d.pause(2.0)
        if d.pipeline.owner_passphrase(d.passphrase) is None:
            d.notes.append("passphrase from settings was refused")
            d.say("    (the passphrase in settings didn't work: check config/settings.json)")
        d.sim._drain()

    def summary(d: DemoRunner) -> None:
        d.speak("What happened while I was out?")

    return [
        Step(1, "Owner leaves", ["Speech -> Bonsai -> set_mode away", "LED blue, \"Away mode on\""],
             leaving),
        Step(2, "Doorbell", ["Photo -> description -> Bonsai greets, offers a message"], doorbell, 6.0),
        Step(3, "Visitor leaves a message", ["fixed greeting at the ring -> visitor speaks -> save_message -> dashboard"],
             message),
        Step(4, "Night, motion", ["Porch light on, photo, warning (yellow)"], night),
        Step(5, "Rule by voice, motion again", ["add_rule -> both lights -> ALARM (red)",
                                                "Bonsai's one confrontation line"], rule),
        Step(6, "Someone tries to disarm", ["\"I can't disarm. Only the owner can do that.\""],
             disarm),
        Step(7, "Owner's passphrase", ["Checked in plain code -> Home (green), alert normal"],
             passphrase),
        Step(8, "What happened?", ["Spoken summary + dashboard card"], summary, 2.0),
    ]


def play(runner: DemoRunner) -> List[str]:
    """Run every step. Returns notes about anything that needed a hand."""
    say = runner.say
    say("\n" + "=" * 64 + "\n  BACKUP DEMO: simulated sensors, real Faraday software\n" + "=" * 64)
    for step in steps():
        say(f"\n{'-' * 64}\n  STEP {step.number}/8  {step.title.upper()}")
        for line in step.lines:
            say(f"    {line}")
        if runner.manual:
            runner.wait_enter("    [Enter] to play this step ")
        step.run(runner)
        if step.number < 8:
            runner.pause(step.pause_after)
    runner.pipeline.wait_background(25)
    say(f"\n{'=' * 64}\n  DEMO DONE." + (" Notes: " + "; ".join(runner.notes) if runner.notes else "")
        + "\n" + "=" * 64)
    return runner.notes
