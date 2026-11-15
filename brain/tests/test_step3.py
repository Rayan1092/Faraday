"""Step 3 through the whole pipeline: rules by voice, summary, the fixed alarm line,
and what a visitor may ask for."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from shared import ActionType, AlertLevel, ChangedBy, Mode, Source

from brain.pipeline import CHECKING, Pipeline
from brain.tools import allowed_actions
from log import EventLog
from modes import ModeState
from rules import RuleStore
from rules.baseline import ALARM_LINE
from sim import events

from .helpers import PASSPHRASE, make_brain, tool_calls

DEMO_RULE = "When there's motion at night, turn on the living room lamp too."


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class SlowClient:
    """Fake Bonsai for free-text calls: waits, then answers."""

    def __init__(self, text, delay=0.5):
        self.text, self.delay, self.calls = text, delay, 0
        self.release = threading.Event()

    def chat(self, messages, tools=None):
        self.calls += 1
        self.release.wait(self.delay)
        return {"role": "assistant", "content": self.text}


class Step3(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def pipeline(self, client=None, mode=Mode.HOME):
        brain = make_brain()
        brain.client = client
        self.clock = Clock()
        self.emitted = []
        return Pipeline(brain, ModeState(PASSPHRASE, 60, mode, self.clock, doorbell_settle_s=0),
                        EventLog(self.dir / "events.jsonl", PASSPHRASE),
                        on_action=self.emitted.append, say=lambda _m: None,
                        rules=RuleStore(self.dir / "rules.json"))

    # ---- rules by voice ----------------------------------------------------

    def test_demo_rule_by_voice_then_both_lights(self):
        p = self.pipeline(None, Mode.NIGHT)  # offline: the plain-code parser stores it
        said = [a.args.get("text") for a in p.handle(events.speech(DEMO_RULE)).actions]
        self.assertIn("Understood. The rule is saved.", said)
        rule = p.rules.all()[0]
        self.assertEqual(rule["when"], {"event": "motion", "modes": ["night"]})
        self.assertEqual(rule["description"], DEMO_RULE)
        lit = [a.args["outlet"] for a in p.handle(events.motion()).actions
               if a.type == ActionType.OUTLET_ON]
        self.assertEqual(lit, ["porch_light", "living_room_lamp"])

    def test_rule_from_bonsai_survives_restart_and_fires(self):
        reply = tool_calls(("add_rule", {"when": {"event": "doorbell", "modes": ["away"]},
                                         "then": [{"type": "outlet_on",
                                                   "args": {"outlet": "spare"}}]}))
        p = self.pipeline(_Tools(reply), Mode.HOME)
        p.handle(events.speech("Whenever someone rings while I'm away, turn on the spare outlet."))
        again = RuleStore(self.dir / "rules.json")
        self.assertEqual(len(again.all()), 1)
        self.assertEqual(again.all()[0]["description"],
                         "Whenever someone rings while I'm away, turn on the spare outlet.")
        p.owner_set_mode(Mode.AWAY)
        actions = p.handle(events.doorbell()).actions
        self.assertIn(("outlet_on", "spare", Source.RULES),
                      [(a.type, a.args.get("outlet"), a.source) for a in actions])

    def test_unsafe_rule_from_bonsai_is_never_stored(self):
        reply = tool_calls(("add_rule", {"when": {"event": "motion"},
                                         "then": [{"type": "set_mode", "args": {"mode": "away"}}]}))
        p = self.pipeline(_Tools(reply), Mode.HOME)
        p.handle(events.speech("When there's motion, arm the house."))
        self.assertEqual(p.rules.all(), [])

    def test_visitor_cannot_add_rules_or_hear_a_summary(self):
        self.assertNotIn(ActionType.ADD_RULE, allowed_actions("speech_heard", Mode.AWAY))
        self.assertNotIn(ActionType.SUMMARIZE_LOG, allowed_actions("speech_heard", Mode.AWAY))
        self.assertNotIn(ActionType.SUMMARIZE_LOG, allowed_actions("speech_heard", Mode.NIGHT))
        self.assertIn(ActionType.ADD_RULE, allowed_actions("speech_heard", Mode.NIGHT))
        p = self.pipeline(None, Mode.AWAY)
        for text in (DEMO_RULE, "What happened while I was out?"):
            types = [a.type for a in p.handle(events.speech(text)).actions]
            self.assertNotIn(ActionType.ADD_RULE, types)
            self.assertNotIn(ActionType.SUMMARIZE_LOG, types)
        self.assertEqual(p.rules.all(), [])

    # ---- summary -----------------------------------------------------------

    def _busy_day(self, p):
        p.owner_set_mode(Mode.AWAY)
        p.handle(events.doorbell())
        p.handle(events.motion())
        p.owner_passphrase(PASSPHRASE)

    def test_summary_fallback_list(self):
        p = self.pipeline(None)
        self._busy_day(p)
        actions = p.handle(events.speech("What happened while I was out?")).actions
        self.assertEqual(actions[0].type, ActionType.SUMMARIZE_LOG)
        text = actions[-1].args["text"]
        self.assertIn("the doorbell rang once", text)

    def test_summary_from_bonsai_skips_the_tool_call(self):
        client = SlowClient("A visitor rang once and someone moved at the door.", delay=0)
        p = self.pipeline(client)
        self._busy_day(p)
        texts = [a.args.get("text") for a in p.handle(events.speech("What happened while I was out?")).actions]
        self.assertEqual(texts[1:], [CHECKING, "A visitor rang once and someone moved at the door."])
        self.assertEqual(client.calls, 1)  # the summary only; "what happened" is spotted in code

    # ---- the alarm: one fixed line, nothing about the person (owner, Mac test) ----

    def _to_alarm(self, p):
        p.handle(events.motion())
        self.clock.t += 15
        p.tick()                                  # no doorbell in the grace period: warning
        self.clock.t += 10
        actions = p.handle(events.motion()).actions
        return next(a for a in actions if a.type == ActionType.TAKE_PHOTO), actions

    def test_the_alarm_says_only_the_fixed_line(self):
        """Owner, Mac test: the alarm said "Leave now with your black hooded jacket and bag"
        and there was no bag. Now the fixed line is the only thing said, in both modes,
        and Bonsai is never asked about the photo."""
        for mode in (Mode.AWAY, Mode.NIGHT):
            client = SlowClient("Leave now with your black hooded jacket and bag.", delay=0)
            p = self.pipeline(client, mode)
            photo, actions = self._to_alarm(p)
            self.assertEqual(p.modes.alert, AlertLevel.ALARM)
            self.assertEqual([a.args.get("text") for a in actions if a.type == ActionType.SPEAK], [ALARM_LINE])
            before = len(self.emitted)
            p.handle(events.photo_described(photo.id, "A man in a black hooded jacket with a bag."))
            p.wait_background(5)
            self.assertEqual([a for a in self.emitted[before:] if a.type == ActionType.SPEAK], [], mode)
            self.assertEqual(client.calls, 0, mode)
            self.assertNotIn("Alarm.", ALARM_LINE)                 # never says "Alarm" to the person

    def test_no_code_line_when_the_camera_failed(self):
        p = self.pipeline(SlowClient("", delay=0), Mode.AWAY)
        photo, _ = self._to_alarm(p)
        before = len(self.emitted)
        from shared import EventType as _E, make_event
        from shared.events import PhotoDescribedData
        p.handle(make_event(_E.PHOTO_DESCRIBED, PhotoDescribedData(photo_path="captures/photos/x.jpg",
                                                                   description="Photo saved."),
                            Source.CAMERA, caused_by=photo.id))     # vision failed (NV camera worker)
        p.wait_background(5)
        self.assertEqual([a for a in self.emitted[before:] if a.type == ActionType.SPEAK], [])


class _Tools:
    """Fake Bonsai for tool calls."""

    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def chat(self, messages, tools=None):
        self.calls += 1
        return self.reply


if __name__ == "__main__":
    unittest.main()
