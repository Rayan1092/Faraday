"""Controls server end to end over HTTP: MJPEG stream, intercom, outlets, Listen,
CORS. Fake camera, fake speaker, fake Flipper. No hardware.

    python -m unittest integration.tests.test_controls
"""
from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from shared import Mode

from hardware.audio import MicGate
from hardware.camera import CameraFeed
from hardware.flipper.fake import FakeFlipper
from hardware.intercom import Intercom
from hardware.settings import CONFIG_DIR
from hardware.tests.test_media import FakeCapture, FakeOutput, wait_until
from integration.app import App
from integration.controls import BOUNDARY, ControlsServer
from integration.standins import StandInCamera
from log import EventLog
from rules import RuleStore


class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="faraday-controls-"))
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0)
        self.fl = FakeFlipper(s, charge_poll_s=0)
        self.app = App(s, self.fl, camera=StandInCamera(), use_llm=False, mode=Mode.HOME,
                       event_log=EventLog(self.tmp / "events.jsonl", s["security"]["passphrase"]),
                       rules=RuleStore(self.tmp / "rules.json"), dashboard=True, port=0,
                       out=lambda *_: None)
        self.app.start()
        self.assertTrue(self.fl.connected.wait(5))
        self.gate = MicGate()
        self.feed = CameraFeed(photos_dir=self.tmp, fps=30, idle_s=5,
                               capture_factory=lambda *a: FakeCapture())
        self.intercom = Intercom(self.gate, stream_factory=FakeOutput, hold_s=0.3)
        self.dash_port = self.app.board.httpd.server_address[1]
        self.srv = ControlsServer("127.0.0.1", 0, feed=self.feed, intercom=self.intercom,
                                  control=self.app.control,
                                  outlet_state=self.app.outlet_states,
                                  dashboard_port=self.dash_port).start()

    def tearDown(self):
        self.srv.stop()
        self.feed.stop()
        self.app.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=10)

    def request(self, method, path, body=None, headers=None):
        c = self.conn()
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def post_json(self, path, obj, origin=None):
        h = {"Content-Type": "application/json", "X-Faraday": "1"}
        if origin:
            h["Origin"] = origin
        r, data = self.request("POST", path, json.dumps(obj).encode(), h)
        return r, json.loads(data or b"{}")

    # ---- camera ------------------------------------------------------------------
    def test_mjpeg_stream_delivers_several_frames(self):
        c = self.conn()
        c.request("GET", "/stream.mjpg")
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertIn(f"boundary={BOUNDARY}", r.getheader("Content-Type"))
        buf = b""
        while buf.count(b"\xff\xd8") < 3:
            buf += r.read1(65536)
        c.close()
        self.assertGreaterEqual(buf.count(f"--{BOUNDARY}".encode()), 3)
        self.assertEqual(self.feed.opens, 1)

    def test_frame_and_photo_share_the_camera(self):
        r, data = self.request("GET", "/frame.jpg")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "image/jpeg"))
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertIsNotNone(self.feed.snapshot())
        self.assertEqual(self.feed.opens, 1)

    # ---- intercom ------------------------------------------------------------------
    def test_intercom_end_to_end_plays_audio_and_mutes_door_mic(self):
        t = np.arange(8000) / 16000
        pcm = (0.3 * np.sin(2 * np.pi * 300 * t) * 32767).astype(np.int16)
        h = {"Content-Type": "application/octet-stream", "X-Faraday": "1", "X-Sample-Rate": "16000"}
        for i in range(0, len(pcm), 2048):
            r, data = self.request("POST", "/api/intercom", pcm[i:i + 2048].tobytes(), h)
            self.assertEqual(r.status, 200, data)
            self.assertTrue(self.gate.muted)
        r, _ = self.request("POST", "/api/intercom/end", b"{}", {"X-Faraday": "1"})
        self.assertEqual(r.status, 200)
        self.assertTrue(wait_until(lambda: not self.intercom.active, 10))
        self.assertFalse(self.gate.muted)
        played = np.concatenate(FakeOutput.last.played)
        heard, sent = np.trim_zeros(played, 'f'), np.trim_zeros(pcm, 'f')
        self.assertTrue(np.array_equal(heard[:len(sent)], sent))   # every sample, in order

    def test_intercom_needs_header_and_sane_rate(self):
        r, _ = self.request("POST", "/api/intercom", b"\x00\x00" * 100, {"X-Sample-Rate": "16000"})
        self.assertEqual(r.status, 403)
        r, _ = self.request("POST", "/api/intercom", b"\x00\x00" * 100,
                            {"X-Faraday": "1", "X-Sample-Rate": "999999"})
        self.assertEqual(r.status, 400)
        self.assertFalse(self.intercom.active)

    # ---- framing (owner 2026-11-15: a page cropped the stream, boxes got cut) -----------
    def test_stream_is_the_whole_frame_and_status_says_its_size(self):
        import cv2
        r, status = self.request("GET", "/api/status")
        self.assertIsNone(json.loads(status)["frame"])                 # no frame yet
        r, jpg = self.request("GET", "/frame.jpg")
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(img.shape[:2], (72, 128))                     # FakeCapture's whole frame
        r, status = self.request("GET", "/api/status")
        self.assertEqual(json.loads(status)["frame"], {"width": 128, "height": 72})
        r, page = self.request("GET", "/")
        self.assertIn(b"object-fit: contain", page)                    # the reference page never crops
        self.assertNotIn(b"object-fit: cover", page)

    # ---- outlets / listen ------------------------------------------------------------
    def test_control_switches_outlet_once(self):
        r, body = self.post_json("/api/control", {"outlet": "living_room_lamp", "on": True})
        self.assertEqual((r.status, body["changed"]), (200, True))
        self.assertTrue(wait_until(lambda: self.fl.device.tx == ["outlet2_on"]))
        r, body = self.post_json("/api/control", {"outlet": "living_room_lamp", "on": True})
        self.assertEqual(body["changed"], False)                      # never resent
        r, status = self.request("GET", "/api/status")
        self.assertEqual(json.loads(status)["outlets"], {"living_room_lamp": "outlet_on"})
        r, body = self.post_json("/api/control", {"outlet": "living_room_lamp", "on": "yes"})
        self.assertEqual(r.status, 400)

    def test_cors_only_for_dashboard_page(self):
        good = f"http://127.0.0.1:{self.dash_port}"
        r, _ = self.request("OPTIONS", "/api/intercom", headers={
            "Origin": good, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-faraday, x-sample-rate, content-type"})
        self.assertEqual(r.status, 204)
        self.assertEqual(r.getheader("Access-Control-Allow-Origin"), good)
        self.assertIn("X-Sample-Rate", r.getheader("Access-Control-Allow-Headers"))
        r, _ = self.request("OPTIONS", "/api/intercom", headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status, 403)
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
        r, _ = self.post_json("/api/control", {"outlet": "porch_light", "on": True},
                              origin=f"http://192.168.1.20:{self.dash_port}")
        self.assertEqual(r.getheader("Access-Control-Allow-Origin"),
                         f"http://192.168.1.20:{self.dash_port}")     # phone on the LAN

    def test_reference_page_is_offline_and_complete(self):
        r, page = self.request("GET", "/")
        self.assertEqual(r.status, 200)
        text = page.decode("utf-8")
        for needle in ("/stream.mjpg", "/api/intercom", "/api/control",
                       "getUserMedia", "X-Sample-Rate"):
            self.assertIn(needle, text)
        self.assertNotRegex(text, r"https?://")                          # no external assets


if __name__ == "__main__":
    unittest.main()
