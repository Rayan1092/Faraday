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

    def test_the_visitor_prompt_never_sees_a_description(self):
        """Spoken lines never describe the person: Bonsai never gets the description to
        talk about (owner, Mac test)."""
        photo = events.photo_described("act_1", "A man in a black hooded jacket with a bag.")
        self.assertNotIn("hooded", _log_line(photo.to_dict()))
        self.assertIn("a photo", _log_line(photo.to_dict()))


if __name__ == "__main__":
    unittest.main()
