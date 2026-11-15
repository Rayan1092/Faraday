"""A visitor who talks about a delivery is a delivery (owner, Mac test).

The detector can't see boxes and vision may fail, so what the visitor says counts:
package, parcel, delivery, courier. Vision is slow (49 s on the Mac), so the visitor
often speaks before the photo is labelled: both orders end in a delivery label."""
import tempfile
import unittest
from pathlib import Path

from shared import ActionType, EventType, Mode, Source, ThreatCategory, make_event, now_iso, validate_event
from shared.events import PhotoDescribedData

from brain.pipeline import Pipeline
from log import EventLog
from modes import ModeState
from sim import events

from .helpers import PASSPHRASE, make_brain

C = ThreatCategory


def photo_described(caused_by, description, **detector):
    """Like NV's camera worker: the description plus described_by, people, faces, detections."""
    return make_event(EventType.PHOTO_DESCRIBED,
                      PhotoDescribedData(photo_path=events.FAKE_PHOTO, description=description, **detector),
                      Source.VISION, caused_by=caused_by)


class LabelFromSpeech(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Pipeline(make_brain(), ModeState(PASSPHRASE, 60, Mode.AWAY),
                          EventLog(Path(self.tmp.name) / "events.jsonl", PASSPHRASE), say=lambda _m: None)

    def tearDown(self):
        self.tmp.cleanup()

    def labels(self):
        return [e for e in self.p.log.recent(200) if e.get("type") == EventType.THREAT_ASSESSED]

    def ring_and_photo(self, description="One person at the front door, face visible.", **data):
        ring = self.p.handle(events.doorbell())
        photo = next(a for a in ring.actions if a.type == ActionType.TAKE_PHOTO)
        return photo, photo_described(photo.id, description, **data)

    def test_speech_after_the_label_updates_it(self):
        photo, described = self.ring_and_photo(people=1, faces=1, described_by="bonsai",
                                               detections=[{"label": "person", "count": 1, "confidence": 0.9}])
        self.p.handle(described)
        self.assertEqual(self.labels()[-1]["data"]["category"], C.VISITOR)
        self.p.handle(events.speech("Hi, I have a package, where should I leave it?", "visitor"))
        new = self.labels()[-1]
        self.assertEqual(new["data"]["category"], C.DELIVERY)
        self.assertEqual(new["caused_by"], described.id)                  # the dashboard updates that photo
        self.assertTrue(new["data"]["reason"].startswith('Said "package".'))
        self.assertEqual(validate_event(new), [])

    def test_speech_before_the_label_is_read_by_it(self):
        photo, described = self.ring_and_photo(people=1, faces=1, described_by="detector")
        self.p.handle(events.speech("Delivery for Sam.", "visitor"))
        self.assertEqual(self.labels(), [])                               # no label yet: nothing to update
        # The camera worker makes the event when vision is done, after the speech (a
        # timestamp from before it made this test depend on the millisecond clock).
        described.timestamp = now_iso()
        self.p.handle(described)                                          # vision failed, detector saw 1
        self.assertEqual(self.labels()[-1]["data"]["category"], C.DELIVERY)

    def test_never_less_careful_and_never_the_owner(self):
        photo, described = self.ring_and_photo("A man wearing a ski mask at the door.", people=1,
                                               described_by="bonsai")
        self.p.handle(described)
        self.assertEqual(self.labels()[-1]["data"]["category"], C.SUSPICIOUS)
        self.p.handle(events.speech("I have a package for you.", "visitor"))
        self.assertEqual(self.labels()[-1]["data"]["category"], C.SUSPICIOUS)
        p2 = Pipeline(make_brain(), ModeState(PASSPHRASE, 60, Mode.HOME),
                      EventLog(Path(self.tmp.name) / "home.jsonl", PASSPHRASE), say=lambda _m: None)
        p2.handle(events.speech("If a delivery comes, ask them to leave it by the side door.", "owner"))
        self.assertEqual([e for e in p2.log.recent(50) if e.get("type") == EventType.THREAT_ASSESSED], [])

    def test_a_second_press_keeps_what_was_said(self):
        """Review: press 2's photo forgot the "parcel" said after press 1."""
        photo, described = self.ring_and_photo(people=1, faces=1, described_by="bonsai",
                                               detections=[{"label": "person", "count": 1, "confidence": 0.9}])
        self.p.handle(described)
        self.p.handle(events.speech("I have a parcel for you.", "visitor"))
        self.assertEqual(self.labels()[-1]["data"]["category"], C.DELIVERY)
        photo2, described2 = self.ring_and_photo(people=1, faces=1, described_by="bonsai",
                                                 detections=[{"label": "person", "count": 1, "confidence": 0.9}])
        self.p.handle(described2)
        self.assertEqual(self.labels()[-1]["data"]["category"], C.DELIVERY)

    def test_a_snapshot_with_no_visit_ignores_old_speech(self):
        self.p.handle(events.speech("Delivery for Sam.", "visitor"))      # no doorbell at all
        snap = self.p.owner_snapshot()
        self.p.handle(photo_described(snap.id, "One person at the door, face visible.", people=1, faces=1,
                                      described_by="bonsai"))
        self.assertEqual(self.labels()[-1]["data"]["category"], C.VISITOR)

    def test_no_delivery_word_no_change(self):
        photo, described = self.ring_and_photo(people=1, faces=1, described_by="bonsai")
        self.p.handle(described)
        before = len(self.labels())
        self.p.handle(events.speech("Is anyone home?", "visitor"))
        self.assertEqual(len(self.labels()), before)


if __name__ == "__main__":
    unittest.main()
