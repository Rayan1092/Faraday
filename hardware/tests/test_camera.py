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
import time
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

    def __init__(self, content="A brown package sits by the door. Nobody is there.", status=200,
                 reasoning=None, finish_reason="stop", delay_s=0.0, models=None, error_body=None):
        self.content, self.status, self.requests = content, status, []
        self.reasoning, self.finish_reason, self.delay_s = reasoning, finish_reason, delay_s
        self.models = models or []          # /api/v0/models "data"
        self.error_body = error_body
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                raw = json.dumps({"data": outer.models}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                time.sleep(outer.delay_s)
                msg = {"content": outer.content}
                if outer.reasoning is not None:
                    msg["reasoning_content"] = outer.reasoning
                raw = json.dumps(outer.error_body if outer.error_body is not None else
                                 {"choices": [{"message": msg, "finish_reason": outer.finish_reason}]}).encode()
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

    # ---- Mac 2026-11-14: the 9B gave "Photo saved." with no error shown ------------
    def describe_with(self, expect_warning=True, **kw):
        lm = FakeLMStudio(**kw)
        try:
            v = Vision(lm.url, "bonsai-9b")
            with self.assertLogs("hardware.vision", "WARNING") if expect_warning else _nothing():
                text = v.describe(self.photo)
        finally:
            lm.close()
        return text, v.last, lm

    def test_default_token_cap_leaves_room_for_thinking(self):
        text, last, lm = self.describe_with(expect_warning=False)
        self.assertEqual(lm.requests[0]["max_tokens"], 300)
        self.assertEqual(last["finish_reason"], "stop")
        self.assertNotIn("error", last)

    def test_thinking_only_answer_says_why(self):
        text, last, _ = self.describe_with(content="", reasoning="The user wants a description. " * 20,
                                           finish_reason="length")
        self.assertIsNone(text)
        self.assertIn("vision_max_tokens", last["error"])
        self.assertIn("finish_reason=length", last["error"])
        self.assertGreater(last["reasoning_chars"], 100)

    def test_unclosed_think_in_content_is_not_a_description(self):
        text, last, _ = self.describe_with(content="<think>The image shows a door and", finish_reason="length")
        self.assertIsNone(text)
        self.assertIn("vision_max_tokens", last["error"])

    def test_closed_think_then_answer_is_used(self):
        text, _, _ = self.describe_with(content="<think>hmm</think>A man holds a box.", expect_warning=False)
        self.assertEqual(text, "A man holds a box.")

    def test_http_error_body_is_kept(self):
        text, last, _ = self.describe_with(status=400, error_body={"error": "Model does not support images"})
        self.assertIsNone(text)
        self.assertIn("HTTP 400", last["error"])
        self.assertIn("does not support images", last["error"])

    def test_timeout_is_named(self):
        lm = FakeLMStudio(delay_s=1.5)
        try:
            v = Vision(lm.url, "bonsai-9b", timeout_s=0.5)
            with self.assertLogs("hardware.vision", "WARNING"):
                self.assertIsNone(v.describe(self.photo))
        finally:
            lm.close()
        self.assertIn("timed out after", v.last["error"])
        self.assertIn("vision_timeout_s", v.last["error"])

    def test_settings_keys(self):
        s = {"llm": {"profile": "mac", "profiles": {"mac": {"base_url": "http://x:1", "model": "nine"}},
                     "timeout_s": 30, "vision_timeout_s": 90, "vision_max_tokens": 500}}
        v = Vision.from_settings(s)
        self.assertEqual((v.model, v.timeout_s, v.max_tokens), ("nine", 90.0, 500))


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CheckVision(Base):
    """python -m hardware.check vision: prints why vision fails."""

    def run_check(self, lm, model="ternary-bonsai-2-9b-mlx"):
        import contextlib
        import io
        from hardware import check
        s = {"llm": {"profile": "mac", "profiles": {"mac": {"base_url": lm.url, "model": model}},
                     "timeout_s": 30, "reasoning_effort": "none"}}
        out = io.StringIO()
        orig = check.load_settings
        check.load_settings = lambda: s
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = check.main(["vision", str(self.photo)])
        finally:
            check.load_settings = orig
        return code, out.getvalue()

    def test_ok_prints_model_state_and_description(self):
        lm = FakeLMStudio(models=[{"id": "ternary-bonsai-2-9b-mlx", "state": "loaded", "type": "vlm",
                                   "arch": "qwen3_vl", "quantization": "2bit"}])
        try:
            code, out = self.run_check(lm)
        finally:
            lm.close()
        self.assertEqual(code, 0, out)
        self.assertIn("model=ternary-bonsai-2-9b-mlx", out)
        self.assertIn("state=loaded type=vlm", out)
        self.assertIn("max_tokens=300", out)
        self.assertIn("description: A brown package sits by the door.", out)

    def test_failure_prints_the_reason(self):
        lm = FakeLMStudio(content="", reasoning="thinking " * 50, finish_reason="length",
                          models=[{"id": "ternary-bonsai-2-2b-mlx", "state": "loaded", "type": "llm"}])
        try:
            code, out = self.run_check(lm)
        finally:
            lm.close()
        self.assertEqual(code, 1, out)
        self.assertIn("is NOT in LM Studio's model list. Loaded: ternary-bonsai-2-2b-mlx", out)
        self.assertIn("finish_reason=length", out)
        self.assertIn("FAILED: empty answer", out)


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

    def test_no_camera_still_sends_event_so_visitor_gets_greeted(self):
        from hardware.camera import NO_CAMERA

        class Broken:
            def snapshot(self):
                raise RuntimeError("no such device")

        for cam in (SavedFileCamera(None), Broken()):
            ev = PhotoCamera(cam, None).take(self.action())
            self.assertEqual(validate_event(ev.to_dict()), [])
            self.assertEqual((ev.data["photo_path"], ev.data["description"], ev.source, ev.caused_by),
                             ("", NO_CAMERA, "camera", "act_12345678"))


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
