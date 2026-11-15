"""Camera feed (one owner, photos + stream) and intercom player. No webcam, no speaker.

    python -m unittest hardware.tests.test_media
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf

from hardware.audio import MicGate
from hardware.camera import CameraFeed, PhotoCamera
from hardware.intercom import Intercom


class FakeCapture:
    """cv2.VideoCapture stand-in: numbered frames, counts reads."""

    instances = []

    def __init__(self, *a, opened=True):
        self.opened, self.n, self.released = opened, 0, False
        FakeCapture.instances.append(self)

    def isOpened(self):
        return self.opened

    def read(self):
        self.n += 1
        img = np.full((72, 128, 3), self.n % 255, np.uint8)
        cv2.putText(img, str(self.n), (5, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
        return True, img

    def release(self):
        self.released = True


def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class Feed(unittest.TestCase):
    def setUp(self):
        FakeCapture.instances = []
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-feed-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def feed(self, **kw):
        f = CameraFeed(photos_dir=self.dir, fps=30, idle_s=kw.pop("idle_s", 0.5),
                       capture_factory=lambda *a: FakeCapture(**kw))
        self.addCleanup(f.stop)
        return f

    def test_snapshot_and_stream_share_one_device(self):
        f = self.feed(idle_s=5)
        with f.viewer():
            first, at = f.next_jpeg(0.0)
            self.assertTrue(first.startswith(b"\xff\xd8"))         # JPEG
            second, at2 = f.next_jpeg(at)
            self.assertGreater(at2, at)                             # a newer frame
            path = f.snapshot()                                     # photo while streaming
        self.assertTrue(path.exists())
        self.assertEqual(f.opens, 1)                                # never opened twice
        self.assertEqual(len(FakeCapture.instances), 1)

    def test_snapshot_is_newer_than_the_request(self):
        f = self.feed(idle_s=5)
        f.jpeg()
        asked = time.monotonic()
        path = f.snapshot()
        img = cv2.imread(str(path))
        self.assertIsNotNone(img)
        self.assertGreater(f._frame_at, asked)

    def test_closes_after_idle_and_reopens_on_demand(self):
        f = self.feed(idle_s=0.3)
        self.assertIsNotNone(f.jpeg())
        self.assertTrue(wait_until(lambda: FakeCapture.instances[0].released, 3))
        self.assertIsNotNone(f.jpeg())                              # opens again
        self.assertEqual(f.opens, 2)

    def test_stays_open_while_watched(self):
        f = self.feed(idle_s=0.2)
        with f.viewer():
            f.jpeg()
            time.sleep(0.6)
            self.assertFalse(FakeCapture.instances[0].released)
        self.assertTrue(wait_until(lambda: FakeCapture.instances[0].released, 3))

    def test_camera_missing_gives_none_and_photo_event_says_unavailable(self):
        f = self.feed(opened=False)
        self.assertIsNone(f.jpeg(timeout=1))
        ev = PhotoCamera(f, None).take()
        self.assertEqual((ev.data["photo_path"], ev.data["description"]), ("", "Camera unavailable."))


class FakeOutput:
    """sounddevice.OutputStream stand-in: pulls blocks through the callback."""

    def __init__(self, samplerate, channels, dtype, device, callback, block=320):
        self.rate, self.cb, self.block = samplerate, callback, block
        self.played, self.running = [], False
        FakeOutput.last = self

    def start(self):
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.running:
            out = np.zeros((self.block, 1), dtype=np.int16)
            self.cb(out, self.block, None, None)
            self.played.append(out[:, 0].copy())
            time.sleep(self.block / self.rate / 4)        # 4x real time, keeps tests fast

    def stop(self):
        self.running = False

    def close(self):
        pass


class IntercomPlayer(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-ic-"))
        t = np.arange(16000) / 16000                        # 1 s of a "voice"
        self.pcm = (0.3 * np.sin(2 * np.pi * 300 * t) * 32767).astype(np.int16)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def chunks(self, size=2048):
        for i in range(0, len(self.pcm), size):
            yield self.pcm[i:i + size].tobytes()

    def test_plays_every_sample_in_order_with_mic_muted(self):
        gate = MicGate()
        ic = Intercom(gate, stream_factory=FakeOutput, hold_s=0.3)
        for chunk in self.chunks():
            ic.feed(chunk, 16000)
            self.assertTrue(gate.muted)                     # door mic off while talking
        ic.end()
        self.assertTrue(wait_until(lambda: not ic.active, 10))
        self.assertFalse(gate.muted)                        # back on afterwards
        played = np.concatenate(FakeOutput.last.played)
        heard, sent = np.trim_zeros(played, 'f'), np.trim_zeros(self.pcm, 'f')
        self.assertTrue(np.array_equal(heard[:len(sent)], sent))   # every sample, in order
        self.assertEqual(ic.played, len(self.pcm))
        self.assertEqual(ic.sessions, 1)

    def test_on_end_runs_after_the_owner_lets_go(self):
        gate, ended = MicGate(), threading.Event()
        ic = Intercom(gate, stream_factory=FakeOutput, hold_s=0.3)
        ic.on_end = ended.set                      # __main__: voice.on_intercom_end
        ic.feed(self.pcm[:4800].tobytes(), 16000)
        ic.end()
        self.assertTrue(ended.wait(5))
        self.assertFalse(gate.muted)

    def test_speaker_that_fails_to_open_never_leaves_the_mic_muted(self):
        """Review of #56: the gate was muted before the stream opened, so a missing
        speaker left the door mic deaf until a restart."""
        class Broken:
            def __init__(self, **kw):
                pass

            def start(self):
                raise OSError("no output device")

            def close(self):
                pass
        gate = MicGate()
        ic = Intercom(gate, stream_factory=Broken, hold_s=0.2)
        with self.assertLogs("hardware.intercom", "ERROR"):
            for chunk in list(self.chunks())[:3]:
                ic.feed(chunk, 16000)
        self.assertFalse(gate.muted)
        self.assertFalse(ic.active)

    def test_closes_by_itself_when_chunks_stop(self):
        gate = MicGate()
        ic = Intercom(gate, stream_factory=FakeOutput, hold_s=0.2)
        ic.feed(self.pcm[:4000].tobytes(), 16000)           # owner's browser vanished, no end()
        self.assertTrue(wait_until(lambda: not ic.active, 5))
        self.assertFalse(gate.muted)

    def test_counted_gate_keeps_mic_muted_while_tts_still_talks(self):
        gate = MicGate()
        gate.mute()                                         # Faraday is speaking
        ic = Intercom(gate, stream_factory=FakeOutput, hold_s=0.2)
        ic.feed(self.pcm[:3200].tobytes(), 16000)
        ic.end()
        self.assertTrue(wait_until(lambda: not ic.active, 5))
        self.assertTrue(gate.muted)                         # TTS still holds it
        gate.unmute()
        self.assertFalse(gate.muted)

    def test_lag_is_capped(self):
        gate = MicGate()

        class Stalled(FakeOutput):                          # speaker never pulls audio
            def start(self):
                self.running = True

        ic = Intercom(gate, stream_factory=Stalled, hold_s=0.2)
        for _ in range(8):                                  # 8 s pushed, nothing played
            ic.feed(self.pcm.tobytes(), 16000)
        self.assertLessEqual(ic._queued, 3 * 16000)
        self.assertGreater(ic.dropped, 0)
        ic.stop()
        self.assertFalse(gate.muted)


if __name__ == "__main__":
    unittest.main()
