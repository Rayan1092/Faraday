"""Diarization: output parsing, the command wrapper, and that it never delays STT.

    python -m unittest hardware.tests.test_diarize
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from shared import Source, make_event, validate_event

from hardware.diarize import Diarizer, parse_voice_count


class Parse(unittest.TestCase):
    def test_shapes(self):
        rttm = ("SPEAKER visitor 1 0.00 1.20 <NA> <NA> SPEAKER_00 <NA> <NA>\n"
                "SPEAKER visitor 1 1.30 0.90 <NA> <NA> SPEAKER_01 <NA> <NA>\n"
                "SPEAKER visitor 1 2.40 1.00 <NA> <NA> SPEAKER_00 <NA> <NA>\n")
        for text, want in [
            (rttm, 2),
            ('[{"start":0,"end":1.2,"speaker":"A"},{"start":1.3,"end":2,"speaker":"B"},'
             '{"start":2,"end":3,"speaker":"A"}]', 2),
            ('{"segments":[{"speakerId":0},{"speakerId":1},{"speakerId":2}]}', 3),
            ('{"speakerCount": 1}', 1),
            ("Speaker 0: 0.00-1.20\nSpeaker 1: 1.30-2.20\nSpeaker 0: 2.40-3.40\n", 2),
            ("[SPEAKER_00] 0.0 1.2\n[SPEAKER_01] 1.3 2.2\n[SPEAKER_02] 2.4 3.0", 3),
            ("Detected 2 speakers in 3.4 s", 2),
            ("speakers: 4", 4),
            ("Loading models...\ndone", None),
            ("", None),
        ]:
            with self.subTest(text=text[:40]):
                self.assertEqual(parse_voice_count(text), want)


class Command(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-diar-"))
        self.wav = self.dir / "visitor.wav"
        self.wav.write_bytes(b"RIFF")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def script(self, body: str) -> Path:
        p = self.dir / "fake_argmax.py"
        p.write_text(body, encoding="utf-8")
        return p

    def test_runs_command_with_wav_and_counts(self):
        s = self.script("import sys\nassert sys.argv[1] == 'diarize'\nassert sys.argv[3].endswith('visitor.wav')\n"
                        "print('SPEAKER v 1 0 1 <NA> <NA> SPEAKER_00 <NA> <NA>')\n"
                        "print('SPEAKER v 1 1 1 <NA> <NA> SPEAKER_01 <NA> <NA>')\n")
        d = Diarizer([sys.executable, str(s), "diarize", "--audio-path", "{wav}"])
        self.assertEqual(d.count_voices(self.wav), 2)

    def test_failure_and_timeout_give_none(self):
        bad = Diarizer([sys.executable, "-c", "import sys; sys.exit(2)"])
        self.assertIsNone(bad.count_voices(self.wav))
        slow = Diarizer([sys.executable, "-c", "import time; time.sleep(5)"], timeout_s=0.5)
        self.assertIsNone(slow.count_voices(self.wav))
        missing = Diarizer(["/no/such/argmax-cli", "diarize", "{wav}"])
        self.assertIsNone(missing.count_voices(self.wav))

    def test_settings(self):
        mac = {"stt": {"mac": {"diarize": {"command": ["argmax-cli", "diarize", "{wav}"], "grace_s": 0.2}},
                       "windows": {"kind": "standin"}}}
        d = Diarizer.from_settings(mac)
        if sys.platform == "darwin":
            self.assertEqual(d.build(Path("x.wav")), ["argmax-cli", "diarize", "x.wav"])
            self.assertEqual(Diarizer.grace_s(mac), 0.2)
        else:
            self.assertIsNone(d)                       # Windows: not configured
            self.assertEqual(Diarizer.grace_s(mac), 0.0)


class FieldValidation(unittest.TestCase):
    def test_voices_field(self):
        ok = make_event("speech_heard", {"text": "hi", "voices": 2}, Source.SPEECH)
        self.assertEqual(validate_event(ok.to_dict()), [])
        none = make_event("speech_heard", {"text": "hi"}, Source.SPEECH)
        self.assertNotIn("voices", {k for k, v in none.data.items() if v is not None})
        for bad in (0, -1, 1.5, "2", True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    make_event("speech_heard", {"text": "hi", "voices": bad}, Source.SPEECH)


class StdoutFirst(unittest.TestCase):
    """Review of the owner voice PR: stdout is read before stderr again."""

    def test_json_on_stdout_with_log_lines_on_stderr(self):
        code = ("import sys; print('{\"speakers\": 2}'); "
                "print('Loading model 2 of 3, speaker_0 ready', file=sys.stderr)")
        self.assertEqual(Diarizer([sys.executable, "-c", code]).count_voices(Path("x.wav")), 2)


class NeverDelays(unittest.TestCase):
    """Voice: the count is attached when diarization beat STT, else the reply goes
    out at once without it. Owner wake requests are not diarized."""

    def setUp(self):
        from hardware.tests.test_listen import LiveMic
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-diar-v-"))
        self.mic = LiveMic()
        self.v = None

    def tearDown(self):
        if self.v is not None:
            self.v.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def say(self, *parts):
        from hardware.tests.test_listen import clip
        self.mic.say(clip(*parts, path=self.dir / f"c{time.monotonic_ns()}.wav"))

    def voice(self, diarizer, grace, stt_texts=("Package for Sam.",)):
        from hardware.audio import MicGate, Recorder
        from integration.tests.test_voice import FakeSTT
        from integration.voice import Voice
        self.got, self.said = [], []
        rec = Recorder(MicGate(), audio_dir=self.dir / "out", stream_factory=self.mic)
        self.v = Voice(rec, FakeSTT(*stt_texts), self.got.append, out=self.said.append,
                       listen={"max_s": 15, "silence_s": 1.0, "no_speech_s": 2.0, "after_speech_s": 0.0},
                       wake={"enabled": True, "silence_s": 0.7},
                       diarizer=diarizer, diarize_grace_s=grace)
        return self.v

    def window(self):
        self.v.listen_once("doorbell")
        self.assertTrue(self.wait(lambda: self.v.windows_opened == 1))
        self.say(("speech", 1.0), ("quiet", 2.0))

    def wait(self, cond, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not cond():
            time.sleep(0.02)
        return cond()

    def test_fast_diarization_adds_voices(self):
        class Fast:
            def count_voices(self, wav):
                return 2

        v = self.voice(Fast(), 0.5)
        self.window()
        self.assertTrue(self.wait(lambda: len(self.got) == 1))
        self.assertEqual(self.got[0].data["voices"], 2)
        self.assertEqual(validate_event(self.got[0].to_dict()), [])

    def test_slow_diarization_never_delays_the_reply(self):
        class Slow:
            called = threading.Event()

            def count_voices(self, wav):
                self.called.set()
                time.sleep(2.0)
                return 3

        slow = Slow()
        v = self.voice(slow, 0.0)
        self.window()
        self.assertTrue(self.wait(lambda: slow.called.is_set()))
        t0 = time.monotonic()
        self.assertTrue(self.wait(lambda: len(self.got) == 1, timeout=5))
        self.assertLess(time.monotonic() - t0, 1.0)             # didn't wait the 2 s
        self.assertIsNone(self.got[0].data.get("voices"))
        self.assertTrue(self.wait(lambda: any("too late" in s for s in self.said), timeout=5))

    def test_owner_wake_request_not_diarized(self):
        calls = []

        class Counting:
            def count_voices(self, wav):
                calls.append(wav)
                return 1

        v = self.voice(Counting(), 0.5, stt_texts=("Hey Faraday.", "Turn on the porch light."))
        v.start_wake()
        self.say(("speech", 0.8), ("quiet", 1.5), ("speech", 1.2), ("quiet", 2.0))
        self.assertTrue(self.wait(lambda: len(self.got) == 1, timeout=15))
        self.assertEqual(self.got[0].data["text"], "Turn on the porch light.")
        self.assertEqual(calls, [])                              # the owner, not a visitor


if __name__ == "__main__":
    unittest.main()
