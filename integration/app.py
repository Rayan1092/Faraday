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

from shared import (Action, ActionType, AlertLevel, Event, EventType, Mode, RecordingSavedData,
                    Source, make_event)

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
                 out: Callable[[str], None] = print, lights_off_at_start: bool = False):
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
        self._speaking = False                     # a line is being spoken right now
        self.lights_off_at_start = lights_off_at_start
        self.video: Any = None                     # hardware.camera.CameraFeed: alarm recording
        self.alarm_record_s = float(settings.get("camera", {}).get("alarm_record_s", 10))
        self._recording = threading.Lock()         # one alarm recording at a time
        self.voice: Any = None                     # integration.voice.Voice, set by __main__
        self.extras: List[Any] = []                # things with stop(): controls server, camera feed
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
        if self.lights_off_at_start:
            self._all_lights_off()
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
        if self.voice is not None:
            self.voice.stop()
        for extra in reversed(self.extras):
            try:
                extra.stop()
            except Exception:
                log.exception("stopping %s failed", type(extra).__name__)
        self.flipper.stop()
        if self.board is not None:
            self.board.stop()

    def speech_busy(self) -> bool:
        """Faraday may still say something: an event is being handled (the 9B may
        be writing the greeting), lines are queued, or one is being spoken. The
        voice opens a listening window only when this is False (owner 2026-11-14:
        it opened while Faraday was still talking). A photo in progress counts:
        its description is spoken (who's at the door)."""
        # unfinished_tasks counts items queued *or still being worked on* (task_done
        # comes after), so there is no gap between get() and the busy flag
        return (self._speaking or self.events.unfinished_tasks > 0
                or self._speech.unfinished_tasks > 0 or self._photos.unfinished_tasks > 0)

    def _all_lights_off(self) -> None:
        """Every start: send off to every outlet, so the real lamps match the
        dashboard (owner 2026-11-14). Logged as `system` actions, so the Lights
        panel shows them off; the skip-repeat state starts as "off"."""
        for outlet in list(self.flipper.outlets):
            action = Action(ActionType.OUTLET_OFF, Source.SYSTEM, {"outlet": outlet})
            self.pipeline.log.write(action)
            with self._sent_lock:
                self._outlet_state[outlet] = ActionType.OUTLET_OFF
            try:
                fut = self.flipper.execute(action)
                fut.add_done_callback(lambda f, a=action: self._check_send(f, a))
            except ValueError as e:
                self.out(f"  [flipper] refused {action.type}: {e}")
        self.out(f"  [startup] all lights off ({', '.join(self.flipper.outlets)})")

    def _record_alarm(self, alert: Event) -> None:
        """About 10 s of frames when the alarm goes off, then a recording_saved
        event in the log (the dashboard shows "Recording saved")."""
        if self.video is None or not self._recording.acquire(blocking=False):
            return                      # no camera, or already recording

        def run() -> None:
            try:
                from datetime import datetime
                from hardware.camera import relative
                from hardware.settings import REPO_ROOT
                captures = self.settings.get("paths", {}).get("captures", "captures")
                out_dir = REPO_ROOT / captures / f"alarm-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                self.out(f"  [camera] alarm: recording {self.alarm_record_s:.0f} s")
                info = self.video.record(self.alarm_record_s, out_dir)
                if not info["frames"]:
                    self.out("  [camera] alarm recording FAILED: no frames (camera unavailable?)")
                    return
                data = RecordingSavedData(
                    path=relative(info["path"]).rstrip("/") + "/", frames=int(info["frames"]),
                    duration_s=float(info["duration_s"]),
                    video_path=relative(info["video_path"]) if info.get("video_path") else None,
                    reason="alarm")
                event = make_event(EventType.RECORDING_SAVED, data, Source.CAMERA, caused_by=alert.id)
                self.pipeline.log.write(event)      # shown by the dashboard; nothing to decide
                self.out(f"  [camera] Recording saved: {data.path} ({data.frames} frames)")
            except Exception:
                log.exception("alarm recording failed")
            finally:
                self._recording.release()
        threading.Thread(target=run, name="faraday-alarm-recording", daemon=True).start()

    def outlet_states(self) -> Dict[str, str]:
        """Last command sent OK per outlet (for the Controls page)."""
        with self._sent_lock:
            return dict(self._outlet_state)

    # ---- inputs ----------------------------------------------------------------
    def submit(self, event: Event) -> None:
        """Any thread may hand in an event (Flipper, camera, speech)."""
        self.events.put(event)

    def control(self, outlet: str, on: bool) -> bool:
        """Dashboard Controls (board.control_hook): switch an outlet through the
        normal dispatcher, so the skip-repeat rule applies. Logged as a
        `dashboard` action so the Lights panel follows. Returns False when the
        outlet is already in that state or unknown."""
        kind = ActionType.OUTLET_ON if on else ActionType.OUTLET_OFF
        if outlet not in self.flipper.outlets:
            self.out(f"  [controls] unknown outlet {outlet!r}")
            return False
        with self._sent_lock:
            if self._outlet_state.get(outlet) == kind:
                self.out(f"  [controls] {outlet} already {kind[7:]}")
                return False
        action = Action(kind, Source.DASHBOARD, {"outlet": outlet})
        self.pipeline.log.write(action)      # not a pipeline action: log it here
        self._on_action(action)
        return True

    # ---- pipeline callbacks (any thread, must be quick) -----------------------
    def _on_pipeline_event(self, event: Event) -> None:
        self.out(f"EVENT  {event.type:<15} {_brief(event.data)}  [{event.source}]")
        if event.type == EventType.DOORBELL and self.voice is not None:
            self.voice.on_doorbell()           # listen for the visitor after the greeting
        if event.type == EventType.ALERT_CHANGED and event.data.get("level") == AlertLevel.ALARM:
            self._record_alarm(event)
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
            try:
                if event is None:
                    if self._stopping.is_set():
                        return
                    continue
                self.pipeline.handle(event)
            except Exception:
                log.exception("pipeline failed on %s", getattr(event, "type", "?"))
                self.out(f"  [integration] pipeline failed on {getattr(event, 'type', '?')} (see log)")
            finally:
                self.events.task_done()

    def _speech_worker(self) -> None:
        while True:
            action = self._speech.get()
            try:
                if action is None:
                    if self._stopping.is_set():
                        return
                    continue
                text = str(action.args.get("text", ""))
                self._speaking = True
                try:
                    self.speaker.say(text)
                except Exception:
                    log.exception("speaker failed")
                finally:
                    self._speaking = False
                if self.voice is not None:
                    # a question opens a listening window; caused_by tells whose question
                    try:
                        self.voice.on_said(text, caused_by=action.caused_by)
                    except Exception:
                        log.exception("voice.on_said failed")
            finally:
                self._speech.task_done()

    def _camera_worker(self) -> None:
        while True:
            action = self._photos.get()
            try:
                if action is None:
                    if self._stopping.is_set():
                        return
                    continue
                if self.camera is None:
                    self.out("  [camera] no camera configured; photo skipped")
                    continue
                event = self.camera.take(action)
                if event is not None:
                    self.submit(event)          # queued before task_done: no busy gap
            except Exception:
                log.exception("camera failed")
            finally:
                self._photos.task_done()


def _brief(d: Optional[Dict[str, Any]]) -> str:
    import json
    text = json.dumps(d or {}, ensure_ascii=False)
    return text if len(text) <= 90 else text[:87] + "..."
