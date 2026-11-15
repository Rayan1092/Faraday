"""Threat assessment: rules, Bonsai parsing, code checks, effects (escalate only)."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from shared import (ActionType, AlertLevel, ChangedBy, Event, EventType, Mode, Source, ThreatCategory,
                    make_action, make_event, validate_event)

from brain import threat
from brain.pipeline import Pipeline
from brain.summary import collect, facts, spoken_list
from dashboard.state import build_state
from log import EventLog
from modes import ModeState
from sim import events

from .helpers import PASSPHRASE, FakeClient, make_brain, tool_calls

C = ThreatCategory
BOX = "A person in a dark jacket holding a cardboard box at the front door."
MASK = "A man wearing a ski mask trying the door handle."
COAT = "A woman in a red coat waving at the camera."
EMPTY = "An empty porch with a doormat. No one is visible."


def ctx(description, mode=Mode.AWAY, cause="doorbell", doorbells=1, hour=15, voices=None):
    return threat.Context(description, mode, cause=cause, doorbells=doorbells,
                          when=datetime(2026, 11, 14, hour, 0), voices=voices)


def classify(category, reason="because"):
    return tool_calls(("classify", {"category": category, "reason": reason}))


PERSON1 = [{"label": "person", "count": 1, "confidence": 0.92}]


def seen(description, cause="doorbell", doorbells=1, people=1, faces=1, detections=None,
         vision_ok=True, mode=Mode.AWAY):
    """A context like NV's camera gives: the 9B's description plus the detector's counts."""
    return threat.Context(description, mode, cause=cause, doorbells=doorbells,
                          when=datetime(2026, 11, 14, 15, 0), people=people, faces=faces,
                          detections=PERSON1 if detections is None else detections, vision_ok=vision_ok)


