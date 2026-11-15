"""Hands-free voice: continuous wake listening, windows, owner/visitor channel. No mic.

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
from hardware.tests.test_listen import LiveMic, clip
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
    """A Voice on one fake live microphone (LiveMic, 4x real time)."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-voice-"))
        self.speech = clip(("quiet", 0.3), ("speech", 1.2), ("quiet", 2.5), path=self.dir / "speech.wav")
        self.got = []
        self.said = []
        self.chimes = []
        self.gate = MicGate()
        self.mic = LiveMic()
        self.busy = False
        self.v = None

    def tearDown(self):
        if self.v is not None:
            self.v.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def voice(self, stt, wake=None, listen=None, **kw):
        rec = Recorder(self.gate, audio_dir=self.dir / "out", stream_factory=self.mic)
        self.v = Voice(rec, stt, self.got.append, out=self.said.append,
                       listen=listen or {"max_s": 15, "silence_s": 1.8, "no_speech_s": 2.0,
                                         "after_speech_s": 0.3},
                       wake={"enabled": True, "silence_s": 0.7, **(wake or {})},
                       busy=lambda: self.busy,
                       chime=lambda: self.chimes.append(self.v is not None and self.v.recorder.window_open),
                       **kw)
        return self.v

    def say(self, *parts):
        self.mic.say(clip(*parts, path=self.dir / f"c{len(self.said)}{time.monotonic_ns()}.wav"))

    def events(self, n=1, timeout=15):
        self.assertTrue(wait_until(lambda: len(self.got) >= n, timeout=timeout), self.said)
        return self.got


class Windows(Base):
    def test_window_gives_speech_heard_with_visitor_channel(self):
        v = self.voice(FakeSTT("Package for Sam, I left it by the side door."))
        self.assertTrue(v.listen_once("doorbell"))
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.say(("speech", 1.2), ("quiet", 2.5))
        ev = self.events()[0]
        self.assertEqual(validate_event(ev.to_dict()), [])
        self.assertEqual((ev.type, ev.source, ev.data["text"], ev.data["channel"]),
                         ("speech_heard", "speech", "Package for Sam, I left it by the side door.", "visitor"))
        self.assertFalse(any("Package" in s for s in self.said))        # raw text never printed

    def test_window_ends_silence_s_after_the_speaker_stops_not_before(self):
        """Owner: 0.8 s cut people off mid-sentence. 1.8 s now."""
        v = self.voice(FakeSTT("Hi, I ... have a package."))
        v.listen_once("doorbell")
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.say(("speech", 0.8), ("quiet", 1.2), ("speech", 0.8), ("quiet", 3.0))   # a 1.2 s pause
        ev = self.events()[0]
        self.assertGreater(ev.data["duration_s"], 0.8 + 1.2 + 0.8)       # both halves, one line

    def test_question_opens_a_window_statement_does_not(self):
        v = self.voice(FakeSTT("Yes please."))
        v.on_said("Night mode on. Sleep well.")
        time.sleep(0.5)
        self.assertEqual(v.windows_opened, 0)
        v.on_said("Would you like to leave a message?")
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.say(("speech", 1.0), ("quiet", 2.5))
        self.events()

    def test_doorbell_opens_window_after_the_greeting(self):
        v = self.voice(FakeSTT("Hello?"))
        v.on_doorbell()
        time.sleep(0.3)
        self.assertEqual(v.windows_opened, 0)              # waits for the greeting
        v.on_said("Hello, I'm Faraday.")                   # no "?" but a doorbell is pending
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))

    def test_window_waits_until_faraday_has_finished_plus_a_moment(self):
        """Owner: it opened while Faraday was still talking. Now: not while a line
        is queued or being made (busy), and only after_speech_s after unmute."""
        v = self.voice(FakeSTT("Hi"))
        self.busy = True
        self.gate.mute()
        self.assertTrue(v.listen_once("question"))
        time.sleep(0.5)
        self.assertEqual(v.windows_opened, 0)
        self.gate.unmute()                                 # one line done, another coming
        time.sleep(0.5)
        self.assertEqual(v.windows_opened, 0)
        self.gate.mute()                                   # the second line
        self.busy = False
        time.sleep(0.3)
        self.assertEqual(v.windows_opened, 0)
        t0 = time.monotonic()
        self.gate.unmute()
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.assertGreaterEqual(time.monotonic() - t0, 0.25)           # after_speech_s

    def test_nobody_spoke_sends_nothing(self):
        v = self.voice(FakeSTT("should not be called"))
        v.listen_once("doorbell")
        self.assertTrue(wait_until(lambda: any("nobody spoke" in s for s in self.said), timeout=10))
        self.assertEqual(self.got, [])

    def test_second_request_refused_while_one_is_pending_or_open(self):
        v = self.voice(FakeSTT("a"))
        self.assertTrue(v.listen_once("doorbell"))
        self.assertFalse(v.listen_once("doorbell"))
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.assertFalse(v.listen_once("doorbell"))

    def test_listening_is_true_only_while_a_window_records(self):
        """board.listening_hook (Dell's mic indicator) reads Voice.listening."""
        v = self.voice(FakeSTT())
        self.assertFalse(v.listening)
        self.gate.mute()
        self.assertTrue(v.listen_once("doorbell"))
        time.sleep(0.3)
        self.assertFalse(v.listening)                      # asked for, but Faraday is talking
        self.gate.unmute()
        self.assertTrue(wait_until(lambda: v.listening))
        self.gate.mute()
        self.assertFalse(v.listening)                      # muted mid-window
        self.gate.unmute()
        v.recorder.cancel()
        self.assertTrue(wait_until(lambda: not v.listening))

    def test_wake_listening_is_not_listening(self):
        v = self.voice(FakeSTT())
        v.start_wake()
        self.assertTrue(wait_until(lambda: v.recorder.window_open))
        self.assertFalse(v.listening)

    def test_real_stt_client_over_http(self):
        """Voice + the real SpeechToText client against a fake Argmax server."""
        from hardware.speech import SpeechToText
        from hardware.tests.test_speech import FakeArgmax
        srv = FakeArgmax("Hi, I have a package for Sam.")
        try:
            v = self.voice(SpeechToText(srv.url, model="large-v3"))
            v.listen_once("doorbell")
            self.assertTrue(wait_until(lambda: v.windows_opened == 1))
            self.say(("speech", 1.0), ("quiet", 2.5))
            self.events()
        finally:
            srv.close()
        self.assertEqual(self.got[0].data["text"], "Hi, I have a package for Sam.")
        self.assertIn(b'name="model"', srv.uploads[0]["body"])


