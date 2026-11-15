"""Runs integration/start_mac.sh in dry-run mode against fake LM Studio / Argmax.

Needs a POSIX bash (Git Bash on Windows, the system bash on the Mac); skipped
otherwise. Nothing is started.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "integration" / "start_mac.sh"


def find_bash():
    for cand in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if cand and Path(cand).exists() and "system32" not in cand.lower():   # not WSL's
            return cand
    return None


BASH = find_bash()

# A stand-in for `argmax-cli serve`: answers HTTP on the given port after 1 s, then
# quits by itself after 25 s (so a test never leaves it running).
FAKE_SERVE = r'''
import sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()
time.sleep(1.0)
srv = HTTPServer(("127.0.0.1", int(sys.argv[1])), H)
threading.Timer(25.0, srv.shutdown).start()
srv.serve_forever()
'''


class Server:
    """Fake LM Studio: /v1/models lists every downloaded model (like the real one),
    /api/v0/models adds each model's state. loaded=None means all are loaded."""

    def __init__(self, models=("bonsai-test",), loaded=None, v0=True):
        outer = self
        self.models = list(models)
        self.loaded = list(models) if loaded is None else list(loaded)
        self.v0 = v0

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/v1/models":
                    raw = json.dumps({"data": [{"id": m} for m in outer.models]}).encode()
                    code = 200
                elif self.path == "/api/v0/models" and outer.v0:
                    raw = json.dumps({"data": [
                        {"id": m, "state": "loaded" if m in outer.loaded else "not-loaded"}
                        for m in outer.models]}).encode()
                    code = 200
                else:
                    raw, code = b'{"error":"not found"}', 404
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Bash32(unittest.TestCase):
    """Owner 2026-11-15: macOS /bin/bash 3.2 failed with "unexpected EOF while looking
    for matching `''": it reads an apostrophe in a heredoc inside $( ) (there: "isn't" in
    a comment) as an open quote. Git Bash here is 5.x and can't show it, so check the text."""

    def test_no_apostrophes_in_heredocs_inside_command_substitutions(self):
        lines = SCRIPT.read_text(encoding="utf-8").splitlines()
        found, inside, start = [], False, 0
        for n, line in enumerate(lines, 1):
            if inside:
                if line == "EOF":
                    inside = False
                elif "'" in line:
                    found.append(f"line {n}: {line.strip()}")
            elif "$(" in line and "<<'EOF'" in line:
                inside, start = True, n
        self.assertFalse(inside, f"heredoc from line {start} never ends")
        self.assertEqual(found, [], "apostrophes in a heredoc inside $( ) break macOS bash 3.2")


