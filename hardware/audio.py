"""Microphone and speaker, via sounddevice + soundfile.

Hands-free (decided 2026-11-14): audio is used only inside a *listening
window*: after a doorbell, after Faraday asks a question, or for the wake
listener. A window records for up to `max_s`, stops about `silence_s` after
the speaker stops (energy VAD), or after `no_speech_s` if nobody spoke. One
mic stream stays open so back-to-back windows have no gaps; while Faraday
speaks it is muted (MicGate), so Faraday never hears itself.

    gate = MicGate()
    rec = Recorder.from_settings(settings, gate)
    rec.listen(on_done)                 # on_done(wav_path_or_None, seconds, spoke)

The older start()/stop() pair still exists for `python -m hardware.check record`.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import numpy as np

from hardware.settings import REPO_ROOT

log = logging.getLogger("hardware.audio")

DEFAULT_MAX_S = 15.0       # longest window (owner 2026-11-14: 6 s cut people off)
DEFAULT_SILENCE_S = 1.8    # speaker stopped this long -> window ends (0.8 cut mid-sentence)
DEFAULT_NO_SPEECH_S = 8.0  # nobody spoke for this long -> window ends with nothing


class MicGate:
    """Muted while anything plays on the speaker (Faraday's voice, the intercom);
    the recorder ignores audio meanwhile. Counted, so the intercom and TTS can
    overlap: the mic opens again only when both are done."""

    def __init__(self) -> None:
        self._count = 0
        self._lock = threading.Lock()
        self._quiet_since = time.monotonic()

    @property
    def muted(self) -> bool:
        return self._count > 0

    def mute(self) -> None:
        with self._lock:
            self._count += 1

    def unmute(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)
            if self._count == 0:
                self._quiet_since = time.monotonic()

    def quiet_for(self, seconds: float) -> bool:
        """Unmuted, and for at least `seconds` (the speaker has really finished)."""
        with self._lock:
            return self._count == 0 and time.monotonic() - self._quiet_since >= seconds

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
    """Listening windows over ONE microphone stream that stays open (no gaps).

    The stream opens on the first listen() and stays open until close(). While
    no window is open the last `preroll_s` of audio is kept, so a window (or the
    next wake segment) starts with the moment just before it: nothing said
    between two windows is lost. Audio is dropped while the gate is muted.

    A window ends: by silence (`silence_s` after speech), by no speech at all
    (`no_speech_s`; None = wait for speech forever, the wake listener), by
    `max_s` (counted from the window's start, or from the first speech when
    no_speech_s is None), or by cancel(). adjust() changes an open window's
    rules in place (a wake segment becoming the owner's request window).
    """

    PREROLL_S = 0.5
    HARD_CAP_S = 90.0      # wall-clock backstop: a stream that delivers nothing

    def __init__(self, gate: Optional[MicGate] = None, sample_rate: int = 16000,
                 device: Any = None, max_s: float = 8.0,
                 audio_dir: Path = REPO_ROOT / "captures" / "audio",
                 stream_factory: Callable[..., Any] = _default_stream,
                 preroll_s: float = PREROLL_S):
        self.gate = gate or MicGate()
        self.sample_rate, self.device, self.max_s = sample_rate, device, max_s
        self.audio_dir = audio_dir
        self.stream_factory = stream_factory
        self.preroll = int(preroll_s * sample_rate)
        self._frames: List[np.ndarray] = []          # start()/stop() recording
        self._stream = None                          # start()/stop() stream
        self._live = None                            # the persistent listening stream
        self._ring: Deque[np.ndarray] = deque()
        self._ring_n = 0
        self._lock = threading.Lock()
        self._started = 0.0
        self._timer: Optional[threading.Timer] = None
        self.on_auto_stop: Optional[Callable[[], None]] = None
        self._window: Optional[Dict[str, Any]] = None

    @classmethod
    def from_settings(cls, settings: Dict[str, Any], gate: Optional[MicGate] = None) -> "Recorder":
        a = settings.get("audio", {})
        captures = settings.get("paths", {}).get("captures", "captures")
        return cls(gate, int(a.get("sample_rate", 16000)), a.get("mic_device"),
                   float(a.get("max_record_s", 8)), REPO_ROOT / captures / "audio")

    @property
    def recording(self) -> bool:
        """start()/stop() recording in progress (hardware.check)."""
        return self._stream is not None

    @property
    def window_open(self) -> bool:
        return self._window is not None

    def window_state(self) -> Dict[str, Any]:
        w = self._window
        if w is None:
            return {"open": False, "speech_started": False, "elapsed_s": 0.0}
        return {"open": True, "speech_started": w["vad"].speech_started,
                "elapsed_s": (w["samples"] - w["base"]) / float(self.sample_rate)}

    # ---- the persistent stream -----------------------------------------------------
    def _ensure_live(self) -> None:
        if self._live is None:
            stream = self.stream_factory(samplerate=self.sample_rate, channels=1, dtype="int16",
                                         device=self.device, callback=self._live_callback)
            try:
                stream.start()
            except Exception:
                try:
                    stream.close()
                except Exception:
                    pass
                raise
            self._live = stream               # only a stream that really started

    def close(self) -> None:
        """Stop listening altogether (app shutdown)."""
        self.cancel()
        live, self._live = self._live, None
        if live is not None:
            try:
                live.stop()
                live.close()
            except Exception:
                log.exception("closing the microphone failed")

    def _live_callback(self, indata, frames, time_info, status) -> None:
        if self.gate.muted:
            # Faraday is speaking: don't hear ourselves, and forget what came just
            # before, so the next window doesn't start with stale audio
            with self._lock:
                self._ring.clear()
                self._ring_n = 0
                w = self._window
                if w is not None and w["dirty"] and not w["done"].is_set():
                    self._restart_locked(w, keep_preroll=False)
            return
        chunk = np.array(indata, copy=True).reshape(-1)
        with self._lock:
            w = self._window
            if w is None or w["done"].is_set():
                self._ring.append(chunk)
                self._ring_n += len(chunk)
                while self._ring and self._ring_n - len(self._ring[0]) >= self.preroll:
                    self._ring_n -= len(self._ring.popleft())
                return
            w["frames"].append(chunk)
            w["kept"] += len(chunk)
            w["samples"] += len(chunk)
            w["dirty"] = True
            vad = w["vad"]
            vad.feed(chunk)
            if vad.speech_started:
                if w["speech_at"] is None:
                    w["speech_at"] = w["samples"]
            else:
                # nobody talking yet: keep only the preroll, so a long wait (the
                # wake listener) doesn't pile up audio
                while w["frames"] and w["kept"] - len(w["frames"][0]) >= self.preroll:
                    w["kept"] -= len(w["frames"].pop(0))
            self._check_end(w)

    def _check_end(self, w: Dict[str, Any]) -> None:
        vad, rate = w["vad"], float(self.sample_rate)
        since = (w["samples"] - w["base"]) / rate
        if w["no_speech_s"] is None:         # wake: max counts from the first speech
            length = ((w["samples"] - w["speech_at"]) / rate) if w["speech_at"] is not None else 0.0
        else:
            length = since
        if ((length >= w["max_s"])
                or (vad.speech_started and vad.silence_s >= w["silence_s"])
                or (not vad.speech_started and w["no_speech_s"] is not None
                    and since >= w["no_speech_s"])):
            w["spoke"] = vad.speech_started
            w["done"].set()

    # ---- hands-free window -------------------------------------------------------
    def listen(self, on_done: Callable[[Optional[Path], float, bool], None],
               max_s: float = DEFAULT_MAX_S, silence_s: float = DEFAULT_SILENCE_S,
               no_speech_s: Optional[float] = DEFAULT_NO_SPEECH_S) -> bool:
        """Open one listening window. Returns False if one is already open.
        on_done(wav or None, seconds, spoke) runs on a worker thread when the
        window ends. Nothing is saved when nobody spoke."""
        with self._lock:
            if self._window is not None or self.recording:
                return False
            vad = EnergyVad(self.sample_rate)
            pre = list(self._ring)
            for chunk in pre:                 # someone may already be talking
                vad.feed(chunk)
            self._ring.clear()
            self._ring_n = 0
            window = {"max_s": max_s, "silence_s": silence_s, "no_speech_s": no_speech_s,
                      "frames": pre, "kept": sum(len(c) for c in pre), "samples": 0, "base": 0,
                      "speech_at": 0 if vad.speech_started else None, "vad": vad,
                      "spoke": False, "cancelled": False, "done": threading.Event(),
                      "epoch": 0, "dirty": bool(pre)}
            self._window = window
        try:
            self._ensure_live()
        except Exception:
            log.exception("could not open the microphone")
            with self._lock:
                self._window = None
            return False
        threading.Thread(target=self._finish_window, args=(window, on_done),
                         name="faraday-listen", daemon=True).start()
        return True

    def adjust(self, restart: bool = True, **rules: Any) -> bool:
        """Change the open window's max_s / silence_s / no_speech_s. restart=True
        counts no_speech_s and max_s from now. False if no window is open."""
        with self._lock:
            w = self._window
            if w is None or w["done"].is_set():
                return False
            w.update({k: v for k, v in rules.items() if k in ("max_s", "silence_s", "no_speech_s")})
            if restart:
                self._restart_locked(w, keep_preroll=True)
            return True

    def _restart_locked(self, w: Dict[str, Any], keep_preroll: bool) -> None:
        """Start the window's rules over from now: timers (incl. the wall-clock
        backstop), speech detection and audio. keep_preroll keeps the last
        `preroll_s` (said just now, after Faraday stopped) and feeds it to the
        new detector, so someone already talking is caught."""
        frames: List[np.ndarray] = []
        if keep_preroll:
            n = 0
            for chunk in reversed(w["frames"]):
                if n >= self.preroll:
                    break
                frames.insert(0, chunk)
                n += len(chunk)
        vad = EnergyVad(self.sample_rate)
        for chunk in frames:
            vad.feed(chunk)
        w.update(frames=frames, kept=sum(len(c) for c in frames), base=w["samples"], vad=vad,
                 speech_at=w["samples"] if vad.speech_started else None,
                 dirty=bool(frames), epoch=w["epoch"] + 1)

    def cancel(self) -> None:
        """End the current window now; its on_done gets (None, seconds, False)."""
        window = self._window
        if window is not None:
            window["cancelled"] = True
            window["done"].set()

    def _finish_window(self, window: Dict[str, Any], on_done) -> None:
        # Wall-clock backstop for a stream that never delivers audio. Muted time
        # doesn't count (a window opened just before Faraday talks waits for it).
        unmuted, t0, epoch = 0.0, time.monotonic(), window["epoch"]
        while not window["done"].wait(0.1):
            if window["no_speech_s"] is None or window["epoch"] != epoch:
                # waiting for speech (wake), or the rules just restarted (a wake
                # segment became a window): the backstop counts from now
                unmuted, t0, epoch = 0.0, time.monotonic(), window["epoch"]
                if window["no_speech_s"] is None:
                    continue
            if not self.gate.muted:
                unmuted += 0.1
            limit = max(window["max_s"], window["no_speech_s"])
            if unmuted > limit + 2.0 or time.monotonic() - t0 > self.HARD_CAP_S:
                break
        with self._lock:
            frames = window["frames"]
            window["done"].set()
            if self._window is window:
                self._window = None
        audio = np.concatenate(frames).reshape(-1) if frames else None
        spoke = bool(window["spoke"]) and not window["cancelled"] and audio is not None
        if spoke:
            seconds = len(audio) / float(self.sample_rate)
        else:
            seconds = window["samples"] / float(self.sample_rate)
        path = self.save(audio) if spoke else None
        try:
            on_done(path, seconds, spoke)
        except Exception:
            log.exception("listen on_done failed")

    # ---- plain start/stop (hardware.check record / mute) ---------------------------
    def _callback(self, indata, frames, time_info, status) -> None:
        if self.gate.muted:
            return
        with self._lock:
            self._frames.append(np.array(indata, copy=True).reshape(-1))

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


def chime_samples(rate: int = 24000) -> np.ndarray:
    """A short soft two-note chime (generated, no asset): float32 in [-1, 1]."""
    out = []
    for freq, secs in ((660.0, 0.09), (990.0, 0.14)):
        t = np.arange(int(rate * secs)) / rate
        env = np.minimum(1.0, np.minimum(t, secs - t) / 0.02)       # 20 ms fade in/out
        out.append(0.12 * env * np.sin(2 * np.pi * freq * t))
    return np.concatenate(out).astype(np.float32)


def play_chime(gate: Optional[MicGate] = None, device: Any = None) -> None:
    """Play the chime with the mic muted (so it isn't heard as speech)."""
    import sounddevice as sd
    if gate is not None:
        gate.mute()
    try:
        sd.play(chime_samples(), 24000, device=device)
        sd.wait()
    except Exception as e:
        log.warning("chime failed: %r", e)
    finally:
        if gate is not None:
            time.sleep(0.1)
            gate.unmute()
