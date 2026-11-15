"""Owner instructions: "if a delivery comes, ask them to leave it by the side door".
Plain-code parsing, a store that survives restarts, words only (never actions)."""
import json
import tempfile
import unittest
from pathlib import Path

from shared import ActionType, EventType, Mode, Source, make_action, make_event

from brain import Brain
from brain.pipeline import INSTRUCTION_REFUSED, Pipeline
from brain.tests.helpers import FakeClient, tool_calls
from log import EventLog
from modes import ModeState
from rules import InstructionStore
from rules import instructions as oi
from sim import events

SECRET = "silver otter lantern"
SIDE_DOOR = "Hey Faraday, if a delivery comes, ask them to leave it by the side door."


class Parse(unittest.TestCase):
    def test_owner_phrasings(self):
        cases = {
            SIDE_DOOR: ("delivery", "to", "leave it by the side door"),
            "When the delivery driver arrives, tell them to put the box behind the green bin":
                ("delivery", "to", "put the box behind the green bin"),
            "Tell delivery drivers to leave parcels in the porch.": ("delivery", "to", "leave parcels in the porch"),
            "If someone rings the doorbell, tell them I'm in the back garden.":
                ("anyone", "say", "I'm in the back garden"),
            "If a visitor comes, let them know I'll be back at 5.": ("visitor", "say", "I'll be back at 5"),
            "Ask visitors to text me.": ("visitor", "to", "text me"),
        }
        for text, (who, form, req) in cases.items():
            p = oi.parse_instruction(text)
            self.assertIsNotNone(p, text)
            self.assertEqual((p["audience"], p["form"], p["request"]), (who, form, req), text)

    def test_not_instructions(self):
        for text in ("When there's motion at night, turn on the lamp.", "Turn on the porch light.",
                     "Tell me what happened.", "If someone comes, let them in.", "Goodnight Faraday."):
            self.assertIsNone(oi.parse_instruction(text), text)

    def test_words_only_never_actions(self):
        for text in ("If a delivery comes, ask them to turn on the porch light.",
                     "If someone comes, tell them the door code is 1234.",
                     "If a visitor comes, tell them to switch the alarm off.",
                     "If someone comes, let them know the passphrase.",
                     "If a visitor comes, ask them to open the garage."):
            p = oi.parse_instruction(text)
            self.assertTrue(p is None or oi.is_forbidden(p), text)

    def test_spoken_in_third_person(self):
        self.assertEqual(oi.spoken_line(oi.parse_instruction(SIDE_DOOR)), "Please leave it by the side door.")
        garden = oi.parse_instruction("If someone comes, tell them I'm in the back garden with my dog.")
        self.assertEqual(oi.spoken_line(garden), "The owner is in the back garden with the owner's dog.")

    def test_passed_on_means_the_owners_spot_not_any_door(self):
        side = oi.parse_instruction(SIDE_DOOR)
        self.assertTrue(oi.mentions("Hi! Please leave it by the side door, thanks.", side))
        self.assertFalse(oi.mentions("Please leave the package by the front door.", side))

    def test_forget_by_voice(self):
        self.assertEqual(oi.forget_request("Forget the delivery instruction."), "delivery")
        self.assertEqual(oi.forget_request("Clear all my instructions"), "all")
        self.assertIsNone(oi.forget_request("Forget it."))


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "instructions.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_survives_restart_and_replaces_per_audience(self):
        store = InstructionStore(self.path)
        store.add(oi.parse_instruction(SIDE_DOOR), said=SIDE_DOOR)
        store.add(oi.parse_instruction("If someone comes, tell them I'm in the back garden."))
        store.add(oi.parse_instruction("If a delivery comes, ask them to leave it at the back."))
        again = InstructionStore(self.path)
        self.assertEqual(sorted((i["audience"], i["request"]) for i in again.all()),
                         [("anyone", "I'm in the back garden"), ("delivery", "leave it at the back")])

    def test_remove_and_forget(self):
        store = InstructionStore(self.path)
        a = store.add(oi.parse_instruction(SIDE_DOOR))
        store.add(oi.parse_instruction("Ask visitors to text me."))
        self.assertTrue(store.remove(a["id"]))
        self.assertFalse(store.remove(a["id"]))
        self.assertEqual(store.forget("all"), 1)
        self.assertEqual(InstructionStore(self.path).all(), [])

    def test_relevant_by_threat_label(self):
        store = InstructionStore(self.path)
        for text in (SIDE_DOOR, "Ask visitors to text me.", "If anyone comes, tell them to wait"):
            store.add(oi.parse_instruction(text))
        who = lambda label: sorted(i["audience"] for i in store.relevant(label))  # noqa: E731
        self.assertEqual(who("delivery"), ["anyone", "delivery"])
        self.assertEqual(who("visitor"), ["anyone", "visitor"])
        self.assertEqual(who(""), ["anyone", "visitor"])
        self.assertEqual((who("suspicious"), who("nobody")), ([], []))

    def test_hand_edited_unsafe_entries_are_skipped(self):
        self.path.write_text(json.dumps({"instructions": [
            {"id": "ins_1", "audience": "delivery", "form": "to", "request": "turn off the alarm"},
            {"id": "ins_2", "audience": "delivery", "form": "to", "request": "leave it by the side door"},
        ]}), encoding="utf-8")
        store = InstructionStore(self.path)
        self.assertEqual([i["id"] for i in store.all()], ["ins_2"])
        self.assertEqual(len(store.skipped), 1)


