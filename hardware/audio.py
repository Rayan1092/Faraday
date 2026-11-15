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

import json
import logging
import shutil
import subprocess
import sys
import tempfile
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
                if w is not None and not w["done"].is_set():
                    if w["vad"].speech_started:
                        # someone was talking (a visitor when the owner presses the
                        # intercom): end the window with what was said, don't lose it
                        w["spoke"] = True
                        w["done"].set()
                    elif w["dirty"]:
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
        if keep_preroll and w["vad"].speech_started:
            frames = list(w["frames"])        # talking right now (after the last mute): keep it all
        elif keep_preroll:
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


# ---- the speaker -----------------------------------------------------------------
# audio.speaker_device (owner 2026-11-15, Mac test: cached lines and the intercom
# played on "Mac mini Speakers" while argmax-cli --play spoke on the system output,
# "onn Rugged Speaker"):
# - null: the system's default output. On the Mac a file plays with afplay, which
#   follows the current default exactly as argmax-cli --play does (PortAudio keeps
#   the default it saw when Faraday started); the intercom looks it up each time.
# - a number or a name: that OUTPUT device. A name never matches a device that
#   can't play (the Mac also lists an input-only "onn Rugged Speaker S"): the exact
#   name first (any case), else the shortest output name that contains it.
# Startup warns when that isn't the system output (speaker_report).

def _is_mac() -> bool:
    return sys.platform == "darwin"


def output_devices() -> List[Tuple[int, str]]:
    """(index, name) of every device that can play sound (PortAudio's list)."""
    import sounddevice as sd
    return [(i, str(d.get("name", ""))) for i, d in enumerate(sd.query_devices())
            if int(d.get("max_output_channels", 0) or 0) > 0]


def _unset(setting: Any) -> bool:
    return setting is None or (isinstance(setting, str) and not setting.strip())


def match_output(setting: Any, devices: List[Tuple[int, str]]) -> Optional[int]:
    """The output device index for audio.speaker_device (None when unset).
    Raises ValueError if it names no output device."""
    if _unset(setting):
        return None
    if isinstance(setting, bool):
        raise ValueError(f"{setting!r} is not a device")
    text = str(setting).strip()
    if isinstance(setting, int) or text.lstrip("-").isdigit():
        if any(i == int(text) for i, _ in devices):
            return int(text)
        raise ValueError(f"no output device number {text}")
    want = text.lower()
    exact = [i for i, n in devices if n.strip().lower() == want]
    if exact:
        return exact[0]
    part = sorted((len(n), i) for i, n in devices if want in n.lower())
    if part:
        return part[0][1]
    raise ValueError(f"no output device named {text!r}")


def _device_name(index: Optional[int], devices: List[Tuple[int, str]]) -> Optional[str]:
    return next((n for i, n in devices if i == index), None)


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


_ca_run_loop_set = False


