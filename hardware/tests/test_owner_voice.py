"""Owner voice check (display only) with a fake argmax-cli diarize.

    python -m unittest hardware.tests.test_owner_voice
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from shared import Source, make_event

from hardware.diarize import Diarizer, parse_rttm
from hardware.owner_voice import OwnerVoice, decide
from hardware.tests.test_listen import clip

# Prints RTTM like argmax-cli diarize: the enrollment part is SPEAKER_00, the request
# part is FAKE_REQ ("00", "01", "both" or "none"); FAKE_SPLIT says where it starts.
FAKE_DIARIZE = r'''
import os, sys
import soundfile as sf
wav = sys.argv[sys.argv.index("--audio-path") + 1]
total = sf.info(wav).duration
split = float(os.environ["FAKE_SPLIT"])
mode = os.environ.get("FAKE_REQ", "00")
if os.environ.get("FAKE_COUNT"):
    print(os.environ["FAKE_COUNT"] + " speakers")
    sys.exit(0)
def line(start, dur, spk):
    print(f"SPEAKER joined 1 {start:.3f} {dur:.3f} <NA> <NA> SPEAKER_{spk} <NA> <NA>")
line(0.2, split - 0.6, "00")
if mode == "00":
    line(split + 0.1, total - split - 0.2, "00")
elif mode == "01":
    line(split + 0.1, total - split - 0.2, "01")
elif mode == "both":
    line(split + 0.1, (total - split) / 2, "00")
    line(split + (total - split) / 2, (total - split) / 2 - 0.1, "01")
'''


class Decide(unittest.TestCase):
    def test_rules(self):
        enrol = [(0.0, 9.0, "S0")]
        self.assertTrue(decide(enrol + [(10.0, 2.0, "S0")], 9.5))
        self.assertFalse(decide(enrol + [(10.0, 2.0, "S1")], 9.5))
        self.assertFalse(decide(enrol + [(10.0, 1.0, "S0"), (11.0, 1.0, "S1")], 9.5))
        self.assertTrue(decide(enrol + [(10.0, 2.0, "S0"), (12.0, 0.1, "S1")], 9.5))   # a click
        self.assertIsNone(decide(enrol, 9.5))                                          # nothing after
        self.assertIsNone(decide([], 9.5))
        # a turn that spans the split counts on both sides
        self.assertTrue(decide([(0.0, 12.0, "S0")], 9.5))

    def test_parse_rttm(self):
        text = ("SPEAKER a 1 0.500 2.250 <NA> <NA> SPEAKER_00 <NA> <NA>\n"
                "noise line\nSPEAKER a 1 3.000 1.000 <NA> <NA> SPEAKER_01 <NA> <NA>\n")
        self.assertEqual(parse_rttm(text), [(0.5, 2.25, "SPEAKER_00"), (3.0, 1.0, "SPEAKER_01")])


class Check(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-owner-"))
        self.fake = self.dir / "fake_diarize.py"
        self.fake.write_text(FAKE_DIARIZE, encoding="utf-8")
        self.enrol = clip(("speech", 9.0), ("quiet", 1.0), path=self.dir / "enrol.wav")
        self.request = clip(("quiet", 0.2), ("speech", 2.0), ("quiet", 0.5), path=self.dir / "req.wav")
        self.checker = OwnerVoice(Diarizer([sys.executable, str(self.fake), "diarize", "--audio-path", "{wav}"]),
                                  self.enrol)
        os.environ["FAKE_SPLIT"] = str(10.0 + 0.5)              # enrollment 10 s + 0.5 s gap
        for k in ("FAKE_REQ", "FAKE_COUNT"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k in ("FAKE_SPLIT", "FAKE_REQ", "FAKE_COUNT"):
            os.environ.pop(k, None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_same_voice_is_the_owner(self):
        self.assertIs(self.checker.check(self.request), True)
        self.assertEqual(self.checker.last["how"], "rttm")

    def test_other_voice_is_not_the_owner(self):
        os.environ["FAKE_REQ"] = "01"
        self.assertIs(self.checker.check(self.request), False)

    def test_owner_plus_someone_else_is_not_the_owner(self):
        os.environ["FAKE_REQ"] = "both"
        self.assertIs(self.checker.check(self.request), False)

    def test_nothing_after_the_enrollment_is_unknown(self):
        os.environ["FAKE_REQ"] = "none"
        self.assertIsNone(self.checker.check(self.request))

    def test_count_only_output_uses_the_owners_rule(self):
        os.environ["FAKE_COUNT"] = "1"
        self.assertIs(self.checker.check(self.request), True)
        os.environ["FAKE_COUNT"] = "2"
        self.assertIs(self.checker.check(self.request), False)

    def test_join_puts_the_request_after_the_enrollment_and_a_gap(self):
        import soundfile as sf
        out = self.dir / "joined.wav"
        split = self.checker.join(self.request, out)
        self.assertAlmostEqual(split, 10.5, places=2)
        self.assertAlmostEqual(sf.info(str(out)).duration, 10.5 + 2.7, places=1)

    def test_broken_diarizer_is_unknown(self):
        bad = OwnerVoice(Diarizer([sys.executable, "-c", "import sys; sys.exit(3)"]), self.enrol)
        self.assertIsNone(bad.check(self.request))

    def test_from_settings_needs_an_enrollment_and_a_command(self):
        cmd = [sys.executable, str(self.fake), "diarize", "--audio-path", "{wav}"]
        key = "mac" if sys.platform == "darwin" else "windows"
        s = {"stt": {key: {"diarize": {"command": cmd}}},
             "audio": {"voice_check": {"enrollment": str(self.dir / "missing.wav")}}}
        self.assertIsNone(OwnerVoice.from_settings(s))
        s["audio"]["voice_check"]["enrollment"] = str(self.enrol)
        self.assertIsNotNone(OwnerVoice.from_settings(s))
        s["audio"]["voice_check"]["enabled"] = False
        self.assertIsNone(OwnerVoice.from_settings(s))


class OnSpeechHeard(unittest.TestCase):
    """Voice puts the result on owner requests only, never delays past grace_s."""

    def setUp(self):
        from hardware.audio import MicGate, Recorder
        from hardware.tests.test_listen import LiveMic
        from integration.tests.test_voice import FakeSTT
        from integration.voice import Voice
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-ov-"))
        self.got, self.said = [], []
        self.mic = LiveMic()
        self.Voice, self.FakeSTT = Voice, FakeSTT
        self.rec = Recorder(MicGate(), audio_dir=self.dir / "out", stream_factory=self.mic)
        self.v = None

    def tearDown(self):
        if self.v is not None:
            self.v.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def voice(self, checker, grace=1.0, texts=("Hey Faraday, turn on the porch light.",)):
        self.v = self.Voice(self.rec, self.FakeSTT(*texts), self.got.append, out=self.said.append,
                            listen={"max_s": 15, "silence_s": 1.0, "no_speech_s": 2.0, "after_speech_s": 0.0},
                            wake={"enabled": True, "silence_s": 0.7},
                            owner_voice=checker, owner_voice_grace_s=grace)
        return self.v

    def speak(self):
        self.mic.say(clip(("speech", 1.2), ("quiet", 1.5), path=self.dir / f"c{time.monotonic_ns()}.wav"))

    def wait(self, cond, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not cond():
            time.sleep(0.02)
        return cond()

    def test_owner_request_carries_the_result(self):
        class Yes:
            calls = 0

            def check(self, wav):
                Yes.calls += 1
                return True
        v = self.voice(Yes())
        v.start_wake()
        self.speak()
        self.assertTrue(self.wait(lambda: self.got))
        ev = self.got[0]
        self.assertEqual((ev.data["channel"], ev.data["owner_voice"]), ("owner", True))
        self.assertTrue(any("the owner's voice" in s for s in self.said))

    def test_visitor_speech_is_not_checked(self):
        class Never:
            def check(self, wav):
                raise AssertionError("visitor speech must not be checked")
        v = self.voice(Never(), texts=("Package for Sam.",))
        v.listen_once("doorbell")
        self.assertTrue(self.wait(lambda: v.windows_opened == 1))
        self.speak()
        self.assertTrue(self.wait(lambda: self.got))
        self.assertIsNone(self.got[0].data.get("owner_voice"))

    def test_slow_check_never_holds_the_reply_past_grace(self):
        class Slow:
            def check(self, wav):
                time.sleep(2.0)
                return False
        v = self.voice(Slow(), grace=0.3)
        v.start_wake()
        self.speak()
        self.assertTrue(self.wait(lambda: self.got))
        self.assertIsNone(self.got[0].data.get("owner_voice"))
        self.assertTrue(self.wait(lambda: any("too late" in s for s in self.said), timeout=5))

    def test_alarm_never_waits_for_the_voice_check(self):
        """Review: "Hey Faraday, <passphrase>" during an alarm must disarm at once."""
        class Slow:
            def check(self, wav):
                time.sleep(1.5)
                return True
        v = self.voice(Slow(), grace=2.0)
        v.urgent = lambda: True
        v.start_wake()
        self.speak()
        self.assertTrue(self.wait(lambda: self.got, timeout=4))
        t_event = time.monotonic()
        self.assertIsNone(self.got[0].data.get("owner_voice"))
        self.assertTrue(self.wait(lambda: any("too late" in s for s in self.said), timeout=5))
        self.assertGreater(time.monotonic() - t_event, 0.3)      # it came after the event

    def test_one_breath_request_checks_alongside_the_wake_stt(self):
        """Review: the check started only after the wake STT, so its wait was pure delay."""
        from integration.tests.test_voice import FakeSTT

        class SlowSTT(FakeSTT):
            def transcribe(self, wav):
                time.sleep(0.6)
                return super().transcribe(wav)

        class Check:
            def check(self, wav):
                time.sleep(0.4)
                return True
        v = self.voice(Check(), grace=0.1)
        v.stt = SlowSTT("Hey Faraday, turn on the porch light.")
        v.start_wake()
        self.speak()
        self.assertTrue(self.wait(lambda: self.got))
        self.assertIs(self.got[0].data.get("owner_voice"), True)  # done while STT ran

    def test_unknown_voice_wording(self):
        from hardware.owner_voice import label
        self.assertEqual((label(True), label(False), label(None)), ("owner", "unknown voice", "can't tell"))

    def test_field_validation(self):
        make_event("speech_heard", {"text": "hi", "owner_voice": False}, Source.SPEECH)
        with self.assertRaises(ValueError):
            make_event("speech_heard", {"text": "hi", "owner_voice": "yes"}, Source.SPEECH)


if __name__ == "__main__":
    unittest.main()
