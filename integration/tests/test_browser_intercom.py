"""Intercom end to end in a real browser: the reference page's JavaScript (mic ->
16 kHz int16 -> POST /api/intercom) against the controls server.

Uses Chrome/Edge headless with a FAKE microphone (Chrome's built-in test tone,
--use-fake-device-for-media-stream), so no real mic and no speaker are touched;
the server plays into a fake output stream. Opt-in, because it needs a browser:

    set FARADAY_BROWSER=1 && python -m unittest integration.tests.test_browser_intercom
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from hardware.audio import MicGate
from hardware.intercom import Intercom
from hardware.tests.test_media import FakeOutput, wait_until
from integration.controls import ControlsServer

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def browser():
    for c in CANDIDATES:
        if Path(c).exists():
            return c
    return shutil.which("google-chrome") or shutil.which("chromium")


@unittest.skipUnless(os.environ.get("FARADAY_BROWSER") and browser(), "set FARADAY_BROWSER=1 (needs Chrome/Edge)")
class BrowserIntercom(unittest.TestCase):
    def test_page_streams_fake_mic_to_the_speaker(self):
        gate = MicGate()
        ic = Intercom(gate, stream_factory=FakeOutput, hold_s=0.5)
        srv = ControlsServer("127.0.0.1", 0, intercom=ic).start()
        profile = Path(tempfile.mkdtemp(prefix="faraday-chrome-"))
        proc = subprocess.Popen([
            browser(), "--headless=new", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile}", "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required",
            "--disable-gpu", f"{srv.url}?autotalk=2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(wait_until(lambda: ic.sessions >= 1, 20), "no audio reached the server")
            self.assertTrue(gate.muted)                          # door mic muted while talking
            self.assertTrue(wait_until(lambda: ic.sessions >= 1 and not ic.active, 20))
            self.assertFalse(gate.muted)                         # and back after release
        finally:
            proc.kill()
            proc.wait(10)
            srv.stop()
            shutil.rmtree(profile, ignore_errors=True)
        played = np.concatenate(FakeOutput.last.played)
        heard = np.flatnonzero(np.abs(played) > 500)
        print(f"\nbrowser sent {ic.played} samples (~{ic.played / 16000:.1f} s at 16 kHz), "
              f"rate {FakeOutput.last.rate}, peak {int(np.abs(played).max())}")
        self.assertEqual(FakeOutput.last.rate, 16000)
        self.assertGreater(ic.played, 16000 * 1.0)               # at least ~1 s of the 2 s hold
        # Chrome's fake mic plays short beeps, not a steady tone: check they came through
        self.assertGreater(len(heard), 1000)
        self.assertGreater(int(np.abs(played).max()), 5000)


if __name__ == "__main__":
    unittest.main()
