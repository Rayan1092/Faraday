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

from hardware.flipper.device import LONG_PAUSE_S
from hardware.flipper.fake import FakeFlipper
from hardware.settings import CONFIG_DIR
from integration.app import App
from integration.standins import StandInCamera
from log import EventLog
from rules import RuleStore

RED, OFF = [255, 0, 0], [0, 0, 0]
LEFTOVER = [0, 0, 255]      # a colour an earlier run left on the LED


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
        # The fake notices an rx pause only from LONG_PAUSE_S: exactly the pauses
        # after which the driver re-sends red. With 0.3 here, an LED re-send slowed
        # to 0.3-0.6 s by a busy machine was noticed but (by design) not re-sent,
        # so the reconnect test failed about 1 run in 13 in the full suite.
        self.fl.device.charge_min_off_s = LONG_PAUSE_S
        self.fl.device.rgb = list(LEFTOVER)
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

    def led_cmds(self):
        return [c for c in self.fl.device.commands if c.startswith("led ")]

    def test_startup_turns_leftover_colour_off(self):
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == OFF))
        self.assertEqual(self.entries("mode_changed")[0]["data"]["changed_by"], "startup")

    def test_modes_and_warning_send_no_led_commands(self):
        """Decided 2026-11-14: the LED is off in every mode and at warning. The
        firmware's charge indicator owns red/green while listening, so mode
        colours were unreadable; the dashboard shows the mode instead."""
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == OFF))
        self.assertTrue(wait_until(self.listening))
        sent = len(self.led_cmds())
        for mode in (Mode.AWAY, Mode.NIGHT):
            self.app.pipeline.owner_set_mode(mode)
        self.wave()                                       # Night warning + outlet send
        self.assertTrue(wait_until(lambda: self.app.pipeline.modes.alert == "warning"))
        self.assertTrue(wait_until(lambda: "outlet1_on" in self.fl.device.tx))
        time.sleep(2.5)                                   # long pause passed, firmware rewrote
        self.assertGreaterEqual(self.fl.device.charge_writes, 1)
        self.assertEqual(len(self.led_cmds()), sent)      # not one LED command for all that
        self.assertEqual(self.fl.device.rgb, OFF)
        self.assertTrue(self.fl.device.rx)

    def test_night_motion_warning_then_alarm_then_disarm(self):
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == OFF))
        self.app.pipeline.owner_set_mode(Mode.NIGHT)

        self.wave()                               # first motion -> warning
        self.assertTrue(wait_until(lambda: self.entries("motion")))
        m = self.entries("motion")[0]
        self.assertEqual((m["source"], m["data"]["packets"]), ("sim", 4))
        self.assertTrue(wait_until(lambda: "outlet1_on" in self.fl.device.tx))
        self.assertTrue(wait_until(lambda: self.entries("photo_described")))   # stand-in camera
        self.assertEqual(self.fl.device.rgb, OFF)                 # warning: still off

        self.wave()                               # second motion within 60 s -> alarm
        self.assertTrue(wait_until(lambda: self.app.pipeline.modes.alert == "alarm"))
        self.assertTrue(wait_until(lambda: "outlet2_on" in self.fl.device.tx))
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == RED and self.fl.device.vibro_count >= 3))
        writes = self.fl.device.charge_writes
        self.assertTrue(wait_until(self.listening))   # listening again after all that
        self.assertGreater(self.fl.device.charge_writes, writes)   # the firmware did rewrite...
        self.assertEqual(self.fl.device.rgb, RED)                  # ...and red was put back
        # the baseline asked for porch light twice; it was only transmitted once
        self.assertEqual(self.fl.device.tx.count("outlet1_on"), 1)
        self.assertTrue(any("porch_light already on, not resent" in s for s in self.said))
        self.assertTrue(self.fl.device.outlet_lit[1] and self.fl.device.outlet_lit[2])

        self.assertIsNotNone(self.app.pipeline.owner_passphrase(
            self.settings["security"]["passphrase"]))
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == OFF))     # alarm over: off
        self.assertEqual(self.app.flipper.current_color(), "off")

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
        self.assertTrue(wait_until(lambda: self.app.pipeline.modes.mode == Mode.AWAY))
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
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == OFF))
        self.app.pipeline.owner_set_mode(Mode.NIGHT)
        self.fl.device.unplug()
        self.assertTrue(wait_until(lambda: not self.fl.connected.is_set()))
        self.fl.device.rgb = list(LEFTOVER)       # a replugged Flipper may show anything
        self.fl.device.plug()
        self.assertTrue(wait_until(lambda: self.fl.connected.is_set()))
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == OFF))
        self.wave()
        self.assertTrue(wait_until(lambda: "outlet1_on" in self.fl.device.tx))

    def test_reconnect_during_alarm_restores_red(self):
        self.app.pipeline.owner_set_mode(Mode.NIGHT)
        self.wave()
        self.assertTrue(wait_until(lambda: self.app.pipeline.modes.alert == "warning"))
        self.wave()                                   # a second burst, not a longer first one
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == RED and self.fl.device.vibro_count >= 3))
        self.assertTrue(wait_until(self.listening))
        self.fl.device.unplug()
        self.assertTrue(wait_until(lambda: not self.fl.connected.is_set()))
        self.fl.device.rgb = list(OFF)
        self.fl.device.plug()
        self.assertTrue(wait_until(lambda: self.fl.connected.is_set()))
        self.assertTrue(wait_until(lambda: self.fl.device.rgb == RED))
        time.sleep(2.5)                               # firmware rewrite after the (re)connect
        self.assertEqual(self.fl.device.rgb, RED)     # and red put back again

    def test_unknown_outlet_failure_is_reported_not_fatal(self):
        self.fl.device.files.discard("outlet1_on")   # file missing on the SD card
        self.app.pipeline.owner_set_mode(Mode.NIGHT)
        self.wave()
        self.assertTrue(wait_until(lambda: any("FAILED" in s for s in self.said)))
        self.assertTrue(wait_until(lambda: self.fl.device.rx))


