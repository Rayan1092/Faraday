"""Tests for shared/ formats. Run from the repo root:

    python -m unittest shared.test_formats

Stdlib only. Also checks every JSON example in docs/FORMATS.md, so the doc
and the code cannot drift apart.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from shared import (Action, AlertChangedData, AlertLevel, DoorbellData, Event,
                    EventType, SetModeArgs, Source, make_action, make_event,
                    validate_action, validate_event)

FORMATS_MD = Path(__file__).resolve().parent.parent / "docs" / "FORMATS.md"


def _event(type, data, source="modes"):
    return {"kind": "event", "id": "evt_00000000", "type": type,
            "timestamp": "2026-11-14T23:41:05.180-05:00", "source": source,
            "caused_by": None, "data": data}


def _action(type, args, source="brain"):
    return {"kind": "action", "id": "act_00000000", "type": type,
            "timestamp": "2026-11-14T23:41:05.180-05:00", "source": source,
            "caused_by": None, "args": args}


def _alert(level, previous, source="modes"):
    return _event("alert_changed", {"level": level, "previous": previous,
                                    "reason": "test"}, source)


class FormatsDocExamples(unittest.TestCase):
    def test_every_example_is_valid_and_round_trips(self):
        blocks = re.findall(r"```json\n(.*?)```", FORMATS_MD.read_text(encoding="utf-8"), re.S)
        seen = set()
        for block in blocks:
            d = json.loads(block)
            if "kind" not in d:
                continue  # the "What the LLM returns" example
            with self.subTest(type=d["type"]):
                if d["kind"] == "event":
                    self.assertEqual(validate_event(d), [])
                    self.assertEqual(Event.from_json(json.dumps(d)).to_dict(), d)
                else:
                    self.assertEqual(validate_action(d), [])
                    self.assertEqual(Action.from_json(json.dumps(d)).to_dict(), d)
                seen.add(d["type"])
        # one example per event and action type
        self.assertEqual(seen, set(EventType.ALL) | {
            "set_mode", "outlet_on", "outlet_off", "speak", "take_photo",
            "save_message", "summarize_log", "add_rule"})


class AlertChanged(unittest.TestCase):
    def test_allowed_steps(self):
        for previous, level in [("normal", "warning"), ("warning", "alarm"),
                                ("warning", "normal"), ("alarm", "normal")]:
            with self.subTest(step=f"{previous}->{level}"):
                self.assertEqual(validate_event(_alert(level, previous)), [])

    def test_forbidden_steps(self):
        for previous, level in [("normal", "alarm"), ("alarm", "warning"),
                                ("normal", "normal"), ("warning", "warning"),
                                ("alarm", "alarm")]:
            with self.subTest(step=f"{previous}->{level}"):
                self.assertNotEqual(validate_event(_alert(level, previous)), [])

    def test_source_must_be_modes(self):
        for source in ("brain", "rules", "sim", "dashboard", "flipper"):
            with self.subTest(source=source):
                errors = validate_event(_alert("warning", "normal", source))
                self.assertIn("alert_changed must have source 'modes'", errors)

    def test_bad_level_and_shape(self):
        self.assertNotEqual(validate_event(_alert("panic", "normal")), [])
        self.assertNotEqual(validate_event(_event("alert_changed", {"level": "warning"})), [])
        self.assertNotEqual(validate_event(_event("alert_changed", {
            "level": "warning", "previous": "normal", "mode": "night"})), [])

    def test_make_event_builds_valid_alert(self):
        ev = make_event(EventType.ALERT_CHANGED,
                        AlertChangedData(level=AlertLevel.WARNING, previous=AlertLevel.NORMAL,
                                         reason="Motion in night mode."),
                        Source.MODES)
        self.assertEqual(validate_event(ev.to_dict()), [])
        with self.assertRaises(ValueError):
            make_event(EventType.ALERT_CHANGED,
                       AlertChangedData(level="alarm", previous="normal"), Source.MODES)

    def test_no_action_can_change_the_alert(self):
        self.assertNotEqual(validate_action(_action("set_alert", {"level": "normal"})), [])
        self.assertNotEqual(validate_action(_action("set_mode", {"mode": "normal"})), [])


class NoAiDisarm(unittest.TestCase):
    def test_set_mode_home_rejected_for_every_source(self):
        for source in Source.ALL:
            with self.subTest(source=source):
                self.assertNotEqual(validate_action(_action("set_mode", {"mode": "home"}, source)), [])

    def test_no_disarm_action(self):
        self.assertNotEqual(validate_action(_action("disarm", {})), [])

    def test_rule_cannot_disarm_or_add_rules(self):
        home = {"type": "set_mode", "args": {"mode": "home"}}
        nested = {"type": "add_rule", "args": {"when": {"event": "motion"}, "then": []}}
        for step in (home, nested):
            with self.subTest(step=step["type"]):
                args = {"when": {"event": "motion", "modes": ["night"]}, "then": [step]}
                self.assertNotEqual(validate_action(_action("add_rule", args)), [])

    def test_mode_changed_to_home_needs_passphrase(self):
        data = {"mode": "home", "previous": "away", "changed_by": "brain"}
        self.assertNotEqual(validate_event(_event("mode_changed", data)), [])
        data["changed_by"] = "passphrase"
        self.assertEqual(validate_event(_event("mode_changed", data)), [])


class Basics(unittest.TestCase):
    def test_make_helpers(self):
        ev = make_event(EventType.DOORBELL, DoorbellData(key="0x00DEC608", packets=9,
                                                         duration_ms=1040), Source.FLIPPER)
        act = make_action("set_mode", SetModeArgs("night"), Source.BRAIN, caused_by=ev.id)
        self.assertTrue(ev.id.startswith("evt_") and act.id.startswith("act_"))
        self.assertEqual(Event.from_json(ev.to_json()).to_dict(), ev.to_dict())

    def test_unknown_keys_and_outlets_rejected(self):
        self.assertNotEqual(validate_action(_action("outlet_on", {"outlet": "garage"})), [])
        self.assertNotEqual(validate_action(_action("outlet_on", {"outlet": "spare", "x": 1})), [])


if __name__ == "__main__":
    unittest.main()
