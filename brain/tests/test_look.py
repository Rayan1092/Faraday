"""'Who's at the door?': plain code takes a fresh photo and speaks what the camera sees
plus the threat label, or 'I can't see the door right now.'"""
import tempfile
import time
import unittest
from pathlib import Path

from shared import ActionType, EventType, Mode, Source, make_event
from shared.events import PhotoDescribedData

from brain import Brain
from brain.look import CANT_SEE, LOOK_REASON, LOOKING, is_look_request, look_line, short_description
from brain.pipeline import Pipeline
from dashboard.server import STATIC_DIR
from log import EventLog
from modes import ModeState
from sim import events

from .helpers import FakeClient, tool_calls

SECRET = "silver otter lantern"
COURIER = "A courier in a blue jacket holding a cardboard box at the front door."


def photo(caused_by, description=COURIER, path="captures/photos/x.jpg", source=Source.VISION):
    return make_event(EventType.PHOTO_DESCRIBED, PhotoDescribedData(photo_path=path, description=description),
                      source, caused_by=caused_by)


def said(actions):
    return [a.args["text"] for a in actions if a.type == ActionType.SPEAK]


class Words(unittest.TestCase):
    def test_spots_the_question(self):
        for text in ("Who's at the door?", "Hey Faraday, who is at the front door?", "What do you see?",
                     "Is anyone outside?", "Is there someone at the door?", "Who rang the bell?"):
            self.assertTrue(is_look_request(text), text)
        for text in ("Turn on the porch light.", "Who are you?", "I'm heading out the door.",
                     "Open the door.", "Goodnight Faraday."):
            self.assertFalse(is_look_request(text), text)

    def test_answer_is_the_count_plus_the_label_never_the_description(self):
        """Owner, Mac test: spoken lines never describe the person (the alarm described a
        bag that wasn't there). The description stays on the dashboard."""
        counted = photo("t", "A courier in a red cap holding a box.")
        counted.data.update(people=1, faces=1)
        self.assertEqual(look_line(counted, "delivery"), "One person is at the front door. It looks like a delivery.")
        two = photo("t", "Two people.")
        two.data.update(people=2, faces=0)
        self.assertEqual(look_line(two, "visitor"), "Two people are at the front door. It looks like a visitor.")
        self.assertEqual(look_line(photo("t"), "delivery"), "Someone is at the front door. It looks like a delivery.")
        for text in ("An empty porch at night", "Nobody is there.", "A dark porch"):
            self.assertEqual(look_line(photo("t", text), "nobody"), "No one is at the front door.")
        for category in ("delivery", "visitor", "suspicious", "nobody"):
            self.assertNotIn("red cap", look_line(counted, category))
            self.assertNotIn("box", look_line(counted, category))

    def test_camera_or_vision_failure(self):
        self.assertEqual(look_line(photo("t", "Camera unavailable.", path="", source=Source.CAMERA), "nobody"),
                         CANT_SEE)
        self.assertEqual(look_line(photo("t", "Photo saved.", source=Source.CAMERA), "nobody"), CANT_SEE)

    def test_long_descriptions_are_cut_at_a_sentence(self):
        text = "A man in a grey hoodie stands on the porch. " * 8
        cut = short_description(text)
        self.assertLessEqual(len(cut), 190)
        self.assertTrue(cut.endswith("porch."))


