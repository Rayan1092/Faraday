"""log.check: counts, duplicates and passphrase leaks in a test-run log."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

from log.check import duplicates, main, passphrase_leaks
from sim import events


def at(event, seconds, source=None):
    d = event.to_dict()
    d["timestamp"] = (datetime(2026, 11, 14, 17, 0, 0).astimezone()
                      + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")
    if source:
        d["source"] = source
    return d


class CheckTests(unittest.TestCase):
    def test_duplicates_only_for_close_sensor_events(self):
        log = [at(events.motion(), 0, "flipper"), at(events.motion(), 1.5, "flipper"),
               at(events.motion(), 10, "flipper"), at(events.doorbell(), 10.5, "flipper"),
               at(events.motion(), 11, "dashboard")]
        found = duplicates(log)
        self.assertEqual(len(found), 1)
        self.assertIn("1.5 s after", found[0])

    def test_passphrase_leaks(self):
        log = [at(events.speech("[passphrase]"), 0), at(events.speech("silver Otter lantern!"), 1)]
        self.assertEqual(passphrase_leaks(log, "silver otter lantern"), 1)

    def test_cli_runs_on_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "join1.jsonl"
            path.write_text("\n".join(json.dumps(at(events.doorbell(), i * 5, "flipper"))
                                      for i in range(3)) + "\n{torn", encoding="utf-8")
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main([str(path), "--last", "2"]), 0)
            text = out.getvalue()
            self.assertIn("3 entries", text)
            self.assertIn("doorbell         flipper    3", text)
            self.assertIn("Likely duplicates (< 3 s apart): 0", text)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(Path(tmp) / "missing.jsonl")]), 1)


if __name__ == "__main__":
    unittest.main()
