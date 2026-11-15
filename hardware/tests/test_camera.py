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
                 refuse_extras=False, thinks_unless=None, delay_for=None, reply_for=None):
        self.content, self.status, self.requests = content, status, []
        self.delay_for = delay_for          # delay_for(body) -> seconds, instead of delay_s
        self.reply_for = reply_for          # reply_for(body) -> (status, content, finish) or None
        # thinks_unless(body) -> bool: like the Mac's MLX 9B, the reply is all thinking
        # (finish "length", empty answer) unless the request turns thinking off this way
        self.thinks_unless = thinks_unless
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
                time.sleep(outer.delay_for(body) if outer.delay_for else outer.delay_s)
                msg = {"content": outer.content}
                finish = outer.finish_reason
                status = outer.status
                special = outer.reply_for(body) if outer.reply_for else None
                if special is not None:
                    status, msg["content"], finish = special
                if outer.reasoning is not None:
                    msg["reasoning_content"] = outer.reasoning
                if outer.thinks_unless is not None and not outer.thinks_unless(body):
                    msg = {"content": "", "reasoning_content": "The user wants a description. " * 100}
                    finish = "length"
                raw = json.dumps(outer.error_body if outer.error_body is not None else
                                 {"choices": [{"message": msg, "finish_reason": finish}]}).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except ConnectionError:
                    pass                    # the client timed out and left (the timeout test)

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