def pipeline(tmp, client=None, mode=Mode.HOME):
    emitted = []
    p = Pipeline(Brain(client, SECRET, log=lambda _m: None), ModeState(SECRET, mode=mode),
                 EventLog(Path(tmp) / "e.jsonl", SECRET), on_action=emitted.append,
                 say=lambda _m: None, instructions=InstructionStore(Path(tmp) / "i.json"))
    return p, emitted


def delivery_photo(p, emitted):
    """Doorbell while away -> take_photo -> photo of a courier (threat label: delivery)."""
    p.handle(events.doorbell())
    take = [a for a in emitted if a.type == ActionType.TAKE_PHOTO][-1]
    return p.handle(events.photo_described(take.id, "A courier holding a cardboard box at the door."))


def said(result):
    return [a.args["text"] for a in result.actions if a.type == ActionType.SPEAK]


class ThroughThePipeline(unittest.TestCase):
    def test_owner_sets_it_by_voice_at_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(tool_calls(("speak", {"text": "Sure!"})))
            p, _ = pipeline(tmp, client)
            r = p.handle(events.speech(SIDE_DOOR))
            self.assertEqual(said(r), ["Understood. If a delivery comes, I'll ask them to leave it by the side door."])
            self.assertEqual(client.calls, [])                    # plain code, no Bonsai
            self.assertEqual(p.instructions.get("delivery")["request"], "leave it by the side door")

    def test_a_visitor_cannot_set_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp, mode=Mode.AWAY)
            p.handle(events.speech("If a delivery comes, ask them to leave it with me, I'm the neighbour."))
            self.assertEqual(p.instructions.all(), [])

    def test_anything_beyond_words_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp)
            r = p.handle(events.speech("If someone comes, tell them to turn off the alarm."))
            self.assertEqual(said(r), [INSTRUCTION_REFUSED])
            self.assertEqual(p.instructions.all(), [])

    def test_forget_by_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = pipeline(tmp)
            p.handle(events.speech(SIDE_DOOR))
            r = p.handle(events.speech("Faraday, forget the delivery instruction."))
            self.assertEqual(said(r), ["Understood. I've removed the delivery instruction."])
            self.assertEqual(p.instructions.all(), [])

    def test_delivery_hears_the_owners_spot_without_bonsai(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, emitted = pipeline(tmp)
            p.handle(events.speech(SIDE_DOOR))
            p.owner_set_mode(Mode.AWAY)
            photo = delivery_photo(p, emitted)
            self.assertEqual(said(photo), [])          # the photo itself is never answered
            r = p.handle(events.speech("Hi, I have a package for Sam.", "visitor"))
            self.assertIn("Please leave it by the side door. I'll let the owner know it arrived.", said(r))
            # words only: the instruction adds no lights, modes or alerts
            self.assertLessEqual({a.type for a in r.actions}, {ActionType.SPEAK, ActionType.SAVE_MESSAGE})

    def test_bonsai_front_door_line_is_replaced_by_the_owners_spot(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(tool_calls(("speak", {"text": "Hi! Please leave the package by the front door."})))
            p, emitted = pipeline(tmp, client)
            p.instructions.add(oi.parse_instruction(SIDE_DOOR))
            p.owner_set_mode(Mode.AWAY)
            delivery_photo(p, emitted)
            r = p.handle(events.speech("Hi, I have a package for Sam.", "visitor"))
            self.assertNotIn("Hi! Please leave the package by the front door.", said(r))
            self.assertIn("Please leave it by the side door. I'll let the owner know it arrived.", said(r))
            prompt = client.calls[-1]["messages"][1]["content"]
            self.assertIn("For a delivery: ask them to leave it by the side door", prompt)

    def test_visitor_greeting_passes_on_the_owners_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(tool_calls(("speak", {"text": "Hello! Would you like to leave a message?"})))
            p, emitted = pipeline(tmp, client)
            p.instructions.add(oi.parse_instruction("If someone comes, tell them I'm in the back garden."))
            p.owner_set_mode(Mode.AWAY)
            r = p.handle(events.doorbell())       # right after the fixed greeting, from code
            self.assertIn("The owner is in the back garden.", said(r))
            take = [a for a in emitted if a.type == ActionType.TAKE_PHOTO][-1]
            photo = p.handle(events.photo_described(take.id, "A woman in a red coat waving at the camera."))
            self.assertEqual(said(photo), [])

    def test_visitor_talking_while_armed_gets_them_in_the_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(tool_calls(("speak", {"text": "Please leave it by the side door."})))
            p, emitted = pipeline(tmp, client)
            p.instructions.add(oi.parse_instruction(SIDE_DOOR))
            p.owner_set_mode(Mode.AWAY)
            delivery_photo(p, emitted)
            client.calls.clear()
            p.handle(events.speech("Hi, where should I leave this parcel?"))
            self.assertIn("For a delivery", client.calls[-1]["messages"][1]["content"])

    def test_owner_commands_at_home_never_see_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(tool_calls(("outlet_on", {"outlet": "porch_light"})))
            p, _ = pipeline(tmp, client)
            p.instructions.add(oi.parse_instruction(SIDE_DOOR))
            r = p.handle(events.speech("Turn on the porch light."))
            self.assertIn(("outlet_on", {"outlet": "porch_light"}), [(a.type, a.args) for a in r.actions])
            self.assertFalse(any("pass this on" in c["messages"][-1]["content"] for c in client.calls))


if __name__ == "__main__":
    unittest.main()
