"""python3 -m hardware.check outlets (owner 2026-11-15: outlet 2 acted as a toggle)
and the vision check unloading the models it loaded, with a fake Flipper CLI.

    python -m unittest hardware.tests.test_outlets
"""
from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from hardware import check
from hardware.flipper.outlets import compare, parse_sub, raw_code, signal_of, verdict
from hardware.settings import load_settings


def key_file(key):
    return ["Size: 160", "Filetype: Flipper SubGhz Key File", "Version: 1", "Frequency: 433920000",
            "Preset: FuriHalSubGhzPresetOok650Async", "Protocol: Princeton", "Bit: 24",
            f"Key: 00 00 00 00 00 {key}", "TE: 400"]


def pt2262(bits, repeats=4, noise=True):
    """RAW_Data for a code sent `repeats` times, with junk before it."""
    out = [120, -90, 2000, -300, 60, -5000] if noise else []
    for _ in range(repeats):
        for b in bits:
            out += [1050, -350] if b == "1" else [350, -1050]
        out += [350, -10850]                             # sync: short high, long gap
    return out


CODE_ON = "101100111000101011110000"
CODE_OFF = "101100111000101011110011"


class Files(unittest.TestCase):
    def test_key_file(self):
        info = parse_sub(key_file("95 D5 D4"))
        self.assertEqual(signal_of(info), "Princeton 24bit 0x95D5D4")

    def test_raw_recordings_are_decoded_to_their_repeated_frame(self):
        self.assertEqual(raw_code(pt2262(CODE_ON)), CODE_ON)
        lines = ["Filetype: Flipper SubGhz RAW File", "Protocol: RAW",
                 "RAW_Data: " + " ".join(map(str, pt2262(CODE_OFF)[:40])),
                 "RAW_Data: " + " ".join(map(str, pt2262(CODE_OFF)[40:]))]
        self.assertEqual(signal_of(parse_sub(lines)), f"RAW 24 bits {int(CODE_OFF, 2):#x}")
        self.assertIsNone(raw_code([300, -900] * 5))           # too short to tell
        self.assertIsNone(raw_code(pt2262(CODE_ON, repeats=1)))  # one frame: nothing repeats

    def test_compare(self):
        on, off = parse_sub(key_file("95 D5 D1")), parse_sub(key_file("95 D5 D1"))
        self.assertEqual(compare(on, off)[0], "same")
        self.assertEqual(compare(on, parse_sub(key_file("95 D5 D4")))[0], "different")
        missing = parse_sub(["Storage error: file/dir not exist"])
        self.assertEqual(compare(on, missing)[0], "missing")
        raw_junk = parse_sub(["Protocol: RAW", "RAW_Data: 100 -100 100"])
        self.assertEqual(compare(on, raw_junk)[0], "unknown")


class Verdict(unittest.TestCase):
    # STEPS: off, off, on, on, off
    def test_works(self):
        self.assertEqual(verdict(True, [False, False, True, True, False])[0], "works")
        self.assertEqual(verdict(False, [False, False, True, True, False])[0], "works")

    def test_the_mac_test_a_toggle(self):
        self.assertEqual(verdict(False, [True, False, True, False, True])[0], "toggles")

    def test_swapped(self):
        self.assertEqual(verdict(False, [True, True, False, False, True])[0], "swapped")

    def test_deaf(self):
        self.assertEqual(verdict(False, [False] * 5)[0], "deaf")

    def test_only_on_toggles(self):
        kind, text = verdict(True, [False, False, True, False, False])
        self.assertEqual(kind, "toggles")
        self.assertIn("ON twice", text)

    def test_off_that_does_nothing_is_mixed(self):
        self.assertEqual(verdict(False, [False, False, True, True, True])[0], "mixed")

    def test_stopped_early(self):
        self.assertEqual(verdict(False, [True])[0], "incomplete")


class FakeCli:
    """The Flipper CLI: storage read answers from `files`; sends flip `lamp`
    the way the outlet would (toggle, or real on/off)."""

    def __init__(self, files, toggle=False):
        self.files, self.toggle, self.lamp, self.sent, self.closed = files, toggle, False, [], False

    def run(self, cmd, timeout=3.0):
        if cmd.startswith("storage read "):
            name = cmd.rsplit("/", 1)[1][:-4]
            return True, self.files.get(name, ["Storage error: file/dir not exist"])
        name = cmd.split("/ext/subghz/")[1].split(".sub")[0]
        self.sent.append(name)
        self.lamp = (not self.lamp) if self.toggle else name.endswith("_on")
        return True, []

    def close(self):
        self.closed = True


