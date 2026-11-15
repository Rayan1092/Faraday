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
                 reasoning=None, finish_reason="stop", delay_s=0.0, models=None, error_body=None,
                 refuse_extras=False):
        self.content, self.status, self.requests = content, status, []
        self.refuse_extras = refuse_extras          # 400 when chat_template_kwargs is sent
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
                if outer.refuse_extras and "chat_template_kwargs" in body:
                    raw = b'{"error": "Unrecognized key chat_template_kwargs"}'
                    self.send_response(400)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
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


class FakeDetector:
    """Stands in for hardware.detect.Detector.analyse()."""

    def __init__(self, people=0, faces=None, things=()):
        self.people, self.faces, self.things = people, faces, things

    def analyse(self, frame):
        dets = ([{"label": "person", "count": self.people, "confidence": 0.9}] if self.people else [])
        dets += [{"label": t, "count": 1, "confidence": 0.6} for t in self.things]
        out = {"people": self.people, "detections": dets, "boxes": [],
               "close_people": self.people}
        if self.faces is not None:
            out["faces"] = self.faces
        return out


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
    def test_short_description_keeps_every_fact_that_fits(self):
        from hardware.vision import short_description
        # review of #43: a two-sentence cap dropped "face covered" (Dell's threat cue)
        three = "One person is at the door. They hold a box. Their face is covered by a hood."
        self.assertEqual(short_description(three), three)
        self.assertEqual(short_description("A man in a U.S. Postal Service uniform holds a package. "
                                           "Their face is visible."),
                         "A man in a U.S. Postal Service uniform holds a package. Their face is visible.")
        long = "One person stands at the door holding a large box. " * 8
        cut = short_description(long)
        self.assertLessEqual(len(cut), 280)
        self.assertTrue(cut.endswith("box."))
        self.assertEqual(short_description('  "<think>x</think> A box on the step "'), "A box on the step")
        self.assertEqual(short_description("**Description:** One person holds a box."),
                         "One person holds a box.")
        self.assertIsNone(short_description("   "))
        self.assertLessEqual(len(short_description("word " * 100)), 281)

    def test_reasoning_is_stripped(self):
        from hardware.vision import short_description, strip_reasoning
        # a template that only shows the closing tag (thinking printed as plain text)
        self.assertEqual(short_description("Thinking Process: 1. look...\n</think>\n\nNobody is there."),
                         "Nobody is there.")
        self.assertIsNone(short_description("<think>The image shows a door and"))   # unfinished
        self.assertEqual(strip_reasoning("<think>a</think>B<think>c</think>D"), "D")

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
        self.assertEqual(text, "A brown package sits by the door. Nobody is there.")
        content = lm.requests[0]["messages"][0]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(lm.requests[0]["reasoning_effort"], "none")
        self.assertEqual(lm.requests[0]["chat_template_kwargs"], {"enable_thinking": False})
        for words in ("how many people", "Nobody is there.", "face is visible or covered", "holds"):
            self.assertIn(words, content[0]["text"])

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
        self.assertEqual(lm.requests[0]["max_tokens"], 768)
        self.assertEqual(last["finish_reason"], "stop")
        self.assertNotIn("error", last)

    def test_cut_off_while_thinking_retries_once_with_more_tokens(self):
        text, last, lm = self.describe_with(content="", reasoning="Let me look. " * 50,
                                            finish_reason="length")
        self.assertIsNone(text)
        self.assertEqual([r["max_tokens"] for r in lm.requests], [768, 2048])
        self.assertEqual(last["attempts"], 2)

    def test_thinking_cut_off_without_tags_is_not_a_description(self):
        """Review of #43 (HIGH): plain-text thinking that hit max_tokens before any
        </think> was taken as the description."""
        text, last, lm = self.describe_with(
            content="Thinking Process: 1. Analyze the Request: The user wants a description of the photo.",
            finish_reason="length")
        self.assertIsNone(text)
        self.assertEqual(last["attempts"], 2)                   # retried with 2048 first
        text, last, lm = self.describe_with(content="Thinking Process: the photo shows", finish_reason="stop")
        self.assertIsNone(text)

    def test_retry_only_if_there_is_time_for_it(self):
        lm = FakeLMStudio(content="", reasoning="hmm " * 50, finish_reason="length", delay_s=0.6)
        try:
            v = Vision(lm.url, "bonsai-9b", timeout_s=1.5)
            with self.assertLogs("hardware.vision", "WARNING"):
                self.assertIsNone(v.describe(self.photo))
        finally:
            lm.close()
        self.assertEqual(len(lm.requests), 1)                   # 0.9 s left < 2 x 0.6 + 1

    def test_a_400_for_another_reason_keeps_the_thinking_switch(self):
        lm = FakeLMStudio(status=400, error_body={"error": "No models loaded"})
        try:
            v = Vision(lm.url, "bonsai-9b")
            with self.assertLogs("hardware.vision", "WARNING"):
                self.assertIsNone(v.warm_up())
        finally:
            lm.close()
        self.assertTrue(v._extras_ok)                           # still sent next time
        self.assertEqual(["chat_template_kwargs" in r for r in lm.requests], [True, False])

    def test_server_refusing_the_thinking_switch_is_retried_without_it(self):
        lm = FakeLMStudio(refuse_extras=True)
        try:
            v = Vision(lm.url, "bonsai-9b")
            self.assertEqual(v.describe(self.photo), "A brown package sits by the door. Nobody is there.")
            v.describe(self.photo)
        finally:
            lm.close()
        self.assertEqual(["chat_template_kwargs" in r for r in lm.requests], [True, False, False])

    def test_detector_hint_goes_into_the_prompt(self):
        lm = FakeLMStudio()
        try:
            Vision(lm.url, "bonsai-9b").describe(self.photo, hint="The object detector found: 1 person.")
        finally:
            lm.close()
        self.assertTrue(lm.requests[0]["messages"][0]["content"][0]["text"]
                        .endswith("Hint: The object detector found: 1 person."))

    def test_warm_up_sends_a_tiny_image(self):
        lm = FakeLMStudio(content="Gray.")
        try:
            secs = Vision(lm.url, "bonsai-9b").warm_up()
        finally:
            lm.close()
        self.assertIsNotNone(secs)
        self.assertEqual(lm.requests[0]["max_tokens"], 16)
        self.assertEqual(lm.requests[0]["messages"][0]["content"][1]["type"], "image_url")

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
        self.assertIn("max_tokens=768", out)
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

    def test_detections_go_on_the_event_and_into_the_prompt(self):
        lm = FakeLMStudio(content="One person holds a box. Their face is visible.")
        try:
            ev = PhotoCamera(SavedFileCamera(self.photo), Vision(lm.url, "bonsai-test"),
                             FakeDetector(people=1, faces=1, things=("backpack",))).take(self.action())
        finally:
            lm.close()
        self.assertEqual(validate_event(ev.to_dict()), [])
        d = ev.data
        self.assertEqual((ev.source, d["described_by"], d["people"], d["faces"]), ("vision", "bonsai", 1, 1))
        self.assertEqual([x["label"] for x in d["detections"]], ["person", "backpack"])
        self.assertIn("Hint: The object detector found: 1 person, 1 backpack. Visible faces: 1.",
                      lm.requests[0]["messages"][0]["content"][0]["text"])

    def test_vision_down_with_detector_still_describes(self):
        cam = PhotoCamera(SavedFileCamera(self.photo), Vision("http://127.0.0.1:9", "m", timeout_s=2),
                          FakeDetector(people=0))
        with self.assertLogs("hardware.vision", "WARNING"):
            ev = cam.take(self.action())
        self.assertEqual(validate_event(ev.to_dict()), [])
        self.assertEqual((ev.data["description"], ev.source, ev.data["described_by"], ev.data["model"]),
                         ("Nobody is there.", "vision", "detector", None))
        cam = PhotoCamera(SavedFileCamera(self.photo), None, FakeDetector(people=2, faces=0))
        ev = cam.take(self.action())
        self.assertEqual(ev.data["description"],
                         "2 people are at the door. No face is visible: covered or turned away.")

    def test_failed_detector_is_not_nobody(self):
        """Review of #43: a detector that raises must not become "Nobody is there."."""
        class Broken:
            def analyse(self, frame):
                return None                                     # Detector.analyse on failure
        cam = PhotoCamera(SavedFileCamera(self.photo), Vision("http://127.0.0.1:9", "m", timeout_s=2),
                          Broken())
        with self.assertLogs("hardware.vision", "WARNING"):
            ev = cam.take(self.action())
        self.assertEqual((ev.data["description"], ev.source, ev.data["described_by"], ev.data["people"]),
                         (NO_VISION, "camera", None, None))

    def test_no_hint_when_the_detector_saw_nothing(self):
        lm = FakeLMStudio(content="A dark porch. Nobody is there.")
        try:
            ev = PhotoCamera(SavedFileCamera(self.photo), Vision(lm.url, "bonsai-test"),
                             FakeDetector(people=0)).take(self.action())
        finally:
            lm.close()
        self.assertNotIn("Hint:", lm.requests[0]["messages"][0]["content"][0]["text"])
        self.assertEqual((ev.data["people"], ev.data["detections"]), (0, []))

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
