"""Whole-flow tests with the fake Flipper: radio -> pipeline -> outlets + LED.

The fake only replaces the serial port, so the real driver code runs.
Offline brain (rules only), temp log and rules, no hardware.

    python -m unittest integration.tests.test_app
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from shared import Mode

from hardware.flipper.fake import FakeFlipper
from hardware.settings import CONFIG_DIR
from integration.app import App
from integration.standins import StandInCamera
from log import EventLog
from rules import RuleStore

GREEN, BLUE, PURPLE, YELLOW, RED = [0, 255, 0], [0, 0, 255], [140, 0, 255], [255, 140, 0], [255, 0, 0]


def wait_until(cond, timeout=8.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


class Flow(unittest.TestCase):
    dashboard = False

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="faraday-int-"))
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"]["burst_gap_s"] = 0.3       # fast tests
        s["flipper"]["led_refresh_s"] = 0       # deterministic LED commands
        self.settings = s
        self.said = []
        self.fl = FakeFlipper(s)
        self.app = App(s, self.fl, camera=StandInCamera(), use_llm=False, mode=Mode.HOME,
                       event_log=EventLog(self.tmp / "events.jsonl", s["security"]["passphrase"]),
                       rules=RuleStore(self.tmp / "rules.json"),
                       dashboard=self.dashboard, port=0, out=self.said.append)
        self.app.start()
        self.assertTrue(self.fl.connected.wait(5))

    def tearDown(self):
        self.app.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def log_types(self, kind=None):
        return [(e["kind"], e["type"]) for e in self.app.pipeline.log.recent(500)
                if kind is None or e["kind"] == kind]

    def entries(self, typ):
        return [e for e in self.app.pipeline.log.recent(500) if e["type"] == typ]

    def wave(self):
        self.fl.device.wave(packets=4, gap_s=0.05)

    def test_startup_shows_home_green(self):
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == GREEN))
        self.assertEqual(self.entries("mode_changed")[0]["data"]["changed_by"], "startup")

    def test_night_motion_warning_then_alarm_then_disarm(self):
        self.app.pipeline.owner_set_mode(Mode.NIGHT)
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == PURPLE))

        self.wave()                               # first motion -> warning
        self.assertTrue(wait_until(lambda: self.entries("motion")))
        m = self.entries("motion")[0]
        self.assertEqual((m["source"], m["data"]["packets"]), ("sim", 4))
        self.assertTrue(wait_until(lambda: "outlet1_on" in self.fl.device.tx))
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == YELLOW))
        self.assertTrue(wait_until(lambda: self.entries("photo_described")))   # stand-in camera

        self.wave()                               # second motion within 60 s -> alarm
        self.assertTrue(wait_until(lambda: self.app.pipeline.modes.alert == "alarm"))
        self.assertTrue(wait_until(lambda: "outlet2_on" in self.fl.device.tx))
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == RED and self.fl.device.vibro_count >= 3))
        self.assertTrue(self.fl.device.rx)        # listening again after all that

        self.assertIsNotNone(self.app.pipeline.owner_passphrase(
            self.settings["security"]["passphrase"]))
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == GREEN))

    def test_away_doorbell_takes_photo(self):
        self.app.pipeline.owner_set_mode(Mode.AWAY)
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == BLUE))
        self.fl.device.press_doorbell()
        self.assertTrue(wait_until(lambda: self.entries("doorbell")))
        self.assertTrue(wait_until(lambda: self.entries("take_photo")))
        self.assertTrue(wait_until(lambda: self.entries("photo_described")))
        photo = self.entries("photo_described")[0]
        self.assertEqual(photo["caused_by"], self.entries("take_photo")[0]["id"])
        self.assertTrue(wait_until(lambda: any(s.startswith("  SAY:") for s in self.said)))

    def test_home_motion_does_nothing_to_lights(self):
        self.wave()
        self.assertTrue(wait_until(lambda: self.entries("motion")))
        time.sleep(0.5)
        self.assertEqual(self.fl.device.tx, [])
        self.assertEqual(self.app.pipeline.modes.alert, "normal")

    def test_cable_drop_reconnects_and_keeps_working(self):
        self.app.pipeline.owner_set_mode(Mode.NIGHT)
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == PURPLE))
        self.fl.device.unplug()
        self.assertTrue(wait_until(lambda: not self.fl.connected.is_set()))
        self.fl.device.rgb = [0, 0, 0]            # a real Flipper loses the LED on replug
        self.fl.device.plug()
        self.assertTrue(wait_until(lambda: self.fl.connected.is_set()))
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == PURPLE))
        self.wave()
        self.assertTrue(wait_until(lambda: "outlet1_on" in self.fl.device.tx))

    def test_unknown_outlet_failure_is_reported_not_fatal(self):
        self.fl.device.files.discard("outlet1_on")   # file missing on the SD card
        self.app.pipeline.owner_set_mode(Mode.NIGHT)
        self.wave()
        self.assertTrue(wait_until(lambda: any("FAILED" in s for s in self.said)))
        self.assertTrue(wait_until(lambda: self.fl.device.rx))


class WithDashboard(Flow):
    dashboard = True

    def test_dashboard_shows_live_state(self):
        self.app.pipeline.owner_set_mode(Mode.NIGHT)
        self.wave()
        self.assertTrue(wait_until(lambda: self.app.pipeline.modes.alert == "warning"))
        with urllib.request.urlopen(self.app.board.url + "api/state", timeout=5) as r:
            state = json.loads(r.read())
        self.assertEqual((state["mode"], state["alert"], state["status"]["color"]),
                         ("night", "warning", "yellow"))
        self.assertTrue(any(l["id"] == "porch_light" and l["on"] for l in state["lights"]))

    # the base-class flow tests also run with the dashboard up
