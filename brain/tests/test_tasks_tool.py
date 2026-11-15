"""One standing-task tool (owner, 2026-11-15): remember / update_task / remove_task /
list_tasks, chosen by Bonsai and checked by code, with no preset per phrasing."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from shared import ActionType, EventType, Mode, Outlet

from brain import lines, owner
from brain import tasks as planner
from brain.core import Brain
from brain.pipeline import Pipeline
from log import EventLog
from modes import ModeState
from rules import tasks as T
from rules.tasks import TaskStore
from sim import events

from .helpers import FakeClient, tool_calls

SECRET = "blue pelican seventeen"


def route(intent, **slots):
    return tool_calls(("route", dict(intent=intent, **slots)))


class Check(unittest.TestCase):
    """Code's say on one tool call, for the owner's examples."""

    def test_reminder_when_i_get_home(self):
        p = planner.check("remember", {"trigger": "home", "action": "say_owner", "text": "take out the trash"},
                          "Remind me to take out the trash when I get home.", [])
        self.assertEqual(p.op, planner.REMEMBER)
        self.assertEqual((p.task["trigger"], p.task["action"], p.task["repeat"]), (T.HOME, T.SAY_OWNER, T.ONCE))
        self.assertEqual(T.confirmation(p.task), "Okay. When you get home, I'll remind you to take out the trash.")

    def test_words_for_a_delivery(self):
        p = planner.check("remember", {"trigger": "delivery", "action": "say_visitor", "text": "use the main door"},
                          "If a delivery comes, tell them to use the main door.", [])
        self.assertEqual(T.confirmation(p.task), "Okay. If a delivery comes, I'll tell them to use the main door.")

    def test_the_2b_wrote_leave_for_put(self):
        """Mac retest 3: "I wanted to tell him to put the delivery box next to the side door"
        was refused because Bonsai wrote "leave"."""
        said = "If a delivery comes, I wanted to tell him to put the delivery box next to the side door."
        p = planner.check("remember", {"trigger": "delivery", "action": "say_visitor",
                                       "text": "leave the delivery box next to the side door"}, said, [])
        self.assertEqual(p.op, planner.REMEMBER, p.note)

    def test_lights_at_night(self):
        p = planner.check("remember", {"trigger": "doorbell", "action": "lights_on", "outlets": ["porch_light"]},
                          "Whenever someone rings at night, turn on both lights.", [])
        self.assertEqual(p.task["outlets"], [Outlet.PORCH_LIGHT, Outlet.LIVING_ROOM_LAMP])   # "both" is the words'
        self.assertTrue(p.task["night"])
        self.assertEqual(T.confirmation(p.task), "Okay. Whenever someone rings at night, I'll turn on the porch "
                                                 "light and the living room lamp.")

    def test_clock_time_mode(self):
        p = planner.check("remember", {"trigger": "time", "time": "09:00", "action": "set_mode", "to_mode": "night"},
                          "At 9 PM, switch to night mode.", [])
        self.assertEqual(p.task["time"], "21:00")          # the owner's time, not Bonsai's
        self.assertEqual(T.confirmation(p.task), "Okay. At 9 PM, I'll switch to night mode.")

    def test_actually_make_that_the_side_door(self):
        old = T.normalize({"trigger": "delivery", "action": "say_visitor", "text": "use the main door"})
        old.update(id="task_1")
        for args in ({"task_id": "task_1", "text": "use the side door"}, {"text": "the side door"}):
            p = planner.check("update_task", args, "Actually, make that the side door.", [old])
            self.assertEqual((p.op, p.task["text"]), (planner.UPDATE, "use the side door"), args)
        p = planner.check("remember", {"trigger": "delivery", "action": "say_visitor", "text": "the side door"},
                          "Actually, make that the side door.", [old])
        self.assertEqual((p.op, p.task["text"]), (planner.UPDATE, "use the side door"))
        self.assertEqual(T.updated_line(p.task), "Okay. Now if a delivery comes, I'll tell them to use the side door.")

    def test_never_disarm_and_never_invent(self):
        no = planner.check("remember", {"trigger": "time", "action": "set_mode", "to_mode": "home"},
                           "At 7 AM, disarm the house.", [])
        self.assertEqual(no.op, "")
        made_up = planner.check("remember", {"trigger": "doorbell", "action": "say_visitor",
                                             "text": "the code is 1234"}, "If someone rings, greet them.", [])
        self.assertEqual(made_up.op, "")
        no_time = planner.check("remember", {"trigger": "time", "time": "21:00", "action": "say_owner",
                                             "text": "lock up"}, "Remind me to lock up.", [])
        self.assertEqual(no_time.op, "")

    def test_remove_and_list(self):
        t = T.normalize({"trigger": "home", "action": "say_owner", "text": "take out the trash"})
        t.update(id="task_9")
        p = planner.check("remove_task", {"task_id": "nope"}, "Cancel the reminder.", [t])
        self.assertEqual((p.op, p.task_id), (planner.REMOVE, "task_9"))
        self.assertEqual(T.removed_line(t), "Okay. I won't remind you to take out the trash when you get home.")
        self.assertEqual(planner.check("remove_task", {"task_id": "all"}, "Cancel it.", [t]).op, "")
        self.assertEqual(planner.check("list_tasks", {}, "What are my tasks?", [t]).op, planner.LIST)


class Routing(unittest.TestCase):
    """Bench, first run: these never reached the task tool."""

    def test_later_is_a_task_not_now(self):
        for text in ("Any courier who shows up should use the main door.",
                     "At night, if the doorbell goes, switch on the porch light and the lamp.",
                     "Put the house in night mode at 10 tonight.", "At 9 PM, switch to night mode.",
                     "Actually, make that the side door.", "What tasks do I have?",
                     "When I get back, remind me to call the plumber."):
            self.assertEqual(owner.strong(text, tasks=True), owner.TASK, text)
        self.assertEqual(owner.strong("Turn on the porch light.", tasks=True), owner.LIGHT)
        self.assertEqual(owner.strong("I'm heading out.", tasks=True), owner.ARM_AWAY)

    def test_look_up_is_not_a_look(self):
        self.assertFalse(owner.backed(owner.LOOK, "Can you look up the news for me?"))
        self.assertTrue(owner.backed(owner.LOOK, "Look outside."))

    def test_cancel_and_tell_replaces(self):
        old = T.normalize({"trigger": "delivery", "action": "say_visitor", "text": "leave the package by the side door"})
        old.update(id="task_1")
        p = planner.check("remove_task", {"task_id": "task_1"}, "Actually, cancel the instruction before and tell "
                          "the delivery man to leave the package by the main door.", [old])
        self.assertEqual((p.op, p.task_id, p.task["text"]),
                         (planner.UPDATE, "task_1", "leave the package by the main door"))


class Words(unittest.TestCase):
    def test_times(self):
        for said, want in (("at 9 PM", "21:00"), ("at 9pm", "21:00"), ("at 7:30 am", "07:30"),
                           ("at 21:15", "21:15"), ("at noon", "12:00"), ("at midnight", "00:00"),
                           ("at 9 p.m.", "21:00"), ("no time here", None)):
            self.assertEqual(planner.words_time(said), want, said)

    def test_out_of_scope_topic(self):
        self.assertEqual(owner.topic_for("What's the weather tomorrow?", "check the weather"), "check the weather")
        self.assertEqual(owner.topic_for("What's the weather tomorrow?", None), "check the weather")
        self.assertEqual(owner.topic_for("Text my mom that I'm late", "text my mom"), "text your mom")
        self.assertEqual(owner.topic_for("Can you google pizza places?", "find pizza places"), "find pizza places")
        self.assertEqual(owner.topic_for("I'm sorry.", "apologize"), "")
        self.assertEqual(lines.cant_do("check the weather"),
                         "Sorry, I can't check the weather. I work offline, with no internet, but I can watch "
                         "the door, take messages, switch the lights and remember tasks for you.")
        self.assertEqual(lines.cant_do(""), lines.CANT_DO_ANY)


class Flow(unittest.TestCase):
    """The examples end to end: the 2B routes, the smart Bonsai picks the tool, code
    stores it and Faraday says back what it understood."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.said = []
        self.router = owner.OwnerRouter(FakeClient(route("task")), "fast", log=lambda _m: None)
        self.router.tasks_mode = True
        self.p = Pipeline(Brain(None, SECRET, log=lambda _m: None), ModeState(SECRET),
                          EventLog(root / "events.jsonl", SECRET), say=lambda _m: None, router=self.router,
                          tasks=TaskStore(root / "tasks.json"))
        self.addCleanup(self.tmp.cleanup)

    def tell(self, text, tool=None, args=None, routed="task"):
        self.router.client = FakeClient(route(routed))
        self.p.task_client = FakeClient(tool_calls((tool, args or {}))) if tool else None
        r = self.p.handle(events.speech(text, "owner"))
        return [a.args["text"] for a in r.actions if a.type == ActionType.SPEAK]

    def test_reminder_said_right_after_welcome_home(self):
        said = self.tell("Remind me to take out the trash when I get home.", "remember",
                         {"trigger": "home", "action": "say_owner", "text": "take out the trash"})
        self.assertEqual(said, ["Okay. When you get home, I'll remind you to take out the trash."])
        self.p.owner_set_mode(Mode.AWAY)
        r = self.p.owner_passphrase(SECRET)
        spoken = [a.args["text"] for a in r.actions if a.type == ActionType.SPEAK]
        self.assertEqual(spoken[-2:], [lines.DISARMED, "Reminder: take out the trash."])
        self.assertEqual(self.p.tasks.all(), [])            # said once

    def test_delivery_words_then_corrected(self):
        self.tell("If a delivery comes, tell them to use the main door.", "remember",
                  {"trigger": "delivery", "action": "say_visitor", "text": "use the main door"})
        said = self.tell("Actually, make that the side door.", "update_task", {"text": "the side door"})
        self.assertEqual(said, ["Okay. Now if a delivery comes, I'll tell them to use the side door."])
        self.assertEqual([i["request"] for i in self.p.instructions.relevant("delivery")], ["use the side door"])

    def test_cancel_and_replace_in_one_breath(self):
        """Mac retest 3: "cancel the instruction before and tell the delivery man to leave
        the package by the main door" only removed it."""
        self.tell("If a delivery comes, tell him to leave the package by the side door.", "remember",
                  {"trigger": "delivery", "action": "say_visitor", "text": "leave the package by the side door"})
        said = self.tell("Actually, I want you to cancel the instruction before and tell the delivery man to "
                         "leave the package by the main door.", "update_task",
                         {"text": "leave the package by the main door"})
        self.assertEqual(said, ["Okay. Now if a delivery comes, I'll tell them to leave the package by the main door."])
        self.assertEqual(len(self.p.tasks.all()), 1)

    def test_lights_when_someone_rings_at_night(self):
        self.tell("Whenever someone rings at night, turn on both lights.", "remember",
                  {"trigger": "doorbell", "night": True, "action": "lights_on",
                   "outlets": ["porch_light", "living_room_lamp"]})
        self.p.owner_set_mode(Mode.NIGHT)
        r = self.p.handle(events.doorbell())
        on = [a.args["outlet"] for a in r.actions if a.type == ActionType.OUTLET_ON]
        self.assertIn(Outlet.LIVING_ROOM_LAMP, on)
        self.assertIn(Outlet.PORCH_LIGHT, on)

    def test_at_9_pm_night_mode_is_not_now(self):
        said = self.tell("At 9 PM, switch to night mode.", "remember",
                         {"trigger": "time", "time": "21:00", "action": "set_mode", "to_mode": "night"},
                         routed="arm_night")
        self.assertEqual(said, ["Okay. At 9 PM, I'll switch to night mode."])
        self.assertEqual(self.p.modes.mode, Mode.HOME)                       # not armed now
        actions = self.p.tasks.due(Mode.HOME, now=datetime(2026, 11, 15, 21, 0, 5))
        self.assertEqual([(a.type, a.args) for a in actions], [(ActionType.SET_MODE, {"mode": Mode.NIGHT})])

    def test_list_and_out_of_scope(self):
        self.tell("Remind me to call my mom the next time I talk to you.", "remember",
                  {"trigger": "owner_speaks", "action": "say_owner", "text": "call my mom"})
        said = self.tell("What are my tasks?", "list_tasks", {})
        self.assertEqual(said, ["Reminder: call your mom.", "You have no tasks set."])
        self.router.tasks_mode = True
        self.router.client = FakeClient(route("unclear", topic="check the weather"))
        r = self.p.handle(events.speech("What's the weather tomorrow?", "owner"))
        self.assertEqual([a.args["text"] for a in r.actions if a.type == ActionType.SPEAK],
                         [lines.cant_do("check the weather")])

    def test_no_model_keywords_still_store_it(self):
        said = self.tell("If a delivery comes, ask them to leave it by the side door.")
        self.assertEqual(said, ["Okay. If a delivery comes, I'll tell them to leave it by the side door."])


if __name__ == "__main__":
    unittest.main()
