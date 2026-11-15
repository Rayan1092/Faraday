"""Owner, Mac retest: replies never claim abilities, and label reasons agree with the label.

- Bonsai told someone at the intercom "I will check the app settings for you". Faraday
  can only talk, take a message and pass on the owner's words, so a line that promises
  anything else is dropped and the fallback answers.
- A "visitor" label came with the reason "doorbell while away, no one visible".
"""
import unittest
from datetime import datetime

from shared import ActionType, Mode, ThreatCategory

from brain import lines, threat
from brain.guard import claims_ability
from sim import events

from .helpers import FakeClient, make_brain, tool_calls

C = ThreatCategory
PERSON1 = [{"label": "person", "count": 1, "confidence": 0.92}]


class Promises(unittest.TestCase):
    def test_promises_it_cannot_keep(self):
        for text in ("I will check the app settings for you.", "Let me check that for you.",
                     "I'll call the owner right away.", "I'm calling the owner now.",
                     "I can open the gate for you.", "I'll take care of it.", "I'll be right back.",
                     "I'll go ahead and fix the speaker.", "We will send someone over.",
                     "I can help you with the app.", "I'll look into it.",
                     # review: an allowed first verb hid these
                     "Sure, I'll let you in.", "Let me take a look at the app settings for you.",
                     "I'll take a look.", "I'll give Sam a call right away.", "I'll put you through to the owner.",
                     "I'll make sure someone calls you back.", "I'll keep you posted.",
                     "I'll let you know when the owner is back.",
                     "I can help you with that. Would you like to leave a message?",
                     "I am checking the app settings for you.", "I'd be happy to check that.",
                     "Please wait while I check that for you.", "Sure, checking now.",
                     "I've called the police. Stay calm.", "I have contacted the owner."):
            self.assertTrue(claims_ability(text), text)

    def test_questions_and_the_owners_words(self):
        self.assertFalse(claims_ability("Hello! Is there anything I can help you with?"))
        self.assertFalse(claims_ability("I'd be happy to take a message."))
        for good in ("I can only take messages right now.", "I can take messages for the owner.",
                     "Hello! I can help you leave a message for the owner.", "I'll take that message for the owner.",
                     "I can take down your message.", "I'll make sure your message gets to the owner.",
                     "I'll be sure to let the owner know.", "I will relay your message.",
                     "I'll share that with the owner.", "I'll keep that in mind.",
                     "Would you like me to take a message?"):
            self.assertFalse(claims_ability(good), good)
        for offer in ("Would you like me to call the owner?", "Shall I check the app settings for you?",
                      "Should I open the gate for you?", "I can check that for you, would that help?",
                      "I'll check the app settings for you, okay?", "I've called the owner, is there anything else?"):
            self.assertTrue(claims_ability(offer), offer)
        owner_said = lambda s: "back at five" in s
        self.assertFalse(claims_ability("We'll be back at five.", skip=owner_said))
        self.assertTrue(claims_ability("We'll be back at five.") and claims_ability(
            "We'll be back at five. I'll call you later.", skip=owner_said))

    def test_what_it_can_do_is_fine(self):
        for text in lines.FIXED_LINES + [
                lines.MESSAGE_SAVED, lines.CANT_HELP, lines.DELIVERY_DEFAULT, lines.SORRY,
                "I'll let the owner know.", "Thank you. I'll pass that along.",
                "I can take a message for the owner.", "I can help with your delivery.",
                "Please let me know if you'd like to leave a message.", "I can't open the door.",
                "I cannot do that, but I can take a message.", "I'll make sure the owner gets it.",
                "I'll tell the owner you stopped by.", "Away mode is on. I'll keep watch."]:
            self.assertFalse(claims_ability(text), text)

    def test_the_promise_is_not_said(self):
        said = "When I press hold to talk to a visitor, it's not playing on the speaker."
        brain = make_brain(tool_calls(("speak", {"text": "I will check the app settings for you."})))
        d = brain.decide(events.speech(said, "visitor"), Mode.HOME)
        texts = [a.args.get("text") for a in d.actions if a.type == ActionType.SPEAK]
        self.assertNotIn("I will check the app settings for you.", texts)
        self.assertEqual(texts, [lines.MESSAGE_SAVED])     # the fallback keeps their words
        self.assertIn("promises something Faraday can't do", " ".join(why for _, why in d.dropped))

    def test_armed_the_message_is_still_kept(self):
        brain = make_brain(tool_calls(("speak", {"text": "Let me call the owner for you."})))
        d = brain.decide(events.speech("Is Sam home? I need to talk to him.", "visitor"), Mode.AWAY)
        kinds = [a.type for a in d.actions]
        self.assertIn(ActionType.SAVE_MESSAGE, kinds)
        self.assertEqual([a.args["text"] for a in d.actions if a.type == ActionType.SPEAK],
                         [lines.MESSAGE_SAVED])

    def test_a_good_line_is_kept(self):
        brain = make_brain(tool_calls(("speak", {"text": "I'll let the owner know you stopped by."})))
        d = brain.decide(events.speech("Tell Sam that Alex stopped by.", "visitor"), Mode.HOME)
        self.assertEqual([a.args["text"] for a in d.actions if a.type == ActionType.SPEAK],
                         ["I'll let the owner know you stopped by."])


