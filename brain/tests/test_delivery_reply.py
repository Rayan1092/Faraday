"""A visitor with a delivery always gets code's reply (owner, Mac test).

With no owner instruction, "Hi, I have a package, where should I leave it?" got "I can
take a picture of the package and save it for you". Now code answers: "You can leave
it by the door. I'll let the owner know it arrived." With an instruction, the owner's
words come first. The visitor's words are saved for the owner either way."""
import unittest

from shared import ActionType, Mode

from brain import lines
from brain.core import _log_line
from sim import events

from .helpers import make_brain, tool_calls

QUESTION = "Hi, I have a package, where should I leave it?"


class _Store:
    def __init__(self, items):
        self.items = items

    def relevant(self, label=""):
        return [i for i in self.items if i["audience"] in ("delivery", "anyone")]


def said(actions):
    return [a.args["text"] for a in actions if a.type == ActionType.SPEAK]


class DeliveryReply(unittest.TestCase):
    def test_no_instruction_gets_the_default_line(self):
        brain = make_brain(tool_calls(("speak", {"text": "I can take a picture of the package and save it for you."})))
        d = brain.decide(events.speech(QUESTION, "visitor"), Mode.AWAY)
        self.assertEqual(said(d.actions), [lines.DELIVERY_DEFAULT])
        saved = [a.args["text"] for a in d.actions if a.type == ActionType.SAVE_MESSAGE]
        self.assertEqual(saved, [QUESTION])
        self.assertIn("corrected: code's delivery reply", d.fixups)

    def test_with_an_instruction_the_owners_words(self):
        from rules import instructions as oi
        brain = make_brain(tool_calls(("speak", {"text": "Sure, leave it on the porch."})))
        brain.instructions = _Store([dict(oi.parse_instruction(
            "If a delivery comes, ask them to leave it by the side door."), id="ins_1")])
        d = brain.decide(events.speech(QUESTION, "visitor"), Mode.AWAY)
        self.assertEqual(said(d.actions), ["Please leave it by the side door. I'll let the owner know it arrived."])

    def test_the_fallback_gives_the_same_reply(self):
        brain = make_brain(RuntimeError("LM Studio down"))
        d = brain.decide(events.speech(QUESTION, "visitor"), Mode.NIGHT)
        self.assertEqual(said(d.actions), [lines.DELIVERY_DEFAULT])

    def test_the_owner_is_never_answered_like_a_courier(self):
        brain = make_brain(tool_calls(("speak", {"text": "Okay."})))
        d = brain.decide(events.speech("Did a package come today?", "owner"), Mode.HOME)
        self.assertNotIn(lines.DELIVERY_DEFAULT, said(d.actions))

    def test_a_delivery_label_alone_keeps_bonsais_reply(self):
        """Review: in a delivery-labelled visit, "Hello?" got the delivery line and was
        saved as the message. Only the visitor's own delivery words bring code's line."""
        from shared import EventType, Source, make_event
        label = make_event(EventType.THREAT_ASSESSED, {"category": "delivery", "reason": "r"}, Source.BRAIN)
        brain = make_brain(tool_calls(("speak", {"text": "Hello! How can I help you?"})))
        d = brain.decide(events.speech("Hello?", "visitor"), Mode.AWAY, [label.to_dict()])
        self.assertEqual(said(d.actions), ["Hello! How can I help you?"])
        self.assertNotIn(ActionType.SAVE_MESSAGE, [a.type for a in d.actions])

    def test_the_owners_other_words_are_kept(self):
        """Review: an "anyone" instruction Bonsai was meant to say was dropped with its speech."""
        from rules import instructions as oi
        brain = make_brain(tool_calls(("speak", {"text": "Okay."})))
        brain.instructions = _Store([dict(oi.parse_instruction(
            "If anyone comes, tell them I'm in the back garden."), id="ins_2")])
        d = brain.decide(events.speech(QUESTION, "visitor"), Mode.AWAY)
        self.assertEqual(said(d.actions), ["The owner is in the back garden. " + lines.DELIVERY_DEFAULT])

    def test_a_non_drop_off_instruction_gets_no_arrival_promise(self):
        self.assertEqual(lines.delivery_line("Please come back tomorrow."), "Please come back tomorrow.")
        self.assertTrue(lines.delivery_line("Please leave it by the side door.").endswith("arrived."))

    def test_the_visitor_prompt_never_sees_a_description(self):
        """Spoken lines never describe the person: Bonsai never gets the description to
        talk about (owner, Mac test)."""
        photo = events.photo_described("act_1", "A man in a black hooded jacket with a bag.")
        self.assertNotIn("hooded", _log_line(photo.to_dict()))
        self.assertIn("a photo", _log_line(photo.to_dict()))


if __name__ == "__main__":
    unittest.main()