class Wake(Base):
    def test_continuous_one_stream_no_gaps(self):
        """Owner: 4 s chunks with gaps missed "Hey Faraday". Now one stream, cut
        at pauses: three phrases in a row all reach STT, on one stream."""
        stt = FakeSTT("Faraday is great.", "nothing", "Hey Faraday, I'm heading out.")
        v = self.voice(stt)
        v.start_wake()
        self.say(("speech", 0.8), ("quiet", 1.0), ("speech", 0.8), ("quiet", 1.0),
                 ("speech", 1.2), ("quiet", 1.5))
        ev = self.events()[0]
        self.assertEqual((ev.data["text"], ev.data["channel"]), ("I'm heading out.", "owner"))
        self.assertEqual(self.mic.streams, 1)
        self.assertEqual(len(stt.calls), 3)
        self.assertEqual((v.wake_heard, v.wake_ignored), (1, 2))
        self.assertEqual(self.chimes, [])                             # request in one breath

    def test_plain_faraday_is_dropped_and_audio_deleted(self):
        v = self.voice(FakeSTT("Faraday is great."))
        v.start_wake()
        self.say(("speech", 1.0), ("quiet", 1.5))
        self.assertTrue(wait_until(lambda: v.wake_ignored == 1, timeout=10))
        time.sleep(0.3)
        self.assertEqual(self.got, [])
        self.assertEqual(list((self.dir / "out").glob("*.wav")), [])

    def test_phrase_alone_chimes_and_the_next_words_are_the_request(self):
        stt = FakeSTT("Hey Faraday.", "Turn on the porch light.")
        v = self.voice(stt)
        v.start_wake()
        self.say(("speech", 0.8), ("quiet", 1.5))                     # "Hey Faraday" ... pause
        self.assertTrue(wait_until(lambda: self.chimes, timeout=10))
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))     # became the request window
        self.say(("speech", 1.5), ("quiet", 2.5))                     # the request
        ev = self.events()[0]
        self.assertEqual((ev.data["text"], ev.data["channel"]), ("Turn on the porch light.", "owner"))
        self.assertEqual(self.mic.streams, 1)
        self.assertEqual(v.wake_ignored, 0)

    def test_request_right_after_the_phrase_is_captured_whole(self):
        """"Hey Faraday." <short pause> "turn on the light": the request starts
        while the phrase is still at STT. It is kept, not cut and not re-chimed."""
        stt = FakeSTT("Hey Faraday.", "Turn on the porch light.")

        class SlowSTT(FakeSTT):
            def transcribe(self, wav):
                time.sleep(0.4)                                        # STT latency
                return super().transcribe(wav)
        stt = SlowSTT("Hey Faraday.", "Turn on the porch light.")
        v = self.voice(stt)
        v.start_wake()
        self.say(("speech", 0.8), ("quiet", 0.9), ("speech", 1.5), ("quiet", 2.5))
        ev = self.events()[0]
        self.assertEqual((ev.data["text"], ev.data["channel"]), ("Turn on the porch light.", "owner"))
        self.assertGreaterEqual(ev.data["duration_s"], 1.5)            # the whole sentence

    def test_owner_answer_to_faradays_question_is_owner(self):
        stt = FakeSTT("Hey Faraday, turn off the lamp.", "Yes, the living room one.")
        v = self.voice(stt)
        v.start_wake()
        self.say(("speech", 1.2), ("quiet", 1.5))
        owner_event = self.events()[0]
        # Faraday asks back: the speak action's caused_by is the owner's speech_heard
        v.on_said("Which lamp do you mean?", caused_by=owner_event.id)
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.say(("speech", 1.0), ("quiet", 2.5))
        ev = self.events(2)[1]
        self.assertEqual((ev.data["text"], ev.data["channel"]), ("Yes, the living room one.", "owner"))

    def test_question_not_caused_by_the_owner_is_for_the_visitor(self):
        """Reviewer: ring, the owner asks something, the greeting ends in "?" and the
        visitor's reply came out as owner. The greeting is for the visitor."""
        stt = FakeSTT("Hey Faraday, who's at the door?", "Turn off the porch light.")
        v = self.voice(stt)
        v.start_wake()
        v.on_doorbell()
        self.say(("speech", 1.2), ("quiet", 1.5))
        owner_event = self.events()[0]
        self.assertEqual(owner_event.data["channel"], "owner")
        v.on_said("Hello. Would you like to leave a message?", caused_by="evt_doorbell1")
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.say(("speech", 1.0), ("quiet", 2.5))
        ev = self.events(2)[1]
        self.assertEqual(ev.data["channel"], "visitor")

    def test_slow_stt_phrase_then_request_keeps_the_request(self):
        """Reviewer: with 2 s STT the request segment ended before the phrase was
        checked, was checked on its own, didn't match and was deleted."""
        class SlowSTT(FakeSTT):
            def transcribe(self, wav):
                time.sleep(2.0)
                return super().transcribe(wav)
        v = self.voice(SlowSTT("Hey Faraday.", "Turn on the porch light."))
        v.start_wake()
        self.say(("speech", 0.8), ("quiet", 0.9), ("speech", 1.5), ("quiet", 2.5))
        ev = self.events(timeout=20)[0]
        self.assertEqual((ev.data["text"], ev.data["channel"]), ("Turn on the porch light.", "owner"))
        self.assertEqual(v.wake_ignored, 0)
        self.assertEqual(self.chimes, [])                  # the owner had already gone on talking

    def test_long_idle_wake_segment_becomes_a_full_window(self):
        """Reviewer (HIGH): the backstop counted from when the wake segment opened, so
        after a long quiet wait a doorbell window closed at once ("nobody spoke")."""
        v = self.voice(FakeSTT("Package for Sam."),
                       listen={"max_s": 3, "silence_s": 1.0, "no_speech_s": 3.0, "after_speech_s": 0.3})
        v.start_wake()
        time.sleep(5.6)                                    # quiet room: limit 3 + 2 s of wall time
        self.assertTrue(v.listen_once("doorbell"))
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        # the old code closed the window on its first backstop check (0.1 s); the visitor
        # answers right away (3 s no-speech limit = 0.75 s of wall time at 4x)
        self.say(("speech", 1.0), ("quiet", 2.0))
        ev = self.events()[0]
        self.assertEqual((ev.data["text"], ev.data["channel"]), ("Package for Sam.", "visitor"))

    def test_speech_just_before_faraday_talks_is_not_the_reply(self):
        """Reviewer: audio from the moment before Faraday spoke started the next window
        as "already spoke", and that stale clip was sent as the visitor's reply."""
        self.mic = LiveMic(speed=1.0)                       # real time: the 0.5 s preroll matters
        v = self.voice(FakeSTT("stale words"))
        v.listen_once("doorbell")                           # opens the stream...
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        v.recorder.cancel()                                 # ...then idle: audio goes to the preroll
        self.assertTrue(wait_until(lambda: not v.recorder.window_open))
        self.say(("speech", 0.4))                           # someone, right before Faraday talks
        while not self.mic.idle:
            time.sleep(0.01)
        self.gate.mute()                                    # Faraday: "Would you like ...?"
        self.assertTrue(v.listen_once("question"))
        time.sleep(0.4)
        self.gate.unmute()
        self.assertTrue(wait_until(lambda: v.windows_opened == 2))
        # the question window must wait for a real answer: nobody answers, so it ends
        # with "nobody spoke" (the first, cancelled window already printed one)
        self.assertTrue(wait_until(lambda: sum("nobody spoke" in s for s in self.said) >= 2,
                                   timeout=10), self.said)
        time.sleep(0.3)
        self.assertEqual(self.got, [])

    def test_intercom_end_listens_for_the_visitor(self):
        v = self.voice(FakeSTT("Thanks, I'll wait."))
        v.on_intercom_end()
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.assertTrue(any("listening (intercom)" in s for s in self.said))
        self.say(("speech", 1.0), ("quiet", 2.5))
        self.assertEqual(self.events()[0].data["channel"], "visitor")

    def test_question_with_another_cause_is_for_the_visitor(self):
        """Review of #45: the caused_by rule without a doorbell in the way."""
        v = self.voice(FakeSTT("Just walking by."))
        v.on_said("Can I help you?", caused_by="evt_motion01")
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.say(("speech", 1.0), ("quiet", 2.5))
        self.assertEqual(self.events()[0].data["channel"], "visitor")

    def test_greeting_takes_over_a_silent_owner_window(self):
        """Review of #45: "Hey Faraday" alone, then the doorbell: the visitor's answer
        to the greeting was recorded in the owner window as owner."""
        v = self.voice(FakeSTT("Package for Sam."))
        self.assertTrue(v.listen_once("wake", purpose="owner"))
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        v.on_doorbell()
        self.gate.mute()                                    # the greeting
        time.sleep(0.2)
        self.gate.unmute()
        v.on_said("Hello. Would you like to leave a message?")
        self.assertFalse(v._pending_doorbell)
        self.assertTrue(any("listening (doorbell)" in s for s in self.said))
        self.say(("speech", 1.0), ("quiet", 2.5))
        self.assertEqual(self.events()[0].data["channel"], "visitor")

    def test_doorbell_waits_while_the_owner_is_talking(self):
        v = self.voice(FakeSTT("Turn on the lamp.", "Hello?"))
        self.assertTrue(v.listen_once("wake", purpose="owner"))
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.say(("speech", 2.0), ("quiet", 2.5))            # the owner is talking
        self.assertTrue(wait_until(lambda: v.recorder.window_state()["speech_started"]))
        v.on_doorbell()
        v.on_said("Hello, I'm Faraday.")
        self.assertTrue(v._pending_doorbell)                 # not taken from the owner
        first = self.events()[0]
        self.assertEqual(first.data["channel"], "owner")
        self.assertTrue(wait_until(lambda: any("listening (doorbell)" in s for s in self.said), timeout=8))

    def test_stale_phrase_does_not_make_later_speech_an_owner_request(self):
        """Review of #45: after "Hey Faraday." alone was answered by an owner window,
        a leftover flag sent the next unrelated speech as an owner request."""
        v = self.voice(FakeSTT("Hey Faraday.", "Turn on the porch light.", "Buy the new phone now."))
        v.start_wake()
        self.say(("speech", 0.8), ("quiet", 1.0))           # "Hey Faraday." ... then Faraday is busy
        self.assertTrue(wait_until(lambda: v.wake_heard == 1, timeout=10))
        self.assertTrue(wait_until(lambda: v.windows_opened == 1, timeout=10))
        self.say(("speech", 1.2), ("quiet", 2.5))            # the request
        self.events()
        self.say(("speech", 1.0), ("quiet", 1.5))            # later: no wake phrase
        self.assertTrue(wait_until(lambda: v.wake_ignored == 1, timeout=10), self.said)
        time.sleep(0.3)
        self.assertEqual([e.data["text"] for e in self.got], ["Turn on the porch light."])

    def test_phrase_said_twice_waits_for_the_real_request(self):
        """Review of #45: with slow STT, "Hey Faraday. ... Hey Faraday. ... turn on the
        porch light" sent "Hey Faraday." as the request and deleted the real one."""
        class SlowSTT(FakeSTT):
            def transcribe(self, wav):
                time.sleep(1.5)
                return super().transcribe(wav)
        v = self.voice(SlowSTT("Hey Faraday.", "Hey Faraday.", "Turn on the porch light."))
        v.start_wake()
        self.say(("speech", 0.8), ("quiet", 0.9), ("speech", 0.8), ("quiet", 0.9),
                 ("speech", 1.5), ("quiet", 2.5))
        ev = self.events(timeout=25)[0]
        self.assertEqual((ev.data["text"], ev.data["channel"]), ("Turn on the porch light.", "owner"))
        self.assertEqual(v.wake_ignored, 0)

    def test_chime_only_once_a_window_is_open(self):
        v = self.voice(FakeSTT("Hey Faraday.", "Lights on."))
        v.start_wake()
        self.say(("speech", 0.8), ("quiet", 1.5))
        self.assertTrue(wait_until(lambda: self.chimes, timeout=10))
        self.assertEqual(self.chimes, [True])

    def test_mute_in_the_middle_of_speech_keeps_what_was_said(self):
        """Review of #45: the mute reset threw away a visitor's sentence when the owner
        pressed the intercom mid-sentence. Now the window ends with what was said."""
        v = self.voice(FakeSTT("I have a package for"))
        v.listen_once("doorbell")
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.say(("speech", 2.0))
        self.assertTrue(wait_until(lambda: v.recorder.window_state()["speech_started"]))
        time.sleep(0.15)
        self.gate.mute()                                     # the owner presses the intercom
        ev = self.events()[0]
        self.gate.unmute()
        self.assertEqual(ev.data["text"], "I have a package for")
        self.assertGreater(ev.data["duration_s"], 0.4)

    def test_stop_ends_doorbell_retries(self):
        v = self.voice(FakeSTT())
        v.on_doorbell()
        v.stop()
        v._doorbell_fallback()                               # a timer firing during shutdown
        self.assertIsNone(v._doorbell_timer)
        self.assertEqual(v.windows_opened, 0)

    def test_visitor_window_takes_over_the_wake_segment_in_place(self):
        """A doorbell during wake listening: the open segment becomes the visitor
        window at once (no new stream), and no wake check runs on it."""
        stt = FakeSTT("Package for Sam.")
        v = self.voice(stt)
        v.start_wake()
        self.assertTrue(wait_until(lambda: v.recorder.window_open))
        v.on_doorbell()
        v.on_said("Hello, I'm Faraday.")
        self.assertTrue(wait_until(lambda: v.windows_opened == 1))
        self.say(("speech", 1.0), ("quiet", 2.5))
        ev = self.events()[0]
        self.assertEqual((ev.data["text"], ev.data["channel"]), ("Package for Sam.", "visitor"))
        self.assertEqual(self.mic.streams, 1)
        self.assertEqual((v.wake_heard, v.wake_ignored), (0, 0))

    def test_wake_off_without_stt(self):
        class NoSTT:
            available = False
        v = self.voice(NoSTT())
        self.assertFalse(v.start_wake())
        self.assertTrue(any("no STT" in s for s in self.said))

    def test_stop_closes_the_microphone(self):
        v = self.voice(FakeSTT())
        v.start_wake()
        self.assertTrue(wait_until(lambda: self.mic.streams == 1))
        v.stop()
        self.assertTrue(self.mic._stop.is_set())
        self.assertFalse(v.recorder.window_open)