class BusyMachine(unittest.TestCase):
    """Reconnect during an alarm when every `led` command is slow, as on a loaded
    machine. This used to leave the LED off about 1 full-suite run in 13: the
    reconnect's own red batch kept rx paused, so the scheduled re-send came
    before the firmware's rewrite and the rewrite then wiped it."""

    def test_reconnect_during_alarm_restores_red_with_slow_led(self):
        from shared import Event
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0, led_settle_s=Flow.led_settle_s)
        fl = FakeFlipper(s, charge_poll_s=0.2)
        fl.device.charge_min_off_s = LONG_PAUSE_S
        fl.start()
        try:
            self.assertTrue(fl.connected.wait(5))
            fl.apply_event(Event("alert_changed", "modes",
                                 {"level": "alarm", "previous": "warning", "reason": ""}))
            self.assertTrue(wait_until(lambda: fl.device.vibro_count >= 3 and fl.device.rx
                                       and fl._refresh_due is None and fl._cmds.empty()))
            for led_time_s in (0.12, 0.18):
                with self.subTest(led_time_s=led_time_s):
                    fl.device.led_time_s = led_time_s
                    fl.device.unplug()
                    self.assertTrue(wait_until(lambda: not fl.connected.is_set()))
                    fl.device.rgb = list(OFF)
                    fl.device.plug()
                    self.assertTrue(wait_until(lambda: fl.connected.is_set()))
                    # settled: listening, nothing queued or scheduled, firmware has rewritten
                    self.assertTrue(wait_until(lambda: fl.device.rx and fl._cmds.empty()
                                               and fl._refresh_due is None
                                               and fl.device.charge_state == "not_charging"))
                    time.sleep(1.0)
                    self.assertEqual(fl.device.rgb, RED)
        finally:
            fl.stop()


class WithoutLedSettle(unittest.TestCase):
    """The firmware's charge indicator with the red re-send switched off: shows
    why it is still needed for the alarm, and that the fake really models it."""

    def test_alarm_red_is_wiped_without_the_resend(self):
        from shared import Event
        s = json.loads((CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8"))
        s["flipper"].update(burst_gap_s=0.3, led_refresh_s=0, led_settle_s=0)
        fl = FakeFlipper(s, charge_poll_s=0.2)
        fl.device.charge_min_off_s = 0.3
        fl.start()
        try:
            self.assertTrue(fl.connected.wait(5))
            fl.apply_event(Event("alert_changed", "modes",
                                 {"level": "alarm", "previous": "warning", "reason": ""}))
            self.assertTrue(wait_until(lambda: fl.device.vibro_count >= 3 and fl.device.rx))
            writes = fl.device.charge_writes
            self.assertTrue(wait_until(lambda: fl.device.charge_writes > writes, timeout=3))
            self.assertEqual(fl.device.rgb[0], 0)         # red wiped by the firmware
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
