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
    led_settle_s = 0.6

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="faraday-int-"))
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"]["burst_gap_s"] = 0.3       # fast tests
        s["flipper"]["led_refresh_s"] = 0       # deterministic LED commands
        s["flipper"]["led_settle_s"] = self.led_settle_s
        self.settings = s
        self.said = []
        # The fake models the firmware's charge indicator rewriting red/green
        # after every long rx pause (startup, outlet send, buzz).
        self.fl = FakeFlipper(s, charge_poll_s=0.2, tx_time_s=0.5)
        self.fl.device.charge_min_off_s = 0.3
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

    def listening(self):
        """True when the Flipper is in rx with nothing queued or scheduled. Radio
        sent during a pause (send, LED re-send) is lost, on the fake as on the device."""
        return (self.fl.device.rx and self.fl._cmds.empty() and self.fl._refresh_due is None)

    def wave(self):
        self.assertTrue(wait_until(self.listening))
        self.fl.device.wave(packets=4, gap_s=0.05)

    def test_startup_shows_home_green(self):
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == GREEN))
        self.assertEqual(self.entries("mode_changed")[0]["data"]["changed_by"], "startup")

    def test_led_holds_despite_charge_indicator(self):
        """Join 1 bug 1: the Flipper's charge indicator rewrites red/green about a
        second after listening restarts, so the LED was dark for most of the run."""
        time.sleep(1.5)                                   # startup: firmware rewrite, then settle
        self.assertGreaterEqual(self.fl.device.charge_writes, 1)   # the firmware did interfere
        self.assertEqual(self.fl.device.rgb, GREEN)
        self.app.pipeline.owner_set_mode(Mode.NIGHT)
        self.wave()                                       # warning + outlet send (long pause)
        self.assertTrue(wait_until(lambda: "outlet1_on" in self.fl.device.tx))
        writes = self.fl.device.charge_writes
        time.sleep(2.5)
        self.assertGreater(self.fl.device.charge_writes, writes)   # rewritten after the send
        self.assertEqual(self.fl.device.rgb, YELLOW)               # and put back
        self.assertTrue(self.fl.device.rx)

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
        self.assertTrue(wait_until(self.listening))   # listening again after all that
        self.assertEqual(self.fl.device.rgb, RED)     # still red after the firmware's rewrite
        # the baseline asked for porch light twice; it was only transmitted once
        self.assertEqual(self.fl.device.tx.count("outlet1_on"), 1)
        self.assertTrue(any("porch_light already on, not resent" in s for s in self.said))
        self.assertTrue(self.fl.device.outlet_lit[1] and self.fl.device.outlet_lit[2])

        self.assertIsNotNone(self.app.pipeline.owner_passphrase(
            self.settings["security"]["passphrase"]))
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == GREEN))

    def test_second_wave_after_61s_keeps_porch_light_on(self):
        """Join 1 bug: wave 2 came 61.7 s after wave 1 (no alarm, correct), the
        baseline asked for outlet_on porch_light again, the code was resent, and
        the toggling outlet turned the porch light OFF."""
        self.app.pipeline.owner_set_mode(Mode.NIGHT)
        self.wave()
        self.assertTrue(wait_until(lambda: self.fl.device.outlet_lit[1]))
        self.assertTrue(wait_until(lambda: self.app.pipeline.modes.alert == "warning"))
        self.app.pipeline.modes._last_motion -= 61.7          # replay the real timing...
        import integration.app as app_module                   # ...for the app's clock too

        class Later:                                            # monotonic() 61.7 s ahead
            def __getattr__(self, name):
                return getattr(time, name)

            @staticmethod
            def monotonic():
                return time.monotonic() + 61.7

        if hasattr(app_module, "time"):                        # old code timed its repeat skip
            self.addCleanup(setattr, app_module, "time", app_module.time)
            app_module.time = Later()
        self.wave()
        self.assertTrue(wait_until(lambda: len(self.entries("motion")) == 2))
        self.assertTrue(wait_until(lambda: len(self.entries("outlet_on")) == 2))
        time.sleep(0.5)
        self.assertEqual(self.app.pipeline.modes.alert, "warning")   # outside 60 s: no alarm
        self.assertEqual(self.fl.device.tx, ["outlet1_on"])          # not resent
        self.assertTrue(self.fl.device.outlet_lit[1])                # porch light still ON

    def test_outlet_off_then_on_is_sent_again(self):
        from shared import Action
        for kind in ("outlet_on", "outlet_off", "outlet_on"):
            self.app._on_action(Action(kind, "dashboard", {"outlet": "spare"}))
        self.assertTrue(wait_until(lambda: len(self.fl.device.tx) == 3))
        self.assertEqual(self.fl.device.tx, ["outlet3_on", "outlet3_off", "outlet3_on"])

    def test_failed_send_is_retried_next_time(self):
        from shared import Action
        self.fl.device.files.discard("outlet3_on")
        self.app._on_action(Action("outlet_on", "dashboard", {"outlet": "spare"}))
        self.assertTrue(wait_until(lambda: any("FAILED" in s for s in self.said)))
        self.fl.device.files.add("outlet3_on")
        self.app._on_action(Action("outlet_on", "dashboard", {"outlet": "spare"}))
        self.assertTrue(wait_until(lambda: self.fl.device.tx == ["outlet3_on"]))

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


