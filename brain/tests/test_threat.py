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
        self.assertEqual((cat, note), (C.VISITOR, ""))
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
