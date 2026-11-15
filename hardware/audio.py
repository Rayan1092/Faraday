"""Microphone and speaker, via sounddevice + soundfile.

Hands-free (decided 2026-11-14): the mic is open only during a *listening
window*. A window is opened after a doorbell, after Faraday asks a question,
by the dashboard Listen button, or by the wake listener. It records for up to
`max_s`, stops about `silence_s` after the speaker stops (energy VAD), or after
`no_speech_s` if nobody spoke at all. Outside windows the mic is closed, and
while Faraday speaks it is muted (MicGate), so Faraday never hears itself.

    gate = MicGate()
    rec = Recorder.from_settings(settings, gate)
    rec.listen(on_done)                 # on_done(wav_path_or_None, seconds, spoke)

The older start()/stop() pair still exists for `python -m hardware.check record`.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from hardware.settings import REPO_ROOT

log = logging.getLogger("hardware.audio")

DEFAULT_MAX_S = 6.0        # longest window
DEFAULT_SILENCE_S = 0.8    # speaker stopped for this long -> window ends
DEFAULT_NO_SPEECH_S = 3.0  # nobody spoke for this long -> window ends with nothing


class MicGate:
    """Set while the speaker talks; the recorder ignores audio meanwhile."""

    def __init__(self) -> None:
        self._muted = threading.Event()

    @property
    def muted(self) -> bool:
        return self._muted.is_set()

    def mute(self) -> None:
        self._muted.set()

    def unmute(self) -> None:
        self._muted.clear()

    def wait_unmuted(self, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while self.muted and time.monotonic() < end:
            time.sleep(0.05)
        return not self.muted


class EnergyVad:
    """Tiny voice activity detector on int16 frames: RMS against a noise floor
    taken from the first ~300 ms. No dependencies, good enough for a quiet
    hallway; the Argmax STT does the real work on what we send it."""

    def __init__(self, sample_rate: int, frame_ms: int = 30, floor_frames: int = 10,
                 ratio: float = 3.0, min_rms: float = 350.0, start_frames: int = 2):
        self.frame = max(1, int(sample_rate * frame_ms / 1000))
        self.frame_s = self.frame / float(sample_rate)
        self.floor_frames, self.ratio, self.min_rms, self.start_frames = floor_frames, ratio, min_rms, start_frames
        self.threshold: Optional[float] = None
        self.speech_started = False
        self.silence_frames = 0
        self._floor: List[float] = []
        self._seen = 0
        self._run = 0
        self._rest = np.empty(0, dtype=np.int16)

    def feed(self, samples: np.ndarray) -> None:
        data = np.concatenate([self._rest, samples.reshape(-1).astype(np.int16)])
        n = (len(data) // self.frame) * self.frame
        for i in range(0, n, self.frame):
            self._frame(data[i:i + self.frame])
        self._rest = data[n:]

    def _frame(self, f: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))
        if self.threshold is None:
            threshold = self.min_rms * 2      # someone may start talking at once
            self._seen += 1
            if rms <= threshold:              # only quiet frames tell us the room's noise
                self._floor.append(rms)
            if self._seen >= self.floor_frames:
                self.threshold = (max(self.min_rms, min(self._floor) * self.ratio)
                                  if self._floor else threshold)
        else:
            threshold = self.threshold
        if rms > threshold:
            self._run += 1
            self.silence_frames = 0
            if self._run >= self.start_frames:
                self.speech_started = True
        else:
            self._run = 0
            if self.speech_started:
                self.silence_frames += 1

    @property
    def silence_s(self) -> float:
        return self.silence_frames * self.frame_s


def _default_stream(**kwargs: Any):
    import sounddevice as sd
    return sd.InputStream(**kwargs)


class Recorder:
    def __init__(self, gate: Optional[MicGate] = None, sample_rate: int = 16000,
                 device: Any = None, max_s: float = 8.0,
                 audio_dir: Path = REPO_ROOT / "captures" / "audio",
                 stream_factory: Callable[..., Any] = _default_stream):
        self.gate = gate or MicGate()
        self.sample_rate, self.device, self.max_s = sample_rate, device, max_s
        self.audio_dir = audio_dir
        self.stream_factory = stream_factory
        self._frames: List[np.ndarray] = []
        self._stream = None
        self._lock = threading.Lock()
        self._started = 0.0
        self._timer: Optional[threading.Timer] = None
        self.on_auto_stop: Optional[Callable[[], None]] = None
        self._window: Optional[Dict[str, Any]] = None
        self._vad: Optional[EnergyVad] = None

    @classmethod
    def from_settings(cls, settings: Dict[str, Any], gate: Optional[MicGate] = None) -> "Recorder":
        a = settings.get("audio", {})
        captures = settings.get("paths", {}).get("captures", "captures")
        return cls(gate, int(a.get("sample_rate", 16000)), a.get("mic_device"),
                   float(a.get("max_record_s", 8)), REPO_ROOT / captures / "audio")

    @property
    def recording(self) -> bool:
        return self._stream is not None

    # ---- shared ------------------------------------------------------------------
    def _callback(self, indata, frames, time_info, status) -> None:
        if self.gate.muted:
            return                       # Faraday is speaking: don't hear ourselves
        window = self._window
        with self._lock:
            if window is not None and window["done"].is_set():
                return                   # window already decided, ignore the tail
            chunk = np.array(indata, copy=True).reshape(-1)
            self._frames.append(chunk)
        if window is None or self._vad is None:
            return
        window["samples"] += len(chunk)
        self._vad.feed(chunk)
        elapsed = window["samples"] / float(self.sample_rate)
        vad = self._vad
        if (elapsed >= window["max_s"]
                or (vad.speech_started and vad.silence_s >= window["silence_s"])
                or (not vad.speech_started and elapsed >= window["no_speech_s"])):
            window["spoke"] = vad.speech_started
            window["done"].set()

    def _open(self) -> None:
        with self._lock:
            self._frames = []
        self._stream = self.stream_factory(samplerate=self.sample_rate, channels=1, dtype="int16",
                                           device=self.device, callback=self._callback)
        self._started = time.monotonic()
        self._stream.start()

    def _collect(self) -> Tuple[Optional[np.ndarray], float]:
        stream, self._stream = self._stream, None
        if stream is None:
            return None, 0.0
        stream.stop()
        stream.close()
        with self._lock:
            frames, self._frames = self._frames, []
        if not frames:
            return None, 0.0
        audio = np.concatenate(frames).reshape(-1)
        return audio, len(audio) / float(self.sample_rate)

    def save(self, audio: np.ndarray) -> Path:
        import soundfile as sf
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        path = self.audio_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}.wav"
        sf.write(str(path), audio, self.sample_rate, subtype="PCM_16")
        return path

    # ---- hands-free window -------------------------------------------------------
    def listen(self, on_done: Callable[[Optional[Path], float, bool], None],
               max_s: float = DEFAULT_MAX_S, silence_s: float = DEFAULT_SILENCE_S,
               no_speech_s: float = DEFAULT_NO_SPEECH_S) -> bool:
        """Open one listening window. Returns False if the mic is already in use.
        on_done(wav or None, seconds, spoke) runs on a worker thread when the
        window ends: by silence after speech, by no speech at all, by max_s, or
        by cancel(). Nothing is saved when nobody spoke."""
        with self._lock:
            if self.recording or self._window is not None:
                return False
            window = {"max_s": max_s, "silence_s": silence_s, "no_speech_s": no_speech_s,
                      "samples": 0, "spoke": False, "cancelled": False,
                      "done": threading.Event()}
            self._vad = EnergyVad(self.sample_rate)
            self._window = window
        try:
            self._open()
        except Exception:
            log.exception("could not open the microphone")
            with self._lock:
                self._stream, self._window, self._vad = None, None, None
            return False
        threading.Thread(target=self._finish_window, args=(window, on_done),
                         name="faraday-listen", daemon=True).start()
        return True

    def cancel(self) -> None:
        """End the current window now; its on_done gets (None, seconds, False)."""
        window = self._window
        if window is not None:
            window["cancelled"] = True
            window["done"].set()

    def _finish_window(self, window: Dict[str, Any], on_done) -> None:
        # Wall-clock backstop: with the mic muted the whole time no frame ever
        # arrives, so the sample-based rules never fire.
        window["done"].wait(window["max_s"] + 2.0)
        # Close the stream first and only then free the mic: until then no other
        # window can open, so two streams never overlap and nothing clears the
        # next window's state by mistake.
        stream = self._stream
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            self._stream = None
            frames, self._frames = self._frames, []
            if self._window is window:
                self._window, self._vad = None, None
        audio = np.concatenate(frames).reshape(-1) if frames else None
        seconds = len(audio) / float(self.sample_rate) if audio is not None else 0.0
        spoke = bool(window["spoke"]) and not window["cancelled"] and audio is not None
        path = self.save(audio) if spoke else None
        try:
            on_done(path, seconds, spoke)
        except Exception:
            log.exception("listen on_done failed")

    # ---- plain start/stop (hardware.check record) ---------------------------------
    def start(self) -> None:
        if self.recording:
            return
        self._open()
        self._timer = threading.Timer(self.max_s, self._auto_stop)
        self._timer.daemon = True
        self._timer.start()

    def _auto_stop(self) -> None:
        if self.recording and self.on_auto_stop:
            self.on_auto_stop()

    def stop(self) -> Tuple[Optional[Path], float]:
        """Stop and save. Returns (wav path or None if nothing heard, seconds)."""
        if self._timer:
            self._timer.cancel()
        audio, seconds = self._collect()
        if audio is None or seconds < 0.3:
            return None, seconds
        return self.save(audio), seconds


def play_wav(path: Path, device: Any = None) -> None:
    """Play a wav file and wait until it's done."""
    import sounddevice as sd
    import soundfile as sf
    data, rate = sf.read(str(path), dtype="float32")
    sd.play(data, rate, device=device)
    sd.wait()
