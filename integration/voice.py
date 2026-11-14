"""Push-to-talk: record -> STT -> speech_heard event into the app queue."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from shared import Event, EventType, Source, SpeechHeardData, make_event

from hardware.camera import relative

log = logging.getLogger("integration.voice")


class Voice:
    def __init__(self, recorder: Any, stt: Any, submit: Callable[[Event], None],
                 out: Callable[[str], None] = print):
        self.recorder, self.stt, self.submit, self.out = recorder, stt, submit, out
        self._lock = threading.Lock()
        recorder.on_auto_stop = lambda: self.stop(auto=True)

    @property
    def recording(self) -> bool:
        return self.recorder.recording

    def toggle(self) -> None:
        self.stop() if self.recording else self.start()

    def start(self) -> None:
        with self._lock:
            if self.recording:
                return
            if self.recorder.gate.muted:
                self.out("  [mic] Faraday is talking; try again in a second")
                return
            self.recorder.start()
        self.out("  [mic] recording... press Enter to stop")

    def stop(self, auto: bool = False) -> Optional[threading.Thread]:
        with self._lock:
            if not self.recording:
                return None
            wav, seconds = self.recorder.stop()
        self.out(f"  [mic] {'time limit, ' if auto else ''}stopped after {seconds:.1f}s")
        if wav is None:
            self.out("  [mic] heard nothing")
            return None
        t = threading.Thread(target=self._transcribe, args=(wav, seconds), daemon=True)
        t.start()
        return t

    def _transcribe(self, wav, seconds: float) -> None:
        text = self.stt.transcribe(wav)
        if not text:
            self.out("  [stt] no words recognized" + ("" if self.stt.available
                                                      else " (no STT on this OS: use t <text>)"))
            return
        self.out(f'  [stt] heard: "{text}"')
        self.submit(make_event(EventType.SPEECH_HEARD,
                               SpeechHeardData(text=text, audio_path=relative(wav),
                                               duration_s=round(seconds, 2)),
                               Source.SPEECH))
