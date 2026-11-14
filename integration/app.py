"""Faraday main app: wires hardware to Dell's pipeline (Join 1).

    Flipper (real or fake) --events--> queue --> one worker --> Pipeline.handle()
    Pipeline on_action  --> outlet_on/off  -> Flipper.execute()        (non-blocking)
                        --> speak          -> speech worker -> speaker (one at a time)
                        --> take_photo     -> camera worker -> photo_described -> queue
    Pipeline on_event   --> alert_changed -> Flipper LED red + buzz at alarm, else off
    Dashboard           --> calls the pipeline directly (it takes its own lock)

on_action can be called from several threads (dashboard buttons, the alarm
confrontation thread), so nothing slow runs inside it: everything goes onto a
queue (PLAN Q22). The pipeline writes every event and action to the log
itself, so integration/ never logs them again (PLAN Q21).
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

from shared import Action, ActionType, Event, EventType, Mode

from brain.pipeline import Pipeline

log = logging.getLogger("integration")


class PrintSpeaker:
    """Stand-in until hardware/speech is wired: prints what would be spoken."""

    def __init__(self, out: Callable[[str], None] = print):
        self.out = out

    def say(self, text: str) -> None:
        self.out(f"  SAY: {text}")


class App:
    def __init__(self, settings: Dict[str, Any], flipper: Any,
                 pipeline: Optional[Pipeline] = None, *,
                 camera: Any = None, speaker: Any = None,
                 use_llm: bool = True, mode: str = Mode.HOME,
                 event_log: Any = None, rules: Any = None,
                 dashboard: bool = True, port: Optional[int] = None,
                 out: Callable[[str], None] = print):
        self.settings = settings
        self.out = out
        self.flipper = flipper
        self.camera = camera
        self.speaker = speaker or PrintSpeaker(out)
        self.events: "queue.Queue[Optional[Event]]" = queue.Queue()
        self._speech: "queue.Queue[Optional[Action]]" = queue.Queue()
        self._photos: "queue.Queue[Optional[Action]]" = queue.Queue()
        self._threads: List[threading.Thread] = []
        self._stopping = threading.Event()
        self._outlet_state: Dict[str, str] = {}   # outlet -> last command sent OK (on/off)
        self._sent_lock = threading.Lock()
        self.flipper.on_event = self.submit
        self.pipeline = pipeline or Pipeline.from_settings(
            settings, use_llm=use_llm, log=event_log, mode=mode, rules=rules,
            on_event=self._on_pipeline_event, on_action=self._on_action, say=out)
        self.pipeline.on_event = self._on_pipeline_event
        self.pipeline.on_action = self._on_action
        self.board = None
        if dashboard:
            from dashboard import Dashboard
            self.board = Dashboard.from_settings(self.pipeline, settings, port)

    # ---- lifecycle -----------------------------------------------------------
    def start(self) -> "App":
        self._stopping.clear()
        for name, target in (("events", self._event_worker), ("speech", self._speech_worker),
                             ("camera", self._camera_worker)):
            t = threading.Thread(target=target, name=f"faraday-{name}", daemon=True)
            t.start()
            self._threads.append(t)
        self.flipper.start()
        if self.board is not None:
            self.board.start()
        self.pipeline.start()          # mode_changed (startup) -> LED shows the mode
        return self

    def stop(self) -> None:
        self._stopping.set()
        for q in (self.events, self._speech, self._photos):
            q.put(None)
        for t in self._threads:
            t.join(5)
        self._threads = []
        self.pipeline.wait_background(5)
        self.flipper.stop()
        if self.board is not None:
            self.board.stop()

    # ---- inputs ----------------------------------------------------------------
    def submit(self, event: Event) -> None:
        """Any thread may hand in an event (Flipper, camera, speech)."""
        self.events.put(event)

    # ---- pipeline callbacks (any thread, must be quick) -----------------------
    def _on_pipeline_event(self, event: Event) -> None:
        self.out(f"EVENT  {event.type:<15} {_brief(event.data)}  [{event.source}]")
        if event.type in (EventType.MODE_CHANGED, EventType.ALERT_CHANGED):
            fut = self.flipper.apply_event(event)
            if fut is not None:
                fut.add_done_callback(lambda f, t=event.type: self._check_led(f, t))

    def _on_action(self, action: Action) -> None:
        self.out(f"  ACTION {action.type:<13} {_brief(action.args)}  [{action.source}]")
        if action.type in (ActionType.OUTLET_ON, ActionType.OUTLET_OFF):
            outlet = action.args.get("outlet")
            with self._sent_lock:
                if self._outlet_state.get(outlet) == action.type:
                    # Never send the same command twice in a row: the real outlets
                    # TOGGLE on a repeated code (a second outlet1_on turned the porch
                    # light off in Join 1), and each send blinds rx ~1.2 s anyway.
                    self.out(f"  [flipper] {outlet} already {action.type[7:]}, not resent")
                    return
                self._outlet_state[outlet] = action.type
            try:
                fut = self.flipper.execute(action)
                fut.add_done_callback(lambda f, a=action: self._check_send(f, a))
            except ValueError as e:
                self.out(f"  [flipper] refused {action.type}: {e}")
        elif action.type == ActionType.SPEAK:
            self._speech.put(action)
        elif action.type == ActionType.TAKE_PHOTO:
            self._photos.put(action)

    def _check_send(self, fut, action: Action) -> None:
        try:
            ok, out = fut.result()
        except Exception as e:  # cable dropped mid-send
            ok, out = False, [repr(e)]
        if not ok:
            with self._sent_lock:               # state unknown now: allow the next attempt
                self._outlet_state.pop(action.args.get("outlet"), None)
            self.out(f"  [flipper] {action.type} {action.args.get('outlet')} FAILED: {out[-1:]}")

    def _check_led(self, fut, what: str) -> None:
        try:
            ok, out = fut.result()
        except Exception as e:
            ok, out = False, [repr(e)]
        if not ok:
            self.out(f"  [flipper] LED for {what} FAILED: {out[-2:]}")

    # ---- workers -----------------------------------------------------------------
    def _event_worker(self) -> None:
        while True:
            event = self.events.get()
            if event is None:
                if self._stopping.is_set():
                    return
                continue
            try:
                self.pipeline.handle(event)
            except Exception:
                log.exception("pipeline failed on %s", event.type)
                self.out(f"  [integration] pipeline failed on {event.type} (see log)")

    def _speech_worker(self) -> None:
        while True:
            action = self._speech.get()
            if action is None:
                if self._stopping.is_set():
                    return
                continue
            try:
                self.speaker.say(str(action.args.get("text", "")))
            except Exception:
                log.exception("speaker failed")

    def _camera_worker(self) -> None:
        while True:
            action = self._photos.get()
            if action is None:
                if self._stopping.is_set():
                    return
                continue
            if self.camera is None:
                self.out("  [camera] no camera configured; photo skipped")
                continue
            try:
                event = self.camera.take(action)
            except Exception:
                log.exception("camera failed")
                continue
            if event is not None:
                self.submit(event)


def _brief(d: Optional[Dict[str, Any]]) -> str:
    import json
    text = json.dumps(d or {}, ensure_ascii=False)
    return text if len(text) <= 90 else text[:87] + "..."
