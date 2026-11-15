"""Prompt profiles: small for 2B/4B, 9b for the Mac mini, picked from the model id."""
import copy
import unittest

from brain import Brain
from brain.prompts import PROFILES, pick
from brain.settings import load_settings
from sim import events

from .helpers import FakeClient, tool_calls


class Profiles(unittest.TestCase):
    def test_pick_from_model_id(self):
        self.assertEqual(pick("bonsai-2-9b"), "9b")
        self.assertEqual(pick("ternary-bonsai-2-9B@q4"), "9b")
        self.assertEqual(pick("ternary-bonsai-2-4b@q2_0"), "small")
        self.assertEqual(pick("bonsai-2-19b"), "small")      # not fooled by 19b
        self.assertEqual(pick("bonsai-2-4b", "9b"), "9b")     # explicit setting wins
        with self.assertRaises(ValueError):
            pick("x", "huge")

    def test_from_settings_switches_with_the_model(self):
        settings = copy.deepcopy(load_settings())
        llm = settings["llm"]
        llm["profiles"]["test"] = {"base_url": "http://127.0.0.1:9", "model": "bonsai-2-9b"}
        llm["profile"] = "test"
        brain = Brain.from_settings(settings, log=lambda _m: None)
        self.assertEqual(brain.prompt_profile, "9b")
        self.assertEqual(brain.recent_lines, 12)
        llm["prompt_profile"] = "small"
        self.assertEqual(Brain.from_settings(settings, log=lambda _m: None).prompt_profile, "small")

    def test_profile_is_what_bonsai_sees(self):
        client = FakeClient(tool_calls(("speak", {"text": "Hi."})))
        brain = Brain(client, "secret words", log=lambda _m: None)
        brain.use_prompts("9b")
        brain.decide(events.speech("Hello, is anyone home?", "visitor"), "away")
        self.assertEqual(client.calls[0]["messages"][0]["content"], PROFILES["9b"]["system"])

    def test_every_profile_has_every_prompt_and_stays_short(self):
        for name, profile in PROFILES.items():
            for key in ("system", "confront", "summary"):
                self.assertIn(key, profile, name)
                self.assertLess(len(profile[key]), 700, (name, key))
            self.assertIn("never disarm", profile["system"], name)


if __name__ == "__main__":
    unittest.main()
