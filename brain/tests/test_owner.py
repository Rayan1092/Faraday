"""Owner requests ("Hey Faraday, ...") get their own router with fixed intents.

From the Mac full-system test:
- "Hey Faraday, a piece of paper" got "I saved your message": owner speech must never
  be saved as a visitor message.
- "if a delivery comes, ask them to" (cut off) triggered the summary: it must ask the
  owner to repeat.
- While armed, wake requests change nothing except through the passphrase.
"""
import tempfile
import unittest
from pathlib import Path

from shared import ActionType, EventType, Mode, Outlet

from brain import Brain, lines, owner
from brain.owner import OwnerRouter
from brain.pipeline import Pipeline
from log import EventLog
from modes import ModeState
from rules import InstructionStore, RuleStore
from sim import events

from .helpers import FakeClient, tool_calls

SECRET = "silver otter lantern"


def route_reply(intent, **slots):
    return tool_calls(("route", {"intent": intent, **slots}))


def said(actions):
    return [a.args["text"] for a in actions if a.type == ActionType.SPEAK]


def kinds(actions):
    return [a.type for a in actions]


class Router(unittest.TestCase):
    def test_keywords_without_a_model(self):
        r = OwnerRouter(None, log=lambda _m: None)
        cases = {
            "I'm heading out.": owner.ARM_AWAY,
            "Goodnight, I'm going to bed.": owner.ARM_NIGHT,
            "Who's at the door?": owner.LOOK,
            "What happened while I was out?": owner.SUMMARY,
            "Turn off the porch light.": owner.LIGHT,
            "If a delivery comes, ask them to leave it by the side door.": owner.ADD_INSTRUCTION,
            "When there's motion at night, turn on the living room lamp.": owner.ADD_RULE,
            "Disarm the system.": owner.PASSPHRASE,
            "A piece of paper.": owner.UNCLEAR,
        }
        for text, want in cases.items():
            self.assertEqual(r.route(text).intent, want, text)

    def test_cut_off_requests_ask_to_repeat(self):
        r = OwnerRouter(None, log=lambda _m: None)
        for text in ("If a delivery comes, ask them to", "if a delivery comes", "Tell visitors that",
                     "When someone rings, turn on the"):
            got = r.route(text)
            self.assertEqual((got.intent, got.cut_off), (owner.UNCLEAR, True), text)
        self.assertFalse(r.route("A piece of paper.").cut_off)

    def test_light_needs_which_and_on_or_off(self):
        r = OwnerRouter(None, log=lambda _m: None)
        got = r.route("Turn the living room lamp on please")
        self.assertEqual((got.intent, got.outlet, got.on), (owner.LIGHT, Outlet.LIVING_ROOM_LAMP, True))
        self.assertEqual(r.route("Turn on the lights").outlet, Outlet.PORCH_LIGHT)
        self.assertEqual(r.route("The lamp.").intent, owner.UNCLEAR)

    def test_the_model_picks_and_code_checks(self):
        def route(text, intent, **slots):
            return OwnerRouter(FakeClient(route_reply(intent, **slots)), log=lambda _m: None).route(text)

        got = route("Could you get the lamp going in the living room, on", owner.LIGHT,
                    outlet=Outlet.LIVING_ROOM_LAMP, on=True)
        self.assertEqual((got.intent, got.via), (owner.LIGHT, "bonsai"))
        # the Mac bug: a cut-off instruction became the summary
        got = route("If a delivery comes, ask them to", owner.SUMMARY)
        self.assertEqual((got.intent, got.cut_off, got.model_intent), (owner.UNCLEAR, True, owner.SUMMARY))
        # nothing in the words for it
        self.assertEqual(route("A piece of paper.", owner.ADD_INSTRUCTION).intent, owner.UNCLEAR)
        self.assertEqual(route("A piece of paper.", owner.SUMMARY).intent, owner.UNCLEAR)
        # clear words win over the model
        got = route("I'm heading out, see you later", owner.ARM_NIGHT)
        self.assertEqual((got.intent, got.via), (owner.ARM_AWAY, "code"))
        # the model's paraphrase is accepted when the words back it
        self.assertEqual(route("Anything going on at the door right now?", owner.LOOK).intent, owner.LOOK)

    def test_model_failure_falls_back_to_keywords(self):
        got = OwnerRouter(FakeClient(RuntimeError("down")), log=lambda _m: None).route("Who's at the door?")
        self.assertEqual((got.intent, got.via), (owner.LOOK, "rules"))
        got = OwnerRouter(FakeClient({"content": "hmm"}), log=lambda _m: None).route("I'm heading out")
        self.assertEqual(got.intent, owner.ARM_AWAY)

    def test_who_is_the_owner(self):
        self.assertTrue(owner.is_owner_speech(events.speech("hi", "owner"), Mode.AWAY))
        self.assertFalse(owner.is_owner_speech(events.speech("hi", "visitor"), Mode.HOME))
        self.assertTrue(owner.is_owner_speech(events.speech("hi"), Mode.HOME))      # typed, at home
        self.assertFalse(owner.is_owner_speech(events.speech("hi"), Mode.NIGHT))