class Honest(unittest.TestCase):
    """Owner 2026-11-15: grounded and honest. Zero people detected is always nobody, a
    failed vision is "unknown" (never a guess), and the reason names what was seen."""

    def test_the_four_bench_scenes(self):
        empty = seen("Nobody is there.", cause="motion", doorbells=0, people=0, faces=0, detections=[])
        box = seen("One person holding a cardboard box at the door.")
        plain = seen("A woman in a red coat waving at the camera.")
        hood = seen("A person with the hood up and face covered standing at the door.", cause="motion",
                    doorbells=0, faces=0, detections=PERSON1 + [{"label": "backpack", "count": 1, "confidence": 0.5}])
        self.assertEqual(threat.rules(empty), (C.NOBODY, "No person detected."))
        self.assertEqual(threat.rules(box), (C.DELIVERY, "1 person, holding a box, rang the doorbell."))
        self.assertEqual(threat.rules(plain), (C.VISITOR, "1 person, rang the doorbell."))
        self.assertEqual(threat.rules(hood), (C.SUSPICIOUS, "1 person, backpack, face covered."))

    def test_zero_people_is_nobody_whatever_the_words_and_no_model(self):
        client = FakeClient(classify("visitor", "a man at the door"))
        a = threat.ThreatAssessor(client, "fast", log=lambda _m: None).assess(
            seen("A man in a hoodie at the door.", people=0, faces=0,
                 detections=[{"label": "car", "count": 1, "confidence": 0.7}]))
        self.assertEqual((a.category, a.via, client.calls), (C.NOBODY, "rules", []))
        self.assertEqual(a.reason, "No person detected, car in view.")

    def test_vision_failed_labels_from_the_detector(self):
        """Owner, Mac test: vision failed, the detector saw 1 person and a face, and the
        label said "unknown". Now the detector's facts plus the context decide."""
        client = FakeClient(classify("suspicious", "someone"))
        a = threat.ThreatAssessor(client, "fast", log=lambda _m: None).assess(
            seen("One person is at the door.", vision_ok=False))
        self.assertEqual((a.category, a.via, client.calls), (C.VISITOR, "rules", []))
        self.assertEqual(a.reason, "1 person, face visible, rang the doorbell.")
        stayed = threat.rules(seen("One person is at the door.", cause="motion", doorbells=0,
                                   faces=0, vision_ok=False))
        self.assertEqual(stayed, (C.SUSPICIOUS, "1 person, stayed at the door without ringing while armed."))
        armed_knife = threat.rules(seen("One person is at the door.", vision_ok=False, detections=[
            PERSON1[0], {"label": "knife", "count": 1, "confidence": 0.6}]))
        self.assertEqual(armed_knife[0], C.SUSPICIOUS)
        home = threat.rules(seen("One person is at the door.", cause="", doorbells=0, mode=Mode.HOME,
                                 vision_ok=False))
        self.assertEqual(home, (C.VISITOR, "1 person, face visible, at the door."))

    def test_unknown_only_when_vision_and_the_detector_failed(self):
        a = threat.ThreatAssessor(None).assess(seen("Photo saved.", people=None, detections=[],
                                                    vision_ok=False))
        self.assertEqual((a.category, a.reason), (C.UNKNOWN, "Couldn't analyse the photo."))

    def test_a_box_in_the_description_is_a_delivery_even_if_they_rang(self):
        """Mac bench: "person with a box who rang" 0/10, "courier with a box" 6/10. The 2B
        said visitor with a reason like "rang the doorbell", and code let it through."""
        for description in ("One person holding a cardboard box at the front door, face visible.",
                            "A person in a dark jacket holding a cardboard box at the front door."):
            client = FakeClient(classify("visitor", "someone who rang the doorbell while away"))
            a = threat.ThreatAssessor(client, "fast", log=lambda _m: None).assess(seen(description))
            self.assertEqual(a.category, C.DELIVERY, description)
            self.assertIn("holding a box", a.reason)
            self.assertIn("package", a.note)

    def test_the_prompt_says_what_a_delivery_is(self):
        self.assertIn("holding or carrying a box", threat.SYSTEM_PROMPT)
        self.assertIn("even if they rang", threat.SYSTEM_PROMPT)
        self.assertIn("can't see boxes", threat.SYSTEM_PROMPT)

    def test_what_the_visitor_said_makes_it_a_delivery(self):
        """Owner, Mac test: "package, parcel, delivery, courier in speech_heard means delivery"."""
        said = threat.Context("One person at the front door, face visible.", Mode.AWAY, cause="doorbell",
                              doorbells=1, people=1, faces=1, detections=PERSON1, said="package")
        self.assertEqual(threat.rules(said), (C.DELIVERY, '1 person, said "package", rang the doorbell.'))
        client = FakeClient(classify("visitor", "1 person, rang the doorbell"))
        self.assertEqual(threat.ThreatAssessor(client, "fast", log=lambda _m: None).assess(said).category,
                         C.DELIVERY)
        blind = threat.Context("One person is at the door.", Mode.AWAY, cause="doorbell", doorbells=1,
                               people=1, faces=1, detections=PERSON1, vision_ok=False, said="parcel")
        self.assertEqual(threat.rules(blind)[0], C.DELIVERY)
        masked = threat.Context("A man wearing a ski mask at the door.", Mode.AWAY, cause="doorbell",
                                doorbells=1, people=1, detections=PERSON1, said="package")
        self.assertEqual(threat.rules(masked)[0], C.SUSPICIOUS)            # never less careful

    def test_from_the_real_event(self):
        """context_for reads NV's fields; "detector" and a bare camera photo mean vision failed."""
        from shared.events import PhotoDescribedData
        bell = events.doorbell()
        shot = make_action(ActionType.TAKE_PHOTO, {"reason": "doorbell while away"}, Source.RULES,
                           caused_by=bell.id)
        recent = [bell.to_dict(), shot.to_dict()]

        def photo(by, source=Source.VISION, desc="One person is at the door."):
            return make_event(EventType.PHOTO_DESCRIBED, PhotoDescribedData(
                photo_path="captures/photos/x.jpg", description=desc, described_by=by, people=1, faces=1,
                detections=PERSON1), source, caused_by=shot.id)
        c = threat.context_for(photo("bonsai"), Mode.AWAY, recent)
        self.assertEqual((c.people, c.faces, c.detections, c.vision_ok), (1, 1, PERSON1, True))
        self.assertFalse(threat.context_for(photo("detector"), Mode.AWAY, recent).vision_ok)
        bare = make_event(EventType.PHOTO_DESCRIBED, PhotoDescribedData(photo_path="captures/photos/x.jpg",
                                                                        description="Photo saved."),
                          Source.CAMERA, caused_by=shot.id)
        self.assertEqual(threat.rules(threat.context_for(bare, Mode.AWAY, recent))[0], C.UNKNOWN)
        self.assertTrue(threat.context_for(events.photo_described(shot.id), Mode.AWAY, recent).vision_ok)

    def test_code_checks_the_model(self):
        check = lambda cat, why, c: threat.check(cat, why, c)  # noqa: E731
        box = seen("One person holding a cardboard box at the door.")
        self.assertEqual(check(C.NOBODY, "empty", box)[0], C.DELIVERY)           # a person is detected
        self.assertEqual(check(C.UNKNOWN, "can't tell", box)[0], C.DELIVERY)     # unknown is code's alone
        self.assertEqual(check(C.DELIVERY, "because", box)[1], "1 person, holding a box, rang the doorbell.")
        self.assertEqual(check(C.DELIVERY, "Holding a parcel, rang the bell", box)[1],
                         "Holding a parcel, rang the bell")                      # grounded: kept
        self.assertNotIn("unknown", threat.TOOL["function"]["parameters"]["properties"]["category"]["enum"])

    def test_the_model_sees_the_detector(self):
        hood = seen("A person with the hood up.", cause="motion", doorbells=0, faces=0)
        self.assertIn("Object detector: 1 person\n", threat.prompt(hood))     # 0 faces: can't tell

    def test_review_hidden_face_that_rang_is_not_a_plain_visitor(self):
        for words in ("One person with the hood up, face not visible.", "A man, his face hidden by a scarf.",
                      "A person with a scarf over their face."):
            c = seen(words)
            self.assertEqual(threat.rules(c)[0], C.SUSPICIOUS, words)
            self.assertEqual(threat.check(C.SUSPICIOUS, "face not visible", c)[0], C.SUSPICIOUS, words)
        self.assertIsNone(threat.STRONG.search("face not visible"))     # no early confrontation for it

    def test_review_stayed_without_ringing_is_enforced(self):
        c = seen("A person with the hood up at the door.", cause="motion", doorbells=0)
        self.assertEqual(threat.check(C.VISITOR, "Visitor at the door", c)[0], C.SUSPICIOUS)

    def test_review_no_invented_facts(self):
        courier = seen("A courier in a uniform at the door.")
        self.assertEqual(threat.rules(courier), (C.DELIVERY, "1 person, a courier, rang the doorbell."))
        plain = seen("A woman in a red coat waving at the camera.")
        cat, why, note = threat.check(C.DELIVERY, "Likely a delivery", plain)
        self.assertEqual((cat, why), (C.VISITOR, "1 person, rang the doorbell."))
        self.assertIn("rules decided", note)
        self.assertNotIn("package", why)

    def test_review_grounded_means_seen_or_true_context(self):
        c = seen("One person holding a cardboard box at the door.")
        for good in ("Holding a box", "Rang the doorbell", "Cardboard box in hand"):
            self.assertTrue(threat.grounded(good, c), good)
        for bad in ("The door is closed", "1", "one", "It is night", "camera", "Probably a burglar at the door",
                    "Stayed without ringing"):
            self.assertFalse(threat.grounded(bad, c), bad)
        quiet = seen("A man in a hoodie at the door.", cause="motion", doorbells=0)
        self.assertTrue(threat.grounded("Stayed without ringing", quiet))
        self.assertTrue(threat.grounded("No doorbell, lingering while armed", quiet))
        self.assertFalse(threat.grounded("Rang the bell", quiet))       # no ring happened
        empty = threat.Context("An empty porch with a doormat. No one is visible.", Mode.AWAY, cause="motion")
        self.assertTrue(threat.grounded("No one visible.", empty))
        self.assertFalse(threat.grounded("No one visible.", c))         # a person is there

    def test_review_a_seen_face_is_a_person(self):
        self.assertEqual(threat.rules(seen("A face at the door.", people=0, faces=1))[0], C.VISITOR)

    def test_review_plurals_and_weapons_first(self):
        c = seen("x", detections=[{"label": "person", "count": 1, "confidence": 0.9},
                                  {"label": "backpack", "count": 2, "confidence": 0.8},
                                  {"label": "umbrella", "count": 1, "confidence": 0.7},
                                  {"label": "bench", "count": 1, "confidence": 0.6},
                                  {"label": "knife", "count": 2, "confidence": 0.3}])
        self.assertEqual(threat.facts(c), "1 person, 2 knives, 2 backpacks, umbrella")

    def test_review_faces_in_the_prompt_only_when_seen(self):
        self.assertNotIn("faces visible", threat.prompt(seen("A person far away.", faces=0)))
        self.assertIn("faces visible: 1", threat.prompt(seen("A person close up.", faces=1)))

    def test_unknown_is_a_valid_event_and_shown(self):
        photo = events.photo_described(None)
        ev = threat.event_for(threat.Assessment(C.UNKNOWN, "Couldn't analyse the photo.", "rules"), photo)
        self.assertEqual(validate_event(ev.to_dict()), [])
        from dashboard.state import THREAT_NAMES
        self.assertEqual(THREAT_NAMES[C.UNKNOWN], "Unknown")