class Hooks(unittest.TestCase):
    """control_hook wiring through the App, on the fake Flipper."""

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

    def test_on_said_failing_does_not_stop_the_speech_worker(self):
        class Broken:
            def on_said(self, text, caused_by=None):
                raise RuntimeError("boom")

            def stop(self):
                pass
        from shared import Action
        self.app.voice = Broken()
        for text in ("One.", "Two."):
            self.app._on_action(Action("speak", "rules", {"text": text}))
        self.assertTrue(wait_until(lambda: not self.app.speech_busy()))
        self.assertTrue(any("Two." in s for s in self.said))

    def test_only_a_who_is_at_the_door_photo_counts_as_busy(self):
        """Dell PR "doorbell flow" (PLAN Q32): the doorbell photo isn't talked about,
        so it must not hold the visitor's window; the dashboard's look photo is."""
        from shared import Action
        release = threading.Event()

        class SlowCamera:
            def take(self, action):
                release.wait(2.0)
                return None
        self.app.camera = SlowCamera()
        self.app._on_action(Action("take_photo", "rules", {"reason": "doorbell"}))
        time.sleep(0.2)
        self.assertFalse(self.app.speech_busy())                     # doorbell photo: not busy
        release.set()
        self.assertTrue(wait_until(lambda: self.app._photos.unfinished_tasks == 0))
        release.clear()
        self.app._on_action(Action("take_photo", "dashboard", {"reason": "who's at the door"}))
        time.sleep(0.2)
        self.assertTrue(self.app.speech_busy())                      # its answer will be spoken
        release.set()
        self.assertTrue(wait_until(lambda: not self.app.speech_busy()))

    def test_speech_busy_while_a_line_is_queued_or_spoken(self):
        from shared import Action
        done = threading.Event()

        class SlowSpeaker:
            def say(self, text):
                done.wait(1.0)
        self.app.speaker = SlowSpeaker()
        self.assertFalse(self.app.speech_busy())
        self.app._on_action(Action("speak", "rules", {"text": "Hello there."}))
        self.assertTrue(self.app.speech_busy())
        time.sleep(0.2)
        self.assertTrue(self.app.speech_busy())                        # being spoken
        done.set()
        self.assertTrue(wait_until(lambda: not self.app.speech_busy()))


