"""The owner's delivery instruction must reach the courier (Mac test: with "if a delivery
comes, ask them to leave it by the side door" stored, a visitor with a package only got
"Thank you. I saved your message."). Code guarantees it: a visitor who mentions a
delivery, or follows a delivery label, hears the instruction."""
import tempfile
import unittest
from pathlib import Path

from shared import ActionType, Mode

from brain import Brain
from brain.bonsai import BonsaiError
from brain.pipeline import Pipeline
from log import EventLog
from modes import ModeState
from rules import InstructionStore
from rules import instructions as oi
from sim import events

from .helpers import FakeClient, tool_calls

SECRET = "silver otter lantern"
SIDE_DOOR = "If a delivery comes, ask them to leave it by the side door."
SAVED_ONLY = tool_calls(("save_message", {"text": "Package for Sam", "sender": "visitor"}),
                        ("speak", {"text": "Thank you. I saved your message."}))


def said(actions):
    return [a.args["text"] for a in actions
            if a.type == ActionType.SPEAK and a.args["text"] != "One moment, please."]


class DeliveryInstruction(unittest.TestCase):
    def make(self, reply=SAVED_ONLY, instruction=SIDE_DOOR, mode=Mode.AWAY):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = InstructionStore(Path(tmp.name) / "ins.json")
        if instruction:
            store.add(oi.parse_instruction(instruction))
        self.client = FakeClient(reply)
        self.p = Pipeline(Brain(self.client, SECRET, log=lambda _m: None), ModeState(SECRET, mode=mode),
                          EventLog(Path(tmp.name) / "e.jsonl", SECRET), say=lambda _m: None,
                          instructions=store)

    def visitor(self, text, channel="visitor"):
        return self.p.handle(events.speech(text, channel))

    def test_package_mentioned_gets_the_instruction(self):
        self.make()
        r = self.visitor("Hi, I have a package for Sam.")
        self.assertTrue(any("side door" in line.lower() for line in said(r.actions)), said(r.actions))
        self.assertIn(ActionType.SAVE_MESSAGE, [a.type for a in r.actions])

    def test_untagged_speech_while_armed_too(self):
        self.make()
        r = self.visitor("Delivery for this address.", channel=None)
        self.assertTrue(any("side door" in line.lower() for line in said(r.actions)))

    def test_when_bonsai_fails(self):
        self.make(reply=BonsaiError("timeout"))
        r = self.visitor("Amazon delivery, where do you want it?")
        self.assertTrue(any("side door" in line.lower() for line in said(r.actions)), said(r.actions))

    def test_after_a_delivery_label_without_the_word(self):
        self.make(reply=tool_calls(("speak", {"text": "Thank you."})))
        bell = events.doorbell()
        self.p.handle(bell)
        take = next(e for e in self.p.log.recent(20) if e.get("type") == ActionType.TAKE_PHOTO)
        self.client.reply = tool_calls(("speak", {"text": "Thank you."}))
        photo = self.p.handle(events.photo_described(take["id"], "A courier holding a cardboard box at the door."))
        r = self.visitor("Where should I put it?")
        both = said(photo.actions) + said(r.actions)
        self.assertEqual(sum("side door" in line.lower() for line in both), 1, both)   # once per visit

    def test_a_different_spot_from_bonsai_is_replaced(self):
        self.make(reply=tool_calls(("speak", {"text": "Please leave the package by the front door."})))
        r = self.visitor("I've got a parcel here.")
        lines = " ".join(said(r.actions)).lower()
        self.assertIn("side door", lines)
        self.assertNotIn("front door", lines)

    def test_no_duplicate_when_bonsai_said_it(self):
        self.make(reply=tool_calls(("speak", {"text": "Please leave it by the side door. Thank you."})))
        r = self.visitor("I have a package.")
        self.assertEqual(sum("side door" in line.lower() for line in said(r.actions)), 1)

    def test_review_old_label_is_another_visit(self):
        from datetime import datetime, timedelta
        from brain.core import latest_label
        old = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        now = datetime.now().isoformat(timespec="seconds")
        entry = {"kind": "event", "type": "threat_assessed", "timestamp": old, "data": {"category": "delivery"}}
        self.assertEqual(latest_label([entry]), "delivery")
        self.assertEqual(latest_label([entry], now, 180), "")

    def test_review_ordinary_words_are_not_a_delivery(self):
        self.make(reply=tool_calls(("save_message", {"text": "dog is out"}),
                                   ("speak", {"text": "I'll put it in Sam's messages."})))
        r = self.visitor("Are any grown-ups home? Tell them their dog is out.")
        self.assertEqual(said(r.actions), ["I'll put it in Sam's messages."])

    def test_review_message_confirmation_kept(self):
        self.make(reply=tool_calls(("save_message", {"text": "Package for Sam", "sender": "visitor"})))
        r = self.visitor("I have a package for Sam.")
        # One line from code (owner, Mac test): the owner's words, then the promise.
        self.assertEqual(said(r.actions), ["Please leave it by the side door. I'll let the owner know it arrived."])
        self.assertIn(ActionType.SAVE_MESSAGE, [a.type for a in r.actions])

    def test_review_said_once_per_visit(self):
        self.make()
        self.visitor("I have a package for Sam.")
        self.client.reply = tool_calls(("speak", {"text": "Thank you, goodbye."}))
        r = self.visitor("Okay, thanks, I'll leave the package then. Bye.")
        self.assertFalse(any("side door" in line.lower() for line in said(r.actions)), said(r.actions))

    def test_owner_confirmation_is_not_the_visitor_line(self):
        """The owner's "Okay, when a delivery comes I'll ask them..." is not saying it to a visitor."""
        self.make(instruction=None, mode=Mode.HOME)
        self.p.handle(events.speech(SIDE_DOOR, "owner"))
        self.p.owner_set_mode(Mode.AWAY)
        r = self.visitor("I have a package for Sam.")
        self.assertTrue(any("side door" in line.lower() for line in said(r.actions)), said(r.actions))

    def test_not_for_a_plain_visitor_or_without_an_instruction(self):
        self.make(reply=tool_calls(("speak", {"text": "Hello, how can I help?"})))
        r = self.visitor("Hi, is Sam home? I'm a friend from work.")
        self.assertFalse(any("side door" in line.lower() for line in said(r.actions)))
        self.make(instruction=None)
        r = self.visitor("I have a package for Sam.")
        self.assertFalse(any("side door" in line.lower() for line in said(r.actions)))


if __name__ == "__main__":
    unittest.main()
