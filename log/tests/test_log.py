"""logs/events.jsonl writer/reader, including passphrase redaction."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from shared import ActionType, Source, make_action, validate_action, validate_event

from log import EventLog
from sim import events

SECRET = "blue pelican seventeen"


class EventLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "logs" / "events.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def lines(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def test_writes_one_valid_object_per_line(self):
        log = EventLog(self.path, SECRET)
        bell = events.doorbell()
        log.write(bell)
        log.write(make_action(ActionType.TAKE_PHOTO, {"reason": "doorbell"}, Source.RULES, bell.id))
        rows = self.lines()
        self.assertEqual([r["kind"] for r in rows], ["event", "action"])
        self.assertEqual(validate_event(rows[0]), [])
        self.assertEqual(validate_action(rows[1]), [])
        self.assertEqual(rows[0], bell.to_dict())

    def test_passphrase_never_written_or_returned(self):
        log = EventLog(self.path, SECRET)
        log.write(events.speech("Okay. Blue pelican 17, disarm."))
        log.write(make_action(ActionType.SPEAK, {"text": "You said blue pelican seventeen"},
                              Source.BRAIN))
        log.write({"kind": "event", "nested": [{"deep": "BLUE PELICAN SEVENTEEN"}]})
        raw = self.path.read_text(encoding="utf-8").lower()
        for word in ("pelican", "seventeen", " 17"):
            self.assertNotIn(word, raw)
        self.assertEqual(raw.count("[passphrase]"), 3)
        recent = json.dumps(log.recent(10)).lower()
        self.assertNotIn("pelican", recent)
        self.assertEqual(log.recent(10)[0]["data"]["text"], "Okay. [passphrase], disarm.")

    def test_write_returns_the_stored_copy_and_leaves_input_alone(self):
        log = EventLog(self.path, SECRET)
        heard = events.speech("blue pelican seventeen")
        stored = log.write(heard)
        self.assertEqual(stored["data"]["text"], "[passphrase]")
        self.assertEqual(heard.data["text"], "blue pelican seventeen")

    def test_recent_order_size_and_copies(self):
        log = EventLog(self.path, SECRET)
        written = [log.write(events.speech(f"line {i}")) for i in range(15)]
        recent = log.recent(10)
        self.assertEqual([r["id"] for r in recent], [w["id"] for w in written[-10:]])
        self.assertEqual(log.recent(0), [])
        recent[0]["data"]["text"] = "changed"
        self.assertNotEqual(log.recent(10)[0]["data"]["text"], "changed")

    def test_reload_keeps_tail_and_skips_torn_lines(self):
        log = EventLog(self.path, SECRET)
        first = log.write(events.motion())
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"kind": "event", "torn')  # crash mid-write
        again = EventLog(self.path, SECRET)
        self.assertEqual(again.recent(5)[0]["id"], first["id"])
        self.assertEqual(len(again.recent(5)), 1)

    def test_messages(self):
        log = EventLog(self.path, SECRET)
        log.write(events.doorbell())
        log.write(make_action(ActionType.SAVE_MESSAGE, {"text": "Package for Sam."}, Source.BRAIN))
        self.assertEqual([m["args"]["text"] for m in log.messages()], ["Package for Sam."])

    def test_threads_do_not_interleave_lines(self):
        log = EventLog(self.path, SECRET)
        def worker():
            for _ in range(50):
                log.write(events.motion())
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = self.lines()
        self.assertEqual(len(rows), 200)
        for row in rows:
            self.assertEqual(validate_event(row), [])

    def test_from_settings_uses_paths_logs(self):
        log = EventLog.from_settings({"paths": {"logs": str(Path(self.tmp.name) / "x")},
                                      "security": {"passphrase": SECRET}})
        self.assertEqual(log.path, Path(self.tmp.name) / "x" / "events.jsonl")


if __name__ == "__main__":
    unittest.main()