class OutletsCommand(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings()

    def files(self, outlet2_off="95 D5 D1"):
        return {"outlet1_on": key_file("11 11 11"), "outlet1_off": key_file("11 11 14"),
                "outlet2_on": key_file("95 D5 D1"), "outlet2_off": key_file(outlet2_off),
                "outlet3_on": key_file("33 33 31"), "outlet3_off": key_file("33 33 34")}

    def run_check(self, cli, arg=None):
        def ask(prompt):
            if prompt.startswith("Press Enter"):
                return ""
            return "y" if cli.lamp else "n"            # the owner looks at the lamp
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.outlets(self.settings, arg, ask=ask, conn=cli)
        return code, out.getvalue()

    def test_same_signal_and_toggling_gives_the_rerecord_steps(self):
        cli = FakeCli(self.files(), toggle=True)
        code, out = self.run_check(cli)
        self.assertEqual(code, 1)
        self.assertIn("SAME SIGNAL", out)
        self.assertEqual(cli.sent, ["outlet2_off", "outlet2_off", "outlet2_on", "outlet2_on", "outlet2_off"])
        self.assertIn("every send flips the lamp", out)
        self.assertIn("Re-record outlet 2", out)
        self.assertIn("outlet2_on_old", out)
        self.assertIn('"This name exists!"', out)
        self.assertTrue(cli.closed)

    def test_working_outlet(self):
        cli = FakeCli(self.files(outlet2_off="95 D5 D4"))
        code, out = self.run_check(cli, "living_room_lamp")
        self.assertEqual(code, 0)
        self.assertIn("different signals (good)", out)
        self.assertIn('"lights_off_at_start": true', out)
        self.assertIn("The lamp is OFF now.", out)

    def test_different_files_that_still_toggle_need_re_pairing(self):
        cli = FakeCli(self.files(outlet2_off="95 D5 D4"), toggle=True)
        _, out = self.run_check(cli, "2")
        self.assertIn("Re-pair it", out)
        self.assertNotIn("Re-record outlet 2", out)

    def test_compare_only_and_missing_files(self):
        files = self.files(outlet2_off="95 D5 D4")
        del files["outlet3_off"]
        cli = FakeCli(files)
        code, out = self.run_check(cli, "none")
        self.assertEqual(code, 1)
        self.assertIn("the OFF file is missing", out)
        self.assertEqual(cli.sent, [])                      # no sends without a guided test

    def test_q_stops_without_sending(self):
        cli = FakeCli(self.files())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.outlets(self.settings, "2", ask=lambda p: "q", conn=cli)
        self.assertEqual((code, cli.sent), (1, []))

    def test_unknown_outlet(self):
        code, out = self.run_check(FakeCli(self.files()), "7")
        self.assertEqual(code, 1)
        self.assertIn("No outlet '7'", out)


class UnloadAfterVisionCheck(unittest.TestCase):
    def test_only_models_the_check_loaded_are_unloaded(self):
        state = {"loaded": ["9b", "2b", "4b"]}
        unloaded = []

        def fake_unload(base, model):
            unloaded.append(model)
            state["loaded"].remove(model)
            return True
        lines = []
        with mock.patch.object(check, "_unload", side_effect=fake_unload), \
                mock.patch.object(check, "_loaded", side_effect=lambda base: list(state["loaded"])):
            check.unload_test_models("http://lm", ["9b", "4b"], before=["9b", "2b"], keep={"9b", "2b"},
                                     out=lines.append)
        self.assertEqual(unloaded, ["4b"])
        self.assertEqual(lines, ["Unloaded 4b: the check loaded it only to time it."])

    def test_still_loaded_says_how_to_eject(self):
        lines = []
        with mock.patch.object(check, "_unload", return_value=False), \
                mock.patch.object(check, "_loaded", return_value=["9b", "4b"]):
            check.unload_test_models("http://lm", ["4b"], before=[], keep={"9b"}, out=lines.append)
        self.assertIn("lms unload 4b", lines[0])

    def test_nothing_when_it_could_not_tell_what_was_loaded(self):
        with mock.patch.object(check, "_unload") as unload:
            check.unload_test_models("http://lm", ["4b"], before=None, keep=set(), out=print)
        unload.assert_not_called()

    def test_unload_tries_the_rest_api_then_lms(self):
        ok = mock.Mock(status_code=200)
        with mock.patch("requests.post", return_value=ok) as post:
            self.assertTrue(check._unload("http://lm", "4b"))
        self.assertEqual(post.call_args[0][0], "http://lm/api/v1/models/unload")
        self.assertEqual(post.call_args[1]["json"], {"instance_id": "4b"})
        done = mock.Mock(returncode=0)
        with mock.patch("requests.post", return_value=mock.Mock(status_code=404)), \
                mock.patch("shutil.which", return_value="/usr/local/bin/lms"), \
                mock.patch("subprocess.run", return_value=done) as run:
            self.assertTrue(check._unload("http://lm", "4b"))
        self.assertEqual(run.call_args[0][0], ["/usr/local/bin/lms", "unload", "4b"])


if __name__ == "__main__":
    unittest.main()
