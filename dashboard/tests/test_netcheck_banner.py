"""The real offline check and the big audience banners."""
import socket
import unittest
from datetime import datetime, timedelta

from shared import ActionType, ChangedBy, Mode, Source, make_action

from brain.fallback import CANT_DISARM
from dashboard.netcheck import NetCheck, route_address
from dashboard.state import build_state
from modes import ModeState
from sim import events


class NetCheckTests(unittest.TestCase):
    def test_no_route_means_offline(self):
        self.assertEqual(NetCheck(probe=lambda f, t: None).status()["offline"], True)

    def test_any_route_means_online(self):
        status = NetCheck(probe=lambda f, t: "10.0.0.5" if f == socket.AF_INET else None).status()
        self.assertFalse(status["offline"])
        self.assertNotIn("10.0.0.5", str(status))  # no local address on the projector

    def test_cached_between_polls(self):
        calls = []
        t = [0.0]
        check = NetCheck(ttl_s=3, probe=lambda f, tg: calls.append(1), clock=lambda: t[0])
        check.status()
        check.status()
        self.assertEqual(len(calls), 2)          # one call per probe (IPv4 + IPv6), once
        t[0] = 4
        check.status()
        self.assertEqual(len(calls), 4)

    def test_route_address_never_raises(self):
        # Whatever this machine's network is, it answers with an address or None.
        result = route_address(socket.AF_INET, "192.0.2.1")
        self.assertTrue(result is None or isinstance(result, str))

    def test_network_flows_into_state(self):
        s = build_state([], "home", "normal", network={"offline": False, "detail": "x"})
        self.assertFalse(s["offline"])
        self.assertEqual(s["network"]["detail"], "x")


class Banners(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now().astimezone()

    def at(self, item, seconds_ago):
        d = item.to_dict()
        d["timestamp"] = (self.now - timedelta(seconds=seconds_ago)).isoformat(timespec="milliseconds")
        return d

    def state(self, entries, mode, alert="normal"):
        return build_state(entries, mode, alert, now=self.now)["banner"]

    def test_alarm_beats_everything(self):
        refusal = make_action(ActionType.SPEAK, {"text": CANT_DISARM}, Source.BRAIN)
        b = self.state([self.at(refusal, 2)], Mode.AWAY, "alarm")
        self.assertEqual((b["kind"], b["title"]), ("alarm", "ALARM"))

    def test_refusal_then_passphrase(self):
        refusal = self.at(make_action(ActionType.SPEAK, {"text": CANT_DISARM}, Source.BRAIN), 5)
        b = self.state([refusal], Mode.NIGHT)
        self.assertEqual(b["title"], "THE AI CAN'T DISARM")
        disarm = ModeState("x y", mode=Mode.NIGHT).disarm_with_passphrase("x y")[0]
        b = self.state([refusal, self.at(disarm, 1)], Mode.HOME)
        self.assertEqual(b["title"], "DISARMED BY PASSPHRASE")
        self.assertIsNone(self.state([refusal, self.at(disarm, 30)], Mode.HOME))  # fades after 20 s

    def test_refusal_banner_fades(self):
        refusal = self.at(make_action(ActionType.SPEAK, {"text": CANT_DISARM}, Source.BRAIN), 90)
        self.assertIsNone(self.state([refusal], Mode.AWAY))

    def test_warning_and_night_doorbell(self):
        self.assertEqual(self.state([], Mode.NIGHT, "warning")["title"], "WARNING")
        night = ModeState("x").arm(Mode.NIGHT, ChangedBy.DASHBOARD)[0]
        b = self.state([self.at(night, 60), self.at(events.doorbell(), 30)], Mode.NIGHT)
        self.assertEqual(b["title"], "DOORBELL AT NIGHT")
        self.assertIsNone(self.state([self.at(night, 400), self.at(events.doorbell(), 300)], Mode.NIGHT))

    def test_doorbell_pressed_banner_per_mode(self):
        for mode, words in ((Mode.HOME, "Someone is at the door"),
                            (Mode.AWAY, "Faraday will answer"),
                            (Mode.NIGHT, "no sound")):
            entries = []
            if mode != Mode.HOME:
                entries.append(self.at(ModeState("x").arm(mode, ChangedBy.DASHBOARD)[0], 30))
            entries.append(self.at(events.doorbell(), 3))
            b = self.state(entries, mode)
            self.assertEqual(b["title"], "DOORBELL PRESSED", mode)
            self.assertIn(words, b["text"])
            self.assertIsNone(self.state([self.at(events.doorbell(), 20)], Mode.HOME))

    def test_alarm_beats_doorbell(self):
        b = self.state([self.at(events.doorbell(), 2)], Mode.AWAY, "alarm")
        self.assertEqual(b["title"], "ALARM")

    def test_quiet_home(self):
        self.assertIsNone(self.state([], Mode.HOME))


if __name__ == "__main__":
    unittest.main()
