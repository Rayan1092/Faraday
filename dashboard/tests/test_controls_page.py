"""Controls page: served by the dashboard, Snapshot through the pipeline, and (opt-in)
Talk to visitor end to end in a real browser against integration's controls server."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from shared import ActionType, EventType, Mode, Source

from brain import Brain
from brain.pipeline import SNAPSHOT_REASON, Pipeline
from brain.tests.helpers import FakeClient, tool_calls
from dashboard import Dashboard
from dashboard.server import STATIC_DIR
from log import EventLog
from modes import ModeState
from rules import RuleStore
from sim import events

SECRET = "silver otter lantern"


def make(tmp, client=None, mode=Mode.HOME):
    emitted = []
    p = Pipeline(Brain(client, SECRET, log=lambda _m: None), ModeState(SECRET, mode=mode),
                 EventLog(Path(tmp) / "events.jsonl", SECRET), on_action=emitted.append,
                 say=lambda _m: None, rules=RuleStore(Path(tmp) / "rules.json"))
    return p, emitted


class Page(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pipeline, self.emitted = make(self.tmp.name)
        self.board = Dashboard(self.pipeline, "127.0.0.1", 0).start()
        self.base = self.board.url.rstrip("/")

    def tearDown(self):
        self.board.stop()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers.get("Content-Type"), r.read().decode("utf-8")

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Faraday": "1"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_controls_page_is_served_and_linked_from_the_dashboard(self):
        code, ctype, html = self.get("/controls")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("Hold to talk to visitor", html)
        self.assertIn('href="/controls"', self.get("/")[2])
        # No text fallback for talking: the page has no text box at all.
        self.assertNotIn("<input", html)
        self.assertNotIn("<textarea", html)

    def test_state_says_where_the_controls_server_is(self):
        self.assertEqual(self.board.state()["controls_port"], self.board.httpd.server_address[1] + 1)
        board = Dashboard.from_settings(self.pipeline, {"dashboard": {"port": 0},
                                                        "controls": {"port": 9555}})
        try:
            self.assertEqual(board.state()["controls_port"], 9555)
        finally:
            board.httpd.server_close()

    def test_snapshot_takes_a_dashboard_photo_and_shows_its_description(self):
        code, reply = self.post("/api/snapshot", {})
        self.assertEqual(code, 202)
        for _ in range(100):
            if self.emitted:
                break
            time.sleep(0.02)
        photo = self.emitted[0]
        self.assertEqual((photo.type, photo.source, photo.id, photo.args["reason"]),
                         (ActionType.TAKE_PHOTO, Source.DASHBOARD, reply["id"], SNAPSHOT_REASON))
        self.pipeline.handle(events.photo_described(photo.id, "A courier holding a parcel at the door."))
        shown = self.board.state()["photo"]
        self.assertEqual(shown["request"], reply["id"])
        self.assertEqual(shown["description"], "A courier holding a parcel at the door.")
        self.assertEqual(shown["threat"]["category"], "delivery")

    def test_page_speaks_to_both_servers(self):
        js = (STATIC_DIR / "controls.js").read_text(encoding="utf-8")
        html = (STATIC_DIR / "controls.html").read_text(encoding="utf-8")
        for needle in ("/stream.mjpg", "/api/status", "/api/intercom", "X-Sample-Rate", "/api/intercom/end",
                       "getUserMedia", "isSecureContext", "/api/snapshot", "/api/control"):
            self.assertIn(needle, js, needle)
        self.assertIn("chrome://flags/#unsafely-treat-insecure-origin-as-secure", html)


class OwnerSnapshot(unittest.TestCase):
    def test_nobody_is_greeted_for_an_owner_photo(self):
        """A visitor-style reply to the owner's own snapshot would talk to an empty porch."""
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(tool_calls(("speak", {"text": "Hello! How can I help?"})))
            p, emitted = make(tmp, client, Mode.AWAY)
            photo = p.owner_snapshot()
            result = p.handle(events.photo_described(photo.id, "A person at the door."))
            self.assertEqual(client.calls, [])
            self.assertEqual([a.type for a in result.actions], [])
            self.assertIn(EventType.THREAT_ASSESSED, [e.type for e in result.events])
            # a doorbell photo isn't answered either: the greeting was spoken at the ring
            p.handle(events.doorbell())
            take = [a for a in emitted if a.type == ActionType.TAKE_PHOTO][-1]
            p.handle(events.photo_described(take.id, "A person at the door."))
            self.assertEqual(client.calls, [])


# ---- opt-in: the real page, a fake mic, integration's controls server -------------------

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def browser():
    for c in CANDIDATES:
        if Path(c).exists():
            return c
    return shutil.which("google-chrome") or shutil.which("chromium")


@unittest.skipUnless(os.environ.get("FARADAY_BROWSER") and browser(), "set FARADAY_BROWSER=1 (needs Chrome/Edge)")
class TalkInTheBrowser(unittest.TestCase):
    def test_hold_to_talk_reaches_the_door_speaker_across_ports(self):
        import numpy as np
        from hardware.audio import MicGate
        from hardware.intercom import Intercom
        from hardware.tests.test_media import FakeOutput, wait_until
        from integration.controls import ControlsServer

        with tempfile.TemporaryDirectory() as tmp:
            p, _ = make(tmp)
            board = Dashboard(p, "127.0.0.1", 0).start()
            gate = MicGate()
            ic = Intercom(gate, stream_factory=FakeOutput, hold_s=0.5)
            srv = ControlsServer("127.0.0.1", 0, intercom=ic,
                                 dashboard_port=board.httpd.server_address[1]).start()
            board._controls_port = srv.port            # the page finds it through /api/state
            profile = Path(tempfile.mkdtemp(prefix="faraday-chrome-"))
            proc = subprocess.Popen([
                browser(), "--headless=new", "--no-first-run", "--no-default-browser-check",
                f"--user-data-dir={profile}", "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required",
                "--disable-gpu", f"{board.url}controls?autotalk=2"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self.assertTrue(wait_until(lambda: ic.sessions >= 1, 25), "no audio reached the speaker")
                self.assertTrue(gate.muted)                       # door mic muted while talking
                self.assertTrue(wait_until(lambda: not ic.active, 20))
                self.assertFalse(gate.muted)                      # and back after release
            finally:
                proc.kill()
                proc.wait(10)
                srv.stop()
                board.stop()
                shutil.rmtree(profile, ignore_errors=True)
            played = np.concatenate(FakeOutput.last.played)
            self.assertGreater(ic.played, 16000 * 1.0)            # at least ~1 s of the 2 s hold
            self.assertGreater(int(np.abs(played).max()), 5000)   # the fake mic's beeps came through


if __name__ == "__main__":
    unittest.main()
