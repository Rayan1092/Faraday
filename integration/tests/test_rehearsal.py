"""Full demo rehearsal: the whole demo, end to end, on the real wiring.

    python -m unittest integration.tests.test_rehearsal

It runs integration/__main__.build_app (the same wiring as on the Mac) with
Dell's real pipeline (modes, rules, brain offline, log, dashboard) and only the
hardware swapped for stand-ins:
- the fake Flipper (the real driver code over a fake serial port),
- a camera capture that serves sample photos (the real CameraFeed, PhotoCamera,
  object detector and alarm recording), vision answered by a fake LM Studio,
- a live fake mic (the real Recorder, Voice, wake phrase, windows) that "says"
  each line as a tone of its own pitch, and an STT that knows the lines by pitch,
- a speaker that records what Faraday says (the real mute-while-talking gate).

The demo, in order: start (all lights off) -> "Hey Faraday, I'm heading out"
(Away) -> doorbell, greeting, the visitor answers -> walk-up then ring (no
warning) -> motion (warning after the grace period) -> motion again (alarm:
lights, LED, buzz, recording) -> "Hey Faraday, <passphrase>" (disarm, lights
off) -> "Hey Faraday, what happened while I was out?" (summary) -> "Hey Faraday,
who's at the door?" (photo + description). Then: every log entry is valid, no
part logged an error, the dashboard answers.

Run it after every merge (NV's and Dell's): it is part of the full suite.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf

from shared import Mode, validate_action, validate_event

from hardware.audio import MicGate
from hardware.flipper.fake import FakeFlipper
from hardware.settings import CONFIG_DIR
from hardware.speech import TextToSpeech
from hardware.tests.test_camera import FakeLMStudio, isolate_vision_cache, make_scene
from hardware.tests.test_listen import LiveMic

RATE = 16000
DESCRIPTION = "One person in a blue jacket holds a box at the door. Their face is visible."


def tone(freq: float, seconds: float, path: Path, tail_s: float = 2.0) -> Path:
    """A "spoken line": a tone at its own pitch, then quiet."""
    t = np.arange(int(RATE * seconds)) / RATE
    voice = 0.3 * np.sin(2 * np.pi * freq * t) * (1 + 0.3 * np.sin(2 * np.pi * 3 * t))
    quiet = np.random.default_rng(int(freq)).normal(0, 0.002, int(RATE * tail_s))
    sf.write(str(path), (np.concatenate([voice, quiet]) * 32767).astype(np.int16), RATE,
             subtype="PCM_16")
    return path


class ToneSTT:
    """Knows each line by its pitch (the strongest frequency in the recording)."""
    available = True

    def __init__(self, lines):
        self.lines = dict(lines)            # freq -> text
        self.heard = []

    def transcribe(self, wav):
        data, rate = sf.read(str(wav), dtype="float32")
        spectrum = np.abs(np.fft.rfft(data))
        peak = np.fft.rfftfreq(len(data), 1.0 / rate)[int(np.argmax(spectrum[1:])) + 1]
        freq = min(self.lines, key=lambda f: abs(f - peak))
        if abs(freq - peak) > 25:
            return None
        self.heard.append(self.lines[freq])
        return self.lines[freq]


class RecordingTTS(TextToSpeech):
    """Faraday's voice: records each line; the base class mutes the mic around it."""

    def __init__(self, gate, said):
        super().__init__(gate)
        self.said = said

    def _speak(self, text):
        self.said.append(text)
        time.sleep(0.05)
        return True


class SampleCapture:
    """cv2.VideoCapture stand-in serving sample photos (a door scene with a box)."""

    def __init__(self, frames):
        self.frames, self.n = frames, 0

    def isOpened(self):
        return True

    def read(self):
        self.n += 1
        time.sleep(0.03)
        return True, self.frames[self.n % len(self.frames)].copy()

    def release(self):
        pass