def _coreaudio_default_output() -> Optional[str]:
    """The default output device's name, straight from CoreAudio (no PortAudio:
    it only knows the default from when this process started)."""
    import ctypes
    import ctypes.util

    class Address(ctypes.Structure):
        _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                    ("element", ctypes.c_uint32)]

    ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    get = ca.AudioObjectGetPropertyData
    get.argtypes = [ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    get.restype = ctypes.c_int32
    scope = _fourcc("glob")                          # kAudioObjectPropertyScopeGlobal
    global _ca_run_loop_set
    if not _ca_run_loop_set:
        # The HAL delivers "default device changed" on the main run loop, which this
        # app never runs (its main thread waits in input()), so the default would stay
        # the one from startup. A NULL run loop makes the HAL use its own thread.
        put = ca.AudioObjectSetPropertyData
        put.argtypes = [ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32, ctypes.c_void_p,
                        ctypes.c_uint32, ctypes.c_void_p]
        put.restype = ctypes.c_int32
        loop = ctypes.c_void_p(None)
        addr = Address(_fourcc("rnlp"), scope, 0)    # kAudioHardwarePropertyRunLoop
        put(1, ctypes.byref(addr), 0, None, ctypes.sizeof(loop), ctypes.byref(loop))
        _ca_run_loop_set = True
    device = ctypes.c_uint32(0)
    size = ctypes.c_uint32(ctypes.sizeof(device))
    addr = Address(_fourcc("dOut"), scope, 0)        # kAudioHardwarePropertyDefaultOutputDevice
    if get(1, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(device)) or not device.value:
        return None                                  # 1 = kAudioObjectSystemObject
    name = ctypes.c_void_p(None)
    size = ctypes.c_uint32(ctypes.sizeof(name))
    addr = Address(_fourcc("lnam"), scope, 0)        # kAudioObjectPropertyName (a CFString)
    if get(device.value, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(name)) \
            or not name.value:
        return None
    try:
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        buf = ctypes.create_string_buffer(1024)
        if not cf.CFStringGetCString(name, buf, ctypes.sizeof(buf), 0x08000100):   # UTF-8
            return None
        return buf.value.decode("utf-8", "replace")
    finally:
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease(name)


def default_output_from_profiler(data: Dict[str, Any]) -> Optional[str]:
    """The default output in `system_profiler SPAudioDataType -json` output."""
    for group in data.get("SPAudioDataType", []) or []:
        for item in group.get("_items", []) or []:
            if any("default_audio_output" in k and "yes" in str(v).lower() for k, v in item.items()):
                return item.get("_name")
    return None


def _profiler_default_output() -> Optional[str]:
    out = subprocess.run(["system_profiler", "SPAudioDataType", "-json"], capture_output=True,
                         text=True, timeout=10).stdout
    return default_output_from_profiler(json.loads(out or "{}"))


def system_output(profiler: bool = True) -> Optional[str]:
    """Mac: the name of the system's default output (System Settings > Sound >
    Output), where argmax-cli --play and afplay play. None elsewhere or if
    unreadable. profiler=False skips the slow system_profiler fallback."""
    if not _is_mac():
        return None
    reads = (_coreaudio_default_output, _profiler_default_output) if profiler else (_coreaudio_default_output,)
    for read in reads:
        try:
            name = read()
        except Exception as e:
            log.debug("default output via %s failed: %r", read.__name__, e)
            continue
        if name:
            return name
    return None


def system_output_index() -> Optional[int]:
    """PortAudio's index for the system's current default output (Mac), when
    PortAudio lists that device (only devices present at startup); else None.
    CoreAudio only (quick): it runs when the intercom opens."""
    name = system_output(profiler=False)
    if not name:
        return None
    want = name.strip().lower()
    return next((i for i, n in output_devices() if n.strip().lower() == want), None)


def resolve_speaker(setting: Any) -> Optional[int]:
    """audio.speaker_device -> the output device index (None = the system default).
    A name or number that isn't an output device is logged and the default is
    used: Faraday is never silent over a typo or an unplugged speaker."""
    if _unset(setting):
        return None
    try:
        return match_output(setting, output_devices())
    except Exception as e:
        log.warning("audio.speaker_device %r: %s; using the system default output", setting, e)
        return None


def speaker_report(setting: Any, devices: Optional[List[Tuple[int, str]]] = None,
                   system: Optional[str] = None, default_name: Optional[str] = None,
                   mac: Optional[bool] = None) -> List[Tuple[str, str]]:
    """Startup lines (level "OK" / "WARN", text): where Faraday's own sound goes
    (cached lines, the chime, the intercom). WARN when that isn't the system
    output, where argmax-cli --play speaks every live line."""
    mac = _is_mac() if mac is None else mac
    devices = output_devices() if devices is None else devices
    if system is None and mac:
        system = system_output()
    if _unset(setting):
        if mac and system:
            return [("OK", f"speaker: {system} (the system output, followed live like argmax-cli --play)")]
        if default_name is None:
            import sounddevice as sd
            default_name = str(sd.query_devices(kind="output")["name"])
        return [("OK", f"speaker: {default_name} (the system default output)")]
    try:
        name = _device_name(match_output(setting, devices), devices) or str(setting)
    except ValueError as e:
        outs = ", ".join(f'"{n}"' for _, n in devices) or "none"
        return [("WARN", f'audio.speaker_device "{setting}": {e}, so Faraday plays on the system '
                         f"output instead. Output devices: {outs}")]
    lines = [("OK", f'speaker: {name} (audio.speaker_device "{setting}")')]
    if system and system.strip().lower() != name.strip().lower():
        lines.append(("WARN", f'Faraday plays cached lines, the chime and the intercom on "{name}", but '
                              f'the system output is "{system}", where argmax-cli --play speaks. Make them '
                              f'the same: System Settings > Sound > Output > "{name}", or set '
                              f'"speaker_device": null.'))
    elif mac and not system:
        lines.append(("WARN", f"can't read the system output: make sure System Settings > Sound > Output "
                              f'is "{name}", where argmax-cli --play speaks.'))
    return lines


def _afplay(path: Path) -> bool:
    """Mac: play a file on the system's current default output (afplay ships
    with macOS). False if afplay is missing or failed."""
    exe = shutil.which("afplay")
    if exe is None:
        return False
    try:
        import soundfile as sf
        secs = float(sf.info(str(path)).duration)
    except Exception:
        secs = 30.0
    try:
        r = subprocess.run([exe, str(path)], capture_output=True, timeout=secs + 10)
    except subprocess.TimeoutExpired:
        log.warning("afplay timed out on %s", path)
        return True                     # it was playing: never say the line twice
    except OSError as e:
        log.warning("afplay could not start (%r)", e)
        return False
    if r.returncode != 0:
        log.warning("afplay failed (%s): %s", r.returncode, (r.stderr or b"")[-200:])
        return False
    return True


def play_wav(path: Path, device: Any = None) -> None:
    """Play a wav file and wait until it's done. device None = the system default:
    on the Mac through afplay, which follows the current default output like
    argmax-cli --play. A device that fails (unplugged) falls back to the default."""
    if device is None and _is_mac() and _afplay(Path(path)):
        return
    import sounddevice as sd
    import soundfile as sf
    data, rate = sf.read(str(path), dtype="float32")
    try:
        sd.play(data, rate, device=device)
        sd.wait()
    except Exception as e:
        if device is None:
            raise
        log.warning("speaker %r failed (%r); playing on the system default", device, e)
        if _is_mac() and _afplay(Path(path)):
            return
        sd.play(data, rate)
        sd.wait()


def chime_samples(rate: int = 24000) -> np.ndarray:
    """A short soft two-note chime (generated, no asset): float32 in [-1, 1]."""
    out = []
    for freq, secs in ((660.0, 0.09), (990.0, 0.14)):
        t = np.arange(int(rate * secs)) / rate
        env = np.minimum(1.0, np.minimum(t, secs - t) / 0.02)       # 20 ms fade in/out
        out.append(0.12 * env * np.sin(2 * np.pi * freq * t))
    return np.concatenate(out).astype(np.float32)


_chime_path: Optional[Path] = None


def _chime_file() -> Path:
    """The chime as a wav (for afplay), written once."""
    global _chime_path
    if _chime_path is None or not _chime_path.exists():
        import soundfile as sf
        path = Path(tempfile.gettempdir()) / "faraday-chime.wav"
        sf.write(str(path), chime_samples(), 24000, subtype="PCM_16")
        _chime_path = path
    return _chime_path


def play_chime(gate: Optional[MicGate] = None, device: Any = None) -> None:
    """Play the chime with the mic muted (so it isn't heard as speech), on the
    same speaker as everything else (device None on the Mac: afplay)."""
    if gate is not None:
        gate.mute()
    try:
        if device is None and _is_mac() and _afplay(_chime_file()):
            return
        import sounddevice as sd
        sd.play(chime_samples(), 24000, device=device)
        sd.wait()
    except Exception as e:
        log.warning("chime failed: %r", e)
    finally:
        if gate is not None:
            time.sleep(0.1)
            gate.unmute()