class ThroughThePipeline(unittest.TestCase):
    def make(self, mode=Mode.HOME, brain_reply=None, router_reply=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = FakeClient(brain_reply or tool_calls(("speak", {"text": "Hello."})))
        router = OwnerRouter(FakeClient(router_reply) if router_reply else None, log=lambda _m: None)
        self.p = Pipeline(Brain(self.client, SECRET, log=lambda _m: None), ModeState(SECRET, mode=mode),
                          EventLog(Path(tmp.name) / "e.jsonl", SECRET), say=lambda _m: None,
                          rules=RuleStore(Path(tmp.name) / "rules.json"),
                          instructions=InstructionStore(Path(tmp.name) / "ins.json"), router=router)
        return self.p

    def owner(self, text):
        return self.p.handle(events.speech(text, "owner"))

    def test_owner_speech_is_never_a_visitor_message(self):
        for mode in (Mode.HOME, Mode.AWAY, Mode.NIGHT):
            self.make(mode, brain_reply=tool_calls(("save_message", {"text": "a piece of paper"})))
            r = self.owner("A piece of paper.")
            self.assertNotIn(ActionType.SAVE_MESSAGE, kinds(r.actions), mode)
            self.assertEqual(said(r.actions), [lines.SORRY], mode)
            self.assertEqual(self.client.calls, [], mode)     # never the visitor conversation

    def test_cut_off_instruction_asks_to_repeat(self):
        self.make(router_reply=route_reply(owner.SUMMARY))
        r = self.owner("If a delivery comes, ask them to")
        self.assertEqual(said(r.actions), [lines.REPEAT])
        self.assertNotIn(ActionType.SUMMARIZE_LOG, kinds(r.actions))
        self.assertEqual(self.p.instructions.all(), [])

    def test_armed_wake_requests_change_nothing(self):
        for mode in (Mode.AWAY, Mode.NIGHT):
            self.make(mode)
            for text in ("Turn on the porch light.", "I'm going to bed.", "Whenever the doorbell rings, "
                         "turn on the porch light.", "If a delivery comes, leave it by the side door."):
                r = self.owner(text)
                self.assertEqual(said(r.actions), [lines.ARMED_NO_CHANGE], (mode, text))
                self.assertEqual(kinds(r.actions), [ActionType.SPEAK], (mode, text))
            self.assertEqual(said(self.owner("Disarm please.").actions), [lines.SAY_PASSPHRASE])
            self.assertEqual(self.p.modes.mode, mode)
            self.assertEqual(self.p.rules.all() if hasattr(self.p.rules, "all") else [], [])

    def test_armed_owner_passphrase_still_disarms(self):
        self.make(Mode.AWAY)
        self.owner(f"Hey Faraday, {SECRET}")
        self.assertEqual(self.p.modes.mode, Mode.HOME)

    def test_who_is_at_the_door_at_night_but_not_away(self):
        self.make(Mode.NIGHT)
        r = self.owner("Who's at the door?")
        self.assertIn(ActionType.TAKE_PHOTO, kinds(r.actions))
        self.make(Mode.AWAY)
        self.assertEqual(said(self.owner("Who's at the door?").actions), [lines.ARMED_NO_CHANGE])

    def test_home_intents_run_in_plain_code(self):
        self.make()
        r = self.owner("Turn on the porch light.")
        self.assertEqual([(a.type, a.args) for a in r.actions if a.type != ActionType.SPEAK],
                         [(ActionType.OUTLET_ON, {"outlet": Outlet.PORCH_LIGHT})])
        self.assertEqual(said(r.actions), [lines.light_line(Outlet.PORCH_LIGHT, True)])
        r = self.owner("When there's motion at night, turn on the living room lamp.")
        self.assertIn(ActionType.ADD_RULE, kinds(r.actions))
        self.assertEqual(said(r.actions), [lines.RULE_SAVED])
        r = self.owner("If a delivery comes, ask them to leave it by the side door.")
        self.assertEqual(len(self.p.instructions.all()), 1)
        r = self.owner("What happened today?")
        self.assertIn(ActionType.SUMMARIZE_LOG, kinds(r.actions))
        r = self.owner("I'm heading out.")
        self.assertEqual(said(r.actions)[0], lines.AWAY_ON)
        self.assertEqual(self.p.modes.mode, Mode.AWAY)
        # none of this needed the visitor conversation (only the summary asks Bonsai, no tools)
        self.assertEqual([c for c in self.client.calls if c["tools"]], [])

    def test_visitor_at_home_gets_visitor_tools_only(self):
        self.make(brain_reply=tool_calls(("save_message", {"text": "Package for Sam"}),
                                         ("speak", {"text": "Thank you."})))
        r = self.p.handle(events.speech("Package for Sam, is anyone home?", "visitor"))
        offered = {t["function"]["name"] for t in self.client.calls[-1]["tools"]}
        self.assertEqual(offered, {"save_message", "speak"})
        self.assertIn(ActionType.SAVE_MESSAGE, kinds(r.actions))
        self.assertIn("A visitor is talking", self.client.calls[-1]["messages"][-1]["content"])

    def test_visitor_at_night_cannot_add_rules_or_lights(self):
        self.make(Mode.NIGHT, brain_reply=tool_calls(("outlet_off", {"outlet": "porch_light"})))
        r = self.p.handle(events.speech("Turn off the porch light.", "visitor"))
        self.assertNotIn(ActionType.OUTLET_OFF, kinds(r.actions))
        offered = {t["function"]["name"] for t in self.client.calls[-1]["tools"]}
        self.assertEqual(offered, {"save_message", "speak"})


if __name__ == "__main__":
    unittest.main()
