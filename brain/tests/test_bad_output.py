"""Bad model output, errors and timeouts fall back to rules and never stall."""
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from shared import ActionType, Mode, validate_action

from brain import Brain
from brain.bonsai import BonsaiClient, BonsaiError
from brain.guard import GarbageReply, parse_reply
from sim import events

from .helpers import PASSPHRASE, make_brain, tool_calls


class FakeLMStudio(BaseHTTPRequestHandler):
    """Local stand-in for LM Studio. The test sets `body` and `delay` on the class."""
    body = b"{}"
    status = 200
    delay = 0.0
    last_request = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        type(self).last_request = json.loads(self.rfile.read(length))
        time.sleep(self.delay)
        try:
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(self.body)
        except (ConnectionError, OSError):
            pass  # client gave up (timeout test)

    def log_message(self, *args):
        pass


def wrap(message) -> bytes:
    return json.dumps({"choices": [{"message": message}]}).encode()


class GarbageFallsBack(unittest.TestCase):
    GARBAGE = [
        {"role": "assistant", "content": "Sure! I would take a photo and greet them."},
        {"role": "assistant", "content": "{not json at all"},
        {"role": "assistant", "content": None},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "tool_calls": "take_photo"},
        {"role": "assistant", "content": 42},
        tool_calls(("take_photo", "{bad json")),
        tool_calls(("launch_rockets", {})),
        tool_calls(("speak", {"text": ""})),
        tool_calls(("speak", {"words": "hi"})),
        tool_calls(("outlet_on", {"outlet": "garage"})),
        tool_calls(("outlet_on", "[1, 2]")),
        {"role": "assistant", "tool_calls": [None, 5, {"function": "x"}]},
        ["not", "a", "dict"],
        RuntimeError("boom"),
        BonsaiError("connection failed"),
        socket.timeout("timed out"),
    ]

    def test_visitor_always_gets_a_greeting(self):
        for reply in self.GARBAGE:
            for mode in (Mode.HOME, Mode.AWAY):  # night stays quiet
                decision = make_brain(reply).decide(events.photo_described(None), mode)
                self.assertEqual(decision.via, "fallback", reply)
                self.assertEqual([a.type for a in decision.actions], [ActionType.SPEAK], reply)

    def test_fallback_actions_are_valid(self):
        for reply in self.GARBAGE:
            for mode in Mode.ALL:
                for event in (events.motion(), events.speech("Package for Sam."),
                              events.photo_described(None)):
                    for action in make_brain(reply).decide(event, mode).actions:
                        self.assertEqual(validate_action(action.to_dict()), [])

    def test_text_json_and_tool_tags_are_understood(self):
        for content in ('{"actions": [{"type": "speak", "args": {"text": "Hello."}}]}',
                        'Calling: <tool_call>{"name": "speak", "arguments": {"text": "Hi."}}</tool_call>',
                        '[TOOL_REQUEST]{"name": "speak", "arguments": {"text": "Hey."}}[END_TOOL_REQUEST]'):
            decision = make_brain({"content": content}).decide(events.photo_described(None), Mode.AWAY)
            self.assertEqual(decision.via, "bonsai", content)
            self.assertEqual(decision.actions[0].type, ActionType.SPEAK)

    def test_partly_bad_reply_keeps_the_good_parts(self):
        reply = tool_calls(("save_message", {"text": "Package for Sam."}), ("disarm", {}),
                           ("speak", {"text": "x" * 600}), ("speak", {"text": "x" * 600}),
                           ("take_photo", {"reason": "not offered for speech"}))
        decision = make_brain(reply).decide(events.speech("Package for Sam."), Mode.AWAY)
        self.assertEqual([a.type for a in decision.actions], [ActionType.SAVE_MESSAGE, ActionType.SPEAK])
        self.assertLessEqual(len(decision.actions[1].args["text"]), 240)
        reasons = " ".join(why for _, why in decision.dropped)
        self.assertIn("unknown action", reasons)
        self.assertIn("duplicate", reasons)
        self.assertIn("not offered", reasons)

    def test_at_most_four_actions(self):
        reply = tool_calls(*[("speak", {"text": f"line {i}"}) for i in range(10)])
        decision = make_brain(reply).decide(events.speech("Hello?"), Mode.AWAY)
        self.assertEqual(len(decision.actions), 4)

    def test_parse_reply_raises_on_prose(self):
        with self.assertRaises(GarbageReply):
            parse_reply({"content": "I think a photo is best."})
        self.assertEqual(parse_reply({"content": "  "}), [])


class RealClientAgainstFakeServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeLMStudio)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    visitor = events.photo_described(None)

    def setUp(self):
        FakeLMStudio.body, FakeLMStudio.status, FakeLMStudio.delay = b"{}", 200, 0.0

    def brain(self, timeout=2.0) -> Brain:
        client = BonsaiClient(self.url, "test-model", timeout_s=timeout)
        return Brain(client, PASSPHRASE, log=lambda _m: None)

    def test_tool_calls_round_trip(self):
        FakeLMStudio.body = wrap(tool_calls(("take_photo", {"reason": "not offered"}),
                                            ("speak", {"text": "Hello there."})))
        decision = self.brain().decide(self.visitor, Mode.AWAY)
        self.assertEqual(decision.via, "bonsai")
        self.assertEqual([a.type for a in decision.actions], [ActionType.SPEAK])
        self.assertIsNotNone(decision.elapsed_s)
        sent = FakeLMStudio.last_request
        self.assertEqual(sent["model"], "test-model")
        self.assertEqual({t["function"]["name"] for t in sent["tools"]}, {"speak"})
        self.assertEqual(sent["reasoning_effort"], "none")

    def test_non_json_body(self):
        FakeLMStudio.body = b"<html>502 Bad Gateway</html>"
        self.assertEqual(self.brain().decide(self.visitor, Mode.AWAY).via, "fallback")

    def test_http_error(self):
        FakeLMStudio.status, FakeLMStudio.body = 500, b'{"error": "model not loaded"}'
        decision = self.brain().decide(self.visitor, Mode.AWAY)
        self.assertEqual(decision.via, "fallback")
        self.assertIn("500", decision.error)

    def test_no_choices(self):
        FakeLMStudio.body = b'{"choices": []}'
        self.assertEqual(self.brain().decide(self.visitor, Mode.AWAY).via, "fallback")

    def test_timeout(self):
        FakeLMStudio.delay = 1.5
        FakeLMStudio.body = wrap(tool_calls(("speak", {"text": "Too late."})))
        started = time.perf_counter()
        decision = self.brain(timeout=0.3).decide(self.visitor, Mode.AWAY)
        self.assertLess(time.perf_counter() - started, 1.4)
        self.assertEqual(decision.via, "fallback")
        self.assertEqual(decision.actions[0].type, ActionType.SPEAK)

    def test_nothing_listening(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        client = BonsaiClient(f"http://127.0.0.1:{port}", "m", timeout_s=2)
        decision = Brain(client, PASSPHRASE, log=lambda _m: None).decide(events.photo_described(None), Mode.HOME)
        self.assertEqual(decision.via, "fallback")


if __name__ == "__main__":
    unittest.main()