class WithoutLedSettle(unittest.TestCase):
    """The same firmware behaviour with the fix switched off: shows what the bug
    looked like, and that the fake really models it."""

    def test_home_green_is_wiped_at_startup(self):
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0, led_settle_s=0)
        fl = FakeFlipper(s, charge_poll_s=0.2)
        fl.device.charge_min_off_s = 0.3
        fl.start()
        try:
            self.assertTrue(fl.connected.wait(5))
            self.assertTrue(wait_until(lambda: fl.device.charge_writes >= 1))
            time.sleep(1.0)
            self.assertEqual(fl.device.rgb, [0, 0, 0])    # Home green wiped: LED dark
        finally:
            fl.stop()


class RealCameraPath(unittest.TestCase):
    """take_photo -> PhotoCamera (saved file + fake LM Studio) -> pipeline -> dashboard photo."""

    def test_photo_reaches_log_and_dashboard(self):
        from hardware.camera import PhotoCamera
        from hardware.tests.test_camera import FakeLMStudio, SavedFileCamera, make_scene
        from hardware.vision import Vision
        from hardware.settings import REPO_ROOT
        tmp = Path(tempfile.mkdtemp(prefix="faraday-cam-"))
        photos = Path(tempfile.mkdtemp(prefix="test-", dir=REPO_ROOT / "captures"))
        lm = FakeLMStudio("Someone holds a box at the door.")
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"]["burst_gap_s"], s["flipper"]["led_refresh_s"] = 0.3, 0
        fl = FakeFlipper(s)
        cam = PhotoCamera(SavedFileCamera(make_scene(photos / "door.jpg")), Vision(lm.url, "bonsai-test"))
        app = App(s, fl, camera=cam, use_llm=False, mode=Mode.AWAY,
                  event_log=EventLog(tmp / "events.jsonl", s["security"]["passphrase"]),
                  rules=RuleStore(tmp / "rules.json"), dashboard=True, port=0, out=lambda *_: None)
        try:
            app.start()
            self.assertTrue(fl.connected.wait(5))
            fl.device.press_doorbell()
            self.assertTrue(wait_until(lambda: any(
                e["type"] == "photo_described" for e in app.pipeline.log.recent(100))))
            with urllib.request.urlopen(app.board.url + "api/state", timeout=5) as r:
                photo = json.loads(r.read())["photo"]
            self.assertEqual(photo["description"], "Someone holds a box at the door.")
            with urllib.request.urlopen(app.board.url.rstrip("/") + photo["url"], timeout=5) as r:
                self.assertEqual(r.headers["Content-Type"], "image/jpeg")
        finally:
            app.stop()
            lm.close()
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(photos, ignore_errors=True)


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
