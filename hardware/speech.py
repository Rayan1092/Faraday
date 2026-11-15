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

from hardware.audio import MicGate, play_wav, resolve_speaker
from hardware.settings import REPO_ROOT, for_os

log = logging.getLogger("hardware.speech")
MUTE_TAIL_S = 0.4          # keep the mic muted a moment after speech (room echo)

# Words the TTS voice gets wrong, written the way it says them right. Applied to
# every line before it is spoken, rendered or looked up in the cache (owner
# 2026-11-15: "passphrase" came out as "pass phase").
SAY_AS = ((re.compile(r"\bpass-?phrase(s?)\b", re.I), "pass phrase"),)


def spoken(text: str) -> str:
    """The line as it should be pronounced (SAY_AS); same text otherwise."""
    for pattern, words in SAY_AS:
        def fix(m: "re.Match[str]", words: str = words) -> str:
            out = words + m.group(1).lower()
            return out[:1].upper() + out[1:] if m.group(0)[:1].isupper() else out
        text = pattern.sub(fix, text)
    return text


# ---- speech to text ---------------------------------------------------------------
class SpeechToText:
    """POSTs a wav to an OpenAI-compatible /v1/audio/transcriptions URL
    (argmax-cli serve, default port 50060). kind "standin" = no STT on this
    machine: transcribe() returns None (nothing is heard there; tests use fakes)."""

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
            log.warning("no STT configured on this OS (stt kind standin): speech isn't heard here")
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
        device = resolve_speaker(settings.get("audio", {}).get("speaker_device"))   # output devices only
        if cfg.get("kind") == "argmax" and cfg.get("command"):
            return ArgmaxTTS.from_config(cfg, device, gate)
        if cfg.get("kind") == "pyttsx3" or (cfg.get("kind") == "standin" and _has_pyttsx3()):
            return Pyttsx3TTS(gate, cfg.get("rate"))
        return PrintTTS(gate)

    def say(self, text: str) -> bool:
        text = spoken(" ".join(str(text).split()))
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
    prerender() fills the cache for Faraday's fixed lines at startup; they are the
    backups now that most lines are written live by Bonsai (owner 2026-11-15).
    warm_up() runs the CLI once at startup, so the first live line is quick.
    """

    def __init__(self, command: List[str], device: Any = None, gate: Optional[MicGate] = None,
                 timeout_s: float = 30, player=play_wav, voice: Optional[Dict[str, Any]] = None,
                 cache_dir: Optional[Path] = None):
        super().__init__(gate)
        self.command, self.device, self.timeout_s, self.player = command, device, timeout_s, player
        self.voice = dict(voice or {})
        self.cache_dir = cache_dir
        self._voice_ok = True               # this argmax-cli accepts the voice flags
        self._output_ok = True              # ... and --output-path/--output-format
        self.speaking = False
        self.cache_hits = 0
        self._render_proc: Optional[subprocess.Popen] = None   # a pre-render running now
        self._render_lock = threading.Lock()
        self.renders_stopped = 0            # pre-renders / warm-ups a live line stopped

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
        text = spoken(text)
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
                value = str(v[key])
                args += [flag, value.lower() if key == "model" else value]   # CLI wants 0.6b / 1.7b
        if v.get("instruction") and str(v.get("model", "")).lower() == "1.7b":
            args += ["--instruction", str(v["instruction"])]      # the 0.6B ignores it
        return args

    @property
    def can_cache(self) -> bool:
        """A wav to keep comes out of every line (the CLI takes the output flags)."""
        return self.cache_dir is not None and (not self.plays_itself or self._output_ok)

    def _cmd(self, text: str, tmp: Path, play: bool) -> List[str]:
        cmd = self.build(text, tmp / "say.wav")
        if self._voice_ok:
            cmd += self.voice_args()
        if not self.plays_itself:
            return cmd
        if not play:
            cmd = [part for part in cmd if part != "--play"]
        if self._output_ok:
            cmd += ["--output-path", str(tmp / "say"), "--output-format", "wav"]
        return cmd

    def _timeout(self, text: str) -> float:
        # with --play the process lives for the whole spoken line (~3 words/s)
        return max(self.timeout_s, 10 + 0.15 * len(text))

    USAGE_ERROR = 64                 # swift-argument-parser's exit code for bad flags/values

    def _run(self, cmd: List[str], text: str, track: bool = False) -> Tuple[int, str]:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if track:
            self._render_proc = proc
            if self.speaking:                # a live line began just now: it goes first
                self._stop_render()
        try:
            stdout, stderr = proc.communicate(timeout=self._timeout(text))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
        finally:
            if track:
                self._render_proc = None
        out = (stdout or "") + (stderr or "")
        if proc.returncode != 0 and not (track and proc.returncode < 0):
            log.warning("argmax TTS failed (%s): %s", proc.returncode, out[-300:])
        return proc.returncode, out

    def _usage_error(self, code: int, out: str) -> bool:
        return code == self.USAGE_ERROR or bool(
            re.search(r"unknown option|unexpected argument|is invalid|invalid value", out, re.I))

    def _run_with_fallback(self, text: str, tmp: Path, play: bool,
                           track: bool = False) -> Tuple[bool, str]:
        """Run; on a usage error drop the voice flags first (keep caching), then the
        output flags, so Faraday never goes silent over a flag this CLI lacks."""
        code, out = self._run(self._cmd(text, tmp, play), text, track)
        if code != 0 and self._usage_error(code, out) and self._voice_ok and self.voice_args():
            log.warning("this argmax-cli refuses the voice settings (%s); speaking without them",
                        out.strip().splitlines()[-1][:120] if out.strip() else code)
            self._voice_ok = False
            code, out = self._run(self._cmd(text, tmp, play), text, track)
        if code != 0 and self._usage_error(code, out) and self._output_ok and self.plays_itself:
            log.warning("this argmax-cli refuses --output-path/--output-format; no cache")
            self._output_ok = False
            code, out = self._run(self._cmd(text, tmp, play), text, track)
        return code == 0, out

    # ---- cache ------------------------------------------------------------------------
    def cache_path(self, text: str) -> Optional[Path]:
        if self.cache_dir is None:
            return None
        base = [p for p in self.command if "{text}" not in p and "{out_wav}" not in p and p != "--play"]
        key = json.dumps({"text": " ".join(spoken(str(text)).split()), "cmd": base[1:],
                          "voice": self.voice}, sort_keys=True)
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
        part = dest.with_suffix(f".{threading.get_ident()}.part")   # one per thread
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
            if hit is None:
                self._stop_render()              # a live line never shares the engine
            if hit is not None:
                try:
                    self.player(hit, self.device)
                    self.cache_hits += 1
                    try:
                        os.utime(hit)            # recently used: pruned last
                    except OSError:
                        pass
                    return True
                except Exception as e:           # bad file / device: forget it, synthesize
                    log.warning("cached take failed to play (%r); synthesizing", e)
                    try:
                        hit.unlink()
                    except OSError:
                        pass
                    self._stop_render()          # now it is a live line
            with tempfile.TemporaryDirectory(prefix="faraday-tts-") as tmp_name:
                tmp = Path(tmp_name)
                if self.plays_itself:
                    ok, out = self._run_with_fallback(text, tmp, play=True)
                    if ok and self.can_cache:
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

    def _stop_render(self) -> None:
        """A live line is starting: stop a pre-render that is running (it would
        load the models a second time and make the live line late or choppy).
        prerender() tries that line again afterwards."""
        proc = self._render_proc
        if proc is not None and proc.poll() is None:
            self.renders_stopped += 1
            try:
                proc.kill()
            except OSError:
                pass

    def render(self, text: str) -> Optional[Path]:
        """Make the cached wav for a line without playing it (pre-render)."""
        text = spoken(" ".join(str(text).split()))
        if not text or not self.can_cache:
            return None
        hit = self.cached(text)
        if hit is not None:
            return hit
        with self._render_lock, tempfile.TemporaryDirectory(prefix="faraday-tts-") as tmp_name:
            tmp = Path(tmp_name)
            try:
                ok, out = self._run_with_fallback(text, tmp, play=False, track=True)
            except subprocess.TimeoutExpired:
                log.warning("pre-render timed out: %s", text[:60])
                return None
            if not ok:
                return None
            if not self.plays_itself and (tmp / "say.wav").exists():
                out += "\n" + str(tmp / "say.wav")
            return self._store(text, tmp, out)

    WARM_TEXT = "Ready."

    def warm_up(self, wait: Optional[Callable[[], bool]] = None,
                stop: Optional[threading.Event] = None) -> Optional[float]:
        """Run argmax-cli once at startup without playing (owner 2026-11-15: most
        lines are now written live by Bonsai and spoken with --play, so the first
        one must not pay for loading the voice). Nothing is played or cached.
        Waits while Faraday talks; a live line stops it, and it tries again
        afterwards (up to 3 tries). Returns the seconds the run took, or None if
        it failed or never got to finish."""
        for _ in range(3):
            while (wait is not None and wait()) or self.speaking:
                if stop is None:
                    time.sleep(0.2)
                elif stop.wait(0.2):
                    return None
            if stop is not None and stop.is_set():
                return None
            stopped = self.renders_stopped
            t0 = time.monotonic()
            with self._render_lock, tempfile.TemporaryDirectory(prefix="faraday-tts-") as tmp_name:
                try:
                    ok, _ = self._run_with_fallback(self.WARM_TEXT, Path(tmp_name), play=False, track=True)
                except Exception as e:
                    log.warning("TTS warm-up failed: %r", e)
                    return None
            if ok:
                return round(time.monotonic() - t0, 1)
            if self.renders_stopped == stopped:
                return None                   # it failed by itself: a retry won't help
        return None

    def prerender(self, lines: List[str], wait: Optional[Callable[[], bool]] = None,
                  stop: Optional[threading.Event] = None) -> int:
        """Render every line not cached yet; pauses while `wait()` is True (Faraday
        is talking) so it never competes with a live line. Returns lines made."""
        made = 0
        todo = list(lines)
        tries: Dict[str, int] = {}
        while todo:
            line = todo.pop(0)
            if stop is not None and stop.is_set():
                break
            if not self.can_cache:
                break                         # this CLI can't write a file: nothing to fill
            if self.cached(line) is not None:
                continue
            while (wait is not None and wait()) or self.speaking:
                if stop is not None and stop.wait(0.2):
                    return made
                if stop is None:
                    time.sleep(0.2)
            try:
                path = self.render(line)
            except Exception:
                log.exception("pre-render failed: %s", line[:60])
                path = None
            if path is not None:
                made += 1
            elif self.speaking and tries.get(line, 0) < 2:
                tries[line] = tries.get(line, 0) + 1
                todo.append(line)             # stopped for a live line: once more later
        return made
