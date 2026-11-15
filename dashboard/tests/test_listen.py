"""The mic indicator: a small green mic while integration's listening window is open.
There is no Listen button (owner 2026-11-14/15: "Hey Faraday" and the doorbell open
the mic), so /api/listen and /api/talk are gone."""
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


class MicIndicator(unittest.TestCase):
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
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_listen_routes_are_gone(self):
        self.assertEqual((self.post("/api/listen"), self.post("/api/talk")), (404, 404))
        s = self.board.state()
        self.assertNotIn("listen_available", s)
        self.assertIsNone(s["listening"])                    # no hook: unknown, indicator off
        self.assertIsNone(self.board.listen_hook)            # NV's test checks it stays unset

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
    def test_no_listen_button_and_the_indicator_never_guesses(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
        for gone in ("btn-listen", "btn-talk", "/api/listen", "/api/talk"):
            self.assertNotIn(gone, html + js)
        self.assertIn('id="mic-open"', html)
        self.assertIn("s.listening !== true", js)


if __name__ == "__main__":
    unittest.main()
