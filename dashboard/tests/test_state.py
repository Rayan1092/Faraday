"""The dashboard's data feed, built from log entries."""
import json
import tempfile
import unittest
from pathlib import Path

from shared import ActionType, ChangedBy, Mode, Source, make_action, make_event

from dashboard.state import build_state, rule_text, status
from log import EventLog
from modes import ModeState
from sim import events

SECRET = "silver otter lantern"


class Status(unittest.TestCase):
    def test_alert_beats_mode(self):
        self.assertEqual(status("home", "normal"), {"color": "green", "label": "HOME"})
        self.assertEqual(status("away", "normal")["color"], "blue")
        self.assertEqual(status("night", "normal")["color"], "purple")
        self.assertEqual(status("night", "warning"), {"color": "yellow", "label": "WARNING"})
        self.assertEqual(status("away", "alarm"), {"color": "red", "label": "ALARM"})

    def test_colours_can_be_overridden_but_ignore_led_settings(self):
        self.assertEqual(status("night", "normal", {"night": "blue"})["color"], "blue")

    def test_dashboard_colours_dont_follow_the_flipper_led_setting(self):
        from dashboard import Dashboard
        settings = {"flipper": {"led": {"home": "off", "away": "off", "night": "off",
                                        "warning": "off", "alarm": "red"}},
                    "dashboard": {"host": "127.0.0.1", "port": 0}}
        board = Dashboard.from_settings(object(), settings)
        try:
            self.assertIsNone(board.colors)
        finally:
            board.httpd.server_close()


