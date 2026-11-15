"""The whole path: modes -> log -> baseline -> brain, with fakes for Bonsai."""
import json
import tempfile
import unittest
from pathlib import Path

from shared import (ActionType, AlertLevel, EventType, Mode, Source,
                    validate_action, validate_event)

from brain.pipeline import Pipeline
from modes import MotionOutcome
from rules import RuleStore
from rules.baseline import DOORBELL_GREETING

FILLER = "One moment, please."  # gone (owner, Mac test): checked that it never comes back
from log import EventLog
from modes import ModeState
from sim import events

from .helpers import PASSPHRASE, make_brain, tool_calls


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class Recorder:
    """Collects everything the pipeline emits, in order, and what the model saw."""

    def __init__(self):
        self.seen = []

    def event(self, e):
        self.seen.append(("event", e.type, e))

    def action(self, a):
        self.seen.append(("action", a.type, a))


class SpyClient:
    """Fake Bonsai that records what had already been emitted when it was called."""

    def __init__(self, recorder, reply):
        self.recorder, self.reply, self.calls = recorder, reply, []

    def chat(self, messages, tools=None):
        self.calls.append({"emitted_before": list(self.recorder.seen), "messages": messages})
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self.tmp.name) / "events.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def pipeline(self, reply=None, mode=Mode.HOME, use_client=True):
        rec = Recorder()
        brain = make_brain()
        if use_client:
            brain.client = SpyClient(rec, reply if reply is not None else {"content": ""})
        self.clock = Clock()
        modes = ModeState(PASSPHRASE, 60, mode, self.clock, doorbell_settle_s=0)
        p = Pipeline(brain, modes, EventLog(self.log_path, PASSPHRASE),
                     on_event=rec.event, on_action=rec.action, say=lambda _m: None)
        return p, rec, brain.client

    def logged(self):
        return [json.loads(l) for l in self.log_path.read_text(encoding="utf-8").splitlines()]

    # ---- baseline first ----------------------------------------------------

    def test_motion_is_plain_code_and_never_waits_for_bonsai(self):
        p, rec, client = self.pipeline(tool_calls(("speak", {"text": "Hello visitor!"})), Mode.NIGHT)
        result = p.handle(events.motion())
        self.assertEqual([(k, t) for k, t, _ in rec.seen], [("event", "motion"), ("action", "outlet_on")])
        self.assertEqual(p.modes.alert, AlertLevel.NORMAL)                # grace: porch light only
        self.clock.t += 15
        warned = p.tick()                                                  # no doorbell: warning
        self.assertEqual([(k, t) for k, t, _ in rec.seen][2:],
                         [("event", "alert_changed"), ("action", "take_photo"), ("action", "speak")])
        self.assertEqual([a.source for a in result.actions + warned.actions], [Source.RULES] * 3)
        self.assertEqual(p.modes.alert, AlertLevel.WARNING)
        self.assertEqual(client.calls, [])

    def test_walk_up_then_ring_is_a_visitor(self):
        """Owner 2026-11-15: motion, then the doorbell within the grace period: no warning,
        the normal doorbell greeting, and their shuffling about stays quiet."""
        p, _, _ = self.pipeline(None, Mode.AWAY, use_client=False)
        walk = [a.type for a in p.handle(events.motion()).actions]
        self.assertEqual(walk, [ActionType.OUTLET_ON])
        self.clock.t += 6
        ring = p.handle(events.doorbell()).actions
        self.assertIn(DOORBELL_GREETING, [a.args.get("text") for a in ring])
        self.clock.t += 20
        self.assertIsNone(p.tick())                                        # cancelled by the doorbell
        self.clock.t += 10
        again = p.handle(events.motion())
        self.assertEqual([a.type for a in again.actions], [ActionType.OUTLET_ON])
        self.assertEqual(p.modes.last_outcome, MotionOutcome.QUIET)       # not a new grace period
        self.clock.t += 30
        self.assertIsNone(p.tick())
        self.assertEqual(p.modes.alert, AlertLevel.NORMAL)

    def test_a_rule_photo_never_warns_a_visitor_before_they_ring(self):
        """Review: "when there's motion at night, take a photo" took a photo in the grace
        period, its label (no doorbell yet) said suspicious, and the visitor was warned."""
        p, _, _ = self.pipeline(None, Mode.NIGHT, use_client=False)
        p.rules = RuleStore(Path(self.tmp.name) / "rules.json")
        p.rules.add({"when": {"event": "motion", "modes": ["night"]},
                     "then": [{"type": "take_photo", "args": {"reason": "rule"}},
                              {"type": "outlet_on", "args": {"outlet": "living_room_lamp"}}]})
        walk = p.handle(events.motion())
        self.assertEqual([a.type for a in walk.actions], [ActionType.OUTLET_ON, ActionType.OUTLET_ON])
        self.clock.t += 5
        p.handle(events.doorbell())
        self.assertEqual(p.modes.alert, AlertLevel.NORMAL)
        self.clock.t += 30
        self.assertIsNone(p.tick())

    def test_the_timer_waits_again_if_it_woke_early(self):
        p, _, _ = self.pipeline(None, Mode.AWAY, use_client=False)
        restarted = []
        p._start_grace_timer = lambda delay=None: restarted.append(delay)
        p.handle(events.motion())
        self.assertEqual(restarted, [None])
        self.clock.t += 10
        p._grace_timer_fired()                                             # 5 s early
        self.assertEqual(restarted, [None, 5.0])
        self.assertEqual(p.modes.alert, AlertLevel.NORMAL)
        self.clock.t += 5
        p._grace_timer_fired()
        self.assertEqual(p.modes.alert, AlertLevel.WARNING)

    def test_a_missed_timer_is_caught_by_the_next_event(self):
        p, _, _ = self.pipeline(None, Mode.AWAY, use_client=False)
        p._start_grace_timer = lambda delay=None: None                     # the timer never fires
        p.handle(events.motion())
        self.clock.t += 20
        p.handle(events.motion())
        self.assertEqual(p.modes.alert, AlertLevel.WARNING)

    def test_walk_up_no_ring_then_lingering(self):
        p, _, _ = self.pipeline(None, Mode.AWAY, use_client=False)
        p.handle(events.motion())
        self.clock.t += 15
        warned = p.tick()
        self.assertIn("Please be aware this property is monitored and you are on camera.",
                      [a.args.get("text") for a in warned.actions])
        photo = next(a for a in warned.actions if a.type == ActionType.TAKE_PHOTO)
        self.assertIn("no doorbell", photo.args["reason"])
        label = p.handle(events.photo_described(photo.id, "A man standing at the door."))
        tag = next(e for e in label.events if e.type == EventType.THREAT_ASSESSED)
        self.assertEqual(tag.data["category"], "suspicious")                # stayed without ringing
        self.clock.t += 9
        alarm = p.handle(events.motion())
        self.assertEqual([e.data["level"] for e in alarm.events if e.type == EventType.ALERT_CHANGED],
                         ["alarm"])

    def test_brain_repeats_are_not_duplicated(self):
        p, _, _ = self.pipeline(tool_calls(("speak", {"text": "Thank you."}), ("speak", {"text": "Thank you."}),
                                           ("save_message", {"text": "Package for Sam."})), Mode.AWAY)
        texts = [a.args.get("text") for a in p.handle(events.speech("Package for Sam.")).actions]
        self.assertEqual(texts.count("Thank you."), 1)

    def test_baseline_survives_a_dead_or_crashing_brain(self):
        for reply in (RuntimeError("down"), {"content": "garbage"}):
            p, _, _ = self.pipeline(reply, Mode.AWAY)
            self.assertIn(ActionType.TAKE_PHOTO, [a.type for a in p.handle(events.doorbell()).actions])
        p, _, _ = self.pipeline(None, Mode.NIGHT)
        p.brain.decide = lambda *a, **k: 1 / 0
        types = [a.type for a in p.handle(events.motion()).actions]
        self.assertEqual(types, [ActionType.OUTLET_ON])
        self.clock.t += 15
        self.assertEqual([a.type for a in p.tick().actions], [ActionType.TAKE_PHOTO, ActionType.SPEAK])

    def test_second_motion_within_window_raises_alarm(self):
        p, _, _ = self.pipeline(None, Mode.AWAY, use_client=False)
        p.handle(events.motion())
        self.clock.t += 15
        p.tick()
        self.clock.t += 20
        result = p.handle(events.motion())
        alerts = [e.data["level"] for e in result.events if e.type == EventType.ALERT_CHANGED]
        self.assertEqual(alerts, ["alarm"])

    # ---- doorbell: one fixed greeting at the ring, then only replies -------------

    def test_visitor_speech_has_no_filler(self):
        p, rec, client = self.pipeline(tool_calls(("save_message", {"text": "Package for Sam."})),
                                       Mode.AWAY)
        result = p.handle(events.speech("Package for Sam."))
        self.assertEqual(result.actions[0].type, ActionType.SAVE_MESSAGE)
        # The model forgot to answer; plain code thanks the visitor.
        self.assertEqual(result.actions[1].type, ActionType.SPEAK)
        self.assertNotIn(FILLER, [a.args.get("text") for a in result.actions])

    def test_doorbell_greets_at_once_and_the_photo_gets_no_second_greeting(self):
        p, _, client = self.pipeline(tool_calls(("speak", {"text": "Hello!"})), Mode.AWAY)
        ring = p.handle(events.doorbell()).actions
        self.assertEqual([a.args.get("text") for a in ring if a.type == ActionType.SPEAK], [DOORBELL_GREETING])
        self.assertEqual(ring[0].type, ActionType.SPEAK)                   # before the photo
        bell_photo = next(a for a in ring if a.type == ActionType.TAKE_PHOTO)
        for description in (events.FAKE_DESCRIPTION, "A woman in a red coat waving at the camera."):
            result = p.handle(events.photo_described(bell_photo.id, description))
            self.assertEqual([a for a in result.actions if a.type == ActionType.SPEAK], [])
            self.assertIn(EventType.THREAT_ASSESSED, [e.type for e in result.events])
        self.assertEqual(client.calls, [])                                 # Bonsai only replies to speech
        motion = p.handle(events.motion()).actions                         # the visitor moves about
        self.assertEqual([a.type for a in motion], [ActionType.OUTLET_ON])  # never warned
        self.assertEqual(p.modes.last_outcome, MotionOutcome.QUIET)

    def test_motion_photo_traced_even_after_a_busy_log(self):
        p, _, client = self.pipeline(tool_calls(("speak", {"text": "Hello visitor!"})), Mode.AWAY)
        p.handle(events.motion())
        self.clock.t += 15
        photo = next(a for a in p.tick().actions if a.type == ActionType.TAKE_PHOTO)
        for i in range(12):  # push the motion event past the prompt's 10-line window
            p.log.write(events.speech(f"filler {i}"))
        calls = len(client.calls)
        self.assertEqual(p.handle(events.photo_described(photo.id)).actions, [])
        self.assertEqual(len(client.calls), calls)  # answered in code, no greeting

    def test_second_press_and_alerts_get_no_friendly_greeting(self):
        p, _, _ = self.pipeline(None, Mode.AWAY, use_client=False)
        first = [a.args.get("text") for a in p.handle(events.doorbell()).actions]
        again = [a.args.get("text") for a in p.handle(events.doorbell()).actions]
        self.assertIn(DOORBELL_GREETING, first)
        self.assertNotIn(DOORBELL_GREETING, again)              # within 60 s: once is enough
        self.log_path = Path(self.tmp.name) / "second.jsonl"     # a fresh log: no greeting in it
        p, _, _ = self.pipeline(None, Mode.AWAY, use_client=False)
        p.handle(events.motion())
        self.clock.t += 15
        p.tick()                                                 # warning: nobody rang
        ring = p.handle(events.doorbell()).actions
        self.assertNotIn(DOORBELL_GREETING, [a.args.get("text") for a in ring])
        self.assertIn(ActionType.TAKE_PHOTO, [a.type for a in ring])   # the photo still happens

    def test_no_filler_anywhere(self):
        cases = [(Mode.HOME, events.speech("Turn on the porch light"), True),   # owner at home
                 (Mode.AWAY, events.speech("Package for Sam."), False),        # offline
                 (Mode.AWAY, events.speech("Package for Sam."), True),
                 (Mode.AWAY, events.doorbell(), True),
                 (Mode.AWAY, events.speech("Faraday, disarm"), True)]          # answered in code
        for mode, event, use_client in cases:
            p, _, _ = self.pipeline(tool_calls(("speak", {"text": "ok"})), mode, use_client)
            texts = [a.args.get("text") for a in p.handle(event).actions]
            self.assertNotIn(FILLER, texts, event.data)

    # ---- passphrase and mode changes ---------------------------------------

    def test_spoken_passphrase_disarms_and_never_leaks(self):
        p, rec, client = self.pipeline(tool_calls(("speak", {"text": "hi"})), Mode.NIGHT)
        p.handle(events.motion())
        self.clock.t += 15
        p.tick()
        result = p.handle(events.speech("Okay, Blue Pelican 17."))
        self.assertEqual([e.type for e in result.events],
                         [EventType.SPEECH_HEARD, EventType.MODE_CHANGED, EventType.ALERT_CHANGED])
        self.assertEqual(result.events[0].data["text"], "Okay, [passphrase].")
        self.assertEqual(result.events[1].caused_by, result.events[0].id)
        self.assertEqual(result.events[2].caused_by, result.events[0].id)
        self.assertEqual((p.modes.mode, p.modes.alert), (Mode.HOME, AlertLevel.NORMAL))
        everything = (self.log_path.read_text(encoding="utf-8")
                      + json.dumps([c["messages"] for c in client.calls])
                      + repr([x for _, _, x in rec.seen])).lower()
        self.assertNotIn("pelican", everything)

    def test_typed_passphrase(self):
        p, _, _ = self.pipeline(None, Mode.AWAY, use_client=False)
        self.assertIsNone(p.owner_passphrase("wrong"))
        self.assertFalse(self.log_path.exists())  # a wrong attempt is never logged
        result = p.owner_passphrase("blue pelican seventeen")
        self.assertEqual(result.events[0].data["changed_by"], "passphrase")
        self.assertNotIn("pelican", self.log_path.read_text(encoding="utf-8"))

    def test_brain_set_mode_goes_through_modes(self):
        p, _, _ = self.pipeline(tool_calls(("set_mode", {"mode": "away"})), Mode.HOME)
        result = p.handle(events.speech("I'm heading out."))
        changes = [e for e in result.events if e.type == EventType.MODE_CHANGED]
        self.assertEqual(changes[0].data["changed_by"], "brain")
        self.assertEqual(p.modes.mode, Mode.AWAY)

    def test_owner_mode_keys(self):
        p, _, _ = self.pipeline(None, Mode.HOME, use_client=False)
        self.assertIsNotNone(p.owner_set_mode(Mode.NIGHT))
        self.assertIsNone(p.owner_set_mode(Mode.NIGHT))
        self.assertIsNone(p.owner_set_mode(Mode.HOME))
        self.assertEqual(p.modes.mode, Mode.NIGHT)

    def test_everything_logged_is_valid(self):
        p, _, _ = self.pipeline(tool_calls(("speak", {"text": "Hello."})), Mode.HOME)
        p.start()
        p.handle(events.speech("I'm heading out"))
        p.owner_set_mode(Mode.NIGHT)
        p.handle(events.doorbell())
        p.handle(events.motion())
        p.handle(events.speech("blue pelican seventeen"))
        rows = self.logged()
        self.assertGreater(len(rows), 8)
        for row in rows:
            check = validate_event if row["kind"] == "event" else validate_action
            self.assertEqual(check(row), [], row)


if __name__ == "__main__":
    unittest.main()