class Errors(logging.Handler):
    """Collects ERROR records (and exceptions) from every part during the run."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(f"{record.name}: {record.getMessage()}")


def wait_until(cond, timeout=15.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


class Rehearsal(unittest.TestCase):
    def setUp(self):
        from integration.__main__ import build_app
        isolate_vision_cache(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="faraday-rehearsal-"))
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        self.passphrase = s["security"]["passphrase"]
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lm = FakeLMStudio(content=DESCRIPTION)
        self.addCleanup(self.lm.close)
        for prof in s["llm"]["profiles"].values():
            prof["base_url"] = self.lm.url
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0, led_settle_s=0.3)
        # grace 2.0 s, not 1.0: headroom when the machine is busy (Dell PLAN Q40; the grace counts
        # from the motion's first packet, and the walk-up ring comes after the porch light send)
        s["alerts"] = dict(s.get("alerts", {}), motion_grace_s=2.0, min_gap_s=0.4, visitor_quiet_s=2.0)
        # no_speech_s 6.0 = 1.5 s of wall time at the mic's 4x speed: room for a busy machine
        s["audio"]["listen"] = {"max_s": 15, "silence_s": 1.0, "no_speech_s": 6.0, "after_speech_s": 0.2}
        s["audio"]["wake"] = {"enabled": True, "phrase": "hey faraday", "silence_s": 0.6, "max_s": 10}
        s["audio"]["voice_check"] = {"enrollment": str(self.tmp / "no-enrollment.wav")}
        s["camera"].update(alarm_record_s=1.0, idle_s=30, stream_fps=20)
        s["paths"] = {"captures": str(self.tmp / "captures"), "logs": str(self.tmp / "logs")}
        s["dashboard"] = dict(s.get("dashboard", {}), port=0)
        s["integration"] = {"lights_off_at_start": True}
        for key in ("mac", "windows"):            # no real diarize/TTS command on this machine
            s.get("stt", {}).get(key, {}).pop("diarize", None)
        self.settings = s

        scene = cv2.imread(str(make_scene(self.tmp / "door.jpg")))
        self.said, self.chimes = [], []
        self.lines = {300: "Hey Faraday, I'm heading out.",
                      420: "Hi, I have a package for Sam.",
                      540: f"Hey Faraday, {self.passphrase}",
                      660: "Hey Faraday, what happened while I was out?",
                      780: "Hey Faraday, who's at the door?"}
        self.stt = ToneSTT(self.lines)
        self.mic = LiveMic(speed=4.0)
        self.fl = FakeFlipper(s, charge_poll_s=0)
        # The lamps were left on by an earlier session. The startup "off" turns each
        # of them off (the fake toggles on every code, like the real outlets may).
        self.fl.device.outlet_lit = {1: True, 2: True, 3: True}
        args = argparse.Namespace(log=str(self.tmp / "logs" / "events.jsonl"), offline=True,
                                  mode=Mode.HOME, no_dashboard=False, port=0, no_mic=False,
                                  no_wake=False, no_controls=False, controls_port=0,
                                  no_detect=False, fresh=False)
        (self.tmp / "logs").mkdir(parents=True)
        self.errors = Errors()
        logging.getLogger().addHandler(self.errors)
        self.addCleanup(logging.getLogger().removeHandler, self.errors)
        self.thread_errors = []
        old_hook = threading.excepthook
        threading.excepthook = lambda a: self.thread_errors.append(f"{a.thread.name}: {a.exc_value!r}")
        self.addCleanup(setattr, threading, "excepthook", old_hook)
        self.out = []
        self.app = build_app(args, s, parts={
            "flipper": self.fl, "capture_factory": lambda *a: SampleCapture([scene]),
            "speaker": lambda gate: RecordingTTS(gate, self.said),
            "stream_factory": self.mic, "stt": self.stt, "chime": lambda: self.chimes.append(1)})
        self.app.out = self.out.append       # Dell's pipeline prints through it too
        self.addCleanup(self.app.stop)

    def tearDown(self):
        import os
        dump = os.environ.get("FARADAY_REHEARSAL_DUMP")      # debugging: the whole console
        if dump:
            Path(dump).write_text("\n".join(self.out + ["--- said ---"] + self.said), encoding="utf-8")

    # ---- helpers ----------------------------------------------------------------------
    def log(self):
        return self.app.pipeline.log.recent(2000)

    def events(self, typ):
        return [e for e in self.log() if e["kind"] == "event" and e["type"] == typ]

    def speak(self, freq, seconds=1.2):
        self.mic.say(tone(freq, seconds, self.tmp / f"line{freq}-{time.monotonic_ns()}.wav"))

    def quiet(self):
        """Faraday has finished talking, nothing is queued, no photo is in progress."""
        self.assertTrue(wait_until(lambda: not self.app.speech_busy()
                                   and self.app._photos.unfinished_tasks == 0
                                   and self.app.voice.recorder.gate.quiet_for(0.3), 20),
                        f"Faraday never went quiet\nconsole: {self.out[-15:]}")

    def step(self, name, cond, timeout=15.0):
        self.assertTrue(wait_until(cond, timeout), f"rehearsal step failed: {name}\n"
                        f"said: {self.said[-8:]}\nconsole: {self.out[-15:]}\n"
                        f"events: {[e['type'] for e in self.log()[-15:]]}")

    # ---- the demo ---------------------------------------------------------------------
    def test_full_demo(self):
        app, fl, modes = self.app, self.fl, self.app.pipeline.modes
        app.start()
        app.voice.start_wake()
        self.step("flipper connected", fl.connected.is_set, 5)
        self.step("all lights off at start", lambda: fl.device.tx[:3] == ["outlet1_off", "outlet2_off", "outlet3_off"])
        self.step("the lamps really are off", lambda: not any(fl.device.outlet_lit.values()))
        self.quiet()

        # 1. The owner leaves: "Hey Faraday, I'm heading out." -> Away
        self.speak(300)
        self.step("owner request arms Away", lambda: modes.mode == Mode.AWAY)
        owner = [e for e in self.events("speech_heard") if e["data"].get("channel") == "owner"]
        self.assertTrue(owner, "the owner's request has channel owner")
        self.quiet()

        # 2. A visitor rings: fixed greeting, photo, the window opens after the greeting
        from rules.baseline import ALARM_LINE, DOORBELL_GREETING, WARNING_LINE
        fl.device.press_doorbell()
        self.step("doorbell greeting spoken", lambda: DOORBELL_GREETING in self.said)
        self.step("visitor window opens", lambda: any("listening (doorbell)" in s for s in self.out))
        self.speak(420)                                                    # "Hi, I have a package for Sam."
        self.step("doorbell photo described", lambda: any(
            e["data"].get("described_by") for e in self.events("photo_described")))
        photo = self.events("photo_described")[-1]
        self.assertEqual(photo["data"]["description"], DESCRIPTION)
        self.assertIn("detections", photo["data"])                       # the detector ran
        self.step("visitor reply heard", lambda: any(
            e["data"].get("channel") == "visitor" for e in self.events("speech_heard")))
        visitor = [e for e in self.events("speech_heard") if e["data"].get("channel") == "visitor"][-1]
        self.step("Faraday answers the visitor", lambda: any(
            e["kind"] == "action" and e["type"] == "speak" and e.get("caused_by") == visitor["id"]
            for e in self.log()))
        self.quiet()

        # 3. Walk-up then ring: motion, then the doorbell within the grace period -> no warning
        time.sleep(2.2)                                                    # past visitor_quiet_s
        n_alerts = len(self.events("alert_changed"))
        n_motion, n_bells, n_tx = len(self.events("motion")), len(self.events("doorbell")), len(fl.device.tx)
        fl.device.wave(packets=4, gap_s=0.05)
        self.step("walk-up motion arrives", lambda: len(self.events("motion")) > n_motion)
        # the porch light goes on (rx pauses for the send); ring once the radio listens again
        self.step("porch light sent, radio back", lambda: "outlet1_on" in fl.device.tx[n_tx:]
                  and fl.device.rx and fl._cmds.empty())
        n_windows = sum("listening (doorbell)" in s for s in self.out)
        fl.device.press_doorbell()
        self.step("the ring arrives", lambda: len(self.events("doorbell")) > n_bells)
        # a repeat ring gets no greeting: the visitor's window still opens within ~2 s
        self.step("window after a ring with no greeting", lambda: sum(
            "listening (doorbell)" in s for s in self.out) > n_windows, 5)
        time.sleep(2.0 + 1.3)                                             # grace + settle
        self.assertEqual(len(self.events("alert_changed")), n_alerts, "a visitor who walks up and rings is never warned")
        self.assertEqual(modes.alert, "normal")
        self.quiet()

        # 4. Motion alone -> warning after the grace period; motion again -> alarm
        time.sleep(2.2)                                                    # past visitor_quiet_s again
        fl.device.wave(packets=4, gap_s=0.05)
        self.step("warning after the grace period", lambda: modes.alert == "warning")
        self.step("warning line spoken", lambda: WARNING_LINE in self.said)
        time.sleep(0.6)                                                    # past min_gap_s
        fl.device.wave(packets=4, gap_s=0.05)
        self.step("alarm", lambda: modes.alert == "alarm")
        self.step("alarm line spoken", lambda: ALARM_LINE in self.said)
        self.step("both lights on", lambda: fl.device.outlet_lit[1] and fl.device.outlet_lit[2])
        self.step("LED red + buzz", lambda: fl.device.rgb == [255, 0, 0] and fl.device.vibro_count >= 3)
        self.step("alarm recording saved", lambda: self.events("recording_saved"), 20)
        rec = self.events("recording_saved")[-1]
        self.assertGreaterEqual(rec["data"]["frames"], 2)
        self.quiet()

        # 5. Someone rings during the alarm; the owner arrives and disarms with the passphrase
        #    while that visitor window is open (the wake phrase inside a visitor window)
        # the alarm's buzz pauses the radio for 2-3 s, and the fake doorbell is one packet
        # (a real ring holds ~1 s of them): ring once the radio listens again
        self.step("radio back after the buzz", lambda: fl.device.rx and fl._cmds.empty()
                  and fl._refresh_due is None)
        n_windows = sum("listening (doorbell)" in s for s in self.out)
        fl.device.press_doorbell()
        self.step("visitor window open", lambda: sum("listening (doorbell)" in s for s in self.out) > n_windows)
        self.speak(540, 1.6)
        self.step("passphrase disarms", lambda: modes.mode == Mode.HOME and modes.alert == "normal")
        disarm = [e for e in self.events("speech_heard") if "[passphrase]" in e["data"]["text"]][-1]
        # Away, within the visit after a ring (owner 2026-11-15): every word is the visitor's,
        # "Hey Faraday ..." too (nobody is home); modes/ checks the passphrase on any channel
        self.assertEqual(disarm["data"]["channel"], "visitor", "Away, within the visit: the visitor's words")
        self.assertTrue(any("[visit] the wake phrase during a visit in Away" in s for s in self.out),
                        "heard in the visitor window, within the visit")
        self.step("lights off after disarm", lambda: not fl.device.outlet_lit[1] and not fl.device.outlet_lit[2])
        self.step("LED off", lambda: fl.device.rgb == [0, 0, 0])
        self.assertFalse(any(self.passphrase in json.dumps(e) for e in self.log()),
                         "the passphrase never reaches the log")
        self.quiet()

        # 6. "Hey Faraday, what happened while I was out?" -> a spoken summary
        n_said = len(self.said)
        self.speak(660, 1.4)
        self.step("summary request heard", lambda: any(
            "what happened" in e["data"]["text"].lower() for e in self.events("speech_heard")))
        ask = [e for e in self.events("speech_heard") if "what happened" in e["data"]["text"].lower()][-1]
        def summary_spoken():
            entries = self.log()
            summaries = {e["id"] for e in entries if e["kind"] == "action" and e["type"] == "summarize_log"
                         and e.get("caused_by") == ask["id"]}
            return any(e["kind"] == "action" and e["type"] == "speak" and e.get("caused_by") in summaries
                       for e in entries)
        self.step("summary spoken", summary_spoken)
        self.assertGreater(len(self.said), n_said)
        self.quiet()

        # 7. "Hey Faraday, who's at the door?" -> fresh photo, the answer from facts only
        #    (owner, Mac test: spoken lines never describe the person; the description
        #    stays on the dashboard)
        n_photos, n_said = len(self.events("photo_described")), len(self.said)
        self.speak(780, 1.4)
        self.step("look photo taken", lambda: len(self.events("photo_described")) > n_photos)
        self.step("answer spoken", lambda: any("at the front door" in line.lower()
                                               for line in self.said[n_said:]))
        self.assertFalse(any("box" in line.lower() or "jacket" in line.lower()
                             for line in self.said[n_said:]), "the description must not be spoken")

        # ---- the seams: valid log, no errors anywhere, dashboard answers -----------------
        for entry in self.log():
            problems = validate_event(entry) if entry["kind"] == "event" else validate_action(entry)
            self.assertEqual(problems, [], f"invalid log entry: {entry}")
        with urllib.request.urlopen(app.board.url + "api/state", timeout=5) as r:
            state = json.loads(r.read())
        self.assertIn("listening", state)
        import re
        failures = [s for s in self.out if re.search(r"\bfailed\b|crashed|traceback", s, re.I)]
        self.assertEqual(failures, [], "a part reported a failure")
        self.assertEqual(self.thread_errors, [], "a thread crashed")
        self.assertEqual(self.errors.records, [], "a part logged an error")


if __name__ == "__main__":
    unittest.main()
