"""The speaker choice (audio.speaker_device), with a fake sounddevice: output
devices only, the system output followed on the Mac, and the startup warning.

    python -m unittest hardware.tests.test_speaker
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from hardware import audio
from hardware.audio import (MicGate, default_output_from_profiler, match_output, play_chime, play_wav,
                            resolve_speaker, speaker_report)
from hardware.intercom import Intercom

# What the Mac listed on 2026-11-15: an input-only "onn Rugged Speaker S" next to the speaker
MAC_DEVICES = [
    {"name": "onn USB Microphone", "max_input_channels": 1, "max_output_channels": 0},
    {"name": "onn Rugged Speaker S", "max_input_channels": 1, "max_output_channels": 0},
    {"name": "Mac mini Speakers", "max_input_channels": 0, "max_output_channels": 2},
    {"name": "onn Rugged Speaker", "max_input_channels": 0, "max_output_channels": 2},
]


def fake_sounddevice(devices=MAC_DEVICES, fail_on=None):
    sd = types.SimpleNamespace(played=[])

    def query_devices(device=None, kind=None):
        if kind == "output" and device is None:
            return devices[2]
        return devices

    def play(data, rate, device=None):
        if fail_on is not None and device == fail_on:
            raise RuntimeError("device unavailable")
        sd.played.append(device)

    sd.query_devices = query_devices
    sd.play = play
    sd.wait = lambda: None
    return sd


class OutputDevices(unittest.TestCase):
    def setUp(self):
        self.sd = fake_sounddevice()
        patcher = mock.patch.dict(sys.modules, {"sounddevice": self.sd})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_devices_that_can_play_are_listed(self):
        self.assertEqual(audio.output_devices(), [(2, "Mac mini Speakers"), (3, "onn Rugged Speaker")])

    def test_a_name_picks_the_output_never_the_input_only_twin(self):
        self.assertEqual(resolve_speaker("onn Rugged Speaker"), 3)
        self.assertEqual(resolve_speaker("onn rugged speaker"), 3)            # any case
        self.assertEqual(resolve_speaker("Rugged"), 3)                        # part of the name
        with self.assertLogs("hardware.audio", "WARNING"):                    # it can't play:
            self.assertIsNone(resolve_speaker("onn Rugged Speaker S"))        # never chosen

    def test_numbers(self):
        self.assertEqual(resolve_speaker(3), 3)
        self.assertEqual(resolve_speaker("2"), 2)
        with self.assertLogs("hardware.audio", "WARNING"):
            self.assertIsNone(resolve_speaker(1))                             # input-only: the default

    def test_unknown_name_falls_back_to_the_system_default_with_a_warning(self):
        with self.assertLogs("hardware.audio", "WARNING") as logs:
            self.assertIsNone(resolve_speaker("JBL Flip"))
        self.assertIn("system default", logs.output[0])

    def test_unset_is_the_system_default(self):
        for setting in (None, "", "  "):
            self.assertIsNone(resolve_speaker(setting))


class Matching(unittest.TestCase):
    def test_exact_name_beats_a_longer_output_that_contains_it(self):
        devices = [(0, "onn Rugged Speaker S"), (4, "onn Rugged Speaker")]
        self.assertEqual(match_output("onn Rugged Speaker", devices), 4)

    def test_part_of_a_name_picks_the_shortest_match(self):
        devices = [(0, "onn Rugged Speaker Pro Max"), (4, "onn Rugged Speaker 2")]
        self.assertEqual(match_output("rugged", devices), 4)

    def test_bad_settings_raise(self):
        for bad in ("Nope", 9, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                match_output(bad, [(1, "Mac mini Speakers")])


class Report(unittest.TestCase):
    DEVICES = [(2, "Mac mini Speakers"), (3, "onn Rugged Speaker")]

    def report(self, setting, system="onn Rugged Speaker", mac=True):
        return speaker_report(setting, devices=self.DEVICES, system=system, default_name="Speakers",
                              mac=mac)

    def test_unset_follows_the_system_output(self):
        self.assertEqual(self.report(None),
                         [("OK", "speaker: onn Rugged Speaker (the system output, followed live like "
                                 "argmax-cli --play)")])

    def test_same_as_the_system_output_is_ok(self):
        self.assertEqual([lvl for lvl, _ in self.report("onn Rugged Speaker")], ["OK"])

    def test_warns_when_the_app_speaker_is_not_the_system_output(self):
        lines = self.report("Mac mini Speakers")
        self.assertEqual([lvl for lvl, _ in lines], ["OK", "WARN"])
        self.assertIn('"Mac mini Speakers"', lines[1][1])
        self.assertIn('"onn Rugged Speaker"', lines[1][1])
        self.assertIn("argmax-cli --play", lines[1][1])

    def test_unknown_speaker_warns_and_lists_the_outputs(self):
        (level, msg), = self.report("JBL Flip")
        self.assertEqual(level, "WARN")
        self.assertIn('"onn Rugged Speaker"', msg)
        self.assertIn("system output instead", msg)

    def test_unreadable_system_output_on_the_mac_warns(self):
        lines = self.report("onn Rugged Speaker", system=None)
        self.assertEqual([lvl for lvl, _ in lines], ["OK", "WARN"])
        with mock.patch.object(audio, "system_output", return_value=None):
            lines = speaker_report("onn Rugged Speaker", devices=self.DEVICES, mac=True)
        self.assertEqual([lvl for lvl, _ in lines], ["OK", "WARN"])

    def test_other_systems_name_the_default_output(self):
        self.assertEqual(self.report(None, system=None, mac=False),
                         [("OK", "speaker: Speakers (the system default output)")])

    def test_profiler_json(self):
        data = {"SPAudioDataType": [{"_name": "coreaudio_device", "_items": [
            {"_name": "onn Rugged Speaker S", "coreaudio_default_audio_input_device": "spaudio_yes"},
            {"_name": "Mac mini Speakers", "coreaudio_device_output": 2},
            {"_name": "onn Rugged Speaker", "coreaudio_default_audio_output_device": "spaudio_yes",
             "coreaudio_device_output": 2}]}]}
        self.assertEqual(default_output_from_profiler(data), "onn Rugged Speaker")
        self.assertIsNone(default_output_from_profiler({}))

    def test_not_a_mac_has_no_system_output(self):
        with mock.patch.object(audio, "_is_mac", return_value=False):
            self.assertIsNone(audio.system_output())


class Playback(unittest.TestCase):
    def setUp(self):
        import soundfile as sf
        self.dir = Path(tempfile.mkdtemp(prefix="faraday-spk-"))
        self.wav = self.dir / "line.wav"
        sf.write(str(self.wav), np.zeros(2400, dtype=np.float32), 24000)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_mac_default_plays_with_afplay(self):
        sd = fake_sounddevice()
        with mock.patch.dict(sys.modules, {"sounddevice": sd}), \
                mock.patch.object(audio, "_is_mac", return_value=True), \
                mock.patch.object(audio, "_afplay", return_value=True) as afplay:
            play_wav(self.wav)
        afplay.assert_called_once_with(self.wav)
        self.assertEqual(sd.played, [])

    def test_named_device_plays_through_sounddevice(self):
        sd = fake_sounddevice()
        with mock.patch.dict(sys.modules, {"sounddevice": sd}), \
                mock.patch.object(audio, "_is_mac", return_value=True), \
                mock.patch.object(audio, "_afplay", return_value=True) as afplay:
            play_wav(self.wav, 3)
        self.assertEqual(sd.played, [3])
        afplay.assert_not_called()

    def test_a_failing_device_falls_back_to_the_default(self):
        sd = fake_sounddevice(fail_on=3)
        with mock.patch.dict(sys.modules, {"sounddevice": sd}), \
                mock.patch.object(audio, "_is_mac", return_value=False), \
                self.assertLogs("hardware.audio", "WARNING"):
            play_wav(self.wav, 3)
        self.assertEqual(sd.played, [None])

    def test_afplay_failure_falls_back_to_sounddevice(self):
        sd = fake_sounddevice()
        with mock.patch.dict(sys.modules, {"sounddevice": sd}), \
                mock.patch.object(audio, "_is_mac", return_value=True), \
                mock.patch.object(audio, "_afplay", return_value=False):
            play_wav(self.wav)
        self.assertEqual(sd.played, [None])

    def test_afplay_command(self):
        done = subprocess.CompletedProcess(["afplay"], 0, b"", b"")
        with mock.patch.object(audio.shutil, "which", return_value="/usr/bin/afplay"), \
                mock.patch.object(audio.subprocess, "run", return_value=done) as run:
            self.assertTrue(audio._afplay(self.wav))
        self.assertEqual(run.call_args[0][0], ["/usr/bin/afplay", str(self.wav)])
        with mock.patch.object(audio.shutil, "which", return_value=None):
            self.assertFalse(audio._afplay(self.wav))
        bad = subprocess.CompletedProcess(["afplay"], 1, b"", b"Error")
        with mock.patch.object(audio.shutil, "which", return_value="/usr/bin/afplay"), \
                mock.patch.object(audio.subprocess, "run", return_value=bad), \
                self.assertLogs("hardware.audio", "WARNING"):
            self.assertFalse(audio._afplay(self.wav))
        # a timeout means it was playing: never played a second time; a refused start falls back
        with mock.patch.object(audio.shutil, "which", return_value="/usr/bin/afplay"), \
                mock.patch.object(audio.subprocess, "run",
                                  side_effect=subprocess.TimeoutExpired("afplay", 10)), \
                self.assertLogs("hardware.audio", "WARNING"):
            self.assertTrue(audio._afplay(self.wav))
        with mock.patch.object(audio.shutil, "which", return_value="/usr/bin/afplay"), \
                mock.patch.object(audio.subprocess, "run", side_effect=OSError("no")), \
                self.assertLogs("hardware.audio", "WARNING"):
            self.assertFalse(audio._afplay(self.wav))

    def test_chime_on_the_mac_default_uses_afplay_with_the_mic_muted(self):
        gate = MicGate()
        seen = []
        with mock.patch.object(audio, "_is_mac", return_value=True), \
                mock.patch.object(audio, "_afplay", side_effect=lambda p: seen.append(gate.muted) or True):
            play_chime(gate)
        self.assertEqual(seen, [True])
        self.assertFalse(gate.muted)


class IntercomSpeaker(unittest.TestCase):
    def opened_with(self, device, mac, system_index=5, broken=()):
        got = []

        class Stream:
            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        def factory(**kw):
            got.append(kw["device"])
            if kw["device"] in broken:
                raise RuntimeError("PortAudio: invalid device")
            return Stream()
        ic = Intercom(MicGate(), device, stream_factory=factory)
        with mock.patch.object(audio, "_is_mac", return_value=mac), \
                mock.patch.object(audio, "system_output_index", return_value=system_index):
            ic.feed(b"\x01\x00" * 4000, 16000)
        ic.stop()
        return got

    def test_mac_default_follows_the_system_output_each_session(self):
        self.assertEqual(self.opened_with(None, mac=True), [5])

    def test_a_chosen_speaker_is_kept(self):
        self.assertEqual(self.opened_with(3, mac=True), [3])

    def test_elsewhere_the_default_is_portaudio_s(self):
        self.assertEqual(self.opened_with(None, mac=False), [None])

    def test_a_speaker_that_went_away_falls_back_to_the_default(self):
        """Review: a Bluetooth speaker that slept gets a new id; the old one won't open."""
        with self.assertLogs("hardware.intercom", "WARNING"):
            self.assertEqual(self.opened_with(3, mac=True, broken=(3,)), [3, None])


if __name__ == "__main__":
    unittest.main()
