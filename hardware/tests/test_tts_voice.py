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
old = os.environ.get("FAKE_ARGMAX_OLD", "")
refused = {"voice": ("--seed", "--temperature", "--top-k", "--model"),
           "all": ("--seed", "--temperature", "--top-k", "--model", "--output-path", "--output-format")}
for flag in refused.get(old, ()):
    if flag in args:
        print(f"Error: Unknown option '{flag}'", file=sys.stderr)
        sys.exit(64)
def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default
if opt("--model") not in (None, "0.6b", "1.7b"):
    print(f"Error: The value '{opt('--model')}' is invalid for '--model <model>'", file=sys.stderr)
    sys.exit(64)
time.sleep(float(os.environ.get("FAKE_ARGMAX_SLOW", "0")))
if "--play" in args:
    time.sleep(0.2)
out, fmt = opt("--output-path"), opt("--output-format", "m4a")
if out:
    # like AudioOutput.saveAudio: drop any "extension", then add the format's
    folder, name = os.path.split(out)
    path = os.path.join(folder, os.path.splitext(name)[0] + "." + fmt)
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

    def test_cli_without_voice_flags_still_speaks_and_caches(self):
        os.environ["FAKE_ARGMAX_OLD"] = "voice"
        gate = MicGate()
        tts = self.tts(gate=gate)
        with self.assertLogs("hardware.speech", "WARNING"):
            self.assertTrue(tts.say("Hello there."))
        first, second = self.calls()
        self.assertIn("--seed", first)
        self.assertNotIn("--seed", second)
        self.assertIn("--output-path", second)                  # caching kept
        self.assertFalse(tts._voice_ok)
        self.assertIsNotNone(tts.cached("Hello there."))
        self.assertFalse(gate.muted)

    def test_cli_without_any_new_flags_speaks_and_skips_prerender(self):
        os.environ["FAKE_ARGMAX_OLD"] = "all"
        tts = self.tts()
        with self.assertLogs("hardware.speech", "WARNING"):
            self.assertTrue(tts.say("Hello there."))
        self.assertNotIn("--output-path", self.calls()[-1])
        self.assertFalse(tts.can_cache)
        n = len(self.calls())
        self.assertEqual(tts.prerender(["A line.", "Another line."]), 0)
        self.assertEqual(len(self.calls()), n)                  # no pointless runs

    def test_model_value_is_lower_cased(self):
        tts = self.tts(voice={"seed": 7, "model": "1.7B", "instruction": "Speak calmly and warmly"})
        self.assertTrue(tts.say("Hello."))                      # the CLI rejects "1.7B"
        args = self.calls()[0]
        self.assertEqual(args[args.index("--model") + 1], "1.7b")
        self.assertIn("--instruction", args)

    def test_broken_cached_take_falls_back_to_the_cli(self):
        tts = self.tts()
        tts.say("Hello.")
        def broken(path, device):
            raise RuntimeError("device gone")
        tts.player = broken
        with self.assertLogs("hardware.speech", "WARNING"):
            self.assertTrue(tts.say("Hello."))
        self.assertEqual(len(self.calls()), 2)                  # synthesized again

    def test_cache_hit_marks_the_take_recently_used(self):
        tts = self.tts()
        tts.say("Hello.")
        hit = tts.cached("Hello.")
        os.utime(hit, (1, 1))
        tts.say("Hello.")
        self.assertGreater(hit.stat().st_mtime, 1000)

    def test_mic_muted_while_a_cached_take_plays(self):
        gate, seen = MicGate(), []
        tts = self.tts(gate=gate)
        tts.say("Hello.")
        tts.player = lambda p, d: seen.append(gate.muted)
        tts.say("Hello.")
        self.assertEqual(seen, [True])
        self.assertFalse(gate.muted)


class SayAs(Base):
    """Owner 2026-11-15: the voice said "pass phase" for "passphrase"."""

    def test_passphrase_is_said_as_two_words(self):
        from hardware.speech import spoken
        for text, want in (("Please say the passphrase.", "Please say the pass phrase."),
                           ("Passphrase accepted.", "Pass phrase accepted."),
                           ("Two pass-phrases", "Two pass phrases"),
                           ("PASSPHRASES", "Pass phrases"),
                           ("Night mode is on. Good night.", "Night mode is on. Good night.")):
            self.assertEqual(spoken(text), want)

    def test_the_cli_and_the_cache_get_the_spoken_form(self):
        tts = self.tts()
        self.assertTrue(tts.say("Say the passphrase."))
        args = self.calls()[0]
        self.assertEqual(args[args.index("--text") + 1], "Say the pass phrase.")
        self.assertEqual(tts.cache_path("Say the passphrase."), tts.cache_path("Say the pass phrase."))
        self.assertIsNotNone(tts.cached("Say the passphrase."))       # pre-render finds it too


