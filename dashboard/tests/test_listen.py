"""Listen button (replaces push-to-talk): one tap opens one listening window through
integration's listen_hook, and a subtle indicator shows while the mic is open."""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from brain import Brain
from brain.pipeline import Pipeline
from dashboard import Dashboard
from dashboard.server import STATIC_DIR
from log import EventLog
from modes import ModeState

SECRET = "silver otter lantern"


class Listen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = Pipeline(Brain(None, SECRET, log=lambda _m: None), ModeState(SECRET),
                     EventLog(Path(self.tmp.name) / "e.jsonl", SECRET), say=lambda _m: None)
        self.board = Dashboard(p, "127.0.0.1", 0).start()
        self.base = self.board.url.rstrip("/")

    def tearDown(self):
        self.board.stop()
        self.tmp.cleanup()

    def post(self, path):
        req = urllib.request.Request(self.base + path, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "X-Faraday": "1"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_hidden_and_refused_without_the_hook(self):
        s = self.board.state()
        self.assertEqual((s["listen_available"], s["listening"]), (False, None))
        self.assertEqual(self.post("/api/listen")[0], 503)

    def test_one_tap_opens_one_window(self):
        windows = []

        def listen_once():
            if windows:
                return False          # NV's hook: False while a window is already open
            windows.append(1)
            return True
        self.board.listen_hook = listen_once
        self.assertTrue(self.board.state()["listen_available"])
        self.assertEqual(self.post("/api/listen"), (200, {"ok": True, "opened": True}))
        self.assertEqual(self.post("/api/listen"), (200, {"ok": True, "opened": False}))
        self.assertEqual(windows, [1])

    def test_old_talk_route_and_hook_still_work(self):
        calls = []
        self.board.talk_hook = lambda: calls.append(1)      # returns None: counts as opened
        self.assertTrue(self.board.state()["talk_available"])
        self.assertEqual(self.post("/api/talk"), (200, {"ok": True, "opened": True}))
        self.assertEqual(self.post("/api/listen")[1]["opened"], True)
        self.assertEqual(calls, [1, 1])

    def test_mic_open_follows_integration(self):
        mic = {"open": False}
        self.board.listening_hook = lambda: mic["open"]
        self.assertIs(self.board.state()["listening"], False)
        mic["open"] = True
        self.assertIs(self.board.state()["listening"], True)

        def broken():
            raise RuntimeError("voice stopped")
        self.board.listening_hook = broken
        self.assertIsNone(self.board.state()["listening"])  # unknown, never a crash


class Page(unittest.TestCase):
    def test_listen_replaces_push_to_talk(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn('id="btn-listen"', html)
        self.assertNotIn("btn-talk", html + js)
        self.assertIn('id="mic-open"', html)
        self.assertIn('post("/api/listen"', js)
        self.assertIn("s.listening === true", js)


if __name__ == "__main__":
    unittest.main()
