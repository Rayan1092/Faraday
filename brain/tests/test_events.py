"""Each fake event returns valid actions, from the model path and the fallback path."""
import unittest

from shared import (ActionType, ChangedBy, EventType, Mode, Source, make_event,
                    validate_action, validate_event)

from brain.core import SYSTEM_PROMPT
from brain.tools import allowed_actions
from modes import ModeState
from sim import events

from .helpers import PASSPHRASE, make_brain, tool_calls

# A model that proposes one of everything; the guard keeps what fits the event.
EVERYTHING = tool_calls(
    ("set_mode", {"mode": "away"}),
    ("outlet_on", {"outlet": "porch_light"}),
    ("speak", {"text": "Hello."}),
    ("take_photo", {"reason": "check"}),
    ("save_message", {"text": "A message.", "sender": "visitor"}),
    ("summarize_log", {"minutes": 60}),
    ("add_rule", {"when": {"event": "motion", "modes": ["night"]},
                  "then": [{"type": "outlet_on", "args": {"outlet": "living_room_lamp"}}],
                  "description": "Lamp on for night motion."}),
    ("outlet_off", {"outlet": "spare"}),
)


def fake_events():
    state = ModeState(PASSPHRASE)
    return [events.doorbell(), events.motion(), events.speech("I'm heading out."),
            events.speech("From now on, when there's motion at night, turn on the lamp."),
            events.speech("What happened while I was out?"),
            events.speech("Package for Sam, I left it by the side door."),
            events.photo_described("act_12345678"),
            state.arm(Mode.AWAY, ChangedBy.DASHBOARD)[0]]


class EveryEventGivesValidActions(unittest.TestCase):
    def check(self, decision, event, mode):
        allowed = allowed_actions(event.type, mode)
        for action in decision.actions:
            self.assertEqual(validate_action(action.to_dict()), [], action.to_dict())
            self.assertIn(action.type, allowed)
            self.assertEqual(action.source, Source.BRAIN)
            self.assertEqual(action.caused_by, event.id)

    def test_sim_events_are_valid(self):
        for event in fake_events():
            self.assertEqual(validate_event(event.to_dict()), [])
        for event in fake_events()[:-1]:
            self.assertEqual(event.source, Source.SIM)

    def test_fallback_path(self):
        for mode in Mode.ALL:
            for event in fake_events():
                self.check(make_brain().decide(event, mode), event, mode)

    def test_model_path(self):
        for mode in Mode.ALL:
            for event in fake_events():
                decision = make_brain(EVERYTHING).decide(event, mode)
                self.check(decision, event, mode)
                if event.type != EventType.MODE_CHANGED and decision.via == "bonsai":
                    self.assertTrue(decision.actions)

    def test_modes_events_never_reach_the_model(self):
        alert = make_event(EventType.ALERT_CHANGED,
                           {"level": "warning", "previous": "normal"}, Source.MODES)
        change = ModeState(PASSPHRASE).arm(Mode.NIGHT, ChangedBy.DASHBOARD)[0]
        for event in (alert, change):
            brain = make_brain(EVERYTHING)
            decision = brain.decide(event, Mode.NIGHT)
            self.assertEqual(decision.via, "code")
            self.assertEqual(brain.client.calls, [])

    def test_expected_fallback_behaviour(self):
        brain = make_brain()
        # Doorbell photos/lights are rules/baseline.py's job; the brain adds nothing.
        self.assertEqual(brain.decide(events.doorbell(), Mode.AWAY).actions, [])
        night = [a.args for a in brain.decide(events.motion(), Mode.NIGHT).actions]
        self.assertIn({"outlet": "porch_light"}, night)
        self.assertEqual(brain.decide(events.motion(), Mode.HOME).actions, [])
        out = brain.decide(events.speech("Faraday, I'm heading out."), Mode.HOME).actions
        self.assertEqual(out[0].args, {"mode": "away"})
        msg = brain.decide(events.speech("Package for Sam."), Mode.AWAY).actions
        self.assertEqual(msg[0].type, ActionType.SAVE_MESSAGE)
        summary = brain.decide(events.speech("What happened while I was out?"), Mode.HOME).actions
        self.assertEqual(summary[0].type, ActionType.SUMMARIZE_LOG)

    def test_photo_reply_depends_on_why_the_photo_was_taken(self):
        brain = make_brain()
        for reason, mode, want in (("doorbell while away", Mode.AWAY, "Nobody can come"),
                                   ("motion at night", Mode.NIGHT, "on camera"),
                                   ("doorbell", Mode.HOME, "with you shortly")):
            photo = {"kind": "action", "id": "act_aaaaaaaa", "type": "take_photo",
                     "timestamp": "2026-11-14T23:41:05.180-05:00", "source": "rules",
                     "caused_by": None, "args": {"reason": reason}}
            event = events.photo_described("act_aaaaaaaa")
            decision = brain.decide(event, mode, [photo])
            self.assertIn(want, decision.actions[0].args["text"], reason)
            self.assertIn(f"(photo taken for: {reason})", brain.prompt(event, mode, [photo]))
        self.assertEqual(brain.decide(events.photo_described("act_aaaaaaaa"), Mode.HOME, [
            {"id": "act_aaaaaaaa", "type": "take_photo", "args": {"reason": "motion"}}]).actions, [])

    def test_prompt_is_short(self):
        brain = make_brain()
        recent = [events.speech("x" * 300).to_dict() for _ in range(30)]
        prompt = brain.prompt(events.doorbell(), Mode.AWAY, recent)
        self.assertLess(len(SYSTEM_PROMPT), 400)
        self.assertEqual(prompt.count("\n- "), 10)
        self.assertLess(len(prompt), 1000)
        self.assertTrue(prompt.startswith("Mode: away (armed)"))


if __name__ == "__main__":
    unittest.main()