class Feed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = EventLog(Path(self.tmp.name) / "events.jsonl", SECRET)
        self.modes = ModeState(SECRET)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *items):
        for item in items:
            self.log.write(item)

    def state(self, rules=()):
        return build_state(self.log.recent(500), self.modes.mode, self.modes.alert, rules)

    def test_empty_log(self):
        s = self.state()
        self.assertEqual((s["feed"], s["talk"], s["photo"], s["summary"]), ([], [], None, None))
        self.assertTrue(s["offline"])
        self.assertEqual([l["on"] for l in s["lights"]], [False, False, False])
        json.dumps(s)  # must be JSON-serialisable

    def test_lights_follow_the_last_command(self):
        self.write(make_action(ActionType.OUTLET_ON, {"outlet": "porch_light"}, Source.RULES),
                   make_action(ActionType.OUTLET_ON, {"outlet": "living_room_lamp"}, Source.RULES),
                   make_action(ActionType.OUTLET_OFF, {"outlet": "porch_light"}, Source.BRAIN))
        lights = {l["id"]: l["on"] for l in self.state()["lights"]}
        self.assertEqual(lights, {"porch_light": False, "living_room_lamp": True, "spare": False})

    def test_latest_photo_with_reason(self):
        bell = events.doorbell()
        photo = make_action(ActionType.TAKE_PHOTO, {"reason": "doorbell while away"}, Source.RULES,
                            caused_by=bell.id)
        self.write(bell, photo, events.photo_described(photo.id, "A courier with a box."))
        p = self.state()["photo"]
        self.assertEqual(p["description"], "A courier with a box.")
        self.assertEqual(p["url"], "/media/captures/photos/sim.jpg")
        self.assertEqual(p["reason"], "doorbell while away")

    def test_heard_and_said_with_passphrase_redacted(self):
        self.write(events.speech("Okay, Silver otter lantern."),
                   make_action(ActionType.SPEAK, {"text": "Welcome home."}, Source.BRAIN))
        s = self.state()
        self.assertEqual([(t["who"], t["text"]) for t in s["talk"]],
                         [("heard", "Okay, [passphrase]."), ("said", "Welcome home.")])
        self.assertNotIn("otter", json.dumps(s).lower())

    def test_feed_newest_first_with_levels(self):
        self.write(*self.modes.arm(Mode.NIGHT, ChangedBy.DASHBOARD))
        motion = events.motion()
        self.write(motion, *self.modes.on_event(motion))
        feed = self.state()["feed"]
        self.assertEqual(feed[0]["text"], "Alert WARNING")
        self.assertEqual(feed[0]["level"], "warn")
        self.assertEqual(feed[-1]["text"], "Mode NIGHT (dashboard)")

    def test_activity_merges_the_photo_pair_and_skips_speech(self):
        bell = events.doorbell()
        photo = make_action(ActionType.TAKE_PHOTO, {"reason": "doorbell while away"}, Source.RULES,
                            caused_by=bell.id)
        pending = make_action(ActionType.TAKE_PHOTO, {"reason": "motion at night"}, Source.RULES)
        self.write(bell, photo, events.photo_described(photo.id, "A courier with a box."),
                   make_action(ActionType.SPEAK, {"text": "Hello."}, Source.BRAIN), pending)
        s = self.state()
        texts = [f["text"] for f in s["feed"]]
        self.assertEqual(texts, ["Photo: motion at night",          # not described yet: kept
                                 "A courier with a box.",           # the pair, merged
                                 "Doorbell pressed"])
        self.assertEqual(s["feed"][1]["detail"], "Photo · doorbell while away")
        self.assertEqual(s["talk"][-1]["text"], "Hello.")          # speech still in Heard & said

    def test_quiet_night_doorbell_notice(self):
        self.write(*self.modes.arm(Mode.NIGHT, ChangedBy.DASHBOARD), events.doorbell())
        s = self.state()
        self.assertEqual(s["notice"]["text"], "Doorbell at night")
        self.assertEqual(s["feed"][0]["level"], "warn")
        home = build_state([events.doorbell().to_dict()], "home", "normal")
        self.assertIsNone(home["notice"])

    def test_summary_card_and_messages(self):
        heard = events.speech("What happened while I was out?")
        ask = make_action(ActionType.SUMMARIZE_LOG, {"minutes": 60}, Source.BRAIN, caused_by=heard.id)
        self.write(make_action(ActionType.SAVE_MESSAGE, {"text": "Package for Sam."}, Source.BRAIN),
                   heard, ask,
                   make_action(ActionType.SPEAK, {"text": "Let me check."}, Source.BRAIN, caused_by=ask.id),
                   make_action(ActionType.SPEAK, {"text": "One visitor left a message."}, Source.BRAIN,
                               caused_by=ask.id))
        s = self.state()
        self.assertEqual(s["summary"]["text"], "One visitor left a message.")
        self.assertEqual(s["messages"][0]["text"], "Package for Sam.")

    def test_rules_are_listed_readably(self):
        rule = {"id": "rule_aaaaaaaa", "description": "lamp at night",
                "when": {"event": "motion", "modes": ["night"]},
                "then": [{"type": "outlet_on", "args": {"outlet": "living_room_lamp"}}]}
        view = self.state([rule])["rules"][0]
        self.assertEqual(view["text"], "motion in night → living room lamp on")
        self.assertEqual(rule_text({"when": {"event": "doorbell"},
                                    "then": [{"type": "take_photo", "args": {}}]}),
                         "doorbell → take photo")

    def test_version_changes_with_the_log(self):
        v1 = self.state()["version"]
        self.write(events.motion())
        self.assertNotEqual(self.state()["version"], v1)

    def test_feed_is_capped(self):
        for i in range(60):
            self.write(events.speech(f"line {i}"))
        s = self.state()
        self.assertEqual(len(s["feed"]), 25)
        self.assertEqual(len(s["talk"]), 8)
        self.assertEqual(s["feed"][0]["text"], 'Heard: "line 59"')

    def test_odd_entries_dont_break_it(self):
        s = build_state([{"kind": "event"}, "junk", {"type": "speak", "args": "x"},
                         make_event("doorbell", {"key": "0x00DEC608"}, "sim").to_dict()],
                        "away", "normal")
        self.assertEqual(s["feed"][0]["text"], "Doorbell pressed")  # junk skipped, no crash
        json.dumps(s)


if __name__ == "__main__":
    unittest.main()
