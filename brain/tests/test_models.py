"""Right-sized models: fast Bonsai for quick decisions and the threat label, smart for the rest."""
import copy
import json
import unittest
from pathlib import Path

from shared import Mode

from brain import Brain
from brain.bonsai import BonsaiClient
from brain.prompts import PROFILES
from brain.settings import REPO_ROOT, llm_config, load_settings
from brain.threat import ThreatAssessor
from sim import events

from .helpers import FakeClient, tool_calls


def settings_with(fast, smart="ternary-bonsai-2-9b-mlx"):
    s = copy.deepcopy(load_settings())
    s["llm"]["profiles"]["t"] = {"base_url": "http://127.0.0.1:9", "model": smart, "fast_model": fast}
    s["llm"]["profile"] = "t"
    return s


class Settings(unittest.TestCase):
    def test_example_has_separate_fast_and_smart_models(self):
        example = json.loads((REPO_ROOT / "config" / "settings.example.json").read_text(encoding="utf-8"))
        mac = example["llm"]["profiles"]["mac"]
        self.assertEqual((mac["model"], mac["fast_model"]),
                         ("ternary-bonsai-2-9b-mlx", "ternary-bonsai-2-2b-mlx"))
        nv = example["llm"]["profiles"]["nv_backup"]
        self.assertEqual(nv["fast_model"], nv["model"])          # the 4B stands in for both

    def test_llm_config_reads_fast_model(self):
        cfg = llm_config(settings_with("ternary-bonsai-2-2b-mlx"))
        self.assertEqual((cfg["model"], cfg["fast_model"]),
                         ("ternary-bonsai-2-9b-mlx", "ternary-bonsai-2-2b-mlx"))

    def test_brain_and_assessor_get_the_right_clients(self):
        s = settings_with("ternary-bonsai-2-2b-mlx")
        brain = Brain.from_settings(s, log=lambda _m: None)
        self.assertEqual(brain.client.model, "ternary-bonsai-2-9b-mlx")
        self.assertEqual(brain.fast_client.model, "ternary-bonsai-2-2b-mlx")
        self.assertEqual(brain.prompt_profile, "9b")
        assessor = ThreatAssessor.from_settings(s, log=lambda _m: None)
        self.assertEqual(assessor.client.model, "ternary-bonsai-2-2b-mlx")

    def test_same_model_means_one_client(self):
        brain = Brain.from_settings(settings_with("ternary-bonsai-2-9b-mlx"), log=lambda _m: None)
        self.assertIs(brain.fast_client, brain.client)


class Routing(unittest.TestCase):
    def brain(self):
        smart = FakeClient(tool_calls(("speak", {"text": "smart answer"})))
        fast = FakeClient(tool_calls(("speak", {"text": "fast answer"})))
        b = Brain(smart, "pass phrase", log=lambda _m: None)
        b.fast_client, b.fast_model, b.smart_model = fast, "ternary-bonsai-2-2b-mlx", "ternary-bonsai-2-9b-mlx"
        b.use_prompts("9b")
        return b, smart, fast

    def test_owner_commands_at_home_use_the_fast_model(self):
        b, smart, fast = self.brain()
        d = b.decide(events.speech("Turn on the porch light."), Mode.HOME)
        self.assertEqual((d.role, len(fast.calls), len(smart.calls)), ("fast", 1, 0))
        # the fast model gets the short "small" prompt, not the 9B one
        self.assertEqual(fast.calls[0]["messages"][0]["content"], PROFILES["small"]["system"])

    def test_visitors_get_the_smart_model(self):
        b, smart, fast = self.brain()
        for event, mode in ((events.speech("Package for Sam."), Mode.AWAY),
                            (events.speech("Hello?"), Mode.NIGHT),
                            (events.speech("Hello, is anyone home?", "visitor"), Mode.HOME)):
            d = b.decide(event, mode)
            self.assertEqual(d.role, "smart", (event.type, mode))
        self.assertEqual(len(fast.calls), 0)
        self.assertEqual(smart.calls[0]["messages"][0]["content"], PROFILES["9b"]["system"])

    def test_the_summary_uses_the_smart_model(self):
        b, smart, fast = self.brain()
        smart.reply = {"role": "assistant", "content": "The doorbell rang once."}
        b.summarize(["The doorbell rang once."])
        self.assertEqual((len(smart.calls), len(fast.calls)), (1, 0))
        self.assertFalse(hasattr(b, "confront"))       # no confrontation line any more (owner, Mac test)

    def test_bench_can_force_one_model(self):
        b, smart, fast = self.brain()
        b.force_role = "smart"
        self.assertEqual(b.decide(events.speech("Turn on the lamp."), Mode.HOME).role, "smart")
        b.force_role = "fast"
        self.assertEqual(b.decide(events.speech("Package."), Mode.AWAY).role, "fast")


if __name__ == "__main__":
    unittest.main()
