"""Hands-free voice: listening windows, the "Hey Faraday" wake phrase, STT.

One mic stream stays open (hardware.audio.Recorder), cut into back-to-back
segments, so nothing is lost between them:

- Wake listener (Mac, needs STT): while idle every stretch of speech is a
  *wake segment*. Segments are checked one at a time, in order, and acted on
  only if the transcript starts with "hey faraday". "Hey Faraday, <request>"
  in one breath -> the request is sent at once. "Hey Faraday" alone -> the
  next words are the request: a segment that already finished meanwhile, or
  else the segment recording now, which becomes the owner's request window
  (with a soft chime if the owner hasn't started talking yet). Anything else,
  including plain "Faraday", is dropped and its audio deleted.
- Visitor windows: after a doorbell (once Faraday has greeted, or ~2 s after the
  ring when Faraday has nothing to say),
  after every line Faraday says that ends with "?", and after the owner used
  the intercom. A window opens only when Faraday has nothing left to say
  (speech queue empty, nothing being generated) and the speaker has been quiet
  for `after_speech_s` (0.3 s). It records up to max_s (15), ends silence_s
  (1.8) after the speaker stops, or after no_speech_s (8) if nobody spoke.
- The visit (owner 2026-11-15, Mac test: the visitor's "I have a package" came
  after the doorbell window had closed and got the owner's "The system is
  armed"): while armed, speech that starts within `visitor_s` (60) of the ring,
  or of the visitor's last words, is the visitor's, with or without a window.
  Wake segments then end at the visitor's pause (silence_s), not the wake one.
  At Night "Hey Faraday ..." is still the owner (who's at the door?); in Away
  nobody is home, so it's the visitor too. The passphrase works on either.
- After the intercom the visitor's reply window waits only `intercom_reply_s`
  (3) for them to start (owner, Mac test: it caught the owner talking to
  someone else, and Bonsai answered that).
- Owner windows without the wake phrase: after a line Faraday says to the
  owner that ends with "?" or asks them to say it again, or that Dell marks
  with the speak action's `listen: "owner"` (PLAN Q44).

Every speech_heard carries `channel`: "owner" (after the wake phrase, or the
answer to a question Faraday asked *in reply to* an owner request: the speak
action's caused_by is that owner speech_heard) or "visitor" (everything else).

Nothing here prints raw transcripts: the passphrase is redacted only inside
the pipeline.
"""
from __future__ import annotations

import collections
import logging
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Optional, Tuple

from shared import Event, EventType, Mode, Source, SpeechHeardData, make_event

from hardware.camera import relative

log = logging.getLogger("integration.voice")

DEFAULT_WAKE = "hey faraday"
WAKE_SILENCE_S = 0.7          # a pause this long ends a wake segment
WAKE_MAX_S = 10.0
VISITOR_S = 60.0              # armed: speech this long after the ring (or the visitor's words) is theirs
VISIT_MAX_S = 180.0           # ... but never longer than this after the ring (a radio left on)
INTERCOM_REPLY_S = 3.0        # after the intercom the visitor must start answering within this
# A line asking to hear it again is a question, "?" or not ("Please say it again."); not
# "I'll repeat that to the owner" or "I can't say it's safe"
ASKS_AGAIN = re.compile(r"\bsay (?:it|that) again\b|\bsay again\b|\brepeat (?:it|that)\W*$", re.I)
DOORBELL_FALLBACK_S = 2.0     # no greeting coming (Faraday has nothing to say): open after this
DOORBELL_BUSY_POLL_S = 0.5    # a greeting is being written or spoken: check again
DOORBELL_RETRY_S = 1.0        # a doorbell window refused (another window open): try again
DOORBELL_GIVE_UP_S = 30.0
OWNER_NEXT_S = 15.0           # after "Hey Faraday" alone, the next segment within this is the request
REQUEST_GIVE_UP_S = 20.0      # a window that can't open (mic busy/broken) is dropped after this
OWNER, VISITOR = "owner", "visitor"


def wake_pattern(phrase: str = DEFAULT_WAKE) -> "re.Pattern[str]":
    words = [re.escape(w) for w in phrase.lower().split()]
    sep = r"[\s,.!?;:\-]*"
    return re.compile(r"^\s*" + sep.join(words) + r"\b" + sep, re.I)