def isolate_vision_cache(test: unittest.TestCase) -> None:
    """The no-think strategy cache goes to a temp file for this test, never to the
    repo's cache/vision_mode.json (which the Mac's app reads)."""
    from unittest import mock
    import hardware.vision as vision_module
    cache_dir = Path(tempfile.mkdtemp(prefix="faraday-vcache-"))
    patcher = mock.patch.object(vision_module, "CACHE_FILE", cache_dir / "vision_mode.json")
    patcher.start()
    test.addCleanup(patcher.stop)
    test.addCleanup(shutil.rmtree, cache_dir, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        isolate_vision_cache(self)
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


def prefill(body):
    return body["messages"][-1]["role"] == "assistant" and "</think>" in body["messages"][-1]["content"]


def no_think_switch(body):
    return "/no_think" in json.dumps(body["messages"])


class VisionClient(Base):
    def test_one_request_with_every_no_think_switch(self):
        lm = FakeLMStudio()
        try:
            v = Vision(lm.url, "bonsai-test")
            text = v.describe(self.photo)
        finally:
            lm.close()
        self.assertEqual(text, "A brown package sits by the door. Nobody is there.")
        body = lm.requests[0]
        content = body["messages"][0]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual((body["max_tokens"], body["reasoning_effort"]), (120, "none"))
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["messages"][-1], {"role": "assistant", "content": "<think>\n\n</think>\n\n"})
        for words in ("one short factual sentence", "Nobody is there.", "face is visible or covered"):
            self.assertIn(words, content[0]["text"])
        self.assertEqual((len(lm.requests), v.strategy), (1, "prefill"))

    def test_mac_9b_thinking_turned_off_by_the_prefill(self):
        """Mac 2026-11-15: the MLX 9B ignores reasoning_effort for images and thinks
        until max_tokens. The empty think block, prefilled, turns it off."""
        lm = FakeLMStudio(content="One person holds a box at the door.", thinks_unless=prefill)
        try:
            v = Vision(lm.url, "ternary-bonsai-2-9b-mlx")
            self.assertEqual(v.describe(self.photo), "One person holds a box at the door.")
            self.assertEqual(v.strategy, "prefill")
            self.assertEqual(len(lm.requests), 1)                  # the prefill is tried first
            n = len(lm.requests)
            self.assertEqual(v.describe(self.photo), "One person holds a box at the door.")
            self.assertEqual(len(lm.requests), n + 1)             # straight to prefill now
            self.assertEqual(lm.requests[-1]["messages"][-1]["content"], "<think>\n\n</think>\n\n")
        finally:
            lm.close()

    def test_plain_when_the_server_ignores_the_prefill(self):
        lm = FakeLMStudio(thinks_unless=lambda body: body["messages"][-1]["role"] == "user")
        try:
            v = Vision(lm.url, "bonsai-test")
            self.assertIsNotNone(v.describe(self.photo))
        finally:
            lm.close()
        self.assertEqual((v.strategy, len(lm.requests)), ("plain", 2))

    def test_no_think_switch_only_when_set(self):
        """Measured 2026-11-15: /no_think alone doesn't stop Bonsai 2 thinking, so the
        probe skips it; llm.vision_no_think "no_think" still forces it."""
        lm = FakeLMStudio(thinks_unless=no_think_switch)
        try:
            with self.assertLogs("hardware.vision", "WARNING"):
                self.assertIsNone(Vision(lm.url, "bonsai-test").describe(self.photo))
            v = Vision(lm.url, "bonsai-test", strategy="no_think")
            self.assertIsNotNone(v.describe(self.photo))
        finally:
            lm.close()
        self.assertIn("/no_think", lm.requests[-1]["messages"][-1]["content"][0]["text"])

    def test_strategy_remembered_for_the_next_start(self):
        lm = FakeLMStudio(thinks_unless=prefill)
        try:
            Vision(lm.url, "m9b").describe(self.photo)
            again = Vision(lm.url, "m9b")
            self.assertEqual(again.strategy, "prefill")              # no probe needed
            n = len(lm.requests)
            again.describe(self.photo)
            self.assertEqual(len(lm.requests), n + 1)
        finally:
            lm.close()

    def test_strategy_that_stops_working_is_probed_again(self):
        lm = FakeLMStudio(thinks_unless=prefill)
        try:
            v = Vision(lm.url, "m9b", strategy="auto")
            v.strategy = "no_think"                                   # e.g. an old cache entry
            with self.assertLogs("hardware.vision", "WARNING"):
                self.assertIsNotNone(v.describe(self.photo))
            self.assertEqual(v.strategy, "prefill")
        finally:
            lm.close()

    def test_forced_strategy_never_probes(self):
        lm = FakeLMStudio()
        try:
            v = Vision(lm.url, "m", strategy="system")
            v.describe(self.photo)
        finally:
            lm.close()
        self.assertEqual(len(lm.requests), 1)
        self.assertEqual(lm.requests[0]["messages"][0]["role"], "system")

    def test_nothing_works_is_fast_and_says_how_to_fix(self):
        lm = FakeLMStudio(thinks_unless=lambda body: False)
        try:
            v = Vision(lm.url, "m9b")
            with self.assertLogs("hardware.vision", "WARNING") as logs:
                self.assertIsNone(v.describe(self.photo))
            self.assertIn("enable_thinking = false", " ".join(logs.output))
            n = len(lm.requests)
            t0 = time.monotonic()
            self.assertIsNone(v.describe(self.photo))                # skipped: no request, no wait
            self.assertLess(time.monotonic() - t0, 0.5)
            self.assertEqual(len(lm.requests), n)
            self.assertIn("thinks before every answer", v.last["error"])
        finally:
            lm.close()

    def test_disabled_vision_tries_again_in_the_background(self):
        """Fixed in LM Studio meanwhile (template edited, model reloaded): it comes
        back within 5 minutes, and no photo waits for that probe."""
        lm = FakeLMStudio(thinks_unless=lambda body: False)
        try:
            v = Vision(lm.url, "m9b")
            with self.assertLogs("hardware.vision", "WARNING"):
                v.describe(self.photo)
            lm.thinks_unless = prefill                               # fixed
            v.disabled_until = time.monotonic() - 1                  # 5 minutes later
            t0 = time.monotonic()
            self.assertIsNone(v.describe(self.photo))                # this photo: detector
            self.assertLess(time.monotonic() - t0, 0.5)
            deadline = time.monotonic() + 10
            while v.strategy is None and time.monotonic() < deadline:
                time.sleep(0.05)
            with v._lock:
                pass                                                  # the background probe is done
            self.assertEqual((v.strategy, v.disabled_until), ("prefill", 0.0))
            self.assertIsNotNone(v.describe(self.photo))             # the next photo: Bonsai
        finally:
            lm.close()

    def test_warm_up_picks_the_strategy_before_the_first_photo(self):
        lm = FakeLMStudio(content="Nobody is there.", thinks_unless=prefill)
        try:
            v = Vision(lm.url, "m9b")
            self.assertIsNotNone(v.warm_up())
            self.assertEqual(v.strategy, "prefill")
            self.assertIsNotNone(v.answer_s)
            stale = Vision(lm.url, "m9b")
            stale.strategy = "plain"                                 # saved, but it thinks now
            self.assertIsNotNone(stale.warm_up())
            self.assertEqual(stale.strategy, "prefill")
        finally:
            lm.close()

    def test_server_error_gives_none(self):
        lm = FakeLMStudio(status=500)
        try:
            with self.assertLogs("hardware.vision", "WARNING"):
                self.assertIsNone(Vision(lm.url, "m").describe(self.photo))
        finally:
            lm.close()

    def test_unreachable_gives_none_without_trying_every_strategy(self):
        t0 = time.monotonic()
        v = Vision("http://127.0.0.1:9", "m", timeout_s=4)
        with self.assertLogs("hardware.vision", "WARNING"):
            self.assertIsNone(v.describe(self.photo))
        self.assertLess(time.monotonic() - t0, 6.0)
        self.assertEqual(len(v.probe_results), 1)                     # stopped at the first
        self.assertEqual(v.disabled_until, 0.0)                       # no rest: back with the server

    def test_a_timeout_is_no_verdict_about_thinking(self):
        """Review of #64: a slow first image (prefill timed out) must not lead to
        "thinks before every answer" because plain then thought."""
        lm = FakeLMStudio(delay_for=lambda body: 1.2 if prefill(body) else 0.0,
                          thinks_unless=lambda body: False)               # plain would think
        try:
            v = Vision(lm.url, "m9b", timeout_s=3)
            self.assertIsNone(v.probe(per_request=0.5))
        finally:
            lm.close()
        self.assertEqual((len(v.probe_results), v.strategy, v.disabled_until), (1, None, 0.0))
        self.assertIn("timed out", v.probe_results[0]["error"])

    def test_cut_off_reasoning_without_an_opener_is_not_a_description(self):
        """Review of #64: a monologue that runs out of tokens never becomes the
        description, and its strategy is not saved."""
        monologue = ("The image shows a plain door. There are no people visible. I should describe "
                     "this in one sentence as the prompt asks. So the answer is Nobody is there. But "
                     "wait, so I")
        lm = FakeLMStudio(content=monologue, finish_reason="length")
        try:
            v = Vision(lm.url, "m9b")
            with self.assertLogs("hardware.vision", "WARNING"):
                self.assertIsNone(v.describe(self.photo))
        finally:
            lm.close()
        self.assertIsNone(v.strategy)
        self.assertGreater(v.disabled_until, 0.0)                         # rests: photos don't wait
        self.assertNotIn("Prompt Template", v.disabled_why)               # but no "thinks" verdict
        t0 = time.monotonic()
        self.assertIsNone(v.describe(self.photo))
        self.assertLess(time.monotonic() - t0, 0.5)

    def test_real_descriptions_are_not_taken_for_reasoning(self):
        from hardware.vision import REASONING_WORDS, looks_like_thinking
        for text in ("Looking at the camera, one man in a black hoodie holds a brown box.",
                     "So, in the doorway, one woman holds an umbrella.",
                     "I see one man at the front door holding a brown box.",
                     "One delivery driver with an I.D. badge holds a box.",
                     "One woman stands at the door, appearing to wait for an answer."):
            self.assertFalse(looks_like_thinking(text), text)
            self.assertIsNone(REASONING_WORDS.search(text), text)
        for text in ("I should describe this in one sentence.", "But wait, the prompt says",
                     "Let me look again.", "So the answer is Nobody is there."):
            self.assertIsNotNone(REASONING_WORDS.search(text), text)

    def test_empty_cut_off_reply_is_hidden_thinking(self):
        lm = FakeLMStudio(content="", finish_reason="length")
        try:
            v = Vision(lm.url, "m9b")
            with self.assertLogs("hardware.vision", "WARNING") as logs:
                self.assertIsNone(v.describe(self.photo))
        finally:
            lm.close()
        self.assertIn("enable_thinking = false", " ".join(logs.output))   # the real verdict

    def test_whole_sentences_never_invents_one(self):
        from hardware.vision import whole_sentences
        run_on = ("A man in a dark hooded jacket stands at the front door holding a large brown "
                  "cardboard box with both hands " * 4).strip()             # ~400 chars, no stop
        for text, want in (("1. One person holds a box and his face is", None),
                           ('"Nobody is there." The porch light is on and the', "Nobody is there."),
                           ("One man in a Dr. Pepper shirt holds a can and", None),
                           ("One man waits at the door of Apt B. He holds a", None),
                           (run_on, None),
                           ("A man in a U.S. Postal Service uniform holds a package and looks at the", None),
                           ("One person holds a box. It is about 2.", "One person holds a box."),
                           ("A person waits... and then the", None),
                           ("One person stands at the door. They hold a", "One person stands at the door."),
                           ("", None)):
            self.assertEqual(whole_sentences(text), want, text)

    def test_an_answer_cut_after_the_empty_think_block_keeps_whole_sentences(self):
        lm = FakeLMStudio(content="<think>\n\n</think>\n\nNobody is there. The porch light is on and the",
                          finish_reason="length")
        try:
            self.assertEqual(Vision(lm.url, "m", strategy="plain").describe(self.photo), "Nobody is there.")
        finally:
            lm.close()

    def test_a_long_answer_is_not_thinking(self):
        """Review of #64: one wordy answer (120 tokens, no reasoning) must not turn
        vision off or drop the saved strategy."""
        long = ("One person in a dark hooded jacket stands at the door. They hold a brown box "
                "with both hands and look at the camera while the porch light shines on")
        lm = FakeLMStudio(content=long, finish_reason="length")
        try:
            v = Vision(lm.url, "m9b", strategy="auto")
            v.strategy = "prefill"
            self.assertEqual(v.describe(self.photo),
                             "One person in a dark hooded jacket stands at the door.")
            lm.content = "One person in a dark hooded jacket stands at the door holding"
            with self.assertLogs("hardware.vision", "WARNING"):
                self.assertIsNone(v.describe(self.photo))                 # no whole sentence
            self.assertEqual((v.strategy, v.disabled_until), ("prefill", 0.0))
            self.assertEqual(len(lm.requests), 2)                         # no re-probe
        finally:
            lm.close()

    def test_a_server_error_is_no_verdict_but_the_next_is_tried(self):
        lm = FakeLMStudio(thinks_unless=lambda body: False,
                          reply_for=lambda body: (500, "template error", "stop") if prefill(body) else None)
        try:
            v = Vision(lm.url, "m9b")
            with self.assertLogs("hardware.vision", "WARNING"):
                self.assertIsNone(v.describe(self.photo))
        finally:
            lm.close()
        self.assertEqual([r["strategy"] for r in v.probe_results], ["prefill", "plain"])
        self.assertNotIn("Prompt Template", v.disabled_why)               # 500 + thinking: no verdict
        self.assertLess(v.disabled_until - time.monotonic(), 61)          # just a minute's rest

    def test_inside_the_lock_it_rechecks(self):
        """Another probe finished between the check and the lock: use its pick, or
        its verdict (review of #64)."""
        class Lock:
            def __init__(self, v, on_acquire):
                self.real, self.v, self.on_acquire = threading.Lock(), v, on_acquire

            def acquire(self, blocking=True, timeout=-1):
                got = self.real.acquire(blocking, timeout)
                if got:
                    self.on_acquire(self.v)
                return got

            def release(self):
                self.real.release()

            def locked(self):
                return self.real.locked()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *exc):
                self.release()
        lm = FakeLMStudio(thinks_unless=prefill)
        try:
            v = Vision(lm.url, "m9b")
            v._lock = Lock(v, lambda v: setattr(v, "strategy", "prefill"))
            self.assertIsNotNone(v.describe(self.photo))
            self.assertEqual(len(lm.requests), 1)
            self.assertFalse(v._lock.locked())
            w = Vision(lm.url, "m9b-other")
            w._lock = Lock(w, lambda w: setattr(w, "disabled_until", time.monotonic() + 300))
            self.assertIsNone(w.describe(self.photo))
            self.assertEqual(len(lm.requests), 1)                         # no request at all
            self.assertFalse(w._lock.locked())
        finally:
            lm.close()

    def test_a_photo_never_waits_behind_the_warm_up(self):
        lm = FakeLMStudio()
        try:
            v = Vision(lm.url, "m9b")
            with v._lock:                                             # the warm-up is probing
                t0 = time.monotonic()
                with self.assertLogs("hardware.vision", "WARNING"):
                    self.assertIsNone(v.describe(self.photo))
                self.assertLess(time.monotonic() - t0, 0.5)
            self.assertEqual(lm.requests, [])
            self.assertIn("busy", v.last["error"])
            v.strategy = "prefill"                                    # it picked one meanwhile
            self.assertIsNotNone(v.describe(self.photo))
        finally:
            lm.close()

    def test_server_refusing_the_extra_fields_still_gets_the_prefill(self):
        lm = FakeLMStudio(refuse_extras=True, thinks_unless=prefill)
        try:
            v = Vision(lm.url, "m9b")
            self.assertIsNotNone(v.describe(self.photo))
        finally:
            lm.close()
        self.assertEqual(v.strategy, "prefill")
        self.assertNotIn("chat_template_kwargs", lm.requests[-1])
        self.assertEqual(lm.requests[-1]["messages"][-1]["role"], "assistant")

    def test_detector_hint_goes_into_the_prompt(self):
        lm = FakeLMStudio()
        try:
            Vision(lm.url, "bonsai-9b").describe(self.photo, hint="The object detector found: 1 person.")
        finally:
            lm.close()
        self.assertTrue(lm.requests[0]["messages"][0]["content"][0]["text"]
                        .endswith("Hint: The object detector found: 1 person."))

    def test_reasoning_in_the_content_is_not_a_description(self):
        for content, finish in (("<think>The image shows a door and", "length"),
                                ("Thinking Process: 1. look at the photo", "stop"),
                                ("**Thinking Process:**\n\n1. **Analyze the Request:** The user wants", "length"),
                                ("Okay, so I need to describe this photo.", "stop"),
                                ("Here's a thinking process to describe the image.", "stop"),
                                ("One person stands at the door holding a box and looking at the "
                                 "camera while the light is on and the door is closed and", "length")):
            with self.subTest(content=content):
                lm = FakeLMStudio(content=content, finish_reason=finish)
                try:
                    v = Vision(lm.url, "m", strategy="plain")
                    self.assertIsNone(v.describe(self.photo))
                finally:
                    lm.close()

    def test_closed_think_then_answer_is_used(self):
        lm = FakeLMStudio(content="<think>hmm</think>A man holds a box.")
        try:
            self.assertEqual(Vision(lm.url, "m", strategy="plain").describe(self.photo), "A man holds a box.")
        finally:
            lm.close()

    def test_timeout_is_named(self):
        lm = FakeLMStudio(delay_s=1.5)
        try:
            v = Vision(lm.url, "bonsai-9b", timeout_s=0.8, strategy="plain")
            with self.assertLogs("hardware.vision", "WARNING"):
                self.assertIsNone(v.describe(self.photo))
        finally:
            lm.close()
        self.assertIn("timed out after", v.last["error"])

    def test_settings_keys(self):
        s = {"llm": {"profile": "mac", "profiles": {"mac": {"base_url": "http://x:1", "model": "nine"}},
                     "timeout_s": 30, "vision_timeout_s": 15, "vision_model": "ternary-bonsai-2-4b-mlx",
                     "vision_no_think": "prefill"}}
        v = Vision.from_settings(s)
        self.assertEqual((v.model, v.timeout_s, v.max_tokens, v.strategy),
                         ("ternary-bonsai-2-4b-mlx", 15.0, 120, "prefill"))


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CheckVision(Base):
    """python -m hardware.check vision: every strategy, timed, and the pick."""

    def test_the_9b_stays_unless_only_the_4b_makes_5_s(self):
        from hardware.check import pick_model
        nine, four = "ternary-bonsai-2-9b-mlx", "ternary-bonsai-2-4b-mlx"
        for picks, want in (({nine: ("prefill", 2.0), four: ("prefill", 1.0)}, nine),
                            ({nine: ("prefill", 6.0), four: ("prefill", 1.5)}, four),
                            ({nine: ("prefill", 6.0), four: ("prefill", 5.5)}, nine),
                            ({four: ("plain", 7.0)}, four),
                            ({}, None)):
            self.assertEqual(pick_model(picks, nine), want, picks)

    def run_check(self, lm, model="ternary-bonsai-2-9b-mlx"):
        import contextlib
        import io
        from hardware import check
        s = {"llm": {"profile": "mac", "profiles": {"mac": {"base_url": lm.url, "model": model}},
                     "timeout_s": 5, "reasoning_effort": "none"}}
        out = io.StringIO()
        orig = check.load_settings
        check.load_settings = lambda: s
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = check.main(["vision", str(self.photo)])
        finally:
            check.load_settings = orig
        return code, out.getvalue()

    def test_table_and_the_pick(self):
        lm = FakeLMStudio(content="One person holds a box.", thinks_unless=prefill,
                          models=[{"id": "ternary-bonsai-2-9b-mlx", "state": "loaded", "type": "vlm"}])
        try:
            code, out = self.run_check(lm)
        finally:
            lm.close()
        self.assertEqual(code, 0, out)
        for strategy, result in (("prefill", "ok"), ("plain", "THINKS"), ("no_think", "THINKS"),
                                 ("system", "THINKS")):
            self.assertRegex(out, rf"ternary-bonsai-2-9b-mlx\s+{strategy}\s+[\d.]+s\s+{result}")
        self.assertIn("USE: ternary-bonsai-2-9b-mlx with 'prefill'", out)
        self.assertIn("brain ternary-bonsai-2-9b-mlx: ", out)      # Dell's client checked too

    def test_nothing_works_prints_the_fix(self):
        lm = FakeLMStudio(thinks_unless=lambda b: False)
        try:
            code, out = self.run_check(lm)
        finally:
            lm.close()
        self.assertEqual(code, 1, out)
        self.assertIn("NO strategy stops the thinking", out)
        self.assertIn("enable_thinking = false", out)


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
