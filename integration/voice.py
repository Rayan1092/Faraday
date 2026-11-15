"""Hands-free voice: listening windows, the "Hey Faraday" wake phrase, STT.

Windows (owner decision 2026-11-14): the mic opens only
- after a doorbell (once Faraday has greeted the visitor, or after 4 s),
- after every line Faraday says that ends with a question mark,
- when the dashboard Listen button (board.listen_hook) or the console asks,
- for the wake listener (below).
A window records up to ~6 s, ends when the speaker stops, and its audio goes
to Argmax STT -> speech_heard (source "speech"). The mic is muted while
Faraday speaks; a window asked for during speech opens when speech ends.

Wake listener: while idle, short windows run back to back. A transcript is
acted on only if it starts with "hey faraday" (then the rest is the owner's
request). Anything else, including plain "Faraday", is dropped and its audio
deleted. On Windows there is no STT, so the wake listener stays off.

Nothing here prints raw transcripts: the passphrase is redacted only inside
the pipeline.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from shared import Event, EventType, Source, SpeechHeardData, make_event

from hardware.camera import relative

log = logging.getLogger("integration.voice")

DEFAULT_WAKE = "hey faraday"
WAKE_WINDOW_S = 4.0
WAKE_NO_SPEECH_S = 2.0
DOORBELL_FALLBACK_S = 4.0     # open the window even if Faraday never greeted


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
                 diarizer: Any = None):
        self.recorder, self.stt, self.submit, self.out = recorder, stt, submit, out
        listen = listen or {}
        self.max_s = float(listen.get("max_s", 6.0))
        self.silence_s = float(listen.get("silence_s", 0.8))
        self.no_speech_s = float(listen.get("no_speech_s", 3.0))
        wake = wake or {}
        self.wake_enabled = bool(wake.get("enabled", True))
        self.wake_phrase = str(wake.get("phrase", DEFAULT_WAKE))
        self.wake_window_s = float(wake.get("window_s", WAKE_WINDOW_S))
        self.diarizer = diarizer
        self._lock = threading.Lock()
        # idle | wake (wake window open) | checking (wake audio being transcribed:
        # the loop waits, so the owner's request after "Hey Faraday." isn't eaten
        # by the next wake window) | visitor (a real listening window)
        self._state = "idle"
        self._pending_doorbell = False
        self._doorbell_timer: Optional[threading.Timer] = None
        self._stop = threading.Event()
        self._wake_thread: Optional[threading.Thread] = None
        self._window_end = threading.Event()
        self.windows_opened = 0
        self.wake_heard = 0
        self.wake_ignored = 0

    # ---- windows ------------------------------------------------------------------
    @property
    def listening(self) -> bool:
        return self._state == "visitor"

    def listen_once(self, reason: str = "button") -> bool:
        """Open one visitor window (dashboard Listen button, console `r`, question,
        doorbell). Returns False if one is already open."""
        with self._lock:
            if self._state == "visitor":
                return False
            if self._state == "wake":
                self.recorder.cancel()     # the wake window yields to the visitor
            self._state = "visitor"
        threading.Thread(target=self._open_visitor, args=(reason,), daemon=True).start()
        return True

    def _open_visitor(self, reason: str) -> None:
        if self.recorder.gate.muted:        # Faraday is talking: open when it stops
            self.recorder.gate.wait_unmuted(15.0)
        for _ in range(40):                 # the cancelled wake window may still be closing
            if self.recorder.listen(self._visitor_done, self.max_s, self.silence_s, self.no_speech_s):
                self.windows_opened += 1
                self.out(f"  [mic] listening ({reason}), up to {self.max_s:.0f} s")
                return
            time.sleep(0.05)
        with self._lock:
            self._state = "idle"
        self.out("  [mic] could not open the window (mic busy)")

    def _visitor_done(self, wav: Optional[Path], seconds: float, spoke: bool) -> None:
        with self._lock:
            self._state = "idle"
        self._window_end.set()
        if not spoke or wav is None:
            self.out("  [mic] nobody spoke")
            return
        self.out(f"  [mic] heard {seconds:.1f} s of speech")
        self._transcribe(wav, seconds)

    def on_said(self, text: str) -> None:
        """Called after Faraday finished speaking a line."""
        text = (text or "").strip()
        if self._pending_doorbell or text.endswith("?"):
            self._cancel_doorbell_timer()
            self._pending_doorbell = False
            self.listen_once("question" if text.endswith("?") else "doorbell")

    def on_doorbell(self) -> None:
        """A visitor rang: listen after the greeting, or after 4 s regardless."""
        self._pending_doorbell = True
        self._cancel_doorbell_timer()
        self._doorbell_timer = threading.Timer(DOORBELL_FALLBACK_S, self._doorbell_fallback)
        self._doorbell_timer.daemon = True
        self._doorbell_timer.start()

    def _doorbell_fallback(self) -> None:
        if self._pending_doorbell:
            self._pending_doorbell = False
            self.listen_once("doorbell")

    def _cancel_doorbell_timer(self) -> None:
        if self._doorbell_timer is not None:
            self._doorbell_timer.cancel()
            self._doorbell_timer = None

    # ---- STT (+ optional diarization) ----------------------------------------------
    def _transcribe(self, wav: Path, seconds: float, source: str = Source.SPEECH,
                    text_override: Optional[str] = None) -> None:
        voices_thread = None
        voices: Dict[str, Any] = {}
        if self.diarizer is not None:
            voices_thread = threading.Thread(
                target=lambda: voices.update(n=self.diarizer.count_voices(wav)), daemon=True)
            voices_thread.start()
        text = text_override if text_override is not None else self.stt.transcribe(wav)
        if not text:
            self.out("  [stt] no words recognized" + ("" if self.stt.available
                                                      else " (no STT on this OS: use t <text>)"))
            return
        data: Dict[str, Any] = {"text": text, "audio_path": relative(wav), "duration_s": round(seconds, 2)}
        if voices_thread is not None:
            voices_thread.join(0.5)        # diarization runs alongside STT; never delays the reply
            if voices.get("n") is not None:
                data["voices"] = int(voices["n"])
        self.out(f"  [stt] heard {len(text.split())} word(s)"
                 + (f", {data['voices']} voice(s)" if "voices" in data else ""))
        self.submit(make_event(EventType.SPEECH_HEARD, SpeechHeardData(**data), source))

    # ---- wake listener ---------------------------------------------------------------
    def start_wake(self) -> bool:
        if not self.wake_enabled:
            return False
        if not getattr(self.stt, "available", False):
            self.out("  [wake] off: no STT on this OS")
            return False
        self._stop.clear()
        self._wake_thread = threading.Thread(target=self._wake_loop, name="faraday-wake", daemon=True)
        self._wake_thread.start()
        self.out(f'  [wake] listening for "{self.wake_phrase}"')
        return True

    def stop_wake(self) -> None:
        self._stop.set()
        with self._lock:
            if self._state == "wake":
                self.recorder.cancel()
        if self._wake_thread is not None:
            self._wake_thread.join(3)
            self._wake_thread = None

    def _wake_loop(self) -> None:
        while not self._stop.is_set():
            if self.recorder.gate.muted:
                time.sleep(0.1)
                continue
            with self._lock:
                if self._state != "idle":
                    busy = True
                else:
                    busy = False
                    self._state = "wake"
            if busy:
                time.sleep(0.1)
                continue
            self._window_end.clear()
            if not self.recorder.listen(self._wake_done, self.wake_window_s, 0.6, WAKE_NO_SPEECH_S):
                with self._lock:
                    if self._state == "wake":
                        self._state = "idle"
                time.sleep(0.1)
                continue
            with self._lock:
                if self._state != "wake":    # a visitor window was asked for meanwhile
                    self.recorder.cancel()
            self._window_end.wait(self.wake_window_s + 3.0)

    def _wake_done(self, wav: Optional[Path], seconds: float, spoke: bool) -> None:
        check = False
        with self._lock:
            if self._state == "wake":
                check = spoke and wav is not None
                self._state = "checking" if check else "idle"
        self._window_end.set()
        if check:
            threading.Thread(target=self._wake_check, args=(wav, seconds), daemon=True).start()

    def _set_idle_if(self, state: str) -> None:
        with self._lock:
            if self._state == state:
                self._state = "idle"

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
            self._set_idle_if("checking")
            return
        self.wake_heard += 1
        if not rest:
            self.out("  [wake] yes?")      # just the phrase: the request comes next
            self.listen_once("wake")       # checking -> visitor, the loop stays paused
            return
        self.out("  [wake] owner request heard")
        self._set_idle_if("checking")
        self._transcribe(wav, seconds, text_override=rest)