class Pipe(unittest.TestCase):
    def make(self, mode=Mode.HOME, client=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.emitted = []
        self.client = client or FakeClient(tool_calls(("speak", {"text": "Hello! How can I help?"})))
        self.p = Pipeline(Brain(self.client, SECRET, log=lambda _m: None), ModeState(SECRET, mode=mode),
                          EventLog(Path(self.tmp.name) / "e.jsonl", SECRET), on_action=self.emitted.append,
                          say=lambda _m: None)
        return self.p

    def ask(self, text="Who's at the door?", channel=None):
        r = self.p.handle(events.speech(text, channel))
        takes = [a for a in r.actions if a.type == ActionType.TAKE_PHOTO]
        return r, takes

    def test_fresh_photo_then_the_answer_from_plain_code(self):
        self.make()
        r, [take] = self.ask()
        self.assertEqual(said(r.actions), [LOOKING])
        self.assertEqual((take.source, take.args["reason"], take.caused_by),
                         (Source.DASHBOARD, LOOK_REASON, r.events[0].id))
        answer = self.p.handle(photo(take.id))
        # Facts only (owner, Mac test): no detector count on this photo, so "Someone".
        self.assertEqual(said(answer.actions), ["Someone is at the front door. It looks like a delivery."])
        self.assertEqual(self.client.calls, [])               # no Bonsai, no visitor greeting
        self.assertIn(EventType.THREAT_ASSESSED, [e.type for e in answer.events])

    def test_works_at_night_too(self):
        self.make(Mode.NIGHT)
        _, [take] = self.ask("Faraday, is anyone outside?", "owner")
        self.assertEqual(said(self.p.handle(photo(take.id, "An empty porch.")).actions), ["No one is at the front door."])

    def test_not_while_away(self):
        """While away the speaker is at the door: a visitor, not the owner."""
        self.make(Mode.AWAY)
        r, takes = self.ask()
        self.assertNotIn(LOOKING, said(r.actions))

    def test_camera_failure(self):
        self.make()
        _, [take] = self.ask()
        failed = self.p.handle(photo(take.id, "Camera unavailable.", path="", source=Source.CAMERA))
        self.assertEqual(said(failed.actions), [CANT_SEE])

    def test_no_photo_in_time_then_a_late_one(self):
        self.make()
        self.p.look_timeout_s = 0.2
        _, [take] = self.ask()
        for _ in range(100):
            if CANT_SEE in said(self.emitted):
                break
            time.sleep(0.02)
        self.assertIn(CANT_SEE, said(self.emitted))
        late = self.p.handle(photo(take.id))
        self.assertEqual(said(late.actions), [])            # already answered; never greet nobody
        self.assertEqual(self.client.calls, [])

    def test_late_answer_only_if_nothing_else_happened(self):
        """Mac test: "I can't see the door right now" was heard after a motion at Home.
        It may only answer the question: anything in between and the timer stays quiet."""
        self.make()
        self.p.look_timeout_s = 0.3
        self.ask()
        self.p.handle(events.motion())                    # something else happens meanwhile
        time.sleep(0.6)
        self.assertNotIn(CANT_SEE, said(self.emitted))

    def test_timeout_covers_a_photo_waiting_in_line(self):
        """NV's vision gives up after 20 s, but the look photo can queue behind others."""
        self.make()
        self.assertGreaterEqual(self.p.look_timeout_s, 60)

    def test_quiet_even_after_a_busy_log(self):
        """Review: the old check scanned the last 50 log entries; with more than that since
        the question it lost the photo and spoke. The activity count can't scroll away."""
        self.make(Mode.NIGHT)
        self.p.look_timeout_s = 0.3
        self.ask()
        for _ in range(30):
            self.p.handle(events.motion())
        time.sleep(0.6)
        self.assertNotIn(CANT_SEE, said(self.emitted))

    def test_detector_facts_are_spoken_when_vision_fails(self):
        """NV #43: when vision fails the object detector writes the description
        (described_by "detector"): answer from its count, not "I can't see"."""
        self.make()
        _, [take] = self.ask()
        seen = make_event(EventType.PHOTO_DESCRIBED,       # source camera: the case that changed
                          PhotoDescribedData(photo_path="captures/photos/x.jpg",
                                             description="One person is at the door.",
                                             described_by="detector", people=1),
                          Source.CAMERA, caused_by=take.id)
        line = said(self.p.handle(seen).actions)[0]
        self.assertTrue(line.startswith("One person is at the front door."), line)
        failed = make_event(EventType.PHOTO_DESCRIBED,
                            PhotoDescribedData(photo_path="captures/photos/x.jpg", description="Photo saved."),
                            Source.CAMERA, caused_by="act_x")
        from brain.look import photo_failed
        self.assertTrue(photo_failed(failed))

    def test_answer_cancels_the_timer(self):
        self.make()
        self.p.look_timeout_s = 0.3
        _, [take] = self.ask()
        self.p.handle(photo(take.id))
        time.sleep(0.5)
        self.assertNotIn(CANT_SEE, said(self.emitted))


class Captions(unittest.TestCase):
    def test_controls_page_has_live_captions(self):
        html = (STATIC_DIR / "controls.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "controls.js").read_text(encoding="utf-8")
        self.assertIn('id="captions"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn("renderCaptions(s.talk", js)


if __name__ == "__main__":
    unittest.main()
