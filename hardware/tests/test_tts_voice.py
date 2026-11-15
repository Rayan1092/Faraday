"""Argmax TTS voice consistency, cache and pre-render, with a fake argmax-cli.

    python -m unittest hardware.tests.test_tts_voice
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from hardware.audio import MicGate
from hardware.settings import CONFIG_DIR, REPO_ROOT
from hardware.speech import ArgmaxTTS

FAKE_CLI = r'''
import json, os, sys, time, wave
args = sys.argv[1:]
with open(os.environ["FAKE_ARGMAX_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
if os.environ.get("FAKE_ARGMAX_OLD") and any(a in args for a in ("--seed", "--output-path")):
    print("Error: Unknown option '--seed'", file=sys.stderr)
    sys.exit(64)
def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default
if "--play" in args:
    time.sleep(0.2)
out, fmt = opt("--output-path"), opt("--output-format", "m4a")
if out:
    path = out if out.endswith("." + fmt) else out + "." + fmt
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"\x01\x00" * 2400)
    print(path)
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-tts-v-"))
        self.cli = self.dir / "fake_argmax.py"
        self.cli.write_text(FAKE_CLI, encoding="utf-8")
        self.log = self.dir / "calls.jsonl"
        os.environ["FAKE_ARGMAX_LOG"] = str(self.log)
        os.environ.pop("FAKE_ARGMAX_OLD", None)
        self.played = []

    def tearDown(self):
        os.environ.pop("FAKE_ARGMAX_LOG", None)
        os.environ.pop("FAKE_ARGMAX_OLD", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def tts(self, voice=None, cache=True, gate=None):
        cmd = [sys.executable, str(self.cli), "tts", "--text", "{text}", "--speaker", "aiden", "--play"]
        return ArgmaxTTS(cmd, gate=gate, player=lambda p, d: self.played.append(Path(p)),
                         voice=voice if voice is not None else
                         {"seed": 7, "temperature": 0.6, "top_k": 50, "model": "0.6b", "instruction": None},
                         cache_dir=(self.dir / "cache") if cache else None)


class Voice(Base):
    def test_fixed_seed_lower_temperature_and_no_output_m4a(self):
        before = set(Path.cwd().iterdir())
        tts = self.tts()
        self.assertTrue(tts.say("Night mode on. Sleep well."))
        args = self.calls()[0]
        for flag, value in (("--seed", "7"), ("--temperature", "0.6"), ("--top-k", "50"), ("--model", "0.6b"),
                            ("--output-format", "wav")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("--play", args)
        self.assertNotIn("--instruction", args)                     # 0.6B ignores it
        self.assertTrue(args[args.index("--output-path") + 1].startswith(tempfile.gettempdir()[:3]))
        self.assertEqual(set(Path.cwd().iterdir()), before)        # no ./output.m4a
        self.assertEqual(self.played, [])                          # --play spoke it

    def test_repeat_line_plays_the_cached_take(self):
        tts = self.tts()
        tts.say("Away mode on. I'll watch the door.")
        tts.say("Away  mode on. I'll watch the door.")             # same words, other spacing
        self.assertEqual(len(self.calls()), 1)                     # argmax-cli ran once
        self.assertEqual(len(self.played), 1)
        self.assertEqual(self.played[0], tts.cached("Away mode on. I'll watch the door."))
        self.assertEqual(tts.cache_hits, 1)

    def test_other_voice_settings_do_not_reuse_the_cache(self):
        a = self.tts()
        b = self.tts(voice={"seed": 42, "temperature": 0.6})
        self.assertNotEqual(a.cache_path("Hello."), b.cache_path("Hello."))

    def test_instruction_only_with_the_1_7b_model(self):
        tts = self.tts(voice={"seed": 7, "temperature": 0.6, "model": "1.7b",
                              "instruction": "Speak calmly and warmly"})
        tts.say("Hello.")
        args = self.calls()[0]
        self.assertEqual(args[args.index("--model") + 1], "1.7b")
        self.assertEqual(args[args.index("--instruction") + 1], "Speak calmly and warmly")

    def test_old_cli_without_the_flags_still_speaks(self):
        os.environ["FAKE_ARGMAX_OLD"] = "1"
        gate = MicGate()
        tts = self.tts(gate=gate)
        with self.assertLogs("hardware.speech", "WARNING"):
            self.assertTrue(tts.say("Hello there."))
        first, second = self.calls()
        self.assertIn("--seed", first)
        self.assertNotIn("--seed", second)
        self.assertFalse(tts._extras_ok)
        self.assertFalse(gate.muted)

    def test_mic_muted_while_a_cached_take_plays(self):
        gate, seen = MicGate(), []
        tts = self.tts(gate=gate)
        tts.say("Hello.")
        tts.player = lambda p, d: seen.append(gate.muted)
        tts.say("Hello.")
        self.assertEqual(seen, [True])
        self.assertFalse(gate.muted)


class Prerender(Base):
    def test_renders_missing_lines_without_playing_and_waits_for_speech(self):
        tts = self.tts()
        tts.say("Let me look.")                                   # already cached
        busy = threading.Event()
        busy.set()
        result = []
        t = threading.Thread(target=lambda: result.append(
            tts.prerender(["Let me look.", "One moment, please.", "Let me check."], wait=busy.is_set)))
        t.start()
        time.sleep(0.4)
        self.assertEqual(len(self.calls()), 1)                     # waiting: Faraday is talking
        busy.clear()
        t.join(20)
        self.assertEqual(result, [2])
        for args in self.calls()[1:]:
            self.assertNotIn("--play", args)                        # rendered silently
        self.assertIsNotNone(tts.cached("Let me check."))
        self.played.clear()
        tts.say("One moment, please.")
        self.assertEqual(len(self.played), 1)                      # instant, from the cache

    def test_stop_ends_the_prerender(self):
        tts = self.tts()
        stop = threading.Event()
        stop.set()
        self.assertEqual(tts.prerender(["A line.", "Another line."], stop=stop), 0)


class Settings(unittest.TestCase):
    def test_example_mac_config(self):
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        tts = ArgmaxTTS.from_config(s["tts"]["mac"])
        self.assertEqual((tts.voice["seed"], tts.voice["temperature"], tts.voice["model"]), (7, 0.6, "0.6b"))
        self.assertEqual(tts.cache_dir, REPO_ROOT / "cache" / "tts")
        self.assertIn("--play", tts.command)
        self.assertTrue(s["tts"]["mac"]["prerender"])


class FixedLines(unittest.TestCase):
    def test_dells_fixed_lines_are_found(self):
        from integration.fixed_lines import fixed_lines, lines_in
        lines = fixed_lines()
        for want in ("Night mode on. Sleep well.", "Alarm. You are being recorded. Leave now.",
                     "I can't see the door right now.", "Welcome home. Faraday is disarmed."):
            self.assertIn(want, lines)
        self.assertNotIn("Owner asked who's at the door", lines)           # a log reason
        self.assertFalse(any("15:09" in line for line in lines))            # bench data
        src = ('lower_name = "Not upper."\nGREETING = ("Hello. Two "\n            "parts.")\n'
               'LOOK_REASON = "Owner asked."\nFIXED_LINES = ["One more line.", "x"]\n'
               'def f():\n    return _speak("Literal line here.")\n'
               'TEMPLATE_LINE = "Hi {name}."\n')
        self.assertEqual(lines_in(src), ["Hello. Two parts.", "One more line.", "Literal line here."])


if __name__ == "__main__":
    unittest.main()
