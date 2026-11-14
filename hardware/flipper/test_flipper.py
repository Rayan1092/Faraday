"""Unit tests for the Flipper parser, burst merging and driver logic.

Uses the real captures in samples/. No hardware needed. From the repo root:

    python -m unittest hardware.flipper.test_flipper
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import List, Tuple

from shared import Action, Event, EventType, Source, validate_event
from hardware.flipper.bursts import BurstMerger
from hardware.flipper.device import Flipper
from hardware.flipper.parser import Packet, RxParser, normalise_key
from hardware.settings import CONFIG_DIR

SAMPLES = Path(__file__).resolve().parent / "samples"
DOORBELL = "0x00DEC608"
MOTION = "0x00C5AFAE"


def sample_lines(name: str) -> List[Tuple[float, str]]:
    out = []
    for raw in (SAMPLES / name).read_text(encoding="utf-8").splitlines():
        t, _, line = raw.partition("\t")
        out.append((float(t), line))
    return out


def parse(name: str) -> List[Packet]:
    p = RxParser()
    return [pk for t, line in sample_lines(name) if (pk := p.feed(line, t))]


def merge(packets: List[Packet], gap_s: float = 1.5):
    m = BurstMerger(gap_s)
    bursts = []
    for pk in packets:
        bursts += m.add(pk)
    return bursts + m.flush(float("inf"), force=True)


def settings():
    return json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))


class ParserOnRealSamples(unittest.TestCase):
    def test_doorbell_holds(self):
        packets = parse("doorbell_hold.txt")
        self.assertEqual(len(packets), 2)            # two ~1 s holds, quick tap not decoded
        for pk in packets:
            self.assertEqual((pk.protocol, pk.bits, pk.key, pk.te_us),
                             ("Princeton", 24, DOORBELL, 203))

    def test_motion_waves(self):
        packets = parse("motion_wave.txt")
        self.assertGreaterEqual(len(packets), 30)
        self.assertTrue(all(pk.key == MOTION and pk.protocol == "Princeton" and pk.bits == 24
                            for pk in packets))

    def test_ignores_noise_lines(self):
        p = RxParser()
        for line in ["subghz rx 433920000 0", "Load_keystore keeloq_mfcodes OK",
                     "Listening at frequency: 433919830 device: 0. Press CTRL+C to stop",
                     "Packets received 2", "", ">: ", "Yek:0x0010637B", "Te:203us  GT:Te*30"]:
            self.assertIsNone(p.feed(line, 0.0), line)

    def test_key_without_header_is_ignored(self):
        self.assertIsNone(RxParser().feed("Key:0x00DEC608", 0.0))

    def test_other_protocol_and_long_key(self):
        p = RxParser()
        p.feed("CAME 12bit", 1.0)
        pk = p.feed("Key:0x0000000000000ABC", 1.0)
        self.assertEqual((pk.protocol, pk.bits, pk.key), ("CAME", 12, "0x00000ABC"))

    def test_normalise_key(self):
        self.assertEqual(normalise_key("dec608"), DOORBELL)
        self.assertEqual(normalise_key("00C5AFAE"), MOTION)


class BurstsOnRealSamples(unittest.TestCase):
    def test_each_doorbell_hold_is_one_burst(self):
        bursts = merge(parse("doorbell_hold.txt"))
        self.assertEqual(len(bursts), 2)
        self.assertTrue(all(b.key == DOORBELL and b.packets == 1 for b in bursts))

    def test_each_wave_is_one_burst(self):
        bursts = merge(parse("motion_wave.txt"))
        self.assertEqual(len(bursts), 2)
        for b in bursts:
            self.assertEqual(b.key, MOTION)
            self.assertGreaterEqual(b.packets, 15)    # ~17 per trigger
            self.assertGreater(b.duration_ms, 1500)


class BurstMergerRules(unittest.TestCase):
    def pk(self, t, key=MOTION):
        return Packet("Princeton", 24, key, t)

    def test_gap_boundary(self):
        self.assertEqual(len(merge([self.pk(0), self.pk(1.4)])), 1)
        self.assertEqual(len(merge([self.pk(0), self.pk(1.6)])), 2)

    def test_keys_merge_separately(self):
        bursts = merge([self.pk(0, MOTION), self.pk(0.1, DOORBELL), self.pk(0.2, MOTION)])
        self.assertEqual(sorted((b.key, b.packets) for b in bursts),
                         sorted([(DOORBELL, 1), (MOTION, 2)]))

    def test_flush_only_after_gap(self):
        m = BurstMerger(1.5)
        m.add(self.pk(10.0))
        self.assertEqual(m.flush(11.0), [])
        done = m.flush(11.6)
        self.assertEqual((len(done), done[0].packets, done[0].duration_ms), (1, 1, 0))


class DriverLogic(unittest.TestCase):
    """Flipper object without starting its thread or opening the port."""

    def setUp(self):
        self.fl = Flipper(settings())

    def replay(self, name: str) -> List[Event]:
        for t, line in sample_lines(name):
            self.fl._feed(line, t)
            for b in self.fl._merger.flush(t):
                self.fl._emit(b)
        for b in self.fl._merger.flush(float("inf"), force=True):
            self.fl._emit(b)
        events = []
        while not self.fl.events.empty():
            events.append(self.fl.events.get())
        return events

    def test_doorbell_sample_gives_two_valid_events(self):
        events = self.replay("doorbell_hold.txt")
        self.assertEqual([e.type for e in events], ["doorbell", "doorbell"])
        for e in events:
            self.assertEqual(validate_event(e.to_dict()), [])
            self.assertEqual(e.source, Source.FLIPPER)
            self.assertEqual(e.data, {"key": DOORBELL, "protocol": "Princeton", "bits": 24,
                                      "packets": 1, "duration_ms": 0})

    def test_motion_sample_gives_two_valid_events(self):
        events = self.replay("motion_wave.txt")
        self.assertEqual([e.type for e in events], ["motion", "motion"])
        for e in events:
            self.assertEqual(validate_event(e.to_dict()), [])
            self.assertGreaterEqual(e.data["packets"], 15)

    def test_unknown_key_is_logged_not_emitted(self):
        with self.assertLogs("hardware.flipper", level="INFO") as logs:
            self.fl._feed("Princeton 24bit", 1.0)
            self.fl._feed("Key:0x00123456", 1.0)
            for b in self.fl._merger.flush(5.0):
                self.fl._emit(b)
        self.assertTrue(self.fl.events.empty())
        self.assertIn("unknown key 0x00123456", "\n".join(logs.output))

    def feed_packets(self, packets):
        for t, key in packets:
            self.fl._feed("Princeton 24bit", t)
            self.fl._feed(f"Key:{key}", t)
        for b in self.fl._merger.flush(float("inf"), force=True):
            self.fl._emit(b)
        events = []
        while not self.fl.events.empty():
            events.append(self.fl.events.get())
        return events

    def test_doorbell_other_button_matches_by_serial(self):
        # seen on the real doorbell at 15:20: button nibble 1 instead of 8
        events = self.feed_packets([(1.0, "0x00DEC601")])
        self.assertEqual([(e.type, e.data["key"]) for e in events], [("doorbell", "0x00DEC601")])
        self.assertEqual(validate_event(events[0].to_dict()), [])

    def test_mixed_buttons_in_one_press_make_one_event(self):
        events = self.feed_packets([(1.0, "0x00DEC608"), (1.3, "0x00DEC601")])
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].type, events[0].data["packets"]), ("doorbell", 2))

    def test_other_serial_is_still_unknown(self):
        with self.assertLogs("hardware.flipper", level="INFO"):
            self.assertEqual(self.feed_packets([(1.0, "0x00DEC708")]), [])

    def test_on_burst_sees_unknown_keys_for_learn_mode(self):
        seen = []
        self.fl.on_burst = lambda b, name: seen.append((b.key, name))
        with self.assertLogs("hardware.flipper", level="INFO"):
            self.feed_packets([(1.0, "0x00ABCDE1"), (5.0, "0x00C5AFAE")])
        self.assertEqual(seen, [("0x00ABCDE1", None), ("0x00C5AFAE", "motion")])

    def queued(self) -> List[str]:
        cmds = []
        while not self.fl._cmds.empty():
            cmds += [s[1] for s in self.fl._cmds.get().steps if s[0] == "cmd"]
        return cmds

    def test_execute_maps_outlets_to_files(self):
        self.fl.execute(Action("outlet_on", "brain", {"outlet": "porch_light"}))
        self.fl.execute(Action("outlet_off", "brain", {"outlet": "living_room_lamp"}))
        self.assertEqual(self.queued(), [
            "subghz tx_from_file /ext/subghz/outlet1_on.sub 10 0",
            "subghz tx_from_file /ext/subghz/outlet2_off.sub 10 0"])

    def test_execute_rejects_other_actions(self):
        with self.assertRaises(ValueError):
            self.fl.execute(Action("speak", "brain", {"text": "hi"}))
        with self.assertRaises(ValueError):
            self.fl.execute(Action("outlet_on", "brain", {"outlet": "garage"}))

    def mode(self, mode, prev="home"):
        return Event(EventType.MODE_CHANGED, "modes",
                     {"mode": mode, "previous": prev, "changed_by": "brain", "reason": ""})

    def alert(self, level, prev):
        return Event(EventType.ALERT_CHANGED, "modes",
                     {"level": level, "previous": prev, "reason": ""})

    def test_led_alert_beats_mode(self):
        self.assertEqual(self.fl.current_color(), "green")
        self.fl.apply_event(self.mode("away"))
        self.assertEqual(self.fl.current_color(), "blue")
        self.fl.apply_event(self.mode("night", "away"))
        self.assertEqual(self.fl.current_color(), "purple")
        self.fl.apply_event(self.alert("warning", "normal"))
        self.assertEqual(self.fl.current_color(), "yellow")
        self.fl.apply_event(self.mode("away", "night"))      # alert still wins
        self.assertEqual(self.fl.current_color(), "yellow")
        self.fl.apply_event(self.alert("alarm", "warning"))
        self.assertEqual(self.fl.current_color(), "red")
        self.fl.apply_event(self.alert("normal", "alarm"))
        self.assertEqual(self.fl.current_color(), "blue")

    def test_led_commands_and_alarm_vibrates(self):
        self.fl.apply_event(self.mode("night"))
        self.assertEqual(self.queued(), ["led r 140", "led g 0", "led b 255"])
        self.fl.apply_event(self.alert("warning", "normal"))
        # always all three channels, lowering ones first; no vibro on warning
        self.assertEqual(self.queued(), ["led b 0", "led r 255", "led g 140"])
        self.fl.apply_event(self.alert("alarm", "warning"))
        # red first, then three buzz pulses with red re-sent after every vibro
        red = ["led r 255", "led g 0", "led b 0"]
        self.assertEqual(self.queued(), ["led g 0", "led r 255", "led b 0"]
                         + (["vibro 1"] + red + ["vibro 0"] + red) * 3)

    def test_led_refresh_waits_for_quiet_radio(self):
        self.fl.set_color("green")
        self.queued()
        t = self.fl._led_at
        self.fl._maybe_refresh_led(t + 5)                   # too soon
        self.assertEqual(self.queued(), [])
        self.fl._feed("Princeton 24bit", t + 20)
        self.fl._feed("Key:0x00C5AFAE", t + 20)             # burst in progress
        self.fl._maybe_refresh_led(t + 20)
        self.assertEqual(self.queued(), [])
        self.fl._merger.flush(t + 30)
        self.fl._maybe_refresh_led(t + 30)                  # quiet again -> refresh
        self.assertEqual(self.queued(), ["led r 0", "led g 255", "led b 0"])

    def test_alarm_schedules_led_refresh_after_vibro(self):
        self.fl.apply_event(self.alert("warning", "normal"))
        self.fl.apply_event(self.alert("alarm", "warning"))
        self.queued()
        due = self.fl._refresh_due
        self.assertIsNotNone(due)
        self.fl._maybe_refresh_led(due - 0.5)
        self.assertEqual(self.queued(), [])
        self.fl._maybe_refresh_led(due)
        self.assertEqual(self.queued(), ["led r 255", "led g 0", "led b 0"])
        self.assertIsNone(self.fl._refresh_due)

    def test_same_colour_is_resent(self):
        self.fl.set_color("blue")
        self.queued()
        self.fl.set_color("blue")
        self.assertEqual(len(self.queued()), 3)

    def test_ignores_other_events(self):
        self.assertIsNone(self.fl.apply_event(
            Event(EventType.DOORBELL, "flipper", {"key": DOORBELL})))


class LearnMode(unittest.TestCase):
    def test_new_key_prints_exact_config_line(self):
        from hardware.flipper.bursts import Burst
        from hardware.flipper.learn import config_line, report
        b = Burst("Princeton", 24, "0x00ABCDE1", 0.0, 0.5, 3)
        line = config_line("doorbell", b)
        self.assertEqual(line, '"doorbell": {"protocol": "Princeton", "bits": 24, "key": "0x00ABCDE1"},')
        json.loads("{" + line.rstrip(",") + "}")               # pastes as valid JSON
        self.assertIn(line, report(b, None))
        self.assertIn("already configured as 'motion'", report(b, "motion"))

    def test_learned_line_works_as_setting(self):
        from hardware.flipper.bursts import Burst
        from hardware.flipper.learn import config_line
        s = settings()
        s["sensors"].update(json.loads("{" + config_line(
            "doorbell", Burst("Princeton", 24, "0x00ABCDE1", 0, 0, 1)).rstrip(",") + "}"))
        fl = Flipper(s)
        self.assertEqual(fl.sensor_for("0x00ABCDE1"), "doorbell")
        self.assertIsNone(fl.sensor_for("0x00DEC608"))         # old doorbell forgotten


if __name__ == "__main__":
    unittest.main()
