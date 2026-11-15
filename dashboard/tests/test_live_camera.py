"""Owner's list item 9: the live camera in the centre (NV's stream with the detector's
boxes), the label and detection chips under it, photos as thumbnails in the activity
log, "Recording saved" at an alarm, and NV's owner-voice result."""
import unittest

from shared import ActionType, EventType, Source, make_action, make_event
from shared.events import PhotoDescribedData

from dashboard.server import STATIC_DIR
from dashboard.state import build_state
from sim import events

PERSON = {"label": "person", "count": 1, "confidence": 0.91}
BAG = {"label": "backpack", "count": 2, "confidence": 0.55}


def photo_event(caused_by=None, detections=(PERSON, BAG)):
    return make_event(EventType.PHOTO_DESCRIBED, PhotoDescribedData(
        photo_path="captures/photos/door.jpg", description="One person with two backpacks.",
        described_by="bonsai", people=1, faces=1, detections=list(detections)), Source.VISION,
        caused_by=caused_by)


def state(*items, alert="normal"):
    return build_state([i.to_dict() if hasattr(i, "to_dict") else i for i in items], "away", alert)


class Camera(unittest.TestCase):
    def test_chips_from_the_detector(self):
        shot = make_action(ActionType.TAKE_PHOTO, {"reason": "doorbell while away"}, Source.RULES)
        s = state(shot, photo_event(shot.id))
        self.assertEqual(s["photo"]["chips"], [{"label": "person", "count": 1},
                                               {"label": "backpack", "count": 2}])
        self.assertEqual(s["photo"]["people"], 1)
        self.assertEqual(state(shot, photo_event(shot.id, detections=()))["photo"]["chips"], [])

    def test_photos_are_thumbnails_in_the_activity_log(self):
        shot = make_action(ActionType.TAKE_PHOTO, {"reason": "Snapshot from the dashboard"}, Source.DASHBOARD)
        feed = state(shot, photo_event(shot.id))["feed"]
        self.assertEqual(feed[0]["thumb"], "/media/captures/photos/door.jpg")

    def test_recording_saved_at_the_alarm(self):
        alarm = make_event(EventType.ALERT_CHANGED, {"level": "alarm", "previous": "warning", "reason": "x"},
                           Source.MODES)
        saved = make_event(EventType.RECORDING_SAVED, {"path": "captures/alarm-20261115-013000/",
                                                       "frames": 40, "duration_s": 9.6,
                                                       "video_path": "captures/alarm-20261115-013000/clip.avi"},
                           Source.CAMERA)
        s = state(alarm, saved, alert="alarm")
        self.assertTrue(s["recording"]["for_alarm"])
        self.assertEqual(s["recording"]["thumb"], "/media/captures/alarm-20261115-013000/frame-0001.jpg")
        self.assertIn("Recording saved.", s["banner"]["text"])
        self.assertEqual(s["feed"][0]["text"], "Recording saved (10 s)")
        self.assertEqual(s["feed"][0]["level"], "alarm")
        self.assertEqual(s["feed"][0]["thumb"], "/media/captures/alarm-20261115-013000/frame-0001.jpg")
        self.assertIn("Recording.", state(alarm, alert="alarm")["banner"]["text"])   # not saved yet
        later = make_event(EventType.ALERT_CHANGED, {"level": "alarm", "previous": "warning", "reason": "y"},
                           Source.MODES)
        old = state(alarm, saved, later, alert="alarm")
        self.assertFalse(old["recording"]["for_alarm"])         # that one was for the earlier alarm

    def test_owner_voice_result(self):
        def heard(text, voice):
            return make_event(EventType.SPEECH_HEARD, {"text": text, "channel": "owner", "owner_voice": voice},
                              Source.SPEECH)
        talk = state(heard("Hey Faraday, what happened?", True), heard("Turn off the alarm.", False),
                     events.speech("Hello?"))["talk"]
        self.assertEqual([t.get("voice") for t in talk], ["owner", "other", None])

    def test_page(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
        for needed in ('id="live"', 'id="live-badge"', 'id="chips"', 'id="recording"', 'id="threat"'):
            self.assertIn(needed, html, needed)
        self.assertIn('ctrl("/stream.mjpg', js)                 # NV's stream, from the controls port
        self.assertIn("showPhoto(lastPhotoSrc)", js)              # the photo when the stream drops
        self.assertIn('"thumb"', js) if False else self.assertIn("f.thumb", js)
        self.assertIn("Owner's voice", js)
        # the label sits under the camera, before the description
        self.assertLess(html.index('id="threat"'), html.index('id="photo-text"'))


if __name__ == "__main__":
    unittest.main()
