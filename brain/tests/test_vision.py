"""describe_photo: always a valid photo_described event, with or without vision."""
import tempfile
import unittest
from pathlib import Path

from shared import Source, validate_event

from brain.bonsai import BonsaiError
from brain.settings import REPO_ROOT
from brain.vision import NO_DESCRIPTION, Describer, describe_photo, repo_path

from .helpers import FakeClient

# Smallest valid PNG (1x1 pixel).
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082")


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.photo = Path(self.tmp.name) / "door.png"
        self.photo.write_bytes(PNG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_description_from_bonsai(self):
        client = FakeClient({"role": "assistant", "content": "A person holding a box."})
        event = describe_photo(self.photo, "act_12345678", Describer(client, "bonsai-x", log=lambda _m: None))
        self.assertEqual(validate_event(event.to_dict()), [])
        self.assertEqual(event.source, Source.VISION)
        self.assertEqual(event.caused_by, "act_12345678")
        self.assertEqual(event.data["description"], "A person holding a box.")
        self.assertEqual(event.data["model"], "bonsai-x")
        content = client.calls[0]["messages"][0]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_no_model_or_failure_still_gives_an_event(self):
        for describer in (None, Describer(None), Describer(FakeClient(RuntimeError("x")), log=lambda _m: None),
                          Describer(FakeClient({"content": ""}), log=lambda _m: None)):
            event = describe_photo(self.photo, None, describer)
            self.assertEqual(event.data["description"], NO_DESCRIPTION)
            self.assertIsNone(event.data["model"])
            self.assertEqual(validate_event(event.to_dict()), [])

    def test_model_without_vision_is_not_asked_again(self):
        client = FakeClient(BonsaiError("HTTP 400: model does not support images"))
        d = Describer(client, log=lambda _m: None)
        describe_photo(self.photo, None, d)
        describe_photo(self.photo, None, d)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("HTTP 400", d.disabled_reason)

    def test_timeouts_are_retried_next_time(self):
        client = FakeClient(BonsaiError("connection failed: timed out"))
        d = Describer(client, log=lambda _m: None)
        describe_photo(self.photo, None, d)
        describe_photo(self.photo, None, d)
        self.assertEqual(len(client.calls), 2)

    def test_missing_or_wrong_files(self):
        client = FakeClient({"content": "x"})
        d = Describer(client, log=lambda _m: None)
        for path in (Path(self.tmp.name) / "nope.jpg", Path(self.tmp.name) / "notes.txt"):
            self.assertEqual(describe_photo(path, None, d).data["description"], NO_DESCRIPTION)
        self.assertEqual(client.calls, [])

    def test_repo_relative_paths(self):
        self.assertEqual(repo_path(REPO_ROOT / "captures" / "photos" / "a.jpg"), "captures/photos/a.jpg")
        self.assertEqual(repo_path("captures/photos/a.jpg"), "captures/photos/a.jpg")


if __name__ == "__main__":
    unittest.main()
