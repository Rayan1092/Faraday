"""Hands-free listening windows (energy VAD) on synthesized audio. No mic.

    python -m unittest hardware.tests.test_listen
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import time
import unittest
from collections import deque
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
        self.assertFalse(rec.window_open)
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

    def test_muted_mic_hears_nothing_and_ends_after_faraday_stops(self):
        gate = MicGate()
        gate.mute()                                     # Faraday talking over the visitor
        threading.Timer(1.0, gate.unmute).start()
        t0 = time.monotonic()
        r = self.run_window(clip(("speech", 1.0), path=self.dir / "m.wav"), gate=gate,
                            max_s=0.5, silence_s=0.3, no_speech_s=0.4)
        self.assertFalse(r["spoke"])
        self.assertIsNone(r["path"])
        self.assertGreater(time.monotonic() - t0, 1.0)  # muted time didn't count

    def test_cancel_ends_the_window_without_a_file(self):
        done, result = threading.Event(), {}
        wav = clip(("speech", 1.0), path=self.dir / "c.wav")

        class SlowStream(FakeStream):                 # delivers nothing until stopped
            def start(self):
                pass

        rec = Recorder(self.gate, audio_dir=self.dir / "out",
                       stream_factory=lambda **k: SlowStream(wav, **k))
        rec.listen(lambda p, s, spoke: (result.update(p=p, spoke=spoke), done.set()), max_s=6.0)
        self.assertTrue(rec.window_open)
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


class LiveMic:
    """A fake microphone stream that stays open: plays queued clips, quiet noise
    in between, at `speed` x real time (sample counts stay exact)."""

    def __init__(self, speed: float = 4.0, chunk: int = 800):
        self.speed, self.chunk = speed, chunk
        self.clips: "deque[np.ndarray]" = deque()
        self.streams = 0
        self.cb = None
        self._stop = threading.Event()
        self._cur = np.empty(0, dtype=np.int16)
        self._rng = np.random.default_rng(2)

    def __call__(self, **kw):
        self.cb = kw["callback"]
        self.streams += 1
        return self

    def say(self, path: Path) -> None:
        data, _ = sf.read(str(path), dtype="int16")
        self.clips.append(data)

    @property
    def idle(self) -> bool:
        return not self.clips and len(self._cur) == 0

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            if len(self._cur) == 0 and self.clips:
                self._cur = self.clips.popleft()
            if len(self._cur):
                out, self._cur = self._cur[:self.chunk], self._cur[self.chunk:]
                if len(out) < self.chunk:
                    out = np.concatenate([out, np.zeros(self.chunk - len(out), np.int16)])
            else:
                out = (self._rng.normal(0, 0.002, self.chunk) * 32767).astype(np.int16)
            self.cb(out.reshape(-1, 1), self.chunk, None, None)
            time.sleep(self.chunk / RATE / self.speed)

    def stop(self):
        self._stop.set()

    def close(self):
        self._stop.set()


class Continuous(unittest.TestCase):
    """One stream for every window: nothing said between windows is lost."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-live-"))
        self.gate = MicGate()
        self.mic = LiveMic()
        self.rec = Recorder(self.gate, audio_dir=self.dir / "out", stream_factory=self.mic)

    def tearDown(self):
        self.rec.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def window(self, **kw):
        done, result = threading.Event(), {}

        def on_done(path, seconds, spoke):
            result.update(path=path, seconds=seconds, spoke=spoke)
            done.set()
        self.assertTrue(self.rec.listen(on_done, **kw))
        return done, result

    def test_back_to_back_segments_lose_nothing_and_share_one_stream(self):
        """Wake style: "Hey Faraday" ... pause ... "turn on the light". The second
        part starts while the first segment is being closed and a new one opened."""
        segs = []
        all_done = threading.Event()

        def on_done(path, seconds, spoke):
            if spoke:
                segs.append(seconds)
            if len(segs) == 2:
                all_done.set()
            elif not all_done.is_set():
                self.rec.listen(on_done, max_s=10, silence_s=0.6, no_speech_s=None)
        self.assertTrue(self.rec.listen(on_done, max_s=10, silence_s=0.6, no_speech_s=None))
        time.sleep(0.3)
        self.mic.say(clip(("speech", 0.8), ("quiet", 0.7), ("speech", 1.5), ("quiet", 1.5),
                          path=self.dir / "two.wav"))
        self.assertTrue(all_done.wait(15))
        self.assertEqual(self.mic.streams, 1)
        self.assertGreaterEqual(segs[1], 1.5 + 0.6)          # the whole second phrase

    def test_long_quiet_wait_keeps_only_the_preroll(self):
        done, r = self.window(max_s=10, silence_s=0.6, no_speech_s=None)
        self.mic.say(clip(("quiet", 4.0), ("speech", 1.0), ("quiet", 1.0), path=self.dir / "late.wav"))
        self.assertTrue(done.wait(15))
        self.assertTrue(r["spoke"])
        self.assertLess(r["seconds"], 0.5 + 1.0 + 0.6 + 0.3)  # not the 4 s of quiet

    def test_adjust_turns_a_wake_segment_into_a_timed_window(self):
        done, r = self.window(max_s=10, silence_s=0.6, no_speech_s=None)
        time.sleep(0.5)
        self.assertFalse(done.is_set())                       # waits for speech forever
        self.assertTrue(self.rec.adjust(no_speech_s=1.0, max_s=15, silence_s=1.8))
        self.assertTrue(done.wait(5))
        self.assertFalse(r["spoke"])
        self.assertFalse(self.rec.window_open)

    def test_speech_during_mute_is_dropped(self):
        done, r = self.window(max_s=6, silence_s=0.6, no_speech_s=2.0)
        self.gate.mute()
        self.mic.say(clip(("speech", 1.0), path=self.dir / "faraday.wav"))   # Faraday's own voice
        while not self.mic.idle:
            time.sleep(0.05)
        time.sleep(0.1)
        self.gate.unmute()
        self.assertTrue(done.wait(10))
        self.assertFalse(r["spoke"])


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
