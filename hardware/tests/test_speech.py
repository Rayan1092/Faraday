"""Speech tests with saved wav files. No microphone, nothing played on the speaker.

    python -m unittest hardware.tests.test_speech
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf

from shared import validate_event

from hardware.audio import MicGate, Recorder
from hardware.speech import ArgmaxTTS, PrintTTS, SpeechToText, TextToSpeech, _has_pyttsx3
from integration.voice import Voice

RATE = 16000


def make_wav(path: Path, seconds: float = 1.5, freq: float = 440.0) -> Path:
    t = np.arange(int(RATE * seconds)) / RATE
    sf.write(str(path), (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16), RATE,
             subtype="PCM_16")
    return path


class FakeArgmax:
    """OpenAI-style /v1/audio/transcriptions: records the upload, answers {"text": ...}."""

    def __init__(self, text="I left the package by the side door.", status=200):
        self.text, self.status, self.uploads = text, status, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.uploads.append({"path": self.path, "type": self.headers["Content-Type"],
                                      "body": body})
                raw = json.dumps({"text": outer.text}).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/audio/transcriptions"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeStream:
    """Stands in for sounddevice.InputStream: replays a wav through the callback."""

    def __init__(self, wav: Path, gate_events=(), chunk=1600, **kwargs):
        self.cb = kwargs["callback"]
        self.data, _ = sf.read(str(wav), dtype="int16")
        self.chunk, self.gate_events = chunk, dict(gate_events)
        self._t = None

    def start(self):
        def run():
            for i in range(0, len(self.data), self.chunk):
                if i in self.gate_events:
                    self.gate_events[i]()
                self.cb(self.data[i:i + self.chunk].reshape(-1, 1), self.chunk, None, None)
        self._t = threading.Thread(target=run)
        self._t.start()
        self._t.join()

    def stop(self):
        pass

    def close(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-speech-"))
        self.wav = make_wav(self.dir / "visitor.wav")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class STT(Base):
    def test_sends_wav_and_returns_text(self):
        srv = FakeArgmax()
        try:
            text = SpeechToText(srv.url, "en", model="tiny").transcribe(self.wav)
        finally:
            srv.close()
        self.assertEqual(text, "I left the package by the side door.")
        up = srv.uploads[0]
        self.assertEqual(up["path"], "/v1/audio/transcriptions")
        self.assertIn("multipart/form-data", up["type"])
        for part in (b'name="file"', b'filename="visitor.wav"', b'name="language"', b"en",
                     b'name="model"', b"tiny", b"RIFF"):
            self.assertIn(part, up["body"])

    def test_server_error_or_silence_gives_none(self):
        for srv in (FakeArgmax(status=500), FakeArgmax(text="   ")):
            try:
                self.assertIsNone(SpeechToText(srv.url).transcribe(self.wav))
            finally:
                srv.close()

    def test_settings_pick_os_entry(self):
        s = {"stt": {"mac": {"kind": "argmax", "url": "http://localhost:50060/v1/audio/transcriptions"},
                     "windows": {"kind": "standin"}}}
        stt = SpeechToText.from_settings(s)
        if sys.platform == "darwin":
            self.assertEqual(stt.url, "http://localhost:50060/v1/audio/transcriptions")
        else:
            self.assertFalse(stt.available)
            self.assertIsNone(stt.transcribe(self.wav))


class Recording(Base):
    def test_records_saved_wav_through_fake_stream(self):
        rec = Recorder(MicGate(), audio_dir=self.dir / "out",
                       stream_factory=lambda **kw: FakeStream(self.wav, **kw))
        rec.start()
        path, seconds = rec.stop()
        self.assertAlmostEqual(seconds, 1.5, places=1)
        self.assertEqual(sf.info(str(path)).samplerate, RATE)

    def test_mic_is_muted_while_speaker_talks(self):
        gate = MicGate()
        # mute halfway through the clip (sample 12000 of 24000), like TTS starting
        rec = Recorder(gate, audio_dir=self.dir / "out",
                       stream_factory=lambda **kw: FakeStream(self.wav, gate_events={12800: gate.mute}, **kw))
        rec.start()
        _, seconds = rec.stop()
        self.assertAlmostEqual(seconds, 0.8, places=1)   # only the unmuted part was kept

    def test_too_short_is_nothing(self):
        short = make_wav(self.dir / "short.wav", seconds=0.1)
        rec = Recorder(MicGate(), audio_dir=self.dir, stream_factory=lambda **kw: FakeStream(short, **kw))
        rec.start()
        self.assertEqual(rec.stop()[0], None)


class TTS(Base):
    def test_argmax_command_fills_text_and_wav_then_plays(self):
        script = self.dir / "fake_argmax.py"
        script.write_text("import sys, shutil\nshutil.copy(sys.argv[1], sys.argv[2])\n")
        played, gate = [], MicGate()
        tts = ArgmaxTTS([sys.executable, str(script), str(self.wav), "{out_wav}", "--text", "{text}"],
                        gate=gate, player=lambda p, d: played.append((Path(p).name, gate.muted)))
        self.assertEqual(tts.build("Hi there", Path("o.wav"))[-3:], ["o.wav", "--text", "Hi there"])
        self.assertTrue(tts.say("Night mode on."))
        self.assertEqual(played, [("say.wav", True)])     # mic muted while playing
        self.assertFalse(gate.muted)                       # and unmuted after

    def test_argmax_failure_returns_false_and_unmutes(self):
        gate = MicGate()
        tts = ArgmaxTTS([sys.executable, "-c", "import sys; sys.exit(3)"], gate=gate,
                        player=lambda p, d: None)
        self.assertFalse(tts.say("hello"))
        self.assertFalse(gate.muted)

    def test_settings_choose_engine(self):
        s = {"tts": {"mac": {"kind": "argmax", "command": ["argmax-cli", "tts", "{text}"]},
                     "windows": {"kind": "standin"}}}
        tts = TextToSpeech.from_settings(s)
        if sys.platform == "darwin":
            self.assertIsInstance(tts, ArgmaxTTS)
        else:
            self.assertEqual(type(tts).__name__, "Pyttsx3TTS" if _has_pyttsx3() else "PrintTTS")

    @unittest.skipUnless(_has_pyttsx3() and sys.platform.startswith("win"), "pyttsx3 on Windows")
    def test_pyttsx3_renders_offline_to_file(self):
        from hardware.speech import Pyttsx3TTS
        tts = Pyttsx3TTS()
        outs, errors = [], []

        def worker():   # same as integration's speech worker: several lines, one thread
            try:
                for i in range(3):
                    outs.append(tts.render(f"Line {i}. Someone is at the door.", self.dir / f"tts{i}.wav"))
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join(60)
        self.assertFalse(t.is_alive(), "pyttsx3 hung")      # in-process engine hung on line 2
        self.assertEqual(errors, [])
        for out in outs:
            self.assertGreater(sf.info(str(out)).duration, 0.5)


class PushToTalk(Base):
    def test_record_transcribe_submit(self):
        srv = FakeArgmax("Package for Sam.")
        got = []
        rec = Recorder(MicGate(), audio_dir=self.dir / "out",
                       stream_factory=lambda **kw: FakeStream(self.wav, **kw))
        voice = Voice(rec, SpeechToText(srv.url), got.append, out=lambda *_: None)
        try:
            voice.start()
            voice.stop().join(10)
        finally:
            srv.close()
        self.assertEqual(len(got), 1)
        ev = got[0]
        self.assertEqual(validate_event(ev.to_dict()), [])
        self.assertEqual((ev.type, ev.source, ev.data["text"]), ("speech_heard", "speech", "Package for Sam."))
        self.assertAlmostEqual(ev.data["duration_s"], 1.5, places=1)

    def test_cannot_start_while_faraday_talks(self):
        gate = MicGate()
        gate.mute()
        rec = Recorder(gate, stream_factory=lambda **kw: FakeStream(self.wav, **kw))
        Voice(rec, SpeechToText(None), lambda e: None, out=lambda *_: None).start()
        self.assertFalse(rec.recording)


if __name__ == "__main__":
    unittest.main()
