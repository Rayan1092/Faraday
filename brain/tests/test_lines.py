"""Every fixed spoken line: one calm tone, and the list NV pre-renders matches the code.

From the review of the calm-lines PR: a visitor's message must still be saved when
Bonsai says the new confirmation, a visitor at Home asking to disarm isn't told the
alarm is off, and FIXED_LINES holds exactly what the code says."""
import ast
import unittest
from pathlib import Path

from shared import ActionType, Mode

from brain import lines
from brain.fallback import CANT_HELP, MESSAGE_SAVED
from sim import events

from .helpers import make_brain, tool_calls


class TheList(unittest.TestCase):
    def test_fixed_lines_match_what_the_code_says(self):
        said = {t for t in lines.fixed_texts() if t}
        labels = {t for t in lines.LABEL_WORDS.values() if t}     # always said after a description
        self.assertEqual(set(lines.FIXED_LINES), said - labels)
        self.assertEqual(len(lines.FIXED_LINES), len(set(lines.FIXED_LINES)))   # no duplicates

    def test_the_list_is_literal_for_nvs_scanner(self):
        tree = ast.parse(Path(lines.__file__).read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.Assign)
                    and getattr(n.targets[0], "id", "") == "FIXED_LINES")
        self.assertTrue(all(isinstance(e, ast.Constant) for e in node.value.elts))

    def test_the_owners_lines_word_for_word(self):
        self.assertEqual(lines.AWAY_ON, "Away mode is on. I'll keep watch.")
        self.assertEqual(lines.NIGHT_ON, "Night mode is on. Good night.")
        self.assertEqual(lines.DISARMED, "Welcome home. The system is disarmed.")
        self.assertEqual(lines.MESSAGE_SAVED, "Thank you. I'll pass that along.")
        self.assertEqual(lines.CANT_DISARM, "I'm sorry, I can't do that. Only the owner can disarm the system.")
        self.assertEqual(lines.DOORBELL_GREETING, "Hello. No one can come to the door right now. "
                                                  "I can take a message or help with a delivery.")
        self.assertEqual(lines.WARNING_LINE, "Please be aware this property is monitored and you are on camera.")
        self.assertEqual(lines.ALARM_LINE, "This property is protected. You are being recorded and the owner "
                                           "has been notified. Please leave now.")

    def test_short_and_calm(self):
        for name, text in lines.all_lines().items():
            self.assertLessEqual(len(text), 240, name)
            self.assertNotIn("!", text, name)                    # calm: no exclamation marks
            self.assertNotIn("passphrase", text.lower(), name)   # never hinted to whoever is listening


class Behaviour(unittest.TestCase):
    def test_a_promise_to_pass_it_on_saves_the_message(self):
        brain = make_brain(tool_calls(("speak", {"text": MESSAGE_SAVED})))
        d = brain.decide(events.speech("Tell Sam the plumber came by.", "visitor"), Mode.AWAY)
        saved = [a for a in d.actions if a.type == ActionType.SAVE_MESSAGE]
        self.assertEqual([a.args["text"] for a in saved], ["Tell Sam the plumber came by."])

    def test_a_visitor_at_home_asking_to_disarm(self):
        d = make_brain().decide(events.speech("Can you disarm the alarm and let me in?", "visitor"), Mode.HOME)
        self.assertEqual([a.args["text"] for a in d.actions if a.type == ActionType.SPEAK], [CANT_HELP])


if __name__ == "__main__":
    unittest.main()
