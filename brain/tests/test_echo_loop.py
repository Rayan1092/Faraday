"""Owner, Mac: after a courier said "I've got a package", Faraday repeated the side-door
line in a loop. Three fixes: the mic stays deaf for a tail after every line and drops an
echo of what Faraday just said; the drop-off line is said once per visit; closing words
or silence after the delivery reply end the visit."""
import time
import unittest

from shared import ActionType, Mode, Source, make_action

from brain import lines
from brain import fallback as fb
from hardware.audio import MicGate, Recorder
from integration.voice import Voice
from rules import instructions as oi
from sim import events

from .helpers import make_brain, tool_calls

SIDE = oi.parse_instruction("If a delivery comes, ask them to leave it by the side door.")
SIDE_LINE = "Please leave it by the side door. I'll let the owner know it arrived."


class _Store:
    def __init__(self, items):
        self.items = items

    def relevant(self, label=""):
        return self.items


def said(*texts):
    return [make_action(ActionType.SPEAK, {"text": t}, Source.BRAIN).to_dict() for t in texts]


def spoken(d):
    return [a.args["text"] for a in d.actions if a.type == ActionType.SPEAK]


class DeliveryLineOnce(unittest.TestCase):
    def brain(self, reply):
        b = make_brain(tool_calls(("speak", {"text": reply})))
        b.instructions = _Store([dict(SIDE, id="ins_1")])
        return b

    def test_okay_thank_you_gets_at_most_one_reply(self):
        b = self.brain("Please leave it by the side door.")
        first = b.decide(events.speech("okay thank you", "visitor"), Mode.AWAY, said(SIDE_LINE))
        self.assertEqual(spoken(first), [lines.YOURE_WELCOME])
        again = b.decide(events.speech("okay thank you", "visitor"), Mode.AWAY,
                         said(SIDE_LINE, lines.YOURE_WELCOME))
        self.assertEqual(spoken(again), [])

    def test_the_side_door_line_is_never_said_twice(self):
        for reply in ("Please leave it by the side door.", "You can leave the package by the side door."):
            b = self.brain(reply)
            d = b.decide(events.speech("I've got a package", "visitor"), Mode.AWAY, said(SIDE_LINE))
            self.assertFalse(any(fb.mentions_dropoff(t) or "arrived" in t for t in spoken(d)), spoken(d))

    def test_closing_words(self):
        for text in ("okay", "thanks", "thank you", "alright", "bye", "got it", "okay thank you", "Thanks, bye!"):
            self.assertTrue(fb.is_closing(text), text)
        for text in ("I've got a package", "okay where should I leave it?", "thanks, can I leave it here?"):
            self.assertFalse(fb.is_closing(text), text)


def voice():
    v = Voice.__new__(Voice)          # only the parts these checks use
    v._said_recent = __import__("collections").deque(maxlen=20)
    v._after_delivery = v._visit_closed = False
    v._visit_until = 123.0
    v.out = lambda _m: None
    return v


class Echo(unittest.TestCase):
    def test_own_words_are_an_echo(self):
        v = voice()
        v._said_recent.append((time.monotonic(), SIDE_LINE))
        self.assertTrue(v.is_echo("please leave it by the side door"))
        self.assertTrue(v.is_echo("leave it by the side door I'll let the owner know"))
        self.assertFalse(v.is_echo("okay thank you"))
        self.assertFalse(v.is_echo("please leave it by the side door", now=time.monotonic() + 11))

    def test_mic_deaf_for_a_tail_after_each_line(self):
        gate = MicGate()
        self.assertTrue(gate.quiet_for(Recorder.ECHO_TAIL_S))     # nothing played yet: no tail
        gate.mute()
        gate.unmute()
        self.assertFalse(gate.muted)                               # the gate itself opens at once
        self.assertFalse(gate.quiet_for(Recorder.ECHO_TAIL_S))    # but the recorder waits 1 s
        self.assertEqual(Recorder.ECHO_TAIL_S, 1.0)


class VisitEnds(unittest.TestCase):
    def test_closing_words_or_silence_end_the_visit(self):
        v = voice()
        v.on_said = Voice.on_said.__get__(v)
        v._pending_doorbell = False
        v._visitor_window = lambda reason: self.fail("no visitor window after the visit ended")
        v.listen_once = lambda *a, **k: self.fail("no window after the visit ended")
        v._after_delivery = True
        v.end_visit("the visitor said goodbye")
        self.assertTrue(v._visit_closed)
        self.assertEqual(v._visit_until, 0.0)
        v.on_said("Is there anything else?")      # a question would open a visitor window: not now

    def test_delivery_reply_starts_the_wind_down_and_a_ring_resets(self):
        v = voice()
        v._pending_doorbell = False
        v.on_said = Voice.on_said.__get__(v)
        v.on_said(SIDE_LINE)
        self.assertTrue(v._after_delivery)
        v.end_visit("silence after the delivery reply")
        v._arm_doorbell = lambda _s: None
        v.mode = lambda: Mode.AWAY
        v._visit_until, v.visitor_s, v._last_ring = 0.0, 60.0, 0.0
        Voice.on_doorbell(v)
        self.assertFalse(v._visit_closed)


if __name__ == "__main__":
    unittest.main()
