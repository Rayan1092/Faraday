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

    def test_answer_is_the_description_plus_the_label(self):
        self.assertEqual(look_line(photo("t"), "delivery"), COURIER + " It looks like a delivery.")
        self.assertEqual(look_line(photo("t", "An empty porch at night"), "nobody"),
                         "An empty porch at night. Nobody is there.")

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

    def ask(self, text="Who's at the door?"):
        r = self.p.handle(events.speech(text))
        takes = [a for a in r.actions if a.type == ActionType.TAKE_PHOTO]
        return r, takes

    def test_fresh_photo_then_the_answer_from_plain_code(self):
        self.make()
        r, [take] = self.ask()
        self.assertEqual(said(r.actions), [LOOKING])
        self.assertEqual((take.source, take.args["reason"], take.caused_by),
                         (Source.DASHBOARD, LOOK_REASON, r.events[0].id))
        answer = self.p.handle(photo(take.id))
        self.assertEqual(said(answer.actions), [COURIER + " It looks like a delivery."])
        self.assertEqual(self.client.calls, [])               # no Bonsai, no visitor greeting
        self.assertIn(EventType.THREAT_ASSESSED, [e.type for e in answer.events])

    def test_works_at_night_too(self):
        self.make(Mode.NIGHT)
        _, [take] = self.ask("Faraday, is anyone outside?")
        self.assertTrue(said(self.p.handle(photo(take.id, "An empty porch.")).actions)[0].startswith("An empty porch."))

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