@unittest.skipUnless(BASH, "needs bash")
class StartMac(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="faraday-mac-"))
        self.lm, self.argmax = Server(), Server(models=())
        s = json.loads((ROOT / "config" / "settings.example.json").read_text(encoding="utf-8"))
        s["llm"]["profiles"]["mac"] = {"base_url": self.lm.base, "model": "bonsai-test"}
        s["stt"]["mac"]["url"] = self.argmax.base + "/v1/audio/transcriptions"
        # a TTS binary that exists here (the product needs one: a missing one is a FAIL)
        s["tts"]["mac"]["command"] = [Path(sys.executable).as_posix(), "tts", "--text", "{text}"]
        self.settings = s

    def tearDown(self):
        self.lm.close()
        self.argmax.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, settings=None, args=("--no-browser",), **env):
        path = self.tmp / "settings.json"
        if settings is not None:
            path.write_text(json.dumps(settings), encoding="utf-8")
        e = {**os.environ, "PYTHON": Path(sys.executable).as_posix(),
             "FARADAY_SETTINGS": path.as_posix(), "FARADAY_DRY_RUN": "1",
             "FARADAY_DEVICE_CHECKS": "0",          # never probe this laptop's mic/camera
             "FARADAY_USE_LMS": "0",                # never drive this laptop's real LM Studio
             "FARADAY_START_STT": "0",              # never start a real argmax-cli serve
             "FARADAY_WAIT_LM_S": "1", "FARADAY_WAIT_STT_S": "3", **env}
        p = subprocess.run([BASH, SCRIPT.as_posix(), *args], capture_output=True, text=True,
                           env=e, timeout=90, cwd=ROOT)
        return p.returncode, p.stdout + p.stderr

    def test_all_up_passes_and_would_start(self):
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 0, out)
        self.assertIn("has bonsai-test downloaded", out)
        self.assertIn("bonsai-test is loaded", out)
        self.assertNotIn("other models are loaded", out)
        self.assertIn("Argmax STT answering", out)
        self.assertIn("would start", out)
        self.assertIn("-m integration --fresh --no-browser", out)     # --fresh by default
        self.assertIn("READY", out)
        self.assertIn("passphrase is still the public example", out)
        self.assertIn("object detection model present", out)          # from git pull
        self.assertRegex(out, r"\[ OK \]\S*\s+OpenCV \d")              # 4.7+ for detection

    def test_lm_studio_down_fails(self):
        self.settings["llm"]["profiles"]["mac"]["base_url"] = "http://127.0.0.1:9"
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 1, out)
        self.assertIn("LM Studio not answering", out)

    def test_model_missing_fails(self):
        self.settings["llm"]["profiles"]["mac"]["model"] = "bonsai-2-9b"
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 1, out)
        self.assertIn("no model 'bonsai-2-9b'", out)

    def test_downloaded_but_other_model_loaded_fails(self):
        """The Mac also has 27B models installed; only Bonsai 2 may run."""
        self.lm.close()
        self.lm = Server(models=("bonsai-test", "qwen-27b"), loaded=("qwen-27b",))
        self.settings["llm"]["profiles"]["mac"]["base_url"] = self.lm.base
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 1, out)
        self.assertIn("NOT loaded", out)
        self.assertIn("qwen-27b", out)

    def test_extra_loaded_model_only_warns(self):
        self.lm.close()
        self.lm = Server(models=("bonsai-test", "qwen-27b"))
        self.settings["llm"]["profiles"]["mac"]["base_url"] = self.lm.base
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 0, out)
        self.assertIn("other models are loaded (qwen-27b)", out)

    def test_no_v0_endpoint_only_warns(self):
        self.lm.close()
        self.lm = Server(v0=False)                # older LM Studio: /api/v0/models is a 404
        self.settings["llm"]["profiles"]["mac"]["base_url"] = self.lm.base
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 0, out)
        self.assertIn("cannot tell which models are loaded", out)

    def use_lm(self, **kw):
        self.lm.close()
        self.lm = Server(**kw)
        self.settings["llm"]["profiles"]["mac"]["base_url"] = self.lm.base

    def test_fast_model_loaded_too_passes_without_eject_warning(self):
        """Dell's #27: the profile's fast_model (2B) runs next to the 9B."""
        self.use_lm(models=("bonsai-test", "bonsai-fast"))
        self.settings["llm"]["profiles"]["mac"]["fast_model"] = "bonsai-fast"
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 0, out)
        self.assertIn("bonsai-fast is loaded", out)
        self.assertNotIn("eject", out)

    def test_fast_model_not_loaded_fails(self):
        self.use_lm(models=("bonsai-test", "bonsai-fast"), loaded=("bonsai-test",))
        self.settings["llm"]["profiles"]["mac"]["fast_model"] = "bonsai-fast"
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 1, out)
        self.assertIn("bonsai-fast is downloaded but NOT loaded", out)

    def test_fast_model_not_downloaded_fails(self):
        self.settings["llm"]["profiles"]["mac"]["fast_model"] = "bonsai-2-2b-missing"
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 1, out)
        self.assertIn("no model 'bonsai-2-2b-missing' downloaded", out)

    def test_argmax_down_fails(self):
        self.settings["stt"]["mac"]["url"] = "http://127.0.0.1:9/v1/audio/transcriptions"
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 1, out)
        self.assertIn("argmax-cli serve", out)

    def test_starts_argmax_serve_itself_and_waits_for_it(self):
        """One command: if argmax-cli serve isn't running, start_mac.sh starts it
        (stt.mac.serve_command) and waits until it answers."""
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        fake = self.tmp / "fake_serve.py"
        fake.write_text(FAKE_SERVE, encoding="utf-8")
        self.settings["stt"]["mac"]["url"] = f"http://127.0.0.1:{port}/v1/audio/transcriptions"
        self.settings["stt"]["mac"]["serve_command"] = [Path(sys.executable).as_posix(), fake.as_posix(),
                                                       str(port)]
        code, out = self.run_script(self.settings, FARADAY_WAIT_STT_S="20", FARADAY_START_STT="1")
        self.assertEqual(code, 0, out)
        self.assertIn("Argmax STT not running: starting it", out)
        self.assertIn(f"Argmax STT started and answering at http://127.0.0.1:{port}", out)

    def test_serve_that_dies_is_a_fail_with_its_log(self):
        self.settings["stt"]["mac"]["url"] = "http://127.0.0.1:9/v1/audio/transcriptions"
        self.settings["stt"]["mac"]["serve_command"] = [
            Path(sys.executable).as_posix(), "-c", "print('model not found: large-v3'); raise SystemExit(1)"]
        code, out = self.run_script(self.settings, FARADAY_START_STT="1")
        self.assertEqual(code, 1, out)
        self.assertTrue("did not come up" in out or "stopped right away" in out, out)
        self.assertIn("model not found: large-v3", out)              # the log's last lines
        self.assertIn("NOT READY", out)
        self.assertIn("Nothing started", out)

    def test_no_fresh_keeps_the_old_log(self):
        code, out = self.run_script(self.settings, args=("--no-fresh", "--no-browser"))
        self.assertEqual(code, 0, out)
        self.assertIn("would start:", out)
        self.assertNotIn("--fresh", out.split("would start:")[1])

    def test_never_starts_a_real_serve_when_told_not_to(self):
        """Review: on the Mac the suite would otherwise start the real argmax-cli serve."""
        self.settings["stt"]["mac"]["url"] = "http://127.0.0.1:9/v1/audio/transcriptions"
        code, out = self.run_script(self.settings)            # FARADAY_START_STT=0 by default here
        self.assertEqual(code, 1, out)
        self.assertNotIn("starting it", out)
        self.assertIn("Start it: argmax-cli serve", out)

    def test_serve_command_as_a_string_and_waits_in_real_seconds(self):
        import time as _time
        self.settings["stt"]["mac"]["url"] = "http://127.0.0.1:9/v1/audio/transcriptions"
        self.settings["stt"]["mac"]["serve_command"] = Path(sys.executable).as_posix() + " -c pass"
        t0 = _time.monotonic()
        code, out = self.run_script(self.settings, FARADAY_START_STT="1", FARADAY_WAIT_STT_S="3")
        self.assertEqual(code, 1, out)
        self.assertLess(_time.monotonic() - t0, 25)           # not loop turns x curl timeouts
        self.assertIn("starting it", out)

    def test_product_needs_argmax_speech(self):
        """Owner 2026-11-15: the real product only. A stand-in TTS kind or a missing
        TTS binary is a FAIL, not a quiet fallback to printed speech."""
        self.settings["tts"]["mac"]["kind"] = "standin"
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 1, out)
        self.assertIn("the product speaks with Argmax", out)
        self.settings["tts"]["mac"]["kind"] = "argmax"
        self.settings["tts"]["mac"]["command"] = ["/no/such/argmax-cli", "tts"]
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 1, out)
        self.assertIn("Faraday must speak", out)

    def test_missing_settings_created_with_mac_profile(self):
        code, out = self.run_script(None, FARADAY_SKIP_CHECKS="1")
        self.assertEqual(code, 0, out)
        made = json.loads((self.tmp / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(made["llm"]["profile"], "mac")


if __name__ == "__main__":
    unittest.main()
