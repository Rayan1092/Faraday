"""Demo step 3: after Faraday offers to take a message, the visitor's reply is
saved even when Bonsai answers it with another greeting (NV live run, 4B)."""
from __future__ import annotations

import unittest

from shared import ActionType, EventType, Mode, Source, make_action, make_event

from brain import fallback as fb
from brain.tests.helpers import make_brain, tool_calls

OFFER = "Would you like to leave a message?"
VISITOR = "Package for Sam, I left it by the side door."
GREETING = tool_calls(("speak", {"text": "Hello, I'm Faraday. What can I do for you?"}))


def said(text):
    return make_action(ActionType.SPEAK, {"text": text}, Source.BRAIN).to_dict()


def heard(text):
    return make_event(EventType.SPEECH_HEARD, {"text": text}, Source.SPEECH)


def kinds(decision):
    return [(a.type, a.args.get("text")) for a in decision.actions]


class OfferedMessage(unittest.TestCase):
    def test_reply_after_offer_is_saved_and_greeting_replaced(self):
        d = make_brain(GREETING).decide(heard(VISITOR), Mode.AWAY, [said(OFFER)])
        self.assertEqual(kinds(d), [(ActionType.SAVE_MESSAGE, VISITOR),
                                    (ActionType.SPEAK, fb.MESSAGE_SAVED)])
        self.assertEqual(d.actions[0].args["sender"], "visitor")

    def test_fixup_is_recorded_even_when_the_count_stays_the_same(self):
        # Bonsai answers with two lines; they become save_message + the thank-you (still 2).
        offer = make_action(ActionType.SPEAK, {"text": "Would you like to leave a message?"},
                            Source.BRAIN).to_dict()
        ev = make_event(EventType.SPEECH_HEARD, {"text": "Package for Sam."}, Source.SPEECH)
        brain = make_brain(tool_calls(("speak", {"text": "Hello!"}), ("speak", {"text": "Hi there."})))
        d = brain.decide(ev, "away", [offer])
        self.assertEqual([a.type for a in d.actions], [ActionType.SAVE_MESSAGE, ActionType.SPEAK])
        self.assertIn("saved the message Bonsai promised", d.fixups)

    def test_declining_is_not_saved(self):
        for no in ("No thanks", "Nope.", "Not now, thanks"):
            with self.subTest(no=no):
                d = make_brain(GREETING).decide(heard(no), Mode.AWAY, [said(OFFER)])
                self.assertNotIn(ActionType.SAVE_MESSAGE, [a.type for a in d.actions])

    def test_message_starting_with_no_is_still_saved(self):
        text = "No, tell Sam the package is by the side door."
        d = make_brain(GREETING).decide(heard(text), Mode.AWAY, [said(OFFER)])
        self.assertEqual(d.actions[0].type, ActionType.SAVE_MESSAGE)

    def test_without_offer_nothing_changes(self):
        d = make_brain(GREETING).decide(heard(VISITOR), Mode.AWAY,
                                        [said("Away mode on. I'll watch the door.")])
        self.assertEqual([a.type for a in d.actions], [ActionType.SPEAK])

    def test_only_the_last_line_counts(self):
        recent = [said(OFFER), said("Thank you. I saved your message.")]
        d = make_brain(GREETING).decide(heard("Bye!"), Mode.AWAY, recent)
        self.assertEqual([a.type for a in d.actions], [ActionType.SPEAK])

    def test_bonsai_saving_itself_is_left_alone(self):
        reply = tool_calls(("save_message", {"text": VISITOR, "sender": "visitor"}),
                           ("speak", {"text": "Thanks, I'll pass it on."}))
        d = make_brain(reply).decide(heard(VISITOR), Mode.AWAY, [said(OFFER)])
        self.assertEqual(kinds(d), [(ActionType.SAVE_MESSAGE, VISITOR),
                                    (ActionType.SPEAK, "Thanks, I'll pass it on.")])

    def test_home_mode_is_not_a_visitor(self):
        d = make_brain(GREETING).decide(heard(VISITOR), Mode.HOME, [said(OFFER)])
        self.assertNotIn(ActionType.SAVE_MESSAGE, [a.type for a in d.actions])


if __name__ == "__main__":
    unittest.main()
