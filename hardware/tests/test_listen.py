"""Hands-free listening windows (energy VAD) on synthesized audio. No mic.

    python -m unittest hardware.tests.test_listen
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from hardware.audio import EnergyVad, MicGate, Recorder
from hardware.tests.test_speech import FakeStream

RATE = 16000


def clip(*parts, path: Path) -> Path:
    """parts: ("speech", seconds) or ("quiet", seconds). Quiet has a little noise."""
    rng = np.random.default_rng(1)
    out = []
    for kind, seconds in parts:
        n = int(RATE * seconds)
        if kind == "speech":
            t = np.arange(n) / RATE
            tone = 0.3 * np.sin(2 * np.pi * 220 * t) * (1 + 0.5 * np.sin(2 * np.pi * 3 * t))
            out.append(tone)
        else:
            out.append(rng.normal(0, 0.002, n))
    sf.write(str(path), (np.concatenate(out) * 32767).astype(np.int16), RATE, subtype="PCM_16")
    return path


class Window(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-listen-"))
        self.gate = MicGate()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_window(self, wav: Path, gate=None, **kw):
        done, result = threading.Event(), {}
        rec = Recorder(gate or self.gate, audio_dir=self.dir / "out",
                       stream_factory=lambda **k: FakeStream(wav, **k))

        def on_done(path, seconds, spoke):
            result.update(path=path, seconds=seconds, spoke=spoke)
            done.set()

        self.assertTrue(rec.listen(on_done, **kw))
        self.assertTrue(done.wait(15))
        self.assertFalse(rec.recording)
        return result

    def test_stops_when_the_speaker_stops(self):
        r = self.run_window(clip(("quiet", 0.5), ("speech", 1.5), ("quiet", 3.0), path=self.dir / "a.wav"),
                            max_s=6.0, silence_s=0.8, no_speech_s=3.0)
        self.assertTrue(r["spoke"])
        self.assertAlmostEqual(r["seconds"], 2.8, delta=0.3)      # 0.5 + 1.5 + 0.8, not 5
        self.assertTrue(r["path"].exists())
        self.assertAlmostEqual(sf.info(str(r["path"])).duration, r["seconds"], delta=0.1)

    def test_nobody_spoke_ends_early_and_saves_nothing(self):
        r = self.run_window(clip(("quiet", 5.0), path=self.dir / "q.wav"),
                            max_s=6.0, silence_s=0.8, no_speech_s=3.0)
        self.assertFalse(r["spoke"])
        self.assertIsNone(r["path"])
        self.assertAlmostEqual(r["seconds"], 3.0, delta=0.2)
        self.assertEqual(list((self.dir / "out").glob("*.wav")) if (self.dir / "out").exists() else [], [])

    def test_max_length_caps_a_long_speaker(self):
        r = self.run_window(clip(("speech", 9.0), path=self.dir / "long.wav"),
                            max_s=6.0, silence_s=0.8, no_speech_s=3.0)
        self.assertTrue(r["spoke"])
        self.assertAlmostEqual(r["seconds"], 6.0, delta=0.2)

    def test_speech_right_at_the_start_is_caught(self):
        r = self.run_window(clip(("speech", 1.0), ("quiet", 2.0), path=self.dir / "now.wav"),
                            max_s=6.0, silence_s=0.8, no_speech_s=3.0)
        self.assertTrue(r["spoke"])
        self.assertAlmostEqual(r["seconds"], 1.8, delta=0.3)

    def test_muted_mic_hears_nothing_and_still_ends(self):
        gate = MicGate()
        gate.mute()                                     # Faraday talking the whole time
        r = self.run_window(clip(("speech", 1.0), path=self.dir / "m.wav"), gate=gate,
                            max_s=0.5, silence_s=0.3, no_speech_s=0.4)
        self.assertFalse(r["spoke"])
        self.assertIsNone(r["path"])

    def test_cancel_ends_the_window_without_a_file(self):
        done, result = threading.Event(), {}
        wav = clip(("speech", 1.0), path=self.dir / "c.wav")

        class SlowStream(FakeStream):                 # delivers nothing until stopped
            def start(self):
                pass

        rec = Recorder(self.gate, audio_dir=self.dir / "out",
                       stream_factory=lambda **k: SlowStream(wav, **k))
        rec.listen(lambda p, s, spoke: (result.update(p=p, spoke=spoke), done.set()), max_s=6.0)
        self.assertTrue(rec.recording)
        rec.cancel()
        self.assertTrue(done.wait(5))
        self.assertEqual((result["p"], result["spoke"]), (None, False))

    def test_second_window_refused_while_one_is_open(self):
        wav = clip(("speech", 1.0), path=self.dir / "c.wav")

        class SlowStream(FakeStream):
            def start(self):
                pass

        rec = Recorder(self.gate, audio_dir=self.dir / "out",
                       stream_factory=lambda **k: SlowStream(wav, **k))
        self.assertTrue(rec.listen(lambda *a: None, max_s=6.0))
        self.assertFalse(rec.listen(lambda *a: None, max_s=6.0))
        rec.cancel()


class Vad(unittest.TestCase):
    def test_threshold_from_quiet_floor_then_speech(self):
        vad = EnergyVad(RATE)
        quiet = (np.random.default_rng(0).normal(0, 0.002, RATE // 2) * 32767).astype(np.int16)
        vad.feed(quiet)
        self.assertIsNotNone(vad.threshold)
        self.assertFalse(vad.speech_started)
        t = np.arange(RATE // 2) / RATE
        vad.feed((0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16))
        self.assertTrue(vad.speech_started)
        self.assertEqual(vad.silence_frames, 0)
        vad.feed(quiet)
        self.assertGreater(vad.silence_s, 0.4)


if __name__ == "__main__":
    unittest.main()