def seen(description, cause="doorbell", doorbells=1, people=1):
    return threat.Context(description, Mode.AWAY, cause=cause, doorbells=doorbells,
                          when=datetime(2026, 11, 15, 7, 21), people=people, faces=1,
                          detections=PERSON1 if people else [], vision_ok=True,
                          reason="doorbell while away")


def classify(category, reason):
    return tool_calls(("classify", {"category": category, "reason": reason}))


class ReasonAgrees(unittest.TestCase):
    def assess(self, ctx, category, reason):
        return threat.ThreatAssessor(FakeClient(classify(category, reason)), "fast",
                                     log=lambda _m: None).assess(ctx)

    def test_visitor_with_no_one_visible_gets_code_reason(self):
        a = self.assess(seen("One person is standing at the door, looking at the camera."),
                        "visitor", "doorbell while away, no one visible")
        self.assertEqual(a.category, C.VISITOR)
        self.assertEqual(a.reason, "1 person, rang the doorbell.")
        self.assertIn("contradicted", a.note)

    def test_a_reason_that_fits_is_kept(self):
        a = self.assess(seen("One person is standing at the door, looking at the camera."),
                        "visitor", "1 person rang the doorbell")
        self.assertEqual((a.category, a.reason), (C.VISITOR, "1 person rang the doorbell"))

    def test_the_warning_sign_stays_in_the_reason(self):
        """Review: a masked visitor who rang kept "mask" when the 2B's reason contradicted."""
        a = self.assess(seen("A person wearing a ski mask at the door."), "suspicious",
                        "Masked person rang while no one visible")
        self.assertEqual(a.category, C.SUSPICIOUS)
        self.assertIn("mask", a.reason)

    def test_good_reasons_are_not_contradictions(self):
        for reason in ("1 person rang the doorbell, empty-handed", "1 person rang while nobody is home",
                       "1 person at the door, nothing visible in his hands"):
            self.assertTrue(threat.agrees(C.VISITOR, reason), reason)

    def test_agrees(self):
        self.assertFalse(threat.agrees(C.VISITOR, "doorbell while away, no one visible"))
        self.assertFalse(threat.agrees(C.SUSPICIOUS, "Nobody at the door."))
        self.assertFalse(threat.agrees(C.NOBODY, "1 person at the door."))
        self.assertTrue(threat.agrees(C.NOBODY, "No person detected."))
        self.assertTrue(threat.agrees(C.DELIVERY, "1 person, holding a box, rang the doorbell."))
        self.assertTrue(threat.agrees(C.UNKNOWN, "Couldn't analyse the photo."))


if __name__ == "__main__":
    unittest.main()
