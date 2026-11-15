"""Hands-free voice: listening windows, the "Hey Faraday" wake phrase, STT.

One mic stream stays open (hardware.audio.Recorder), cut into back-to-back
segments, so nothing is lost between them:

- Wake listener (Mac, needs STT): while idle every stretch of speech is a
  *wake segment*. It is transcribed and acted on only if it starts with "hey
  faraday". "Hey Faraday, <request>" in one breath -> the request is sent at
  once. "Hey Faraday" alone -> a soft chime, and the segment already recording
  becomes the owner's request window. Anything else, including plain
  "Faraday", is dropped and its audio deleted.
- Visitor windows: after a doorbell (once Faraday has greeted, or after 10 s)
  and after every line Faraday says that ends with "?". A window opens only
  when Faraday has nothing left to say (speech queue empty, nothing being
  generated) and the speaker has been quiet for `after_speech_s` (0.3 s). It
  records up to max_s (15), ends silence_s (1.8) after the speaker stops, or
  after no_speech_s (8) if nobody spoke.

Every speech_heard carries `channel`: "owner" (after the wake phrase, or the
answer to Faraday's question about an owner request) or "visitor".

Nothing here prints raw transcripts: the passphrase is redacted only inside
the pipeline.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from shared import Event, EventType, Source, SpeechHeardData, make_event

from hardware.camera import relative

log = logging.getLogger("integration.voice")

DEFAULT_WAKE = "hey faraday"
WAKE_SILENCE_S = 0.7          # a pause this long ends a wake segment
WAKE_MAX_S = 10.0
DOORBELL_FALLBACK_S = 10.0    # open the window even if Faraday never greeted (9B is slow)
OWNER_FOLLOWUP_S = 30.0       # a question this soon after an owner request is for the owner
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
        self._wake_on = False
        self._owner_until = 0.0
        self._pending_doorbell = False
        self._doorbell_timer: Optional[threading.Timer] = None
        self._stop = threading.Event()
        self._kick = threading.Event()
        self._seg_done = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None
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
        """Ask for one window (console `r`, question, doorbell). It opens once
        Faraday has finished talking. False if one is open or already asked for."""
        with self._lock:
            if self._purpose in (VISITOR, OWNER) or self._request is not None:
                return False
            self._request = (purpose, reason)
        self._ensure_loop()
        self._kick.set()
        return True

    def on_said(self, text: str) -> None:
        """Called after Faraday finished speaking a line."""
        text = (text or "").strip()
        if self._pending_doorbell or text.endswith("?"):
            self._cancel_doorbell_timer()
            self._pending_doorbell = False
            self.listen_once("question" if text.endswith("?") else "doorbell")

    def on_doorbell(self) -> None:
        """A visitor rang: listen after the greeting, or after 10 s regardless."""
        self._owner_until = 0.0
        self._pending_doorbell = True
        self._cancel_doorbell_timer()
        self._doorbell_timer = threading.Timer(DOORBELL_FALLBACK_S, self._doorbell_fallback)
        self._doorbell_timer.daemon = True
        self._doorbell_timer.start()

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

    def _opened(self, purpose: str, reason: str) -> None:
        self.windows_opened += 1
        self.out(f"  [mic] listening ({reason}{', owner' if purpose == OWNER else ''}),"
                 f" up to {self.max_s:.0f} s")

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
                with self._lock:
                    self._purpose = None
                    if purpose != "wake" and self._request is None:
                        self._request = (purpose, reason)      # try again shortly
                time.sleep(0.1)
                continue
            if purpose != "wake":
                self._opened(purpose, reason)
            # Wait for the segment to end. A window asked for meanwhile takes over
            # the open wake segment in place (no new stream, nothing lost).
            while not self._seg_done.wait(0.05):
                if self._stop.is_set():
                    self.recorder.cancel()
                    break
                with self._lock:
                    req = self._request if self._purpose == "wake" else None
                if req is not None and self._quiet():
                    with self._lock:
                        if self._purpose != "wake" or self._request is None:
                            continue
                        self._request = None
                        self._purpose, self._reason = req
                    if self.recorder.adjust(**self._rules(req[0])):
                        self._opened(*req)

    def _segment_done(self, wav: Optional[Path], seconds: float, spoke: bool) -> None:
        with self._lock:
            purpose, reason = self._purpose, self._reason
            self._purpose = None
        self._seg_done.set()
        if purpose == "wake" or purpose is None:
            if spoke and wav is not None:
                threading.Thread(target=self._wake_check, args=(wav, seconds), daemon=True).start()
            return
        if not spoke or wav is None:
            self.out("  [mic] nobody spoke")
            return
        self.out(f"  [mic] heard {seconds:.1f} s of speech")
        channel = purpose
        if purpose == VISITOR and reason == "question" and time.monotonic() < self._owner_until:
            channel = OWNER                    # Faraday asked the owner something back
        threading.Thread(target=self._transcribe, args=(wav, seconds),
                         kwargs={"channel": channel, "diarize": channel == VISITOR},
                         daemon=True).start()

    def _doorbell_fallback(self) -> None:
        if self._pending_doorbell:
            self._pending_doorbell = False
            self.listen_once("doorbell")

    def _cancel_doorbell_timer(self) -> None:
        if self._doorbell_timer is not None:
            self._doorbell_timer.cancel()
            self._doorbell_timer = None

    # ---- wake phrase ------------------------------------------------------------------
    def _wake_check(self, wav: Path, seconds: float) -> None:
        try:
            text = self.stt.transcribe(wav)
        except Exception:
            log.exception("wake STT failed")
            text = None
        rest = strip_wake(text or "", self.wake_phrase)
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
        # Just "Hey Faraday": the segment recording right now becomes the request
        # window, so words that follow at once aren't lost.
        with self._lock:
            promote = self._purpose == "wake"
            if promote:
                self._purpose, self._reason = OWNER, "wake"
        already_talking = promote and self.recorder.window_state().get("speech_started")
        if promote and self.recorder.adjust(restart=not already_talking, **self._rules(OWNER)):
            self._opened(OWNER, "wake")
        else:
            self.listen_once("wake", purpose=OWNER)
        if self.chime is not None and not already_talking:
            self.out("  [wake] yes? (chime)")
            try:
                self.chime()               # mutes the mic around itself
            except Exception:
                log.exception("chime failed")
        else:
            self.out("  [wake] yes?")

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
        if channel == OWNER:
            self._owner_until = time.monotonic() + OWNER_FOLLOWUP_S
        self.out(f"  [stt] heard {len(text.split())} word(s) ({channel})"
                 + (f", {data['voices']} voice(s)" if "voices" in data else ""))
        self.submit(make_event(EventType.SPEECH_HEARD, SpeechHeardData(**data), source))
