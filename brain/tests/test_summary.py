"""summarize_log: facts from the log, Bonsai summary, plain-code list fallback."""
import unittest
from datetime import datetime, timedelta

from shared import ActionType, Source, make_action, make_event

from brain import summary
from brain.core import _clean_line
from modes import ModeState
from shared import ChangedBy, Mode
from sim import events

from .helpers import PASSPHRASE, make_brain


def at(entry, minutes_ago, now):
    entry = entry.to_dict()
    entry["timestamp"] = (now - timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds")
    return entry


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now().astimezone()
        state = ModeState(PASSPHRASE)
        n = self.now
        self.log = [
            at(events.doorbell(), 300, n),                                   # before leaving
            at(state.arm(Mode.AWAY, ChangedBy.BRAIN)[0], 120, n),            # left
            at(events.doorbell(), 100, n),
            at(events.photo_described(None, "A courier holding a box."), 99, n),
            at(make_action(ActionType.SAVE_MESSAGE, {"text": "Package for Sam, side door."},
                           Source.BRAIN), 98, n),
            at(events.motion(), 50, n),
            at(make_event("alert_changed", {"level": "warning", "previous": "normal"}, "modes"), 50, n),
            at(events.motion(), 49, n),
            at(make_event("alert_changed", {"level": "alarm", "previous": "warning"}, "modes"), 49, n),
            at(make_action(ActionType.OUTLET_ON, {"outlet": "porch_light"}, Source.RULES), 49, n),
        ]

    def test_window_starts_when_the_house_was_armed(self):
        picked = summary.window(self.log, 1440, self.now)
        self.assertEqual(picked[0]["type"], "mode_changed")
        self.assertEqual(sum(1 for e in picked if e["type"] == "doorbell"), 1)

    def test_window_respects_minutes(self):
        picked = summary.window(self.log, 60, self.now)
        self.assertEqual({e["type"] for e in picked}, {"motion", "alert_changed", "outlet_on"})

    def test_collect_and_facts(self):
        h = summary.collect(summary.window(self.log, 1440, self.now))
        self.assertEqual((h.doorbells, h.motions, h.alarm), (1, 2, True))
        facts = summary.facts(h)
        text = " ".join(facts)
        for part in ("doorbell rang once", "Motion at the door twice", "alarm went off",
                     "courier holding a box", "Package for Sam", "porch light"):
            self.assertIn(part, text)
        self.assertLessEqual(len(facts), 10)

    def test_fallback_list(self):
        text = summary.spoken_list(summary.collect(summary.window(self.log, 1440, self.now)))
        self.assertTrue(text.startswith("While you were out: the doorbell rang once"))
        self.assertIn("alarm went off", text)
        self.assertIn("Package for Sam", text)
        self.assertLessEqual(len(text), 240)

    def test_nothing_happened(self):
        h = summary.collect([])
        self.assertTrue(h.empty)
        self.assertEqual(summary.facts(h), ["Nothing happened."])
        self.assertEqual(summary.spoken_list(h), "Nothing happened while you were out.")

    def test_long_fallback_is_capped(self):
        many = [make_action(ActionType.SAVE_MESSAGE, {"text": "x" * 400}, Source.BRAIN).to_dict()]
        self.assertLessEqual(len(summary.spoken_list(summary.collect(many))), 240)

    def test_missing_message_is_added_by_code(self):
        h = summary.collect(summary.window(self.log, 1440, self.now))
        bonsai = "An alarm triggered at 15:09 after motion was detected twice at the door."
        fixed = summary.ensure_messages(bonsai, h)
        self.assertTrue(fixed.startswith(bonsai))
        self.assertIn("Visitor message: Package for Sam", fixed)
        mentioned = "A courier left a package for Sam and the alarm went off."
        self.assertEqual(summary.ensure_messages(mentioned, h), mentioned)   # already there
        self.assertEqual(summary.ensure_messages("Quiet day.", summary.collect([])), "Quiet day.")
        long = "x" * 230
        self.assertLessEqual(len(summary.ensure_messages(long, h)), 240)
        self.assertIn("Package", summary.ensure_messages(long, h))

    def test_bonsai_summary_and_failures(self):
        brain = make_brain({"role": "assistant", "content": "  Two visitors came. One left a message.  "})
        self.assertEqual(brain.summarize(["fact"]), "Two visitors came. One left a message.")
        for bad in ({"content": ""}, {"content": '{"actions": []}'}, RuntimeError("down"), ["x"]):
            self.assertIsNone(make_brain(bad).summarize(["fact"]), bad)
        self.assertIsNone(make_brain().summarize(["fact"]))  # no model
        self.assertIsNone(brain.summarize([]))

    def test_clean_line(self):
        self.assertEqual(_clean_line('"Leave now, man in the red cap."'), "Leave now, man in the red cap.")
        self.assertEqual(_clean_line("<think></think> **Go away.**"), "Go away.")
        self.assertLessEqual(len(_clean_line("word " * 100)), 240)
        self.assertIsNone(_clean_line(None))
        self.assertIsNone(_clean_line("   "))


if __name__ == "__main__":
    unittest.main()
