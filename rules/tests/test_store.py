"""User rules: stored, survive a restart, checked by the same guard rails, fired."""
import json
import tempfile
import unittest
from pathlib import Path

from shared import ActionType, Mode, Source, validate_action

from rules import RuleStore, check_rule
from sim import events

LAMP_AT_NIGHT = {"when": {"event": "motion", "modes": ["night"]},
                 "then": [{"type": "outlet_on", "args": {"outlet": "living_room_lamp"}}],
                 "description": "When there's motion at night, turn on the living room lamp too."}


class RuleStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "rules.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_fire_and_survive_restart(self):
        store = RuleStore(self.path)
        rule = store.add(LAMP_AT_NIGHT, caused_by="act_12345678")
        self.assertTrue(rule["id"].startswith("rule_"))
        again = RuleStore(self.path)                       # restart
        self.assertEqual([r["id"] for r in again.all()], [rule["id"]])
        motion = events.motion()
        fired = again.fire(motion, Mode.NIGHT)
        self.assertEqual([(a.type, a.args) for a in fired],
                         [("outlet_on", {"outlet": "living_room_lamp"})])
        self.assertEqual(fired[0].source, Source.RULES)
        self.assertEqual(fired[0].caused_by, motion.id)
        self.assertEqual(validate_action(fired[0].to_dict()), [])

    def test_only_matching_event_and_mode(self):
        store = RuleStore(self.path)
        store.add(LAMP_AT_NIGHT)
        self.assertEqual(store.fire(events.motion(), Mode.AWAY), [])
        self.assertEqual(store.fire(events.doorbell(), Mode.NIGHT), [])
        any_mode = {"when": {"event": "doorbell"},
                    "then": [{"type": "outlet_on", "args": {"outlet": "porch_light"}}]}
        store.add(any_mode)
        for mode in Mode.ALL:
            self.assertEqual(len(store.fire(events.doorbell(), mode)), 1)

    def test_duplicates_are_not_stored_twice(self):
        store = RuleStore(self.path)
        a = store.add(LAMP_AT_NIGHT)
        b = store.add({**LAMP_AT_NIGHT, "description": "same thing, other words"})
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(store.all()), 1)

    def test_guard_rails_refuse_unsafe_rules(self):
        store = RuleStore(self.path)
        bad = [
            {"when": {"event": "motion"}, "then": [{"type": "set_mode", "args": {"mode": "home"}}]},
            {"when": {"event": "motion"}, "then": [{"type": "set_mode", "args": {"mode": "away"}}]},
            {"when": {"event": "motion"}, "then": [{"type": "add_rule", "args": LAMP_AT_NIGHT}]},
            {"when": {"event": "motion"}, "then": [{"type": "disarm", "args": {}}]},
            {"when": {"event": "motion"}, "then": [{"type": "outlet_on", "args": {"outlet": "garage"}}]},
            {"when": {"event": "mode_changed"}, "then": [{"type": "speak", "args": {"text": "hi"}}]},
            {"when": {"event": "alert_changed"}, "then": [{"type": "speak", "args": {"text": "hi"}}]},
            {"when": {"event": "photo_described"}, "then": [{"type": "take_photo", "args": {}}]},
            {"when": {"event": "motion", "modes": ["daytime"]},
             "then": [{"type": "outlet_on", "args": {"outlet": "porch_light"}}]},
            {"when": {"event": "motion"}, "then": []},
            {"when": {"event": "motion"}, "then": [{"type": "speak", "args": {"text": "x"}}] * 4},
            "not a rule",
        ]
        for args in bad:
            self.assertTrue(check_rule(args), args)
            self.assertIsNone(store.add(args), args)
        self.assertEqual(store.all(), [])
        self.assertFalse(self.path.exists())

    def test_hand_edited_unsafe_rules_are_skipped_on_load(self):
        good = {"id": "rule_aaaaaaaa", **{k: LAMP_AT_NIGHT[k] for k in ("when", "then")}}
        evil = {"id": "rule_bbbbbbbb", "when": {"event": "motion", "modes": []},
                "then": [{"type": "set_mode", "args": {"mode": "home"}}]}
        self.path.write_text(json.dumps({"rules": [good, evil, "junk"]}), encoding="utf-8")
        store = RuleStore(self.path)
        self.assertEqual([r["id"] for r in store.all()], ["rule_aaaaaaaa"])
        self.assertEqual(len(store.skipped), 2)

    def test_corrupt_file_doesnt_crash(self):
        self.path.write_text("{not json", encoding="utf-8")
        store = RuleStore(self.path)
        self.assertEqual(store.all(), [])
        self.assertTrue(store.skipped)

    def test_remove(self):
        store = RuleStore(self.path)
        rule = store.add(LAMP_AT_NIGHT)
        self.assertTrue(store.remove(rule["id"]))
        self.assertFalse(store.remove(rule["id"]))
        self.assertEqual(RuleStore(self.path).all(), [])


if __name__ == "__main__":
    unittest.main()
