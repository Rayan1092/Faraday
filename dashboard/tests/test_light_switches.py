"""Real light switches: the dashboard toggles go through integration's control_hook,
and the Lights panel shows the state from the log."""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from shared import ActionType, Mode, Source

from brain import Brain
from brain.pipeline import Pipeline
from dashboard import Dashboard
from dashboard.server import STATIC_DIR
from log import EventLog
from modes import ModeState
from rules import RuleStore
from sim import events
from sim.controls import SimControls

SECRET = "silver otter lantern"


class Switches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.pipeline = Pipeline(Brain(None, SECRET, log=lambda _m: None), ModeState(SECRET),
                                 EventLog(tmp / "events.jsonl", SECRET), say=lambda _m: None,
                                 rules=RuleStore(tmp / "rules.json"))
        self.board = Dashboard(self.pipeline, "127.0.0.1", 0).start()
        self.base = self.board.url.rstrip("/")

    def tearDown(self):
        self.board.stop()
        self.tmp.cleanup()

    def post(self, body, header=True):
        req = urllib.request.Request(self.base + "/api/control", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json",
                                                             **({"X-Faraday": "1"} if header else {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def lights(self):
        return {l["id"]: l["on"] for l in self.board.state()["lights"]}

    def test_without_the_hook_the_switches_say_so(self):
        self.assertFalse(self.board.state()["control_available"])
        code, reply = self.post({"outlet": "porch_light", "on": True})
        self.assertEqual(code, 503)
        self.assertIn("aren't connected", reply["error"])

    def test_hook_gets_the_outlet_and_state(self):
        calls = []
        self.board.control_hook = lambda outlet, on: calls.append((outlet, on)) or True
        self.assertTrue(self.board.state()["control_available"])
        self.assertEqual(self.post({"outlet": "living_room_lamp", "on": True}), (200, {"ok": True, "changed": True}))
        self.assertEqual(self.post({"outlet": "living_room_lamp", "on": False})[0], 200)
        self.assertEqual(calls, [("living_room_lamp", True), ("living_room_lamp", False)])

    def test_bad_requests_never_reach_the_hook(self):
        calls = []
        self.board.control_hook = lambda outlet, on: calls.append((outlet, on)) or True
        for body in ({"outlet": "garage", "on": True}, {"outlet": "porch_light", "on": "yes"},
                     {"outlet": "porch_light"}, {}):
            self.assertEqual(self.post(body)[0], 400, body)
        self.assertEqual(self.post({"outlet": "porch_light", "on": True}, header=False)[0], 403)
        self.assertEqual(calls, [])

    def test_hook_failure_is_a_500_not_a_crash(self):
        def broken(outlet, on):
            raise RuntimeError("cable out")
        self.board.control_hook = broken
        self.assertEqual(self.post({"outlet": "porch_light", "on": True})[0], 500)
        self.assertEqual(self.board.state()["lights"][0]["on"], False)   # server still answers

    def test_panel_shows_the_logged_state(self):
        """The sim's stand-in hook has integration's contract: log a dashboard action,
        False when already in that state."""
        self.board.control_hook = SimControls(self.pipeline)
        self.assertEqual(self.post({"outlet": "porch_light", "on": True}), (200, {"ok": True, "changed": True}))
        self.assertEqual(self.lights(), {"porch_light": True, "living_room_lamp": False, "spare": False})
        self.assertEqual(self.post({"outlet": "porch_light", "on": True}), (200, {"ok": True, "changed": False}))
        self.assertEqual(self.post({"outlet": "porch_light", "on": False})[1]["changed"], True)
        self.assertFalse(self.lights()["porch_light"])
        logged = [e for e in self.pipeline.log.recent(50) if e.get("kind") == "action"]
        self.assertEqual([(e["type"], e["source"]) for e in logged],
                         [(ActionType.OUTLET_ON, Source.DASHBOARD), (ActionType.OUTLET_OFF, Source.DASHBOARD)])

    def test_owner_switched_light_survives_the_disarm(self):
        """Item 3 + 4: lights the owner turned on stay on; Faraday's own go off."""
        self.board.control_hook = SimControls(self.pipeline)
        self.post({"outlet": "living_room_lamp", "on": True})
        self.pipeline.owner_set_mode(Mode.NIGHT)
        self.pipeline.handle(events.motion())                 # porch light on (rules)
        self.assertTrue(self.lights()["porch_light"])
        self.pipeline.owner_passphrase(SECRET)
        self.assertEqual(self.lights(), {"porch_light": False, "living_room_lamp": True, "spare": False})


class Page(unittest.TestCase):
    def test_tiles_are_buttons_that_post_to_control(self):
        js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn('post("/api/control", { outlet, on })', js)
        self.assertIn("aria-pressed", js)
        self.assertIn("control_available", js)


class SimHook(unittest.TestCase):
    def test_unknown_outlet_and_off_when_already_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Pipeline(Brain(None, SECRET, log=lambda _m: None), ModeState(SECRET),
                         EventLog(Path(tmp) / "e.jsonl", SECRET), say=lambda _m: None)
            seen = []
            hook = SimControls(p, seen.append)
            self.assertFalse(hook("garage", True))
            self.assertFalse(hook("spare", False))       # never on: nothing to send
            self.assertTrue(hook("spare", True))
            self.assertEqual([a.type for a in seen], [ActionType.OUTLET_ON])


if __name__ == "__main__":
    unittest.main()
