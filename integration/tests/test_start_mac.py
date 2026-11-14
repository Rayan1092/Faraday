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


class Server:
    def __init__(self, models=("bonsai-test",)):
        outer = self
        self.models = list(models)

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/v1/models":
                    raw = json.dumps({"data": [{"id": m} for m in outer.models]}).encode()
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


@unittest.skipUnless(BASH, "needs bash")
class StartMac(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="faraday-mac-"))
        self.lm, self.argmax = Server(), Server(models=())
        s = json.loads((ROOT / "config" / "settings.example.json").read_text(encoding="utf-8"))
        s["llm"]["profiles"]["mac"] = {"base_url": self.lm.base, "model": "bonsai-test"}
        s["stt"]["mac"]["url"] = self.argmax.base + "/v1/audio/transcriptions"
        self.settings = s

    def tearDown(self):
        self.lm.close()
        self.argmax.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, settings=None, **env):
        path = self.tmp / "settings.json"
        if settings is not None:
            path.write_text(json.dumps(settings), encoding="utf-8")
        e = {**os.environ, "PYTHON": Path(sys.executable).as_posix(),
             "FARADAY_SETTINGS": path.as_posix(), "FARADAY_DRY_RUN": "1", **env}
        p = subprocess.run([BASH, SCRIPT.as_posix(), "--offline"], capture_output=True, text=True,
                           env=e, timeout=60, cwd=ROOT)
        return p.returncode, p.stdout + p.stderr

    def test_all_up_passes_and_would_start(self):
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 0, out)
        self.assertIn("has bonsai-test", out)
        self.assertIn("Argmax STT answering", out)
        self.assertIn("would start", out)
        self.assertIn("-m integration --offline", out)
        self.assertIn("passphrase is still the public example", out)

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

    def test_argmax_down_fails(self):
        self.settings["stt"]["mac"]["url"] = "http://127.0.0.1:9/v1/audio/transcriptions"
        code, out = self.run_script(self.settings)
        self.assertEqual(code, 1, out)
        self.assertIn("argmax-cli serve", out)

    def test_missing_settings_created_with_mac_profile(self):
        code, out = self.run_script(None, FARADAY_SKIP_CHECKS="1")
        self.assertEqual(code, 0, out)
        made = json.loads((self.tmp / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(made["llm"]["profile"], "mac")


if __name__ == "__main__":
    unittest.main()