class BuiltApp(unittest.TestCase):
    """integration/__main__.build_app wires Dell's dashboard hooks (PLAN Q26)."""

    def test_product_start_command_has_no_demo_options(self):  # noqa: D401
        """Owner 2026-11-15: the real product only. No fake radio, typed speech,
        stand-in camera, printed speech or offline brain in the start command."""
        import argparse
        import contextlib
        import io
        from integration import __main__ as main_module
        for flag in ("--fake", "--standin-camera", "--print-speech", "--offline"):
            with self.subTest(flag=flag):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main_module.main([flag])
        keys = main_module.__doc__.split("Console keys", 1)[1]
        for gone in ("t <text>", "d / m", "fake"):
            self.assertNotIn(gone, keys)
        import re
        src = Path(main_module.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"StandInCamera|PrintTTS|FakeFlipper|fake_flipper|standin|print_speech",
                                    src, re.I))
        with self.assertRaises(ValueError):                            # parts are a closed list
            main_module.build_app(argparse.Namespace(), {}, parts={"camera": object()})

    def test_listening_hook_drives_api_state_and_listen_hook_is_gone(self):
        import argparse
        import urllib.request
        from integration.__main__ import build_app
        tmp = Path(tempfile.mkdtemp(prefix="faraday-built-"))
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0)
        s["camera"]["enabled"] = False                                 # no webcam in this test
        args = argparse.Namespace(log=str(tmp / "events.jsonl"), offline=True, mode=Mode.HOME,
                                  no_dashboard=False, port=0, no_mic=False, no_wake=True,
                                  no_controls=True, controls_port=None, fresh=False,
                                  no_detect=True)
        from hardware.speech import PrintTTS
        mic = LiveMic()
        # the product never passes parts; tests hand their stand-ins in here
        app = build_app(args, s, parts={"flipper": FakeFlipper(s, charge_poll_s=0), "stream_factory": mic,
                                         "speaker": lambda gate: PrintTTS(gate), "stt": FakeSTT(),
                                         "chime": lambda: None})
        board = app.board
        self.assertIsNotNone(board.listening_hook)
        self.assertIsNone(board.listen_hook)                           # the Listen button is gone
        self.assertIs(board.control_hook.__func__, App.control)
        self.assertIsNone(app.camera)                                  # camera off: no stand-in
        app.voice.recorder.audio_dir = tmp / "out"

        def listening():
            with urllib.request.urlopen(board.url + "api/state", timeout=5) as r:
                return json.loads(r.read())["listening"]

        app.start()
        try:
            self.assertTrue(wait_until(lambda: not app.speech_busy()))  # startup line said
            self.assertIs(listening(), False)              # not null: the hook is set
            self.assertTrue(app.voice.listen_once("doorbell"))
            self.assertTrue(wait_until(lambda: listening() is True))
            app.voice.recorder.cancel()
            self.assertTrue(wait_until(lambda: listening() is False))
        finally:
            app.stop()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
