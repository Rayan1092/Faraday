"""Each fake event returns valid actions, from the model path and the fallback path."""
import unittest

from shared import (ActionType, ChangedBy, EventType, Mode, Source, make_action,
                    make_event, validate_action, validate_event)

from brain.core import SYSTEM_PROMPT, photo_cause
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
        for mode in Mode.ALL:  # motion is plain code (baseline + modes/), not the brain
            self.assertEqual(brain.decide(events.motion(), mode).actions, [])
        out = brain.decide(events.speech("Faraday, I'm heading out."), Mode.HOME).actions
        self.assertEqual(out[0].args, {"mode": "away"})
        msg = brain.decide(events.speech("Package for Sam."), Mode.AWAY).actions
        self.assertEqual(msg[0].type, ActionType.SAVE_MESSAGE)
        summary = brain.decide(events.speech("What happened while I was out?"), Mode.HOME).actions
        self.assertEqual(summary[0].type, ActionType.SUMMARIZE_LOG)

    def test_photo_reply_depends_on_why_the_photo_was_taken(self):
        """photo_described -> take_photo -> doorbell/motion, traced through caused_by."""
        def chain(origin, reason):
            photo = make_action(ActionType.TAKE_PHOTO, {"reason": reason}, Source.RULES,
                                caused_by=origin.id)
            return events.photo_described(photo.id), [origin.to_dict(), photo.to_dict()]

        # Doorbell photo: greeting plus an offer to take a message (Home has no photo
        # in the mode table, but if one arrives it's greeted the same way).
        for mode in (Mode.HOME, Mode.AWAY):
            event, recent = chain(events.doorbell(), "x")
            text = make_brain().decide(event, mode, recent).actions[0].args["text"]
            self.assertIn("leave a message", text, mode)
        # Motion photos (warning lines come from the baseline) and anything at
        # night (quiet): nothing, and Bonsai is never asked.
        for origin, mode in ((events.motion, Mode.AWAY), (events.motion, Mode.NIGHT),
                             (events.motion, Mode.HOME), (events.doorbell, Mode.NIGHT)):
            brain = make_brain(tool_calls(("speak", {"text": "Hello visitor!"})))
            event, recent = chain(origin(), "x")
            decision = brain.decide(event, mode, recent)
            self.assertEqual((decision.via, decision.actions), ("code", []), (origin, mode))
            self.assertEqual(brain.client.calls, [])

    def test_doorbell_reply_always_offers_a_message(self):
        bell = events.doorbell()
        photo = make_action(ActionType.TAKE_PHOTO, {"reason": "doorbell"}, Source.RULES,
                            caused_by=bell.id)
        recent = [bell.to_dict(), photo.to_dict()]
        brain = make_brain(tool_calls(("speak", {"text": "Hello, can I help you?"})))
        texts = [a.args["text"] for a in brain.decide(events.photo_described(photo.id),
                                                       Mode.AWAY, recent).actions]
        self.assertEqual(texts, ["Hello, can I help you?", "Would you like to leave a message?"])
        brain = make_brain(tool_calls(("speak", {"text": "Hi! Want to leave a message?"})))
        texts = [a.args["text"] for a in brain.decide(events.photo_described(photo.id),
                                                       Mode.AWAY, recent).actions]
        self.assertEqual(texts, ["Hi! Want to leave a message?"])

    def test_promised_message_is_saved_in_code(self):
        brain = make_brain(tool_calls(("speak", {"text": "I'll take that message for you."})))
        actions = brain.decide(events.speech("Package for Sam, side door."), Mode.AWAY).actions
        self.assertEqual(actions[0].type, ActionType.SAVE_MESSAGE)
        self.assertEqual(actions[0].args["text"], "Package for Sam, side door.")
        # No promise, no message: plain chat stays chat.
        brain = make_brain(tool_calls(("speak", {"text": "Hello there!"})))
        actions = brain.decide(events.speech("Hi, is anyone home?"), Mode.AWAY).actions
        self.assertEqual([a.type for a in actions], [ActionType.SPEAK])

    def test_photo_cause_tracing(self):
        bell = events.doorbell()
        photo = make_action(ActionType.TAKE_PHOTO, {"reason": "motion at night"}, Source.RULES,
                            caused_by=bell.id)
        described = events.photo_described(photo.id)
        # caused_by beats the reason text
        self.assertEqual(photo_cause(described, [bell.to_dict(), photo.to_dict()]), "doorbell")
        # origin scrolled out of the log: fall back to the reason text
        self.assertEqual(photo_cause(described, [photo.to_dict()]), "motion")
        # nothing traceable
        self.assertEqual(photo_cause(described, []), "")
        self.assertEqual(photo_cause(events.photo_described(None), [photo.to_dict()]), "")
        # the doorbell prompt tells Bonsai what to do
        prompt = make_brain().prompt(described, Mode.AWAY, [bell.to_dict(), photo.to_dict()])
        self.assertIn("rang the doorbell", prompt)
        self.assertIn("offer to take a message", prompt)

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
