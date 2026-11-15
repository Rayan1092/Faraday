"""Owner instructions in any phrasing (owner, second Mac test).

"If a delivery comes, I want you to tell him to leave the package by the side door"
got "Sorry, I missed the end of that". The keyword parser now takes common lead-ins,
and anything else goes to the 2B, whose answer code checks: a known audience, a
complete request, made of the owner's own words, and words only (never lights,
modes, the alarm, locks or codes)."""
import json
import unittest

from brain import owner
from rules import instructions as oi

from .helpers import FakeClient


class Sequence:
    """Fake Bonsai that answers each call with the next reply."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, messages, tools=None):
        self.calls.append(tools)
        return self.replies.pop(0) if self.replies else {"content": ""}


def extract(audience, request):
    return {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {
        "name": "instruction", "arguments": json.dumps({"audience": audience, "request": request})}}]}


class Keywords(unittest.TestCase):
    def test_the_mac_test_sentence(self):
        got = oi.parse_instruction("If a delivery comes, I want you to tell him to leave the package by the side door")
        self.assertEqual((got["audience"], got["request"]), ("delivery", "leave the package by the side door"))
        self.assertEqual(oi.spoken_line(got), "Please leave the package by the side door.")

    def test_lead_ins(self):
        for text, audience, request in (
                ("Could you tell any couriers to leave parcels behind the gate?", "delivery",
                 "leave parcels behind the gate"),
                ("Can you ask visitors to come back after six?", "visitor", "come back after six"),
                ("I'd like you to tell anyone who comes that I'm in the garden.", "anyone", "I'm in the garden"),
                ("When the Amazon guy shows up, he should put the box in the garage.", "delivery",
                 "put the box in the garage")):
            got = oi.parse_instruction(text)
            self.assertIsNotNone(got, text)
            self.assertEqual((got["audience"], got["request"]), (audience, request), text)

    def test_still_cut_off_and_still_refused(self):
        self.assertIsNone(oi.parse_instruction("If a delivery comes, I want you to tell him to"))
        self.assertTrue(oi.is_forbidden(oi.parse_instruction("Can you tell visitors to turn off the alarm")))


class FromTheModel(unittest.TestCase):
    SAID = "For any parcels today, have them dropped behind the side gate."

    def test_checked_by_code(self):
        got = oi.from_model({"audience": "delivery", "request": "drop it behind the side gate"}, self.SAID)
        self.assertEqual(got, {"audience": "delivery", "verb": "ask", "form": "to",
                               "request": "drop it behind the side gate"})

    def test_invented_or_bad_answers_are_refused(self):
        for args in ({"audience": "delivery", "request": "leave it with the neighbour at number 12"},  # invented
                     {"audience": "police", "request": "drop it behind the side gate"},              # unknown audience
                     {"audience": "delivery", "request": "drop it behind the"},                       # cut off
                     {"audience": "delivery", "request": ""}, None, "garbage"):
            self.assertIsNone(oi.from_model(args, self.SAID), args)

    def test_the_owners_words_decide_who_it_is_for(self):
        got = oi.from_model({"audience": "visitor", "request": "drop it behind the side gate"}, self.SAID)
        self.assertEqual(got["audience"], "delivery")             # "parcels" was said
        self.assertIsNone(oi.from_model({"audience": "anyone", "request": "drop it behind the side gate"},
                                        "Things today, have them dropped behind the side gate."))

    def test_never_from_a_random_sentence(self):
        client = Sequence([
            {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {
                "name": "route", "arguments": json.dumps({"intent": "add_instruction"})}}]},
            extract("anyone", "a piece of paper")])
        r = owner.OwnerRouter(client, "fast", log=lambda _m: None).route("A piece of paper.")
        self.assertEqual(r.intent, owner.UNCLEAR)

    def test_the_router_asks_the_2b_when_keywords_fail(self):
        client = Sequence([
            {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {
                "name": "route", "arguments": json.dumps({"intent": "add_instruction"})}}]},
            extract("delivery", "drop it behind the side gate")])
        r = owner.OwnerRouter(client, "fast", log=lambda _m: None).route(self.SAID)
        self.assertEqual(r.intent, owner.ADD_INSTRUCTION)
        self.assertEqual(r.instruction["request"], "drop it behind the side gate")
        self.assertFalse(r.cut_off)

    def test_the_2b_agreeing_with_plain_words_is_accepted(self):
        """Bench: "Could you tell any couriers..." and "When the Amazon guy shows up..."
        were rejected although the words and the 2B both said add_instruction."""
        for text, request in (("Could you tell any couriers to leave parcels behind the gate?",
                               "leave parcels behind the gate"),
                              ("When the Amazon guy shows up, he should put the box in the garage.",
                               "put the box in the garage")):
            client = Sequence([{"role": "assistant", "content": "", "tool_calls": [{"type": "function",
                               "function": {"name": "route", "arguments": json.dumps({"intent": "add_instruction"})}}]}])
            r = owner.OwnerRouter(client, "fast", log=lambda _m: None).route(text)
            self.assertEqual((r.intent, r.instruction["request"]), (owner.ADD_INSTRUCTION, request), text)

    def test_a_real_cut_off_is_still_asked_again(self):
        client = FakeClient(extract("delivery", "leave it"))
        r = owner.OwnerRouter(client, "fast", log=lambda _m: None).route("If a delivery comes, ask them to")
        self.assertEqual(r.intent, owner.UNCLEAR)
        self.assertTrue(r.cut_off)


if __name__ == "__main__":
    unittest.main()