class RenderCommand(Base):
    """python3 -m integration.fixed_lines --render (owner 2026-11-15: re-render the
    cache after Dell's calm lines)."""

    def test_renders_what_is_missing_then_nothing(self):
        import contextlib
        import io
        from unittest import mock
        from integration import fixed_lines as fl
        cmd = [sys.executable, str(self.cli), "tts", "--text", "{text}", "--speaker", "aiden", "--play"]
        cfg = {"kind": "argmax", "command": cmd, "cache": True, "cache_dir": str(self.dir / "cache"),
               "voice": {"seed": 7}}
        settings = {"tts": {"mac": cfg, "windows": cfg}, "audio": {}}
        lines = ["Night mode is on. Good night.", "The system is already disarmed."]
        out = io.StringIO()
        with mock.patch("hardware.settings.load_settings", return_value=settings),                 mock.patch.object(fl, "fixed_lines", return_value=lines), contextlib.redirect_stdout(out):
            self.assertEqual(fl.main(["--render"]), 0)
            self.assertEqual(fl.main(["--render"]), 0)               # second time: nothing to do
        self.assertEqual(len(list((self.dir / "cache").glob("*.wav"))), 2)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("2 of 2 are cached", out.getvalue())
        (self.dir / "cache" / "old-line.wav").write_bytes(b"RIFF" + b"0" * 100)   # a stale take
        with mock.patch("hardware.settings.load_settings", return_value=settings), \
                mock.patch.object(fl, "fixed_lines", return_value=lines), contextlib.redirect_stdout(out):
            self.assertEqual(fl.main(["--render", "--fresh"]), 0)
        self.assertFalse((self.dir / "cache" / "old-line.wav").exists())
        self.assertEqual(len(list((self.dir / "cache").glob("*.wav"))), 2)      # both again
        self.assertEqual(len(self.calls()), 4)


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

    def test_live_line_stops_a_running_prerender_which_retries_later(self):
        os.environ["FAKE_ARGMAX_SLOW"] = "1.5"
        try:
            tts = self.tts()
            result = []
            t = threading.Thread(target=lambda: result.append(tts.prerender(["Let me look."])))
            t.start()
            from hardware.tests.test_media import wait_until
            self.assertTrue(wait_until(lambda: tts._render_proc is not None, 5))
            os.environ["FAKE_ARGMAX_SLOW"] = "0"
            t0 = time.monotonic()
            self.assertTrue(tts.say("Someone is at the door."))  # live: the render is stopped
            self.assertLess(time.monotonic() - t0, 1.5)
            t.join(20)
            self.assertEqual(result, [1])                       # and made afterwards
            self.assertIsNotNone(tts.cached("Let me look."))
        finally:
            os.environ.pop("FAKE_ARGMAX_SLOW", None)

    def test_stop_ends_the_prerender(self):
        tts = self.tts()
        stop = threading.Event()
        stop.set()
        self.assertEqual(tts.prerender(["A line.", "Another line."], stop=stop), 0)


class WarmUp(Base):
    """Owner 2026-11-15: most lines are written live by Bonsai and spoken with
    --play, so the voice is loaded once at startup."""

    def test_runs_the_cli_once_silently_and_keeps_nothing(self):
        tts = self.tts()
        secs = tts.warm_up()
        self.assertIsNotNone(secs)
        (args,) = self.calls()
        self.assertNotIn("--play", args)                          # nothing is heard
        self.assertEqual(args[args.index("--text") + 1], ArgmaxTTS.WARM_TEXT)
        self.assertIsNone(tts.cached(ArgmaxTTS.WARM_TEXT))        # and nothing is cached
        self.assertEqual(self.played, [])

    def test_waits_while_faraday_talks_and_stop_ends_it(self):
        tts = self.tts()
        busy, stop, result = threading.Event(), threading.Event(), []
        busy.set()
        t = threading.Thread(target=lambda: result.append(tts.warm_up(wait=busy.is_set, stop=stop)))
        t.start()
        time.sleep(0.4)
        self.assertEqual(self.calls(), [])                        # waiting for Faraday
        stop.set()
        t.join(5)
        self.assertEqual(result, [None])
        self.assertEqual(self.calls(), [])

    def test_a_failing_cli_gives_none(self):
        tts = ArgmaxTTS([sys.executable, "-c", "import sys; sys.exit(3)", "{text}"],
                        cache_dir=self.dir / "cache")
        with self.assertLogs("hardware.speech", "WARNING"):
            self.assertIsNone(tts.warm_up())


class Audition(Base):
    def test_takes_are_found_although_the_cli_drops_dotted_names(self):
        """Review of #44: "take01-0.6b-s7-t0.3" was saved as "...-t0.wav" and every take
        was reported FAILED."""
        from hardware import tts_audition
        cmd = [sys.executable, str(self.cli), "tts", "--text", "{text}", "--speaker", "aiden"]
        take = {"take": 3, "model": "0.6b", "seed": 7, "temperature": 0.3}
        out = self.dir / "aud"
        out.mkdir()
        r = tts_audition.render(cmd, "Hello.", take, out)
        self.assertTrue(r["ok"], r)
        self.assertTrue(Path(r["wav"]).exists())
        self.assertIn('"voice": {"seed": 7, "temperature": 0.3', tts_audition.settings_lines(take))


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
        # Dell's final lines (owner 2026-11-15: one calm tone, brain/lines.py FIXED_LINES)
        for want in ("Night mode is on. Good night.",
                     "This property is protected. You are being recorded and the owner has been notified. "
                     "Please leave now.",
                     "I can't see the door right now.", "Welcome home. The system is disarmed."):
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
