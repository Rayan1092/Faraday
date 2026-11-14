"""Microphone (push-to-talk) and speaker playback, via sounddevice + soundfile.

    gate = MicGate()                       # shared: the speaker mutes the mic
    rec = Recorder.from_settings(settings, gate)
    rec.start(); ...; path, seconds = rec.stop()     # captures/audio/<time>.wav

While gate.muted is set (Faraday is talking), incoming audio is dropped, so
Faraday never transcribes its own voice. Recording stops by itself after
audio.max_record_s.
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

    @classmethod
    def from_settings(cls, settings: Dict[str, Any], gate: Optional[MicGate] = None) -> "Recorder":
        a = settings.get("audio", {})
        captures = settings.get("paths", {}).get("captures", "captures")
        return cls(gate, int(a.get("sample_rate", 16000)), a.get("mic_device"),
                   float(a.get("max_record_s", 8)), REPO_ROOT / captures / "audio")

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status) -> None:
        if self.gate.muted:
            return                       # Faraday is speaking: don't hear ourselves
        with self._lock:
            self._frames.append(np.array(indata, copy=True))

    def start(self) -> None:
        if self.recording:
            return
        with self._lock:
            self._frames = []
        self._stream = self.stream_factory(samplerate=self.sample_rate, channels=1, dtype="int16",
                                           device=self.device, callback=self._callback)
        self._stream.start()
        self._started = time.monotonic()
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
        seconds = len(audio) / float(self.sample_rate)
        if seconds < 0.3:
            return None, seconds
        return self.save(audio), seconds

    def save(self, audio: np.ndarray) -> Path:
        import soundfile as sf
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        path = self.audio_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}.wav"
        sf.write(str(path), audio, self.sample_rate, subtype="PCM_16")
        return path


def play_wav(path: Path, device: Any = None) -> None:
    """Play a wav file and wait until it's done."""
    import sounddevice as sd
    import soundfile as sf
    data, rate = sf.read(str(path), dtype="float32")
    sd.play(data, rate, device=device)
    sd.wait()
