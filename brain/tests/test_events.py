"""Each fake event returns valid actions, from the model path and the fallback path."""
import io
import unittest
from contextlib import redirect_stdout

from shared import ActionType, ChangedBy, EventType, Mode, Source, validate_action, validate_event

from brain.core import SYSTEM_PROMPT
from brain.safety import ModeState
from brain.tools import allowed_actions
from sim import events
from sim.__main__ import Sim

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
            state.arm(Mode.AWAY, ChangedBy.DASHBOARD)]


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

    def test_expected_fallback_behaviour(self):
        brain = make_brain()
        self.assertEqual([a.type for a in brain.decide(events.doorbell(), Mode.AWAY).actions],
                         [ActionType.TAKE_PHOTO])
        night = [a.args for a in brain.decide(events.motion(), Mode.NIGHT).actions]
        self.assertIn({"outlet": "porch_light"}, night)
        self.assertEqual(brain.decide(events.motion(), Mode.HOME).actions, [])
        out = brain.decide(events.speech("Faraday, I'm heading out."), Mode.HOME).actions
        self.assertEqual(out[0].args, {"mode": "away"})
        msg = brain.decide(events.speech("Package for Sam."), Mode.AWAY).actions
        self.assertEqual(msg[0].type, ActionType.SAVE_MESSAGE)
        summary = brain.decide(events.speech("What happened while I was out?"), Mode.HOME).actions
        self.assertEqual(summary[0].type, ActionType.SUMMARIZE_LOG)

    def test_prompt_is_short(self):
        brain = make_brain()
        recent = [events.speech("x" * 300).to_dict() for _ in range(30)]
        prompt = brain.prompt(events.doorbell(), Mode.AWAY, recent)
        self.assertLess(len(SYSTEM_PROMPT), 400)
        self.assertEqual(prompt.count("\n- "), 10)
        self.assertLess(len(prompt), 1000)
        self.assertTrue(prompt.startswith("Mode: away (armed)"))


class SimRunsTheDemo(unittest.TestCase):
    def test_demo_script_offline(self):
        sim = Sim(make_brain(), ModeState(PASSPHRASE))
        script = ["t Faraday, I'm heading out.", "d", "t Package for Sam, by the side door.",
                  "3", "m", "t Faraday, disarm", "1 wrong words", "t What happened while I was out?",
                  f"t {PASSPHRASE}", "s", "x"]
        with redirect_stdout(io.StringIO()) as out:
            for line in script:
                self.assertTrue(sim.command(line))
            self.assertFalse(sim.command("q"))
        text = out.getvalue()
        self.assertEqual(sim.modes.mode, Mode.HOME)
        self.assertIn("I can't disarm", text)
        self.assertIn("wrong passphrase", text)
        self.assertNotIn(PASSPHRASE, text.replace(f"t {PASSPHRASE}", ""))
        for entry in sim.log:
            check = validate_event if entry["kind"] == "event" else validate_action
            self.assertEqual(check(entry), [], entry)


if __name__ == "__main__":
    unittest.main()
