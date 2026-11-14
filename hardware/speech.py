"""Speech: STT (Argmax, OpenAI-compatible) and TTS (argmax-cli on the Mac,
pyttsx3 stand-in on Windows). Both behind small interfaces so integration/
doesn't care which OS it runs on.

    stt = SpeechToText.from_settings(settings)
    text = stt.transcribe(Path("captures/audio/x.wav"))      # None if nothing / failed

    tts = TextToSpeech.from_settings(settings, gate)
    tts.say("Night mode on.")      # blocks while speaking; the mic is muted meanwhile
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from hardware.audio import MicGate, play_wav
from hardware.settings import for_os

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
            return ArgmaxTTS(cfg["command"], device, gate, float(cfg.get("timeout_s", 30)))
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


class Pyttsx3TTS(TextToSpeech):
    """Offline Windows stand-in (SAPI5). The engine is created on first use in
    the calling thread: pyttsx3 must stay on one thread (integration's speech worker)."""

    def __init__(self, gate: Optional[MicGate] = None, rate: Optional[int] = None):
        super().__init__(gate)
        self.rate = rate
        self._engine = None

    def _get(self):
        if self._engine is None:
            import pyttsx3
            self._engine = pyttsx3.init()
            if self.rate:
                self._engine.setProperty("rate", int(self.rate))
        return self._engine

    def _speak(self, text: str) -> bool:
        print(f"  SAY: {text}")
        engine = self._get()
        engine.say(text)
        engine.runAndWait()
        return True

    def render(self, text: str, path: Path) -> Path:
        """Write speech to a wav instead of the speaker (tests)."""
        engine = self._get()
        engine.save_to_file(text, str(path))
        engine.runAndWait()
        return path


class ArgmaxTTS(TextToSpeech):
    """Mac: runs the argmax-cli TTS command from settings, then plays the wav.
    command is a list; "{text}" and "{out_wav}" are filled in."""

    def __init__(self, command: List[str], device: Any = None, gate: Optional[MicGate] = None,
                 timeout_s: float = 30, player=play_wav):
        super().__init__(gate)
        self.command, self.device, self.timeout_s, self.player = command, device, timeout_s, player

    def build(self, text: str, out_wav: Path) -> List[str]:
        return [part.replace("{text}", text).replace("{out_wav}", str(out_wav))
                for part in self.command]

    def _speak(self, text: str) -> bool:
        print(f"  SAY: {text}")
        with tempfile.TemporaryDirectory(prefix="faraday-tts-") as tmp:
            out = Path(tmp) / "say.wav"
            cmd = self.build(text, out)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s)
            if proc.returncode != 0 or not out.exists():
                log.warning("argmax TTS failed (%s): %s", proc.returncode,
                            (proc.stderr or proc.stdout)[-300:])
                return False
            self.player(out, self.device)
        return True