def _voice_label(result: Optional[bool]) -> str:
    return {True: "the owner's voice", False: "unknown voice, not the enrolled owner (display only)",
            None: "voice check: can't tell"}[result]


def strip_wake(text: str, phrase: str = DEFAULT_WAKE) -> Optional[str]:
    """The request after the wake phrase, "" if only the phrase was said, or
    None if the transcript doesn't start with it ("Faraday is great" -> None)."""
    m = wake_pattern(phrase).match(text or "")
    return text[m.end():].strip() if m else None


class Voice:
    def __init__(self, recorder: Any, stt: Any, submit: Callable[[Event], None],
                 out: Callable[[str], None] = print, *,
                 listen: Optional[Dict[str, Any]] = None, wake: Optional[Dict[str, Any]] = None,
                 diarizer: Any = None, diarize_grace_s: float = 0.0,
                 busy: Optional[Callable[[], bool]] = None,
                 chime: Optional[Callable[[], None]] = None,
                 owner_voice: Any = None, owner_voice_grace_s: float = 2.0,
                 urgent: Optional[Callable[[], bool]] = None,
                 mode: Optional[Callable[[], Optional[str]]] = None):
        self.recorder, self.stt, self.submit, self.out = recorder, stt, submit, out
        listen = listen or {}
        self.max_s = float(listen.get("max_s", 15.0))
        self.silence_s = float(listen.get("silence_s", 1.8))
        self.no_speech_s = float(listen.get("no_speech_s", 8.0))
        self.after_speech_s = float(listen.get("after_speech_s", 0.3))
        self.visitor_s = float(listen.get("visitor_s", VISITOR_S))
        self.intercom_reply_s = float(listen.get("intercom_reply_s", INTERCOM_REPLY_S))
        self.mode = mode                            # the house mode now (the visit is armed only)
        self._visit_until = 0.0                     # monotonic end of the visit (see the top)
        self._ring_at = 0.0                         # monotonic time of the ring that began it
        self._seg_opened = 0.0                      # when the current segment opened
        wake = wake or {}
        self.wake_enabled = bool(wake.get("enabled", True))
        self.wake_phrase = str(wake.get("phrase", DEFAULT_WAKE))
        self.wake_silence_s = float(wake.get("silence_s", WAKE_SILENCE_S))
        self.wake_max_s = float(wake.get("max_s", WAKE_MAX_S))
        self.diarizer = diarizer
        self.diarize_grace_s = diarize_grace_s     # how long STT may wait for the count
        self.busy = busy or (lambda: False)        # Faraday has a line queued / being made
        self.owner_voice = owner_voice              # hardware.owner_voice.OwnerVoice (display only)
        self.owner_voice_grace_s = owner_voice_grace_s
        # urgent(): a warning or alarm is on. Then the (display-only) voice check never
        # holds a request back: "Hey Faraday, <passphrase>" must disarm at once.
        self.urgent = urgent or (lambda: False)
        self.chime = chime
        self._lock = threading.Lock()
        self._purpose: Optional[str] = None        # open segment: "wake" | VISITOR | OWNER
        self._reason = ""
        self._request: Optional[Tuple[str, str]] = None   # (purpose, reason) waiting to open
        self._request_at = 0.0
        self._wake_on = False
        self._owner_ids: Deque[str] = collections.deque(maxlen=20)   # owner speech_heard ids
        self._seq = 0                              # wake segments, numbered as they end
        self._owner_next_seq: Optional[int] = None # "Hey Faraday" alone: this segment is the request
        self._chime_on_open = False                # chime when the queued owner window opens
        self._pending_doorbell = False
        self._doorbell_since = 0.0
        self._doorbell_timer: Optional[threading.Timer] = None
        self._checks: "queue.Queue[Optional[Tuple[Path, float]]]" = queue.Queue()
        self._checker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._kick = threading.Event()
        self._seg_done = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None
        self._mic_failures = 0
        self.windows_opened = 0
        self.wake_heard = 0
        self.wake_ignored = 0

    # ---- public -------------------------------------------------------------------
    @property
    def listening(self) -> bool:
        """The dashboard's mic indicator (board.listening_hook): True only while a
        visitor/owner window is recording. Not during wake listening, not while a
        requested window waits for Faraday to finish, not while muted."""
        return (self._purpose in (VISITOR, OWNER) and self.recorder.window_open
                and not self.recorder.gate.muted)

    def listen_once(self, reason: str = "console", purpose: str = VISITOR) -> bool:
        """Ask for one window (console `r`, question, doorbell, intercom). It opens
        once Faraday has finished talking. False if one is open or already asked for."""
        with self._lock:
            if self._purpose in (VISITOR, OWNER) or self._request is not None:
                return False
            self._request = (purpose, reason)
            self._request_at = time.monotonic()
        self._ensure_loop()
        self._kick.set()
        return True

    def on_said(self, text: str, caused_by: Optional[str] = None,
                listen: Optional[str] = None) -> None:
        """Called after Faraday finished speaking a line. `caused_by` is the speak
        action's caused_by: a question answering an owner request is for the owner.
        `listen` is the speak action's marker (PLAN Q44): "owner" opens an owner
        window (no wake phrase needed), "visitor" a visitor window; it wins over
        everything else, a pending doorbell included."""
        text = (text or "").strip()
        if listen == OWNER:
            self.listen_once("question", purpose=OWNER)   # a pending doorbell still opens after it
            return
        if listen == VISITOR:
            opened = self._visitor_window("doorbell" if self._pending_doorbell else "question")
            if self._pending_doorbell:
                self._cancel_doorbell_timer()
                if opened:
                    self._pending_doorbell = False
                else:
                    self._arm_doorbell(DOORBELL_RETRY_S)  # a window is busy: try again soon
            return
        if self._pending_doorbell:
            # the greeting (even "... leave a message?") is for the visitor
            self._cancel_doorbell_timer()
            if self._visitor_window("doorbell"):
                self._pending_doorbell = False
            else:
                self._arm_doorbell(DOORBELL_RETRY_S)  # a window is busy: try again soon
            return
        if text.endswith("?") or ASKS_AGAIN.search(text):
            if caused_by is not None and caused_by in self._owner_ids:
                self.listen_once("question", purpose=OWNER)
            else:
                self._visitor_window("question")

    def _visitor_window(self, reason: str) -> bool:
        """Open (or take over) a window for the visitor. An owner window that has
        heard nothing since Faraday spoke becomes the visitor's (the owner said
        "Hey Faraday" and then the doorbell rang); a pending owner request yields."""
        if self.listen_once(reason, purpose=VISITOR):
            return True
        took = False
        with self._lock:
            if self._request is not None and self._request[0] == OWNER:
                self._request = (VISITOR, reason)
                self._request_at = time.monotonic()
                return True
            if self._purpose == OWNER and not self.recorder.window_state().get("speech_started") \
                    and self.recorder.adjust(**self._rules(VISITOR)):
                self._purpose, self._reason = VISITOR, reason
                took = True
        if took:
            self._opened(VISITOR, reason)
        return took

    def on_doorbell(self) -> None:
        """A visitor rang: listen after the greeting. If Faraday says nothing (Dell's
        doorbell flow doesn't greet a repeat ring, Night is quiet), the window opens
        about 2 s after the ring, as soon as Faraday is idle (found by the rehearsal:
        a 10 s wait lost a visitor who spoke right after ringing)."""
        self._pending_doorbell = True
        self._doorbell_since = time.monotonic()
        if self._armed():                                 # the visit: only while armed
            self._ring_at = self._doorbell_since
            self._visit_until = self._ring_at + self.visitor_s
        self._arm_doorbell(DOORBELL_FALLBACK_S)

    def on_intercom_end(self) -> None:
        """The owner let go of the talk button: listen for the visitor's reply. The
        window closes after intercom_reply_s unless the visitor starts answering;
        a visitor window still open (nobody spoke yet) takes the same rule."""
        if self.listen_once("intercom", purpose=VISITOR):
            return
        took = False
        with self._lock:
            if self._request is not None and self._request[0] == VISITOR:
                self._request = (VISITOR, "intercom")
                return
            if self._purpose == VISITOR and not self.recorder.window_state().get("speech_started") \
                    and self.recorder.adjust(**self._rules(VISITOR, "intercom")):
                self._reason = "intercom"
                took = True
        if took:
            self._opened(VISITOR, "intercom")

    def _armed(self) -> bool:
        try:
            return self.mode is not None and self.mode() in Mode.ARMED
        except Exception:
            return False

    def visiting(self, started: Optional[float] = None) -> bool:
        """Armed, and `started` (monotonic, default now) is within the visit: after
        the ring (minus the 0.5 s preroll), before its end. Disarming ends it."""
        if not self._visit_until:
            return False
        if not self._armed():
            self._visit_until = 0.0                       # disarmed: the visit is over
            return False
        when = time.monotonic() if started is None else started
        return self._ring_at - 0.6 <= when < self._visit_until

    def _away(self) -> bool:
        try:
            return self.mode is not None and self.mode() == Mode.AWAY
        except Exception:
            return False

    def start_wake(self) -> bool:
        """Start the wake listener (needs STT). Visitor windows work without it."""
        if not self.wake_enabled:
            return False
        if not getattr(self.stt, "available", False):
            self.out("  [wake] off: no STT on this OS")
            return False
        self._wake_on = True
        self._ensure_loop()
        self._kick.set()
        self.out(f'  [wake] listening for "{self.wake_phrase}" (continuous)')
        return True

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()
        self._cancel_doorbell_timer()
        self.recorder.cancel()
        if self._loop_thread is not None:
            self._loop_thread.join(3)
            self._loop_thread = None
        if self._checker is not None:
            self._checks.put(None)
            self._checker.join(3)
            self._checker = None
        close = getattr(self.recorder, "close", None)
        if close is not None:
            close()

    stop_wake = stop      # old name

    # ---- the segment loop -----------------------------------------------------------
    def _ensure_loop(self) -> None:
        with self._lock:
            if self._loop_thread is None and not self._stop.is_set():
                self._loop_thread = threading.Thread(target=self._loop, name="faraday-voice",
                                                     daemon=True)
                self._loop_thread.start()

    def _quiet(self) -> bool:
        """Faraday has nothing more to say and the speaker has really finished."""
        return not self.busy() and self.recorder.gate.quiet_for(self.after_speech_s)

    def _rules(self, purpose: str, reason: str = "") -> Dict[str, Any]:
        if purpose == "wake":
            if self.visiting():               # the visitor may pause mid-sentence: their pause
                return {"max_s": self.max_s, "silence_s": self.silence_s, "no_speech_s": None}
            return {"max_s": self.wake_max_s, "silence_s": self.wake_silence_s, "no_speech_s": None}
        if reason == "intercom":
            return {"max_s": self.max_s, "silence_s": self.silence_s, "no_speech_s": self.intercom_reply_s}
        return {"max_s": self.max_s, "silence_s": self.silence_s, "no_speech_s": self.no_speech_s}

    def _opened(self, purpose: str, reason: str, chime: bool = False) -> None:
        self.windows_opened += 1
        if reason == "intercom":
            self.out(f"  [mic] listening (intercom): the visitor's reply, if they start within "
                     f"{self.intercom_reply_s:.0f} s")
        else:
            self.out(f"  [mic] listening ({reason}{', owner' if purpose == OWNER else ''}),"
                     f" up to {self.max_s:.0f} s")
        if purpose == OWNER and reason == "wake" and (chime or self._chime_on_open):
            self._chime_on_open = False
            if self.chime is not None:
                self.out("  [wake] yes? (chime)")
                try:
                    self.chime()               # mutes the mic around itself
                except Exception:
                    log.exception("chime failed")

    def _mic_failed(self, purpose: str, reason: str) -> None:
        """listen() refused (mic busy or broken): back off, say it once, and give
        up on a requested window after REQUEST_GIVE_UP_S."""
        self._mic_failures += 1
        if self._mic_failures == 1:
            self.out("  [mic] could not open the microphone; retrying")
        with self._lock:
            self._purpose = None
            if purpose != "wake":
                if time.monotonic() - self._request_at < REQUEST_GIVE_UP_S:
                    if self._request is None:
                        self._request = (purpose, reason)
                else:
                    self.out(f"  [mic] gave up on the {reason} window (microphone unavailable)")
        self._stop.wait(min(5.0, 0.1 * (2 ** min(self._mic_failures, 6))))

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                req = self._request
            if req is None and not self._wake_on:
                self._kick.wait(0.2)
                self._kick.clear()
                continue
            if not self._quiet():
                time.sleep(0.05)
                continue
            with self._lock:
                if self._request is not None:
                    purpose, reason = self._request
                    self._request = None
                else:
                    purpose, reason = "wake", "wake"
                self._purpose, self._reason = purpose, reason
            self._seg_done.clear()
            visit_rules = purpose == "wake" and self.visiting()
            self._seg_opened = time.monotonic()      # speech in this segment starts after this
            if not self.recorder.listen(self._segment_done, **self._rules(purpose, reason)):
                self._mic_failed(purpose, reason)
                continue
            if self._mic_failures:
                self.out("  [mic] microphone back")
                self._mic_failures = 0
            if purpose != "wake":
                self._opened(purpose, reason)
            # Wait for the segment to end. A window asked for meanwhile takes over
            # the open wake segment in place (no new stream, nothing lost).
            while not self._seg_done.wait(0.05):
                if self._stop.is_set():
                    self.recorder.cancel()
                    break
                if visit_rules and not self.visiting():
                    # the visit is over and nobody spoke yet: back to the wake phrase's pause
                    with self._lock:
                        if self._purpose == "wake" and not self.recorder.window_state().get("speech_started"):
                            self.recorder.adjust(restart=False, **self._rules("wake"))
                            visit_rules = False
                if self._request is None or self._purpose != "wake" or not self._quiet():
                    continue
                opened = None
                with self._lock:      # atomic with _segment_done, which also takes the lock
                    req = self._request
                    if self._purpose == "wake" and req is not None \
                            and self.recorder.adjust(**self._rules(*req)):
                        self._request = None
                        self._purpose, self._reason = req
                        opened = req
                if opened is not None:
                    self._opened(*opened)

    def _segment_done(self, wav: Optional[Path], seconds: float, spoke: bool) -> None:
        with self._lock:
            purpose, reason = self._purpose, self._reason
            self._purpose = None
        self._seg_done.set()
        if purpose == "wake" or purpose is None:
            if spoke and wav is not None:
                self._ensure_checker()
                with self._lock:
                    self._seq += 1
                    seq = self._seq
                # the visitor's, if the speech began within the visit: not before the segment
                # opened, and about `seconds` (its audio, with the preroll) before now
                visit = self.visiting(started=max(self._seg_opened, time.monotonic() - seconds))
                self._checks.put((seq, wav, seconds, visit))
            return
        if not spoke or wav is None:
            self.out("  [mic] nobody spoke")
            return
        self.out(f"  [mic] heard {seconds:.1f} s of speech")
        threading.Thread(target=self._transcribe, args=(wav, seconds),
                         kwargs={"channel": purpose, "diarize": purpose == VISITOR},
                         daemon=True).start()

    # ---- doorbell -----------------------------------------------------------------
    def _arm_doorbell(self, delay: float) -> None:
        self._cancel_doorbell_timer()
        self._doorbell_timer = threading.Timer(delay, self._doorbell_fallback)
        self._doorbell_timer.daemon = True
        self._doorbell_timer.start()

    def _doorbell_fallback(self) -> None:
        if not self._pending_doorbell or self._stop.is_set():
            return
        if self.busy() and time.monotonic() - self._doorbell_since < DOORBELL_GIVE_UP_S:
            self._arm_doorbell(DOORBELL_BUSY_POLL_S)   # the greeting is coming: on_said opens it
            return
        if self._visitor_window("doorbell"):
            self._pending_doorbell = False
        elif time.monotonic() - self._doorbell_since < DOORBELL_GIVE_UP_S:
            self._arm_doorbell(DOORBELL_RETRY_S)
        else:
            self._pending_doorbell = False

    def _cancel_doorbell_timer(self) -> None:
        if self._doorbell_timer is not None:
            self._doorbell_timer.cancel()
            self._doorbell_timer = None

    # ---- wake phrase ------------------------------------------------------------------
    def _ensure_checker(self) -> None:
        with self._lock:
            if self._checker is None and not self._stop.is_set():
                self._checker = threading.Thread(target=self._check_loop, name="faraday-wake-check",
                                                 daemon=True)
                self._checker.start()

    def _check_loop(self) -> None:
        """Wake segments one at a time, in the order they were spoken."""
        while True:
            item = self._checks.get()
            if item is None or self._stop.is_set():
                break
            try:
                self._wake_check(*item)
            except Exception:
                log.exception("wake check failed")
        # shutting down: unchecked wake audio is not kept
        while True:
            try:
                left = self._checks.get_nowait()
            except queue.Empty:
                break
            if left is not None:
                try:
                    left[1].unlink()
                except OSError:
                    pass

    def _start_voice_check(self, wav: Path) -> Optional[Dict[str, Any]]:
        """Start the owner voice check (display only) in its own thread."""
        if self.owner_voice is None or wav is None or not Path(wav).exists():
            return None
        check: Dict[str, Any] = {"lock": threading.Lock()}

        def run_check() -> None:
            result = self.owner_voice.check(wav)
            with check["lock"]:
                check["r"] = result
                late = check.get("late")
            if late:
                self.out(f"  [voice] {_voice_label(result)} (too late for this line)")
        check["thread"] = threading.Thread(target=run_check, name="faraday-owner-voice", daemon=True)
        check["thread"].start()
        return check

    def _wake_check(self, seq: int, wav: Path, seconds: float, visit: bool = False) -> None:
        owner_next = self._owner_next_seq == seq
        if owner_next:
            self._owner_next_seq = None
        # "Hey Faraday, <request>" in one breath is long: start the voice check now, so it
        # runs alongside the wake STT instead of after it (dropped if it isn't a request)
        early = self._start_voice_check(wav) if (owner_next or seconds >= 1.2) else None
        try:
            text = self.stt.transcribe(wav)
        except Exception:
            log.exception("wake STT failed")
            text = None
        rest = strip_wake(text or "", self.wake_phrase)
        visit = visit and self._armed()          # a passphrase queued before this one disarmed
        if visit and text and not owner_next and (rest is None or self._away()):
            # the visit: what's said is the visitor's. In Away also "Hey Faraday ..."
            # (nobody is home; the passphrase still disarms on the visitor channel).
            words = text if rest is None else rest
            if not words:
                self.out("  [visit] the wake phrase alone during a visit: still listening for the visitor")
                try:
                    wav.unlink()
                except OSError:
                    pass
                return
            self.out("  [visit] speech within the visit: the visitor's")
            self._transcribe(wav, seconds, text_override=words, channel=VISITOR, diarize=True)
            return
        if owner_next:
            # "Hey Faraday" came alone just before: these are the owner's words
            # (and if it is the phrase again, the request is still to come)
            if rest == "":
                self.wake_heard += 1
                self._after_phrase(seq)
                return
            request = text if rest is None else rest
            if request:
                self.out("  [wake] owner request heard")
                self._transcribe(wav, seconds, text_override=request, channel=OWNER, diarize=False,
                                 check=early)
            else:
                self.out("  [stt] no words recognized")
            return
        if rest is None:
            self.wake_ignored += 1
            try:
                wav.unlink()               # not for us: keep nothing
            except OSError:
                pass
            return
        self.wake_heard += 1
        if rest:
            self.out("  [wake] owner request heard")
            self._transcribe(wav, seconds, text_override=rest, channel=OWNER, diarize=False,
                             check=early)
            return
        self._after_phrase(seq)

    def _after_phrase(self, seq: int) -> None:
        """Just "Hey Faraday" (segment `seq`): the next words are the request."""
        if not self._checks.empty():
            # the owner went on talking and that segment already ended: it's next
            self._owner_next_seq = seq + 1
            self.out("  [wake] yes?")
            return
        promoted, talking = False, False
        with self._lock:      # the segment recording now becomes the request window
            if self._purpose == "wake" and self._request is None:
                talking = bool(self.recorder.window_state().get("speech_started"))
                if self.recorder.adjust(restart=not talking, **self._rules(OWNER)):
                    self._purpose, self._reason = OWNER, "wake"
                    promoted = True
        if promoted:
            self._opened(OWNER, "wake", chime=not talking)
            if talking:
                self.out("  [wake] yes?")
            return
        if not self._checks.empty():
            # it ended just now and is queued: that segment is the request
            self._owner_next_seq = seq + 1
            self.out("  [wake] yes?")
            return
        self.out("  [wake] yes?")
        if self.listen_once("wake", purpose=OWNER):
            self._chime_on_open = True     # chime once that window really opens

    # ---- STT (+ optional diarization) ----------------------------------------------
    def _transcribe(self, wav: Path, seconds: float, source: str = Source.SPEECH,
                    text_override: Optional[str] = None, diarize: bool = True,
                    channel: str = VISITOR, check: Optional[Dict[str, Any]] = None) -> None:
        voices_thread = None
        voices: Dict[str, Any] = {}
        if self.diarizer is not None and diarize:       # visitor recordings only
            t0 = time.monotonic()

            def run() -> None:
                voices["n"] = self.diarizer.count_voices(wav)
                voices["s"] = time.monotonic() - t0
                if voices.get("late"):                   # STT went ahead without it
                    self.out(f"  [diarize] {voices['n']} voice(s) in {voices['s']:.1f} s"
                             f" (too late for this line; grace {self.diarize_grace_s:.1f} s)")

            voices_thread = threading.Thread(target=run, name="faraday-diarize", daemon=True)
            voices_thread.start()
        if channel == OWNER and check is None:
            check = self._start_voice_check(wav)        # alongside STT (display only)
        text = text_override if text_override is not None else self.stt.transcribe(wav)
        if text and channel == VISITOR and text_override is None and self.wake_enabled \
                and self.visiting() and self._away():
            rest = strip_wake(text, self.wake_phrase)
            if rest == "":                     # a visit in Away: nobody home to answer it
                self.out("  [visit] the wake phrase alone during a visit: still listening for the visitor")
                return
            if rest is not None:
                text = rest                    # still the visitor's words (the passphrase disarms anyway)
                self.out("  [visit] the wake phrase during a visit in Away: the visitor's words")
        elif text and channel == VISITOR and text_override is None and self.wake_enabled:
            rest = strip_wake(text, self.wake_phrase)
            if rest == "":                     # just "Hey Faraday" in a visitor window
                self.out("  [wake] yes? (heard in a visitor window)")
                if self.listen_once("wake", purpose=OWNER):
                    self._chime_on_open = True  # chime once the owner window opens
                return
            if rest is not None:               # "Hey Faraday, ..." while a visitor window was open
                self.out("  [wake] owner request heard (in a visitor window)")
                text, channel = rest, OWNER
                voices_thread = None           # the voice count is for visitors
                check = self._start_voice_check(wav)    # the owner voice check, as for any owner request
        if not text:
            self.out("  [stt] no words recognized" + ("" if self.stt.available
                                                      else " (no STT on this OS)"))
            return
        data: Dict[str, Any] = {"text": text, "audio_path": relative(wav),
                                "duration_s": round(seconds, 2), "channel": channel}
        if voices_thread is not None:
            # Diarization ran alongside STT. Wait at most grace_s (0 by default), so
            # it never holds up the reply; a late count is only printed.
            voices_thread.join(self.diarize_grace_s)
            if voices_thread.is_alive():
                voices["late"] = True
            elif voices.get("n") is not None:
                data["voices"] = int(voices["n"])
        if check is not None and channel == OWNER:
            check["thread"].join(0.0 if self.urgent() else self.owner_voice_grace_s)
            with check["lock"]:
                done = "r" in check
                if not done:
                    check["late"] = True           # printed when it finishes
                result = check.get("r")
            if done:
                if result is not None:
                    data["owner_voice"] = bool(result)
                self.out(f"  [voice] {_voice_label(result)}")
        event = make_event(EventType.SPEECH_HEARD, SpeechHeardData(**data), source)
        if channel == OWNER:
            self._owner_ids.append(event.id)      # Faraday's questions back to it are for the owner
        elif self.visiting():                                       # the visitor is still talking
            self._visit_until = min(time.monotonic() + self.visitor_s, self._ring_at + VISIT_MAX_S)
        self.out(f"  [stt] heard {len(text.split())} word(s) ({channel})"
                 + (f", {data['voices']} voice(s)" if "voices" in data else ""))
        self.submit(event)