class Rules(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(threat.rules(ctx(BOX))[0], C.DELIVERY)
        self.assertEqual(threat.rules(ctx(COAT))[0], C.VISITOR)
        self.assertEqual(threat.rules(ctx(EMPTY))[0], C.NOBODY)
        self.assertEqual(threat.rules(ctx("A grey car parked on the street."))[0], C.NOBODY)
        self.assertEqual(threat.rules(ctx(MASK))[0], C.SUSPICIOUS)
        # someone who didn't ring while the house is armed
        self.assertEqual(threat.rules(ctx(COAT, cause="motion", doorbells=0))[0], C.SUSPICIOUS)
        self.assertEqual(threat.rules(ctx(COAT, Mode.HOME, cause="motion", doorbells=0))[0], C.VISITOR)
        # owner 2026-11-15: stayed at the door without ringing while armed = suspicious,
        # even with a box (a courier who rings is a delivery)
        self.assertEqual(threat.rules(ctx(BOX, cause="motion", doorbells=0))[0], C.SUSPICIOUS)
        self.assertEqual(threat.rules(ctx(BOX, cause="doorbell", doorbells=1))[0], C.DELIVERY)


class Parsing(unittest.TestCase):
    def test_tool_call_json_and_bare_words(self):
        self.assertEqual(threat.parse(classify("delivery", "has a box")), ("delivery", "has a box"))
        self.assertEqual(threat.parse({"content": '{"category": "visitor", "reason": "rang"}'})[0], "visitor")
        self.assertEqual(threat.parse({"content": "Suspicious - hiding by the bins"})[0], "suspicious")
        for bad in ({"content": "I think it's fine"}, {"content": ""}, classify("burglar"),
                    {"tool_calls": "x"}, None, ["x"], tool_calls(("classify", "{bad"))):
            self.assertIsNone(threat.parse(bad)[0], bad)

    def test_code_checks(self):
        cat, _, note = threat.check(C.DELIVERY, "box", ctx(MASK))
        self.assertEqual((cat, note.startswith("code escalated")), (C.SUSPICIOUS, True))
        cat, _, note = threat.check(C.NOBODY, "", ctx(COAT))
        self.assertEqual(cat, C.VISITOR)
        self.assertIn("rejected nobody", note)
        cat, reason, note = threat.check(C.VISITOR, '{"x": 1}', ctx(COAT))
        self.assertEqual((cat, note), (C.VISITOR, "code wrote the reason"))
        self.assertNotIn("{", reason)                       # junk reason replaced by the rule's
        _, reason, _ = threat.check(C.VISITOR, "word " * 80, ctx(COAT))
        self.assertLessEqual(len(reason), threat.REASON_MAX)


class Assessor(unittest.TestCase):
    def test_bonsai_answer(self):
        client = FakeClient(classify("delivery", "Holding a box and rang."))
        a = threat.ThreatAssessor(client, "fast-model", log=lambda _m: None).assess(ctx(BOX, voices=1))
        self.assertEqual((a.category, a.via, a.model), (C.DELIVERY, "bonsai", "fast-model"))
        sent = client.calls[0]
        self.assertEqual(sent["tools"][0]["function"]["name"], "classify")
        self.assertIn("Voices heard: 1", sent["messages"][1]["content"])
        self.assertIn("Doorbell in the last few minutes: yes", sent["messages"][1]["content"])

    def test_failures_fall_back_to_rules(self):
        for reply in (RuntimeError("down"), {"content": "no idea"}, classify("alien")):
            a = threat.ThreatAssessor(FakeClient(reply), "m", log=lambda _m: None).assess(ctx(MASK))
            self.assertEqual((a.category, a.via), (C.SUSPICIOUS, "rules"), reply)
        a = threat.ThreatAssessor(None).assess(ctx(BOX))
        self.assertEqual((a.category, a.via), (C.DELIVERY, "rules"))

    def test_event_is_valid(self):
        photo = events.photo_described(None, BOX)
        e = threat.event_for(threat.Assessment(C.DELIVERY, "box", "bonsai", "m"), photo, voices=2)
        self.assertEqual(validate_event(e.to_dict()), [])
        self.assertEqual((e.caused_by, e.source, e.data["voices"]), (photo.id, Source.BRAIN, 2))


class Escalation(unittest.TestCase):
    def label(self, category):
        return make_event(EventType.THREAT_ASSESSED, {"category": category}, Source.BRAIN)

    def test_suspicious_while_armed_raises_normal_to_warning(self):
        s = ModeState(PASSPHRASE, mode=Mode.AWAY)
        out = s.on_event(self.label(C.SUSPICIOUS))
        self.assertEqual([(e.data["previous"], e.data["level"]) for e in out], [("normal", "warning")])
        self.assertEqual(validate_event(out[0].to_dict()), [])

    def test_never_lowers_and_never_acts_otherwise(self):
        home = ModeState(PASSPHRASE)
        self.assertEqual(home.on_event(self.label(C.SUSPICIOUS)), [])          # not armed
        s = ModeState(PASSPHRASE, mode=Mode.NIGHT, min_gap_s=0)
        s.on_event(events.motion()); s.end_grace(force=True); s.on_event(events.motion())   # alarm
        self.assertEqual(s.alert, AlertLevel.ALARM)
        for cat in C.ALL:
            self.assertEqual(s.on_event(self.label(cat)), [], cat)             # nothing lowers it
        self.assertEqual((s.mode, s.alert), (Mode.NIGHT, AlertLevel.ALARM))
        calm = ModeState(PASSPHRASE, mode=Mode.AWAY)
        for cat in (C.DELIVERY, C.VISITOR, C.NOBODY):
            self.assertEqual(calm.on_event(self.label(cat)), [], cat)


class Visits(unittest.TestCase):
    """Review: a photo from motion counted any doorbell in the last 3 minutes, so someone
    who stayed without ringing was "a visitor who rang" after an earlier visitor."""

    def test_motion_photo_counts_only_doorbells_after_that_motion(self):
        from datetime import datetime, timedelta
        from shared import make_action
        t0 = datetime.now().astimezone()

        def at(event, seconds):
            d = event.to_dict()
            d["timestamp"] = (t0 + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")
            return d
        earlier_bell = at(events.doorbell(), 0)
        walk = events.motion()
        shot = make_action(ActionType.TAKE_PHOTO, {"reason": "motion at night, no doorbell"},
                           Source.RULES, caused_by=walk.id)
        photo = events.photo_described(shot.id, "A man standing at the door.")
        recent = [earlier_bell, at(walk, 40), at(shot, 58)]
        ctx = threat.context_for(Event.from_dict(at(photo, 60)), Mode.NIGHT, recent)
        self.assertEqual((ctx.cause, ctx.doorbells), ("motion", 0))
        self.assertEqual(threat.rules(ctx)[0], C.SUSPICIOUS)
        # a box doesn't change that: stayed without ringing while armed
        ctx.description = "A courier holding a cardboard box at the door."
        self.assertEqual(threat.rules(ctx)[0], C.SUSPICIOUS)


class PipelineEffects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def pipeline(self, mode, assessor_reply=None, brain_reply=None):
        brain = make_brain(brain_reply)
        assessor = threat.ThreatAssessor(FakeClient(assessor_reply) if assessor_reply else None,
                                         "fast", log=lambda _m: None)
        self.emitted = []
        return Pipeline(brain, ModeState(PASSPHRASE, mode=mode), EventLog(self.dir / "e.jsonl", PASSPHRASE),
                        on_action=self.emitted.append, say=lambda _m: None, assessor=assessor)

    def doorbell_photo(self, p, description):
        photo = next(a for a in p.handle(events.doorbell()).actions if a.type == ActionType.TAKE_PHOTO)
        return p.handle(events.photo_described(photo.id, description))

    def test_delivery_gets_dropoff_instructions_and_is_logged(self):
        p = self.pipeline(Mode.AWAY)
        result = self.doorbell_photo(p, BOX)
        labels = [e for e in result.events if e.type == EventType.THREAT_ASSESSED]
        self.assertEqual(labels[0].data["category"], C.DELIVERY)
        # the photo is never answered: drop-off words come when the visitor speaks
        self.assertEqual([a for a in result.actions if a.type == ActionType.SPEAK], [])
        self.assertIn("threat_assessed", p.log.path.read_text(encoding="utf-8"))

    def test_bonsai_label_is_used(self):
        p = self.pipeline(Mode.AWAY, assessor_reply=classify("visitor", "A neighbour waving."))
        result = self.doorbell_photo(p, COAT)
        label = next(e for e in result.events if e.type == EventType.THREAT_ASSESSED)
        self.assertEqual((label.data["category"], label.data["via"]), (C.VISITOR, "bonsai"))
        self.assertEqual([a for a in result.actions if a.type == ActionType.SPEAK], [])   # no second greeting

    def test_suspicious_escalates_and_confronts_early(self):
        p = self.pipeline(Mode.AWAY, brain_reply={"role": "assistant", "content": "Step away from the door."})
        result = self.doorbell_photo(p, MASK)
        p.wait_background(5)
        self.assertEqual(p.modes.alert, AlertLevel.WARNING)
        self.assertIn("alert_changed", [e.type for e in result.events])
        said = [a.args.get("text") for a in self.emitted if a.type == ActionType.SPEAK]
        self.assertIn("Step away from the door.", said)                # the early confrontation
        self.assertNotIn("Would you like to leave a message?", said)  # no friendly greeting

    def test_nobody_says_nothing(self):
        p = self.pipeline(Mode.AWAY)
        result = self.doorbell_photo(p, EMPTY)
        self.assertEqual([a for a in result.actions if a.type == ActionType.SPEAK], [])

    def test_label_never_changes_mode_lights_or_disarms(self):
        p = self.pipeline(Mode.NIGHT)
        p.modes.min_gap_s = 0
        p.handle(events.motion())
        p.tick(force=True)
        p.handle(events.motion())
        before = (p.modes.mode, p.modes.alert)
        for description in (BOX, COAT, EMPTY, MASK):
            photo = next(a for a in p.handle(events.doorbell()).actions if a.type == ActionType.TAKE_PHOTO)
            p.handle(events.photo_described(photo.id, description))
        self.assertEqual((p.modes.mode, p.modes.alert), before)
        offs = [a for a in self.emitted if a.type in (ActionType.OUTLET_OFF, ActionType.SET_MODE)]
        self.assertEqual(offs, [])
        p.wait_background(5)     # the confrontation thread is done before the temp dir goes


class Shown(unittest.TestCase):
    def test_dashboard_label_and_activity(self):
        bell = events.doorbell()
        shot = make_action(ActionType.TAKE_PHOTO, {"reason": "doorbell while away"}, Source.RULES,
                           caused_by=bell.id)
        photo = events.photo_described(shot.id, BOX)
        label = make_event(EventType.THREAT_ASSESSED, {"category": "delivery", "reason": "Has a box.",
                                                       "via": "bonsai"}, Source.BRAIN, caused_by=photo.id)
        s = build_state([e.to_dict() for e in (bell, shot, photo, label)], "away", "normal")
        self.assertEqual(s["photo"]["threat"]["name"], "Delivery")
        self.assertEqual(s["photo"]["threat"]["reason"], "Has a box.")
        self.assertNotIn("id", s["photo"])
        self.assertEqual([f["text"] for f in s["feed"]], [BOX, "Doorbell pressed"])
        self.assertEqual(s["feed"][0]["detail"], "Photo · doorbell while away · Delivery")
        json.dumps(s)

    def test_summary_mentions_labels(self):
        log = [make_event(EventType.THREAT_ASSESSED, {"category": c}, Source.BRAIN).to_dict()
               for c in ("delivery", "suspicious", "nobody")]
        h = collect(log)
        self.assertIn("The camera judged: 1 delivery, 1 suspicious.", facts(h))
        self.assertIn("1 suspicious visit", spoken_list(h))


if __name__ == "__main__":
    unittest.main()
