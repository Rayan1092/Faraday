"""Crash safety: each part fails mid-demo; Faraday keeps running, says so clearly,
and recovers when the part is back (owner 2026-11-15).

    python -m unittest integration.tests.test_crash_safety

Same real wiring as the rehearsal (build_app with only the hardware swapped).
Parts: the Flipper (unplugged), the camera (won't open), Argmax STT and LM Studio
(servers that stop answering), Argmax TTS (the speech command fails).
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2

from shared import Action, Mode

from hardware.flipper.fake import FakeFlipper
from hardware.settings import CONFIG_DIR
from hardware.tests.test_camera import make_scene
from hardware.tests.test_listen import LiveMic
from integration.tests.test_rehearsal import RecordingTTS, SampleCapture, ToneSTT, tone, wait_until


class ToggleServer:
    """A local HTTP server that can go "down": it then drops every connection
    without an answer, like a crashed argmax-cli serve or LM Studio."""

    def __init__(self, get=None, post=None):
        outer = self
        self.down = False
        self.posts = 0

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, make):
                if outer.down:
                    self.close_connection = True
                    return
                code, body = make(self) if make else (404, {"error": "not found"})
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._answer(get)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                outer.posts += 1
                self._answer(post)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FlakyCapture(SampleCapture):
    broken = False

    def isOpened(self):
        return not FlakyCapture.broken


class FlakyTTS(RecordingTTS):
    failing = False

    def _speak(self, text):
        if FlakyTTS.failing:
            raise RuntimeError("argmax-cli tts: model failed to load")
        return super()._speak(text)


class Base(unittest.TestCase):
    use_llm = False

    def setUp(self):
        from integration.__main__ import build_app
        self.tmp = Path(tempfile.mkdtemp(prefix="faraday-crash-"))
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        self.lm = ToggleServer(
            get=lambda h: (200, {"data": [{"id": "ternary-bonsai-2-9b-mlx"}]}),
            post=lambda h: (200, {"choices": [{"message": {"content": "One person holds a box at the door."},
                                               "finish_reason": "stop"}]}))
        self.argmax = ToggleServer(post=lambda h: (200, {"text": "Hey Faraday, I'm heading out."}))
        for prof in s["llm"]["profiles"].values():
            prof["base_url"] = self.lm.base
        for key in ("mac", "windows"):
            s["stt"][key] = {"kind": "argmax", "url": self.argmax.base + "/v1/audio/transcriptions", "timeout_s": 3}
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0, led_settle_s=0.3)
        s["audio"]["listen"] = {"max_s": 15, "silence_s": 1.0, "no_speech_s": 3.0, "after_speech_s": 0.2}
        s["audio"]["wake"] = {"enabled": True, "phrase": "hey faraday", "silence_s": 0.6, "max_s": 10}
        s["audio"]["voice_check"] = {"enrollment": str(self.tmp / "none.wav")}
        s["camera"].update(idle_s=30, stream_fps=20)
        s["llm"]["vision_timeout_s"] = 3
        s["paths"] = {"captures": str(self.tmp / "captures"), "logs": str(self.tmp / "logs")}
        s["dashboard"] = dict(s.get("dashboard", {}), port=0)
        s["integration"] = {"lights_off_at_start": False, "health_interval_s": 0.2, "health_grace_s": 0}
        (self.tmp / "logs").mkdir(parents=True)
        FlakyCapture.broken = False
        FlakyTTS.failing = False
        scene = cv2.imread(str(make_scene(self.tmp / "door.jpg")))
        self.said, self.out = [], []
        self.mic = LiveMic(speed=4.0)
        self.fl = FakeFlipper(s, charge_poll_s=0)
        args = argparse.Namespace(log=str(self.tmp / "logs" / "events.jsonl"), offline=not self.use_llm,
                                  mode=Mode.HOME, no_dashboard=False, port=0, no_mic=False, no_wake=False,
                                  no_controls=False, controls_port=0, no_detect=False, fresh=False)
        self.app = build_app(args, s, parts={
            "flipper": self.fl, "capture_factory": lambda *a: FlakyCapture([scene]),
            "speaker": lambda gate: FlakyTTS(gate, self.said), "stream_factory": self.mic,
            "chime": lambda: None})
        self.app.out = self.out.append
        self.app.start()
        self.app.controls.start()
        self.app.voice.start_wake()
        self.assertTrue(self.fl.connected.wait(5))

    def tearDown(self):
        try:
            self.app.stop()
        finally:
            self.lm.close()
            self.argmax.close()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def printed(self, text):
        return any(text in line for line in self.out)

    def events(self, typ):
        return [e for e in self.app.pipeline.log.recent(500) if e["type"] == typ]

    def still_running(self):
        """The app still does its job: a spoken line goes out."""
        n = len(self.said)
        self.app._on_action(Action("speak", "rules", {"text": f"Still here {time.monotonic_ns()}."}))
        return wait_until(lambda: len(self.said) > n, 5)

    def health(self, part):
        import urllib.request
        with urllib.request.urlopen(self.app.controls.url + "api/status", timeout=5) as r:
            return json.loads(r.read())["health"].get(part, {})


class Flipper(Base):
    def test_unplugged_mid_demo_then_back(self):
        self.fl.device.unplug()
        self.assertTrue(wait_until(lambda: self.printed("[health] Flipper DOWN")))
        self.assertFalse(self.health("flipper")["ok"])
        self.assertTrue(self.still_running())
        self.assertTrue(self.app.control("porch_light", True))         # queued meanwhile
        self.fl.device.plug()
        self.assertTrue(wait_until(lambda: self.printed("[health] Flipper back"), 10))
        self.assertTrue(wait_until(lambda: "outlet1_on" in self.fl.device.tx))   # sent once back
        self.fl.device.press_doorbell()
        self.assertTrue(wait_until(lambda: self.events("doorbell")))


class Camera(Base):
    def test_camera_fails_then_back(self):
        FlakyCapture.broken = True
        self.app._on_action(Action("take_photo", "dashboard", {"reason": "who's at the door"}))
        self.assertTrue(wait_until(lambda: any(e["data"]["description"] == "Camera unavailable."
                                               for e in self.events("photo_described")), 15))
        self.assertTrue(wait_until(lambda: self.printed("[health] Camera DOWN")))
        self.assertTrue(self.still_running())
        FlakyCapture.broken = False
        self.assertTrue(wait_until(lambda: self.printed("[health] Camera back"), 10))
        self.app._on_action(Action("take_photo", "dashboard", {"reason": "who's at the door"}))
        self.assertTrue(wait_until(lambda: any(e["data"]["photo_path"] for e in self.events("photo_described")), 15))


class ArgmaxSTT(Base):
    def test_stt_server_dies_then_back(self):
        self.argmax.down = True
        self.assertTrue(wait_until(lambda: self.printed("[health] Argmax STT DOWN")))
        self.mic.say(tone(300, 1.2, self.tmp / "a.wav"))                 # "Hey Faraday, I'm heading out."
        time.sleep(2.0)
        self.assertEqual(self.app.pipeline.modes.mode, Mode.HOME)        # nothing understood, no crash
        self.assertTrue(self.still_running())
        self.argmax.down = False
        self.assertTrue(wait_until(lambda: self.printed("[health] Argmax STT back")))
        self.mic.say(tone(300, 1.2, self.tmp / "b.wav"))
        self.assertTrue(wait_until(lambda: self.app.pipeline.modes.mode == Mode.AWAY, 15))


class ArgmaxTTS(Base):
    def test_speech_command_fails_then_back(self):
        FlakyTTS.failing = True
        self.app._on_action(Action("speak", "rules", {"text": "Hello there."}))
        self.assertTrue(wait_until(lambda: self.printed("[health] Argmax TTS DOWN")))
        self.fl.device.press_doorbell()                                   # the rest keeps working
        self.assertTrue(wait_until(lambda: self.events("doorbell")))
        FlakyTTS.failing = False
        self.assertTrue(self.still_running())
        self.assertTrue(wait_until(lambda: self.printed("[health] Argmax TTS back")))


class Bonsai(Base):
    use_llm = True

    def test_lm_studio_dies_then_back(self):
        self.lm.down = True
        self.assertTrue(wait_until(lambda: self.printed("[health] Bonsai (LM Studio) DOWN")))
        self.app._on_action(Action("take_photo", "dashboard", {"reason": "who's at the door"}))
        self.assertTrue(wait_until(lambda: self.events("photo_described"), 20))
        first = self.events("photo_described")[-1]
        self.assertNotEqual(first["data"].get("described_by"), "bonsai")  # fell back, didn't hang
        self.assertTrue(self.still_running())
        self.lm.down = False
        self.assertTrue(wait_until(lambda: self.printed("[health] Bonsai (LM Studio) back")))
        self.app._on_action(Action("take_photo", "dashboard", {"reason": "who's at the door"}))
        self.assertTrue(wait_until(lambda: any(e["data"].get("described_by") == "bonsai"
                                               for e in self.events("photo_described")), 20))


if __name__ == "__main__":
    unittest.main()
