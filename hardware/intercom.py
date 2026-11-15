"""Intercom: live audio from the owner's browser, played on the door speaker.

The Controls page captures the microphone in the browser and posts it in small
chunks of 16-bit mono PCM. Each chunk is queued here and played by one output
stream. While anything is queued or playing, the door mic is muted (MicGate),
so the visitor hears the owner without Faraday transcribing the owner's voice.
The stream closes, and the mic opens again, `hold_s` after the last chunk.

    ic = Intercom(gate)
    ic.feed(pcm_bytes, 16000)      # from POST /api/intercom
    ic.end()                       # owner released the button
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Callable, Deque, Optional

import numpy as np

from hardware.audio import MicGate

log = logging.getLogger("hardware.intercom")

PREBUFFER_S = 0.12      # collect this much before starting, to ride over network jitter
HOLD_S = 0.8            # no new chunk for this long and nothing queued -> done
MAX_QUEUE_S = 3.0       # drop the oldest audio beyond this, so lag can't build up


def _default_output(**kwargs: Any):
    import sounddevice as sd
    return sd.OutputStream(**kwargs)


class Intercom:
    def __init__(self, gate: Optional[MicGate] = None, device: Any = None,
                 stream_factory: Callable[..., Any] = _default_output, hold_s: float = HOLD_S):
        self.gate = gate or MicGate()
        self.device, self.stream_factory, self.hold_s = device, stream_factory, hold_s
        self._queue: Deque[np.ndarray] = collections.deque()
        self._queued = 0                  # samples waiting
        self._rate: Optional[int] = None
        self._stream: Any = None
        self._started = False             # prebuffer reached, playback running
        self._last_chunk = 0.0
        self._ended = False
        self._lock = threading.Lock()
        self._watch: Optional[threading.Thread] = None
        self.sessions = 0
        self.played = 0                   # samples handed to the speaker
        self.dropped = 0                  # samples dropped to keep lag down

    @property
    def active(self) -> bool:
        return self._stream is not None

    def feed(self, pcm: bytes, rate: int) -> None:
        """Queue one chunk of int16 mono PCM. Opens the speaker on the first one."""
        samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.int16)
        if samples.size == 0:
            return
        old = None
        with self._lock:
            if self._stream is not None and rate != self._rate:
                old = self._detach_locked()   # browser changed its rate: start a new stream
                self._queue.clear()
                self._queued = 0
        self._finish(old)
        with self._lock:
            if self._stream is None:
                self._open_locked(rate)
            self._queue.append(samples)
            self._queued += samples.size
            limit = int(MAX_QUEUE_S * rate)
            while self._queued > limit and len(self._queue) > 1:
                old = self._queue.popleft()
                self._queued -= old.size
                self.dropped += old.size
            if not self._started and self._queued >= int(PREBUFFER_S * rate):
                self._started = True
            self._last_chunk = time.monotonic()
            self._ended = False

    def end(self) -> None:
        """The owner let go: finish what's queued, then close."""
        with self._lock:
            self._ended = True
            self._started = True          # play out even a short last bit

    def stop(self) -> None:
        with self._lock:
            self._queue.clear()
            self._queued = 0
            stream = self._detach_locked()
        self._finish(stream)

    # ---- internals ---------------------------------------------------------------
    def _open_locked(self, rate: int) -> None:
        self._rate = rate
        self._started, self._ended = False, False
        self.gate.mute()                  # door mic off while the owner talks
        self.sessions += 1
        self._stream = self.stream_factory(samplerate=rate, channels=1, dtype="int16",
                                           device=self.device, callback=self._callback)
        self._stream.start()
        if self._watch is None or not self._watch.is_alive():
            self._watch = threading.Thread(target=self._watchdog, name="faraday-intercom",
                                           daemon=True)
            self._watch.start()

    def _detach_locked(self) -> Any:
        """Take the stream out (under the lock); _finish() stops it outside the
        lock, because stopping waits for the audio callback, which needs the lock."""
        stream, self._stream = self._stream, None
        self._started = False
        return stream

    def _finish(self, stream: Any) -> None:
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            log.exception("closing the intercom stream failed")
        self.gate.unmute()

    def _callback(self, outdata, frames, time_info, status) -> None:
        out = np.zeros(frames, dtype=np.int16)
        with self._lock:
            if self._started:
                filled = 0
                while filled < frames and self._queue:
                    chunk = self._queue[0]
                    take = min(frames - filled, chunk.size)
                    out[filled:filled + take] = chunk[:take]
                    filled += take
                    if take == chunk.size:
                        self._queue.popleft()
                    else:
                        self._queue[0] = chunk[take:]
                self._queued -= filled
                self.played += filled
        outdata[:] = out.reshape(-1, 1)

    def _watchdog(self) -> None:
        while True:
            time.sleep(0.05)
            with self._lock:
                if self._stream is None:
                    return
                quiet = time.monotonic() - self._last_chunk
                done = self._queued == 0 and (self._ended or quiet > self.hold_s)
                stream = self._detach_locked() if done else None
            if done:
                self._finish(stream)
                return
