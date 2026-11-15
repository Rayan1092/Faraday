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
- Visitor windows: after a doorbell (once Faraday has greeted, or after 10 s),
  after every line Faraday says that ends with "?", and after the owner used
  the intercom. A window opens only when Faraday has nothing left to say
  (speech queue empty, nothing being generated) and the speaker has been quiet
  for `after_speech_s` (0.3 s). It records up to max_s (15), ends silence_s
  (1.8) after the speaker stops, or after no_speech_s (8) if nobody spoke.

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

from shared import Event, EventType, Source, SpeechHeardData, make_event

from hardware.camera import relative

log = logging.getLogger("integration.voice")

DEFAULT_WAKE = "hey faraday"
WAKE_SILENCE_S = 0.7          # a pause this long ends a wake segment
WAKE_MAX_S = 10.0
DOORBELL_FALLBACK_S = 10.0    # open the window even if Faraday never greeted (9B is slow)
DOORBELL_RETRY_S = 1.0        # a doorbell window refused (another window open): try again
DOORBELL_GIVE_UP_S = 30.0
OWNER_NEXT_S = 15.0           # after "Hey Faraday" alone, the next segment within this is the request
REQUEST_GIVE_UP_S = 20.0      # a window that can't open (mic busy/broken) is dropped after this
OWNER, VISITOR = "owner", "visitor"


def wake_pattern(phrase: str = DEFAULT_WAKE) -> "re.Pattern[str]":
    words = [re.escape(w) for w in phrase.lower().split()]
    sep = r"[\s,.!?;:\-]*"
    return re.compile(r"^\s*" + sep.join(words) + r"\b" + sep, re.I)


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
                 chime: Optional[Callable[[], None]] = None):
        self.recorder, self.stt, self.submit, self.out = recorder, stt, submit, out
        listen = listen or {}
        self.max_s = float(listen.get("max_s", 15.0))
        self.silence_s = float(listen.get("silence_s", 1.8))
        self.no_speech_s = float(listen.get("no_speech_s", 8.0))
        self.after_speech_s = float(listen.get("after_speech_s", 0.3))
        wake = wake or {}
        self.wake_enabled = bool(wake.get("enabled", True))
        self.wake_phrase = str(wake.get("phrase", DEFAULT_WAKE))
        self.wake_silence_s = float(wake.get("silence_s", WAKE_SILENCE_S))
        self.wake_max_s = float(wake.get("max_s", WAKE_MAX_S))
        self.diarizer = diarizer
        self.diarize_grace_s = diarize_grace_s     # how long STT may wait for the count
        self.busy = busy or (lambda: False)        # Faraday has a line queued / being made
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

    def on_said(self, text: str, caused_by: Optional[str] = None) -> None:
        """Called after Faraday finished speaking a line. `caused_by` is the speak
        action's caused_by: a question answering an owner request is for the owner."""
        text = (text or "").strip()
        if self._pending_doorbell:
            # the greeting (even "... leave a message?") is for the visitor
            self._cancel_doorbell_timer()
            if self._visitor_window("doorbell"):
                self._pending_doorbell = False
            else:
                self._arm_doorbell(DOORBELL_RETRY_S)  # a window is busy: try again soon
            return
        if text.endswith("?"):
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
        """A visitor rang: listen after the greeting, or after 10 s regardless."""
        self._pending_doorbell = True
        self._doorbell_since = time.monotonic()
        self._arm_doorbell(DOORBELL_FALLBACK_S)

    def on_intercom_end(self) -> None:
        """The owner let go of the talk button: listen for the visitor's reply."""
        self.listen_once("intercom", purpose=VISITOR)

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

    def _rules(self, purpose: str) -> Dict[str, Any]:
        if purpose == "wake":
            return {"max_s": self.wake_max_s, "silence_s": self.wake_silence_s, "no_speech_s": None}
        return {"max_s": self.max_s, "silence_s": self.silence_s, "no_speech_s": self.no_speech_s}

    def _opened(self, purpose: str, reason: str, chime: bool = False) -> None:
        self.windows_opened += 1
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
            if not self.recorder.listen(self._segment_done, **self._rules(purpose)):
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
                if self._request is None or self._purpose != "wake" or not self._quiet():
                    continue
                opened = None
                with self._lock:      # atomic with _segment_done, which also takes the lock
                    req = self._request
                    if self._purpose == "wake" and req is not None \
                            and self.recorder.adjust(**self._rules(req[0])):
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
                self._checks.put((seq, wav, seconds))
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

    def _wake_check(self, seq: int, wav: Path, seconds: float) -> None:
        owner_next = self._owner_next_seq == seq
        if owner_next:
            self._owner_next_seq = None
        try:
            text = self.stt.transcribe(wav)
        except Exception:
            log.exception("wake STT failed")
            text = None
        rest = strip_wake(text or "", self.wake_phrase)
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
                self._transcribe(wav, seconds, text_override=request, channel=OWNER, diarize=False)
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
            self._transcribe(wav, seconds, text_override=rest, channel=OWNER, diarize=False)
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
                    channel: str = VISITOR) -> None:
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
        text = text_override if text_override is not None else self.stt.transcribe(wav)
        if not text:
            self.out("  [stt] no words recognized" + ("" if self.stt.available
                                                      else " (no STT on this OS: use t <text>)"))
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
        event = make_event(EventType.SPEECH_HEARD, SpeechHeardData(**data), source)
        if channel == OWNER:
            self._owner_ids.append(event.id)      # Faraday's questions back to it are for the owner
        self.out(f"  [stt] heard {len(text.split())} word(s) ({channel})"
                 + (f", {data['voices']} voice(s)" if "voices" in data else ""))
        self.submit(event)
