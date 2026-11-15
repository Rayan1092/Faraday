"""Speech: STT (Argmax, OpenAI-compatible) and TTS (argmax-cli on the Mac,
pyttsx3 stand-in on Windows). Both behind small interfaces so integration/
doesn't care which OS it runs on.

    stt = SpeechToText.from_settings(settings)
    text = stt.transcribe(Path("captures/audio/x.wav"))      # None if nothing / failed

    tts = TextToSpeech.from_settings(settings, gate)
    tts.say("Night mode on.")      # blocks while speaking; the mic is muted meanwhile
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from hardware.audio import MicGate, play_wav
from hardware.settings import REPO_ROOT, for_os

log = logging.getLogger("hardware.speech")
MUTE_TAIL_S = 0.4          # keep the mic muted a moment after speech (room echo)


# ---- speech to text ---------------------------------------------------------------
class SpeechToText:
    """POSTs a wav to an OpenAI-compatible /v1/audio/transcriptions URL
    (argmax-cli serve, default port 50060). kind "standin" = no STT on this
    machine: transcribe() returns None and the console/dashboard text box is used."""

    def __init__(self, url: Optional[str], language: str = "en", model: Optional[str] = None,
                 timeout_s: float = 30):
        self.url, self.language, self.model, self.timeout_s = url, language, model, timeout_s

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "SpeechToText":
        cfg = for_os(settings.get("stt", {})) or {}
        url = cfg.get("url") if cfg.get("kind", "argmax") != "standin" else None
        return cls(url, cfg.get("language", "en"), cfg.get("model"),
                   float(cfg.get("timeout_s", 30)))

    @property
    def available(self) -> bool:
        return bool(self.url)

    def transcribe(self, wav: Path) -> Optional[str]:
        if not self.url:
            log.warning("no STT configured on this OS (stt kind standin); type instead")
            return None
        data = {"language": self.language, "response_format": "json"}
        if self.model:
            data["model"] = self.model
        try:
            with open(wav, "rb") as f:
                r = requests.post(self.url, files={"file": (Path(wav).name, f, "audio/wav")},
                                  data=data, timeout=self.timeout_s)
            r.raise_for_status()
            try:
                text = r.json().get("text", "")
            except ValueError:
                text = r.text           # response_format text servers
            text = " ".join(str(text).split())
            return text or None
        except Exception as e:
            log.warning("STT failed: %r", e)
            return None


# ---- text to speech -----------------------------------------------------------------
class TextToSpeech:
    """Base: mutes the mic while speaking. Subclasses implement _speak()."""

    def __init__(self, gate: Optional[MicGate] = None):
        self.gate = gate or MicGate()

    @staticmethod
    def from_settings(settings: Dict[str, Any], gate: Optional[MicGate] = None) -> "TextToSpeech":
        cfg = for_os(settings.get("tts", {})) or {}
        device = settings.get("audio", {}).get("speaker_device")
        if cfg.get("kind") == "argmax" and cfg.get("command"):
            return ArgmaxTTS.from_config(cfg, device, gate)
        if cfg.get("kind") == "pyttsx3" or (cfg.get("kind") == "standin" and _has_pyttsx3()):
            return Pyttsx3TTS(gate, cfg.get("rate"))
        return PrintTTS(gate)

    def say(self, text: str) -> bool:
        text = " ".join(str(text).split())
        if not text:
            return True
        self.gate.mute()
        try:
            return self._speak(text)
        except Exception as e:
            log.warning("TTS failed (%r); text was: %s", e, text)
            return False
        finally:
            time.sleep(MUTE_TAIL_S)
            self.gate.unmute()

    def _speak(self, text: str) -> bool:
        raise NotImplementedError


class PrintTTS(TextToSpeech):
    def _speak(self, text: str) -> bool:
        print(f"  SAY: {text}")
        return True


def _has_pyttsx3() -> bool:
    try:
        import pyttsx3  # noqa: F401
        return True
    except ImportError:
        return False


_PYTTSX3_CODE = (
    "import sys, pyttsx3\n"
    "e = pyttsx3.init()\n"
    "if sys.argv[2]: e.setProperty('rate', int(sys.argv[2]))\n"
    "if len(sys.argv) > 3: e.save_to_file(sys.argv[1], sys.argv[3])\n"
    "else: e.say(sys.argv[1])\n"
    "e.runAndWait()\n")


class Pyttsx3TTS(TextToSpeech):
    """Offline Windows stand-in (SAPI5).

    Each line runs in its own short Python process: an in-process pyttsx3
    engine hung in runAndWait() on the second use from a worker thread
    (seen on the NV laptop), which would silence Faraday. A process per line
    costs ~0.4 s and a hang is killed by the timeout instead."""

    def __init__(self, gate: Optional[MicGate] = None, rate: Optional[int] = None,
                 python: Optional[str] = None):
        super().__init__(gate)
        import sys
        self.rate, self.python = rate, python or sys.executable

    def _run(self, text: str, *extra: str) -> bool:
        timeout = 10 + 0.15 * len(text)        # generous: ~3 words/s speech
        proc = subprocess.run([self.python, "-c", _PYTTSX3_CODE, text, str(self.rate or ""), *extra],
                              capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            log.warning("pyttsx3 failed: %s", (proc.stderr or "")[-300:])
        return proc.returncode == 0

    def _speak(self, text: str) -> bool:
        print(f"  SAY: {text}")
        return self._run(text)

    def render(self, text: str, path: Path) -> Path:
        """Write speech to a wav instead of the speaker (tests)."""
        self._run(text, str(path))
        return path


class ArgmaxTTS(TextToSpeech):
    """Mac: runs the argmax-cli TTS command from settings. command is a list;
    "{text}" (and "{out_wav}") are filled in.

    - With "{out_wav}": the command writes a wav, which is then played.
    - Without it (the real Mac setup: `argmax-cli tts --text {text} --speaker aiden
      --play`): the command streams to the speaker itself; we wait for it.
    Either way the mic stays muted until the process has finished playing.

    Voice consistency (owner 2026-11-14: Aiden changed mid-run, sometimes
    robotic or rushed). argmax-cli picks a *random seed for every line* unless
    --seed is given (Sources/ArgmaxCLI/TTSCLI.swift), so `voice` adds a fixed
    --seed, a lower --temperature (CLI default 0.9), --top-k, and optionally
    --model 1.7b with --instruction. If an older argmax-cli refuses these
    flags, the line is retried once without them.

    Cache: every line is also saved as a wav (--output-path/--output-format wav;
    without it the CLI writes ./output.m4a on every line). A line already in the
    cache is played from the file: instant and identical every time.
    prerender() fills the cache for Faraday's fixed lines at startup.
    """

    def __init__(self, command: List[str], device: Any = None, gate: Optional[MicGate] = None,
                 timeout_s: float = 30, player=play_wav, voice: Optional[Dict[str, Any]] = None,
                 cache_dir: Optional[Path] = None):
        super().__init__(gate)
        self.command, self.device, self.timeout_s, self.player = command, device, timeout_s, player
        self.voice = dict(voice or {})
        self.cache_dir = cache_dir
        self._extras_ok = True              # this argmax-cli accepts the voice/output flags
        self.speaking = False
        self.cache_hits = 0

    @classmethod
    def from_config(cls, cfg: Dict[str, Any], device: Any = None,
                    gate: Optional[MicGate] = None) -> "ArgmaxTTS":
        cache = None
        if cfg.get("cache", True):
            cache = Path(cfg.get("cache_dir") or (REPO_ROOT / "cache" / "tts"))
        return cls(cfg["command"], device, gate, float(cfg.get("timeout_s", 30)),
                   voice=cfg.get("voice"), cache_dir=cache)

    # ---- command lines ----------------------------------------------------------------
    def build(self, text: str, out_wav: Path) -> List[str]:
        return [part.replace("{text}", text).replace("{out_wav}", str(out_wav))
                for part in self.command]

    @property
    def plays_itself(self) -> bool:
        return not any("{out_wav}" in part for part in self.command)

    def voice_args(self) -> List[str]:
        v, args = self.voice, []
        for key, flag in (("seed", "--seed"), ("temperature", "--temperature"),
                          ("top_k", "--top-k"), ("model", "--model")):
            if v.get(key) is not None:
                args += [flag, str(v[key])]
        if v.get("instruction") and str(v.get("model", "")).lower() == "1.7b":
            args += ["--instruction", str(v["instruction"])]      # the 0.6B ignores it
        return args

    def _cmd(self, text: str, tmp: Path, play: bool, extras: bool) -> List[str]:
        cmd = self.build(text, tmp / "say.wav")
        if not self.plays_itself:
            return cmd + (self.voice_args() if extras else [])
        if not play:
            cmd = [part for part in cmd if part != "--play"]
        if extras:
            cmd += self.voice_args() + ["--output-path", str(tmp / "say"), "--output-format", "wav"]
        return cmd

    def _timeout(self, text: str) -> float:
        # with --play the process lives for the whole spoken line (~3 words/s)
        return max(self.timeout_s, 10 + 0.15 * len(text))

    def _run(self, cmd: List[str], text: str) -> Tuple[bool, str]:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self._timeout(text))
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            log.warning("argmax TTS failed (%s): %s", proc.returncode, out[-300:])
        return proc.returncode == 0, out

    def _run_with_fallback(self, text: str, tmp: Path, play: bool) -> Tuple[bool, str]:
        ok, out = self._run(self._cmd(text, tmp, play, self._extras_ok), text)
        if not ok and self._extras_ok and re.search(r"unknown option|unexpected argument", out, re.I):
            log.warning("this argmax-cli refuses the voice/output flags; using the plain command")
            self._extras_ok = False
            ok, out = self._run(self._cmd(text, tmp, play, False), text)
        return ok, out

    # ---- cache ------------------------------------------------------------------------
    def cache_path(self, text: str) -> Optional[Path]:
        if self.cache_dir is None:
            return None
        base = [p for p in self.command if "{text}" not in p and "{out_wav}" not in p and p != "--play"]
        key = json.dumps({"text": " ".join(str(text).split()), "cmd": base[1:], "voice": self.voice},
                         sort_keys=True)
        return self.cache_dir / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".wav")

    def cached(self, text: str) -> Optional[Path]:
        path = self.cache_path(text)
        return path if path is not None and path.exists() and path.stat().st_size > 44 else None

    def _store(self, text: str, tmp: Path, out: str) -> Optional[Path]:
        """Move the wav this run produced into the cache."""
        dest = self.cache_path(text)
        if dest is None:
            return None
        made = None
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if lines and lines[-1].lower().endswith(".wav") and Path(lines[-1]).exists():
            made = Path(lines[-1])                   # the CLI prints the saved path last
        if made is None:
            wavs = sorted(tmp.glob("*.wav"))
            made = wavs[0] if wavs else None
        if made is None or made.stat().st_size <= 44:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part")
        shutil.copyfile(made, part)
        os.replace(part, dest)
        self._prune()
        return dest

    def _prune(self, keep: int = 400) -> None:
        files = sorted(self.cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        for old in files[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass

    # ---- speaking ---------------------------------------------------------------------
    def _speak(self, text: str) -> bool:
        print(f"  SAY: {text}")
        self.speaking = True
        try:
            hit = self.cached(text)
            if hit is not None:
                self.cache_hits += 1
                self.player(hit, self.device)
                return True
            with tempfile.TemporaryDirectory(prefix="faraday-tts-") as tmp_name:
                tmp = Path(tmp_name)
                if self.plays_itself:
                    ok, out = self._run_with_fallback(text, tmp, play=True)
                    if ok:
                        self._store(text, tmp, out)
                    return ok
                ok, out = self._run_with_fallback(text, tmp, play=False)
                wav = tmp / "say.wav"
                if not ok or not wav.exists():
                    return False
                self.player(wav, self.device)
                self._store(text, tmp, out)
                return True
        finally:
            self.speaking = False

    def render(self, text: str) -> Optional[Path]:
        """Make the cached wav for a line without playing it (pre-render)."""
        text = " ".join(str(text).split())
        if not text or self.cache_dir is None:
            return None
        hit = self.cached(text)
        if hit is not None:
            return hit
        with tempfile.TemporaryDirectory(prefix="faraday-tts-") as tmp_name:
            tmp = Path(tmp_name)
            try:
                ok, out = self._run_with_fallback(text, tmp, play=False)
            except subprocess.TimeoutExpired:
                log.warning("pre-render timed out: %s", text[:60])
                return None
            if not ok:
                return None
            if not self.plays_itself and (tmp / "say.wav").exists():
                out += "\n" + str(tmp / "say.wav")
            return self._store(text, tmp, out)

    def prerender(self, lines: List[str], wait: Optional[Callable[[], bool]] = None,
                  stop: Optional[threading.Event] = None) -> int:
        """Render every line not cached yet; pauses while `wait()` is True (Faraday
        is talking) so it never competes with a live line. Returns lines made."""
        made = 0
        for line in lines:
            if stop is not None and stop.is_set():
                break
            if self.cached(line) is not None:
                continue
            while (wait is not None and wait()) or self.speaking:
                if stop is not None and stop.wait(0.2):
                    return made
                if stop is None:
                    time.sleep(0.2)
            if self.render(line) is not None:
                made += 1
        return made
