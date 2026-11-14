"""Camera + vision tests with a saved image file and a fake LM Studio. No webcam.

    python -m unittest hardware.tests.test_camera
    set FARADAY_LIVE_VISION=1 to also ask the real LM Studio from settings.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

from shared import Action, validate_event

from hardware.camera import NO_VISION, PhotoCamera, relative
from hardware.settings import REPO_ROOT, load_settings
from hardware.vision import Vision, encode_image, one_sentence


def make_scene(path: Path) -> Path:
    img = np.full((720, 1280, 3), 200, np.uint8)
    cv2.rectangle(img, (0, 500), (1280, 720), (90, 90, 90), -1)
    cv2.rectangle(img, (520, 340), (760, 520), (40, 90, 160), -1)
    cv2.putText(img, "PACKAGE", (535, 445), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3)
    cv2.imwrite(str(path), img)
    return path


class FakeLMStudio:
    """Answers /v1/chat/completions with a canned reply (or an error code)."""

    def __init__(self, content="A brown package sits by the door. Nobody is there.", status=200):
        self.content, self.status, self.requests = content, status, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                raw = json.dumps({"choices": [{"message": {"content": outer.content}}]}).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class SavedFileCamera:
    def __init__(self, path):
        self.path = path

    def snapshot(self):
        return self.path


class Base(unittest.TestCase):
    def setUp(self):
        # inside the repo's captures/ (gitignored) so relative() gives captures/...
        self.dir = Path(tempfile.mkdtemp(prefix="test-", dir=REPO_ROOT / "captures")) \
            if (REPO_ROOT / "captures").exists() else Path(tempfile.mkdtemp())
        self.photo = make_scene(self.dir / "scene.jpg")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


(REPO_ROOT / "captures").mkdir(exist_ok=True)


class Helpers(Base):
    def test_one_sentence(self):
        self.assertEqual(one_sentence("A man. He waves."), "A man.")
        self.assertEqual(one_sentence('  "<think>x</think> A box on the step "'), "A box on the step")
        self.assertIsNone(one_sentence("   "))
        self.assertLessEqual(len(one_sentence("word " * 100)), 201)

    def test_image_is_shrunk_for_the_prompt(self):
        raw = base64.b64decode(encode_image(self.photo))
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(max(img.shape[:2]), 512)

    def test_relative_path_uses_forward_slashes(self):
        rel = relative(self.photo)
        self.assertTrue(rel.startswith("captures/") and "\\" not in rel, rel)


class VisionClient(Base):
    def test_describe_sends_image_and_returns_one_sentence(self):
        lm = FakeLMStudio()
        try:
            text = Vision(lm.url, "bonsai-test").describe(self.photo)
        finally:
            lm.close()
        self.assertEqual(text, "A brown package sits by the door.")
        content = lm.requests[0]["messages"][0]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(lm.requests[0]["reasoning_effort"], "none")

    def test_server_error_gives_none(self):
        lm = FakeLMStudio(status=500)
        try:
            self.assertIsNone(Vision(lm.url, "m").describe(self.photo))
        finally:
            lm.close()

    def test_unreachable_gives_none(self):
        self.assertIsNone(Vision("http://127.0.0.1:9", "m", timeout_s=2).describe(self.photo))


class PhotoEvents(Base):
    def action(self):
        return Action("take_photo", "rules", {"reason": "doorbell"}, id="act_12345678")

    def test_described_photo_event(self):
        lm = FakeLMStudio()
        try:
            ev = PhotoCamera(SavedFileCamera(self.photo), Vision(lm.url, "bonsai-test")).take(self.action())
        finally:
            lm.close()
        self.assertEqual(validate_event(ev.to_dict()), [])
        self.assertEqual((ev.source, ev.caused_by, ev.data["model"]), ("vision", "act_12345678", "bonsai-test"))
        self.assertTrue(ev.data["photo_path"].endswith("scene.jpg"))

    def test_vision_down_keeps_photo_and_says_photo_saved(self):
        cam = PhotoCamera(SavedFileCamera(self.photo), Vision("http://127.0.0.1:9", "m", timeout_s=2))
        ev = cam.take(self.action())
        self.assertEqual(validate_event(ev.to_dict()), [])
        self.assertEqual((ev.data["description"], ev.source, ev.data["model"]), (NO_VISION, "camera", None))
        self.assertTrue(self.photo.exists())

    def test_no_camera_no_event(self):
        self.assertIsNone(PhotoCamera(SavedFileCamera(None), None).take(self.action()))


@unittest.skipUnless(os.environ.get("FARADAY_LIVE_VISION"), "set FARADAY_LIVE_VISION=1 for live LM Studio")
class LiveVision(Base):
    def test_real_bonsai_describes_saved_image(self):
        s = load_settings()
        # e.g. FARADAY_LIVE_VISION=nv_backup on the NV laptop (no settings.json there)
        if os.environ["FARADAY_LIVE_VISION"] in s["llm"]["profiles"]:
            s["llm"]["profile"] = os.environ["FARADAY_LIVE_VISION"]
        text = Vision.from_settings(s).describe(self.photo)
        print("\nBonsai says:", text)
        self.assertTrue(text and "package" in text.lower(), text)


if __name__ == "__main__":
    unittest.main()
