"""Hands-free voice: wake phrase, listening windows, dashboard hooks. No mic.

    python -m unittest integration.tests.test_voice
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from shared import Mode, validate_event

from hardware.audio import MicGate, Recorder
from hardware.flipper.fake import FakeFlipper
from hardware.settings import CONFIG_DIR
from hardware.tests.test_listen import clip
from hardware.tests.test_speech import FakeStream
from integration.app import App
from integration.standins import StandInCamera
from integration.tests.test_app import wait_until
from integration.voice import Voice, strip_wake
from log import EventLog
from rules import RuleStore


class FakeSTT:
    available = True

    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []

    def transcribe(self, wav):
        self.calls.append(Path(wav))
        return self.texts.pop(0) if self.texts else None


class Clips:
    """Stream factory: plays the given clips in order, then quiet ones."""

    def __init__(self, *paths, quiet):
        self.paths, self.quiet, self.opened = list(paths), quiet, 0

    def __call__(self, **kw):
        self.opened += 1
        path = self.paths.pop(0) if self.paths else self.quiet
        return FakeStream(path, **kw)


class WakePhrase(unittest.TestCase):
    def test_strip_wake(self):
        for text, want in [
            ("Hey Faraday, I'm heading out.", "I'm heading out."),
            ("hey faraday turn on the porch light", "turn on the porch light"),
            ("HEY, FARADAY! What happened while I was out?", "What happened while I was out?"),
            ("Hey Faraday", ""),
            ("Hey Faraday.", ""),
            ("Faraday is great.", None),                 # plain "Faraday": nothing
            ("Faraday, disarm", None),
            ("I said hey Faraday earlier", None),        # not at the start
            ("Hey Faradays are cool", None),
            ("", None),
        ]:
            with self.subTest(text=text):
                self.assertEqual(strip_wake(text), want)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-voice-"))
        self.quiet = clip(("quiet", 2.5), path=self.dir / "quiet.wav")
        self.speech = clip(("quiet", 0.3), ("speech", 1.2), ("quiet", 1.5), path=self.dir / "speech.wav")
        self.got = []
        self.said = []
        self.gate = MicGate()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def voice(self, stt, *clips, wake=None):
        self.clips = Clips(*clips, quiet=self.quiet)
        rec = Recorder(self.gate, audio_dir=self.dir / "out", stream_factory=self.clips)
        return Voice(rec, stt, self.got.append, out=self.said.append,
                     listen={"max_s": 6, "silence_s": 0.8, "no_speech_s": 1.5},
                     wake={"enabled": True, "window_s": 3, **(wake or {})})


class Windows(Base):
    def test_listen_once_gives_speech_heard(self):
        v = self.voice(FakeSTT("Package for Sam, I left it by the side door."), self.speech)
        self.assertTrue(v.listen_once("button"))
        self.assertTrue(wait_until(lambda: len(self.got) == 1, timeout=10))
        ev = self.got[0]
        self.assertEqual(validate_event(ev.to_dict()), [])
        self.assertEqual((ev.type, ev.source, ev.data["text"]),
                         ("speech_heard", "speech", "Package for Sam, I left it by the side door."))
        self.assertAlmostEqual(ev.data["duration_s"], 2.3, delta=0.4)
        self.assertFalse(any("Package" in s for s in self.said))        # raw text never printed

    def test_question_opens_a_window_statement_does_not(self):
        v = self.voice(FakeSTT("Yes please."), self.speech)
        v.on_said("Night mode on. Sleep well.")
        time.sleep(0.3)
        self.assertEqual(v.windows_opened, 0)
        v.on_said("Would you like to leave a message?")
        self.assertTrue(wait_until(lambda: len(self.got) == 1, timeout=10))
        self.assertEqual(v.windows_opened, 1)

    def test_doorbell_opens_window_after_the_greeting(self):
        v = self.voice(FakeSTT("Hello?"), self.speech)
        v.on_doorbell()
        time.sleep(0.2)
        self.assertEqual(v.windows_opened, 0)              # waits for the greeting
        v.on_said("Hello, I'm Faraday.")                   # no "?" but a doorbell is pending
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.assertTrue(wait_until(lambda: len(self.got) == 1, timeout=10))

    def test_window_waits_while_faraday_talks(self):
        v = self.voice(FakeSTT("Hi"), self.speech)
        self.gate.mute()
        self.assertTrue(v.listen_once("button"))
        time.sleep(0.4)
        self.assertEqual(v.windows_opened, 0)              # muted: not yet
        self.gate.unmute()
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.assertTrue(wait_until(lambda: len(self.got) == 1, timeout=10))

    def test_nobody_spoke_sends_nothing(self):
        v = self.voice(FakeSTT("should not be called"), self.quiet)
        v.listen_once("button")
        self.assertTrue(wait_until(lambda: any("nobody spoke" in s for s in self.said), timeout=10))
        self.assertEqual(self.got, [])

    def test_real_stt_client_over_http(self):
        """Voice + the real SpeechToText client against a fake Argmax server."""
        from hardware.speech import SpeechToText
        from hardware.tests.test_speech import FakeArgmax
        srv = FakeArgmax("Hi, I have a package for Sam.")
        try:
            v = self.voice(SpeechToText(srv.url, model="large-v3"), self.speech)
            v.listen_once("button")
            self.assertTrue(wait_until(lambda: len(self.got) == 1, timeout=10))
        finally:
            srv.close()
        self.assertEqual(self.got[0].data["text"], "Hi, I have a package for Sam.")
        self.assertIn(b'name="model"', srv.uploads[0]["body"])

    def held_voice(self, wake=None):
        """A Voice whose mic stream never ends by itself (until cancel)."""
        class Held(FakeStream):
            def start(self):
                pass
        quiet = self.quiet
        rec = Recorder(self.gate, audio_dir=self.dir / "out",
                       stream_factory=lambda **kw: Held(quiet, **kw))
        return Voice(rec, FakeSTT(), self.got.append, out=self.said.append,
                     listen={"max_s": 6, "silence_s": 0.8, "no_speech_s": 1.5},
                     wake={"enabled": True, "window_s": 3, **(wake or {})})

    def test_listening_is_true_only_while_the_window_records(self):
        """board.listening_hook (Dell's mic indicator) reads Voice.listening."""
        v = self.held_voice()
        self.assertFalse(v.listening)
        self.gate.mute()                                   # Faraday talking: window waits
        self.assertTrue(v.listen_once("button"))
        time.sleep(0.3)
        self.assertFalse(v.listening)                      # asked for, but the mic isn't open
        self.gate.unmute()
        self.assertTrue(wait_until(lambda: v.listening))
        self.gate.mute()
        self.assertFalse(v.listening)                      # muted mid-window
        self.gate.unmute()
        v.recorder.cancel()
        self.assertTrue(wait_until(lambda: not v.listening))
        self.assertTrue(wait_until(lambda: any("nobody spoke" in s for s in self.said)))

    def test_wake_window_is_not_listening(self):
        """The wake listener records all the time on the Mac: that isn't 'listening'."""
        v = self.held_voice()
        v.start_wake()
        try:
            self.assertTrue(wait_until(lambda: v.recorder.recording))
            self.assertFalse(v.listening)
            self.assertTrue(v.listen_once("button"))       # the visitor window takes over
            self.assertTrue(wait_until(lambda: v.listening))
        finally:
            v.recorder.cancel()
            v.stop_wake()

    def test_second_listen_refused_while_open(self):
        v = self.voice(FakeSTT("a", "b"), self.speech, self.speech)
        self.assertTrue(v.listen_once("button"))
        self.assertFalse(v.listen_once("button"))
        self.assertTrue(wait_until(lambda: len(self.got) == 1, timeout=10))


class Wake(Base):
    def test_hey_faraday_submits_the_request(self):
        stt = FakeSTT("Hey Faraday, I'm heading out.")
        v = self.voice(stt, self.speech)
        self.assertTrue(v.start_wake())
        try:
            self.assertTrue(wait_until(lambda: len(self.got) == 1, timeout=10))
        finally:
            v.stop_wake()
        ev = self.got[0]
        self.assertEqual((ev.type, ev.source, ev.data["text"]), ("speech_heard", "speech", "I'm heading out."))
        self.assertEqual(v.wake_heard, 1)

    def test_plain_faraday_is_dropped_and_audio_deleted(self):
        stt = FakeSTT("Faraday is a great name for a project.")
        v = self.voice(stt, self.speech)
        v.start_wake()
        try:
            self.assertTrue(wait_until(lambda: v.wake_ignored == 1, timeout=10))
            time.sleep(0.3)
        finally:
            v.stop_wake()
        self.assertEqual(self.got, [])
        self.assertFalse(stt.calls[0].exists())            # nothing kept

    def test_phrase_alone_opens_a_window_for_the_request(self):
        stt = FakeSTT("Hey Faraday.", "Turn on the porch light.")
        v = self.voice(stt, self.speech, self.speech)
        v.start_wake()
        try:
            self.assertTrue(wait_until(lambda: len(self.got) == 1, timeout=15))
        finally:
            v.stop_wake()
        self.assertEqual(self.got[0].data["text"], "Turn on the porch light.")
        self.assertTrue(any("yes?" in s for s in self.said))

    def test_wake_off_without_stt(self):
        stt = FakeSTT()
        stt.available = False
        v = self.voice(stt)
        self.assertFalse(v.start_wake())
        self.assertEqual(self.clips.opened, 0)

    def test_wake_yields_to_a_visitor_window(self):
        """A visitor window asked for during a (silent) wake window cancels it at
        once and gets the mic, instead of waiting for the wake window to time out."""
        stt = FakeSTT("Package for Sam.")
        speech = self.speech

        class Waiting(FakeStream):                     # a wake window where nobody talks yet
            def start(self):
                pass

        streams = iter([lambda **kw: Waiting(self.quiet, **kw), lambda **kw: FakeStream(speech, **kw)])
        quiet = self.quiet

        def factory(**kw):
            return next(streams, lambda **k: FakeStream(quiet, **k))(**kw)

        rec = Recorder(self.gate, audio_dir=self.dir / "out", stream_factory=factory)
        v = Voice(rec, stt, self.got.append, out=self.said.append,
                  listen={"max_s": 6, "silence_s": 0.8, "no_speech_s": 1.5},
                  wake={"enabled": True, "window_s": 3})
        v.start_wake()
        try:
            self.assertTrue(wait_until(lambda: rec.recording))           # wake window open
            t0 = time.monotonic()
            self.assertTrue(v.listen_once("button"))
            self.assertTrue(wait_until(lambda: len(self.got) == 1, timeout=15))
            self.assertLess(time.monotonic() - t0, 3.0)                  # didn't wait out 3 s + 2 s
        finally:
            v.stop_wake()
        self.assertEqual(self.got[0].data["text"], "Package for Sam.")

    def test_request_after_phrase_is_not_eaten_by_the_next_wake_window(self):
        """Owner: "Hey Faraday." ... "Turn on the porch light." The second clip
        must reach the request window, not a wake window that drops it."""
        stt = FakeSTT("Hey Faraday.", "Turn on the porch light.")
        v = self.voice(stt, self.speech, self.speech)
        v.start_wake()
        try:
            self.assertTrue(wait_until(lambda: len(self.got) == 1, timeout=15))
        finally:
            v.stop_wake()
        self.assertEqual(self.got[0].data["text"], "Turn on the porch light.")
        self.assertEqual(v.wake_ignored, 0)


class Hooks(unittest.TestCase):
    """control_hook / listen_hook wiring through the App, on the fake Flipper."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="faraday-hooks-"))
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0)
        self.said = []
        self.fl = FakeFlipper(s, charge_poll_s=0)
        self.app = App(s, self.fl, camera=StandInCamera(), use_llm=False, mode=Mode.HOME,
                       event_log=EventLog(self.tmp / "events.jsonl", s["security"]["passphrase"]),
                       rules=RuleStore(self.tmp / "rules.json"), dashboard=True, port=0,
                       out=self.said.append)
        self.app.board.control_hook = self.app.control
        self.app.start()
        self.assertTrue(self.fl.connected.wait(5))

    def tearDown(self):
        self.app.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_control_sends_once_logs_and_updates_lights(self):
        self.assertTrue(self.app.control("porch_light", True))
        self.assertTrue(wait_until(lambda: self.fl.device.tx == ["outlet1_on"]))
        self.assertFalse(self.app.control("porch_light", True))      # skip-repeat
        self.assertTrue(self.app.control("porch_light", False))
        self.assertTrue(wait_until(lambda: self.fl.device.tx == ["outlet1_on", "outlet1_off"]))
        acts = [e for e in self.app.pipeline.log.recent(50) if e["kind"] == "action"]
        self.assertEqual([(a["type"], a["source"]) for a in acts],
                         [("outlet_on", "dashboard"), ("outlet_off", "dashboard")])
        import urllib.request
        with urllib.request.urlopen(self.app.board.url + "api/state", timeout=5) as r:
            lights = {l["id"]: l["on"] for l in json.loads(r.read())["lights"]}
        self.assertFalse(lights["porch_light"])

    def test_unknown_outlet_refused(self):
        self.assertFalse(self.app.control("garage", True))
        self.assertEqual(self.fl.device.tx, [])

    def test_pipeline_outlet_then_dashboard_same_state_is_skipped(self):
        self.app.pipeline.owner_set_mode(Mode.NIGHT)
        self.fl.device.wave(packets=4, gap_s=0.05)
        self.assertTrue(wait_until(lambda: "outlet1_on" in self.fl.device.tx))
        self.assertFalse(self.app.control("porch_light", True))       # already on


class BuiltApp(unittest.TestCase):
    """integration/__main__.build_app wires Dell's dashboard hooks (PLAN Q26)."""

    def test_listening_hook_drives_api_state(self):
        import argparse
        import urllib.request
        from integration.__main__ import build_app
        tmp = Path(tempfile.mkdtemp(prefix="faraday-built-"))
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0)
        args = argparse.Namespace(fake=True, log=str(tmp / "events.jsonl"), standin_camera=True,
                                  print_speech=True, offline=True, mode=Mode.HOME,
                                  no_dashboard=False, port=0, no_mic=False, no_wake=True,
                                  no_controls=True, controls_port=None)
        app = build_app(args, s)
        board = app.board
        self.assertIsNotNone(board.listening_hook)
        self.assertIsNotNone(board.listen_hook)
        self.assertIs(board.control_hook.__func__, App.control)

        class Held(FakeStream):
            def start(self):
                pass
        quiet = clip(("quiet", 1.0), path=tmp / "quiet.wav")
        app.voice.recorder.stream_factory = lambda **kw: Held(quiet, **kw)
        app.voice.recorder.audio_dir = tmp / "out"

        def listening():
            with urllib.request.urlopen(board.url + "api/state", timeout=5) as r:
                return json.loads(r.read())["listening"]

        app.start()
        try:
            self.assertIs(listening(), False)              # not null: the hook is set
            self.assertTrue(app.voice.listen_once("button"))
            self.assertTrue(wait_until(lambda: listening() is True))
            app.voice.recorder.cancel()
            self.assertTrue(wait_until(lambda: listening() is False))
        finally:
            app.stop()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
