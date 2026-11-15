"""The dashboard server: offline assets, live state, buttons, passphrase limits."""
import json
import re
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from shared import AlertLevel, Mode

from brain import Brain
from brain.pipeline import Pipeline
from dashboard import Dashboard
from dashboard.server import STATIC_DIR
from log import EventLog
from modes import ModeState
from rules import RuleStore
from sim import events

SECRET = "silver otter lantern"


class StaticAssetsAreOffline(unittest.TestCase):
    def test_no_external_urls_or_fonts(self):
        files = list(STATIC_DIR.iterdir())
        self.assertTrue({"index.html", "dashboard.css", "dashboard.js"} <= {f.name for f in files})
        for f in files:
            text = f.read_text(encoding="utf-8")
            text = text.replace('xmlns="http://www.w3.org/2000/svg"', "")  # a namespace, not a fetch
            self.assertIsNone(re.search(r"https?://|//cdn|@import|url\(\s*['\"]?http", text), f.name)
            self.assertNotIn("fonts.googleapis", text, f.name)

    def test_page_only_references_local_assets(self):
        for page in ("index.html", "controls.html"):
            html = (STATIC_DIR / page).read_text(encoding="utf-8")
            for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
                if ref in ("/", "/controls"):   # links between the two pages
                    continue
                self.assertTrue(ref.startswith("/static/"), (page, ref))
                self.assertTrue((STATIC_DIR / ref[len("/static/"):]).exists(), (page, ref))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        brain = Brain(None, SECRET, log=lambda _m: None)
        self.pipeline = Pipeline(brain, ModeState(SECRET), EventLog(tmp / "events.jsonl", SECRET),
                                 say=lambda _m: None, rules=RuleStore(tmp / "rules.json"))
        self.board = Dashboard(self.pipeline, "127.0.0.1", 0).start()
        self.base = self.board.url.rstrip("/")

    def tearDown(self):
        self.board.stop()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def post(self, path, body, header=True):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              **({"X-Faraday": "1"} if header else {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def state(self):
        return json.loads(self.get("/api/state")[2])

    def test_serves_page_and_assets_with_right_types(self):
        code, ctype, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b'id="live"', body)                 # the live camera in the centre
        self.assertNotIn(b"OFFLINE", body)
        self.assertIn("javascript", self.get("/static/dashboard.js")[1])
        self.assertIn("text/css", self.get("/static/dashboard.css")[1])
        self.assertIn("svg", self.get("/static/no-photo.svg")[1])

    def test_state_feed_updates_live(self):
        self.pipeline.start()
        self.pipeline.owner_set_mode(Mode.AWAY)
        self.pipeline.handle(events.motion())
        self.pipeline.tick(force=True)            # no doorbell in the grace period
        s = self.state()
        self.assertEqual((s["mode"], s["alert"]), ("away", "warning"))
        self.assertEqual(s["status"]["color"], "yellow")
        self.assertTrue(any(l["on"] for l in s["lights"] if l["id"] == "porch_light"))

    def test_mode_buttons(self):
        self.assertEqual(self.post("/api/mode", {"mode": "night"})[1]["mode"], "night")
        code, reply = self.post("/api/mode", {"mode": "home"})
        self.assertEqual(code, 400)                      # Home needs the passphrase
        self.assertEqual(self.pipeline.modes.mode, Mode.NIGHT)

    def test_home_needs_the_passphrase(self):
        self.pipeline.owner_set_mode(Mode.AWAY)
        self.pipeline.handle(events.motion())
        code, reply = self.post("/api/home", {"passphrase": "wrong"})
        self.assertEqual((code, reply["ok"]), (403, False))
        self.assertEqual(self.pipeline.modes.mode, Mode.AWAY)
        code, reply = self.post("/api/home", {"passphrase": "Silver otter lantern"})
        self.assertEqual((code, reply["mode"]), (200, "home"))
        self.assertEqual(self.pipeline.modes.alert, AlertLevel.NORMAL)
        log = self.pipeline.log.path.read_text(encoding="utf-8").lower()
        self.assertNotIn("otter", log)
        self.assertNotIn("wrong", log)                  # attempts are never logged

    def test_passphrase_lockout(self):
        self.pipeline.owner_set_mode(Mode.AWAY)
        for _ in range(5):
            self.assertEqual(self.post("/api/home", {"passphrase": "nope"})[0], 403)
        code, reply = self.post("/api/home", {"passphrase": SECRET})
        self.assertEqual(code, 429)                       # locked even with the right one
        self.assertEqual(self.pipeline.modes.mode, Mode.AWAY)

    def test_posts_need_the_header(self):
        code, _ = self.post("/api/mode", {"mode": "away"}, header=False)
        self.assertEqual(code, 403)
        self.assertEqual(self.pipeline.modes.mode, Mode.HOME)

    def test_rules_by_voice_and_delete_from_the_page(self):
        self.assertEqual(self.post("/api/say", {"text": "hi"})[0], 404)      # no typed speech
        self.pipeline.handle(events.speech("When there's motion at night, turn on the lamp."))
        self.assertEqual(self.state()["rules"][0]["text"], "motion in night → living room lamp on")
        rule_id = self.state()["rules"][0]["id"]
        self.assertEqual(self.post("/api/rules/delete", {"id": rule_id})[0], 200)
        self.assertEqual(self.state()["rules"], [])

    def test_media_only_inside_captures(self):
        for path in ("/media/config/settings.example.json", "/media/captures/../config/settings.json",
                     "/media/../../etc/passwd", "/static/../server.py", "/media/captures/x.txt"):
            with self.assertRaises(urllib.error.HTTPError, msg=path) as ctx:
                self.get(path)
            self.assertEqual(ctx.exception.code, 404, path)

    def test_bad_requests(self):
        self.assertEqual(self.post("/api/nope", {})[0], 404)
        req = urllib.request.Request(self.base + "/api/mode", data=b"[1,2]", method="POST",
                                     headers={"X-Faraday": "1"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
