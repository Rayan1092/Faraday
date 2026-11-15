"""Bonsai vision: one short factual sentence about a photo, via LM Studio image input.

Uses the llm profile's `model` (the 9B on the Mac; `llm.vision_model` overrides
it, e.g. a 4B that doesn't think). The image is shrunk to 512 px on the long
side and sent as a base64 JPEG data URL.

The description must be factual (owner 2026-11-14): how many people (or
"Nobody is there."), what they hold, face visible or covered, what they're
doing. When the object detector saw something, its counts go into the prompt.

Thinking (Mac 2026-11-15): the MLX 9B ignores `reasoning_effort: "none"` for
images. With 768 tokens it thought 3245 chars for 20 s and gave nothing. More
tokens only make that slower, so thinking must be turned OFF. Dell's raw
no-think prompt (#42) can't carry an image (/v1/completions is text only), so
the chat request itself has to do it. Every request sends reasoning_effort
"none" and enable_thinking false (as chat_template_kwargs and top level), and
max_tokens 120 (one sentence). Strategies on top of that:

  prefill   + an assistant turn already holding the empty, closed think block
            ("<think>", a blank line, "</think>"), which LM Studio continues:
            the model starts after its thinking, with the answer
  plain     nothing more (works where the server honours reasoning_effort)
  no_think  + "/no_think" in the system and user text (Qwen 3's soft switch)
  system    + a system instruction to answer at once, with no reasoning

Measured on LM Studio with Bonsai 2 4B (thinks by default, 2026-11-15): alone,
the prefill stops the thinking (answer in 0.2 s); /no_think, the system
instruction, chat_template_kwargs and top-level enable_thinking do not;
reasoning_effort "none" works on GGUF but not on the Mac's MLX. So the probe
tries prefill, then plain (AUTO). no_think and system stay as forced options
(llm.vision_no_think) and in the check's table.

The first that answers without thinking is used from then on (saved in
cache/vision_mode.json per server and model, so the next start skips the probe;
if it stops answering, the probe runs again). If none does, vision is skipped:
photos never wait, the detector describes them, one console line says how to
switch thinking off in LM Studio's template, and a background probe tries again
every 5 minutes. `python3 -m hardware.check vision` times every strategy on the
9B and the 4B and saves the best.

Reasoning that still comes back is stripped: <think>...</think>, a lone closing
</think>, an unfinished <think>, a separate reasoning_content field.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from hardware.settings import REPO_ROOT

log = logging.getLogger("hardware.vision")

PROMPT = ("You are a home security camera at the front door. In one short factual sentence, say "
          "how many people are there (if nobody, say exactly \"Nobody is there.\"), what each holds "
          "or carries, whether their face is visible or covered (mask, hood, helmet, turned away), "
          "and what they are doing. Only say what you can see. No names, no guesses.")
MAX_SIDE = 512
MAX_CHARS = 280
DEFAULT_MAX_TOKENS = 120            # one sentence; a thinking model hits this fast instead of 20 s
DEFAULT_TIMEOUT_S = 20.0
NO_THINK_BLOCK = "<think>\n\n</think>\n\n"
STRATEGIES = ("prefill", "plain", "no_think", "system")      # every one (check table, forced)
AUTO = ("prefill", "plain")         # the probe: the ones that can stop the thinking in LM Studio
SYSTEM_NO_THINK = ("Reply with the answer only. Do not think step by step, do not reason, "
                   "do not use <think>. Answer immediately in one sentence.")
RETRY_DISABLED_S = 300.0
WARM_UP_S = 60.0                    # the warm-up's first image may load the vision part
CACHE_FILE = REPO_ROOT / "cache" / "vision_mode.json"
FIX_HINT = ("LM Studio: My Models -> the model -> settings (gear) -> Prompt Template: add "
            "{%- set enable_thinking = false -%} as the first line, save, and reload the model")


def _llm(settings: Dict[str, Any]) -> Dict[str, Any]:
    llm = settings.get("llm", {})
    profile = llm.get("profiles", {}).get(llm.get("profile"), {})
    return {"base_url": profile.get("base_url", "http://127.0.0.1:1234"),
            "model": llm.get("vision_model") or profile.get("model", ""),
            "timeout_s": float(llm.get("vision_timeout_s", DEFAULT_TIMEOUT_S)),
            "max_tokens": int(llm.get("vision_max_tokens", DEFAULT_MAX_TOKENS)),
            "reasoning_effort": llm.get("reasoning_effort", "none"),
            "strategy": llm.get("vision_no_think", "auto")}


def encode_image(path: Path, max_side: int = MAX_SIDE) -> str:
    """JPEG bytes as base64, shrunk so the long side is at most max_side."""
    import cv2
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"cannot read image {path}")
    return encode_frame(img, max_side)


def encode_frame(img: Any, max_side: int = MAX_SIDE) -> str:
    import cv2
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise ValueError("jpeg encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def test_image() -> str:
    """A small door scene (no people) for the warm-up probe."""
    import cv2
    import numpy as np
    img = np.full((240, 320, 3), 200, np.uint8)
    cv2.rectangle(img, (0, 170), (320, 240), (90, 90, 90), -1)
    cv2.rectangle(img, (130, 110), (190, 170), (40, 90, 160), -1)
    return encode_frame(img)


def strip_reasoning(text: str) -> str:
    """The answer without any thinking: drops <think>...</think>, everything up
    to a lone closing </think>, and an unfinished <think>... (nothing left)."""
    text = text or ""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    text = re.sub(r"<think>.*", "", text, flags=re.S)      # unclosed: still thinking
    return text


def thought_text(content: str) -> str:
    """The reasoning inside the content, without the tags: everything up to the
    last </think> plus an unfinished <think>... at the end. The empty closed block
    a no-think template produces ("<think>\n\n</think>\n\n") gives ""."""
    content = content or ""
    head, tail = content.rsplit("</think>", 1) if "</think>" in content else ("", content)
    m = re.search(r"<think>(.*)", tail, flags=re.S)
    return re.sub(r"</?think>", "", head + (m.group(1) if m else "")).strip()


THINKING_OPENERS = ("<think>", "thinking process", "thinking:", "here's a thinking process",
                    "here is a thinking process", "okay,", "okay so", "ok, so", "hmm", "let me",
                    "let's", "alright", "got it", "the user", "so, the user", "so the user",
                    "i need to", "we need to", "first, i", "analyze the", "1. analyze")


class ServerError(RuntimeError):
    """LM Studio answered 5xx: server trouble (maybe this request's template), not a
    verdict about thinking."""


def looks_like_thinking(text: str) -> bool:
    """Reasoning printed as plain text (no tags): starts like reasoning, before or
    after the markdown is removed ("**Thinking Process:**" too)."""
    for variant in (text or "", re.sub(r"[*_#`]+", "", text or "")):
        if variant.lstrip().lstrip('"').lower()[:60].startswith(THINKING_OPENERS):
            return True
    return False


def short_description(text: str, max_chars: int = MAX_CHARS) -> Optional[str]:
    """Clean model output down to a short description (whole sentences, at most
    max_chars), or None if empty. Every sentence that fits is kept: the prompt
    asks for facts (people, what they hold, face covered) and none may be cut."""
    text = re.sub(r"[*_#`]+", "", strip_reasoning(text))      # markdown
    text = text.strip().strip('"').strip()
    text = re.sub(r"^\s*(description|answer)\s*:\s*", "", text, flags=re.I)
    text = " ".join(text.split())
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= 40:
        return cut[:end + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "."


one_sentence = short_description      # old name


def whole_sentences(text: str) -> Optional[str]:
    """A cut-off answer: only its complete sentences (or None)."""
    clean = short_description(text)
    if not clean:
        return None
    end = max(clean.rfind(". "), clean.rfind("! "), clean.rfind("? "))
    if clean.endswith((".", "!", "?")) and not clean.endswith("..."):
        return clean if len(clean) < MAX_CHARS or end < 0 else clean[:end + 1]
    return clean[:end + 1] if end >= 0 else None


def _load_cache() -> Dict[str, str]:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(key: str, strategy: Optional[str]) -> None:
    data = _load_cache()
    if strategy is None:
        data.pop(key, None)
    else:
        data[key] = strategy
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


class Vision:
    def __init__(self, base_url: str, model: str, timeout_s: float = DEFAULT_TIMEOUT_S,
                 reasoning_effort: str = "none", max_tokens: int = DEFAULT_MAX_TOKENS,
                 strategy: str = "auto", use_cache: bool = True):
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/v1/chat/completions"
        self.model, self.timeout_s, self.reasoning_effort = model, timeout_s, reasoning_effort
        self.max_tokens = max_tokens
        self.forced = strategy if strategy in STRATEGIES else None
        self.use_cache = use_cache
        self._key = f"{self.base_url}|{model}"
        self.strategy: Optional[str] = self.forced or (_load_cache().get(self._key) if use_cache else None)
        if self.strategy not in STRATEGIES:
            self.strategy = None
        self.disabled_until = 0.0           # no strategy works: skip vision; re-probe from then
        self._warned = False
        self._lock = threading.Lock()       # one probe at a time
        # What happened on the last describe() (hardware.check vision prints it)
        self.last: Dict[str, Any] = {}
        self.probe_results: List[Dict[str, Any]] = []
        self.answer_s: Optional[float] = None   # how long the warm-up's description took

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "Vision":
        c = _llm(settings)
        return cls(c["base_url"], c["model"], c["timeout_s"], c["reasoning_effort"], c["max_tokens"],
                   c["strategy"])

    # ---- requests ---------------------------------------------------------------------
    def _body(self, b64: str, prompt: str, max_tokens: int, strategy: str) -> Dict[str, Any]:
        text = prompt + (" /no_think" if strategy == "no_think" else "")
        messages: List[Dict[str, Any]] = []
        if strategy == "no_think":
            messages.append({"role": "system", "content": "/no_think"})
        elif strategy == "system":
            messages.append({"role": "system", "content": SYSTEM_NO_THINK})
        messages.append({"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]})
        if strategy == "prefill":
            messages.append({"role": "assistant", "content": NO_THINK_BLOCK})
        return {"model": self.model, "max_tokens": max_tokens, "temperature": 0.1,
                "reasoning_effort": self.reasoning_effort, "messages": messages,
                "chat_template_kwargs": {"enable_thinking": False}, "enable_thinking": False}

    def _ask(self, b64: str, prompt: str, strategy: str, timeout: float) -> Dict[str, Any]:
        """One request. {"answer", "thinks", "finish", "reasoning_chars", "seconds", "status"}."""
        t0 = time.monotonic()
        body = self._body(b64, prompt, self.max_tokens, strategy)
        r = requests.post(self.url, json=body, timeout=timeout)
        out: Dict[str, Any] = {"strategy": strategy, "status": r.status_code}
        if r.status_code == 400:
            # an older server may refuse the extra fields: once without them (the prefill
            # turn, the /no_think text and the system line stay; reasoning_effort too,
            # unless the error names it: it is what turns thinking off on GGUF)
            drop = {"chat_template_kwargs", "enable_thinking"}
            if "reasoning" in r.text.lower():
                drop.add("reasoning_effort")
            body = {k: v for k, v in body.items() if k not in drop}
            r = requests.post(self.url, json=body, timeout=max(1.0, timeout - (time.monotonic() - t0)))
            out["status"] = r.status_code
        out["seconds"] = round(time.monotonic() - t0, 1)
        if r.status_code >= 500:
            raise ServerError(f"HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        choice = r.json()["choices"][0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        finish = choice.get("finish_reason")
        answer = short_description(content)
        plain_reasoning = looks_like_thinking(strip_reasoning(content)) or (
            answer is not None and looks_like_thinking(answer))
        thought = (reasoning.strip() + " " + thought_text(content)).strip()
        # thinks: it reasoned before (or instead of) answering. An empty closed
        # <think></think> is not thinking.
        thinks = bool(thought) or plain_reasoning
        cut = finish == "length" and "</think>" not in content
        if thinks and (cut or plain_reasoning):
            answer = None                    # reasoning (cut off or as plain text) is no description
        # Ran out of tokens with no reasoning at all: an over-long answer, not thinking.
        # Only its whole sentences count; never a reason to drop the strategy.
        too_long = cut and not thinks
        if too_long:
            answer = whole_sentences(content)
        out.update(answer=answer, thinks=thinks, too_long=too_long, finish=finish,
                   content=content[:500], reasoning_chars=len(thought))
        return out

    # ---- choosing a strategy ----------------------------------------------------------
    def probe(self, b64: Optional[str] = None, prompt: str = PROMPT, deadline: Optional[float] = None,
              strategies=AUTO, per_request: float = 15.0) -> Optional[Dict[str, Any]]:
        """Try the strategies in order; keep the first that answers without thinking
        (else the fastest that still answered within the token cap). Returns that
        reply (its answer is a real description of `b64`), or None. Waits for a
        probe already running (background callers only: describe() never waits)."""
        with self._lock:
            return self._probe(b64, prompt, deadline, strategies, per_request)

    def _probe(self, b64: Optional[str], prompt: str, deadline: Optional[float],
               strategies=AUTO, per_request: float = 15.0) -> Optional[Dict[str, Any]]:
        """probe() without the lock: the caller holds self._lock."""
        b64 = b64 or test_image()
        self.probe_results = []
        if deadline is None:
            deadline = time.monotonic() + max(self.timeout_s * 2, per_request * len(strategies))
        for strategy in strategies:
            left = deadline - time.monotonic()
            if left <= 1:
                break
            timeout = min(left, per_request)
            try:
                reply = self._ask(b64, prompt, strategy, timeout)
            except requests.RequestException as e:
                # down, refused or timed out: no point trying the next one (and no
                # verdict about thinking: it never answered)
                why = f"timed out after {timeout:.0f} s" if isinstance(e, requests.Timeout) else repr(e)
                self.probe_results.append({"strategy": strategy, "error": why[:300], "answer": None,
                                           "thinks": None, "no_verdict": True})
                break
            except ServerError as e:         # 5xx: try the next, but it's no verdict about thinking
                reply = {"strategy": strategy, "error": str(e)[:300], "answer": None, "thinks": None,
                         "no_verdict": True}
            except Exception as e:           # 4xx: this request's shape is refused; try the next
                reply = {"strategy": strategy, "error": repr(e)[:300], "answer": None, "thinks": None}
            self.probe_results.append(reply)
            if reply.get("answer") and not reply.get("thinks"):
                return self._choose(reply, "answers without thinking")
        answered = [r for r in self.probe_results if r.get("answer")]
        if answered:                         # it thinks a little, but answers within the cap
            return self._choose(min(answered, key=lambda r: r.get("seconds", 99)),
                                "thinks a little but answers within the token cap")
        unsure = any(r.get("no_verdict") or r.get("too_long") for r in self.probe_results)
        if any(r.get("thinks") for r in self.probe_results) and not unsure:   # only thinking
            self.strategy = None
            self.disabled_until = time.monotonic() + RETRY_DISABLED_S
            if self.use_cache:
                _save_cache(self._key, None)
            if not self._warned:
                self._warned = True
                log.warning("vision: %s thinks before every answer, whatever the request. "
                            "Photos are described by the object detector until that's fixed. %s",
                            self.model, FIX_HINT)
        return None

    def _choose(self, reply: Dict[str, Any], how: str) -> Dict[str, Any]:
        if self.use_cache and not self.forced:
            _save_cache(self._key, reply["strategy"])      # before it shows: nothing races the file
        self.strategy, self.disabled_until, self._warned = reply["strategy"], 0.0, False
        log.info("vision: %s %s with '%s' (%.1f s)", self.model, how, self.strategy,
                 reply.get("seconds", 0))
        return reply

    def _reprobe_in_background(self) -> None:
        """Every 5 minutes while disabled (the template may have been fixed and the
        model reloaded): probe with the test image off the photo path."""
        if not self._lock.acquire(blocking=False):
            return                                    # a probe is running already
        self.disabled_until = time.monotonic() + RETRY_DISABLED_S   # the next try after this one

        def run() -> None:
            try:
                self._probe(None, PROMPT, None)
            except Exception:
                log.exception("background vision probe failed")
            finally:
                self._lock.release()
        try:
            threading.Thread(target=run, name="faraday-vision-probe", daemon=True).start()
        except Exception:
            self._lock.release()

    def warm_up(self) -> Optional[float]:
        """Before the first doorbell: load the vision part in LM Studio and pick the
        no-think strategy (a saved one is checked: it must still answer). Seconds in
        all, or None; self.answer_s is how long the description itself took."""
        t0 = time.monotonic()
        self.answer_s = None
        load_s = max(self.timeout_s, WARM_UP_S)       # the first image also loads the vision part
        try:
            with self._lock:                          # a photo meanwhile doesn't wait: the detector
                if self.strategy is not None:
                    reply = self._ask(test_image(), PROMPT, self.strategy, load_s)
                    if reply.get("answer"):
                        self.answer_s = reply.get("seconds")
                        return round(time.monotonic() - t0, 1)
                    if self.forced:
                        log.warning("vision warm-up: '%s' gave no answer (the model thought). %s",
                                    self.strategy, FIX_HINT)
                        return None
                    log.info("vision warm-up: the saved '%s' gave no answer; probing", self.strategy)
                    self.strategy = None
                reply = self._probe(None, PROMPT, None, AUTO, per_request=load_s)
            if reply is None:
                return None
            self.answer_s = reply.get("seconds")
            return round(time.monotonic() - t0, 1)
        except Exception as e:
            log.warning("vision warm-up failed: %r", e)
            return None

    # ---- describing a photo -------------------------------------------------------------
    def describe(self, path: Path, hint: Optional[str] = None) -> Optional[str]:
        """One short factual sentence, or None (the caller falls back to the
        detector). The reason for a None is logged and kept in self.last."""
        last: Dict[str, Any] = {"model": self.model, "url": self.url, "attempts": 0,
                                "max_tokens": self.max_tokens}
        self.last = last
        t0 = time.monotonic()
        deadline = t0 + self.timeout_s
        try:
            until = self.disabled_until
            if until:                        # nothing stops the thinking: never make a photo wait
                if time.monotonic() >= until:
                    self._reprobe_in_background()
                last["error"] = "vision skipped: the model thinks before every answer (" + FIX_HINT + ")"
                return None
            b64 = encode_image(Path(path))
            last["image_kb"] = round(len(b64) * 3 / 4 / 1024, 1)
            prompt = PROMPT + (f" Hint: {hint}" if hint else "")
            if self.strategy is not None:
                last["attempts"] += 1
                reply = self._ask(b64, prompt, self.strategy, max(1.0, deadline - time.monotonic()))
                self._note(last, reply)
                if reply.get("answer"):
                    return reply["answer"]
                if reply.get("too_long") and not reply.get("thinks"):
                    last["error"] = f"the answer ran past {self.max_tokens} tokens with no whole sentence"
                    log.warning("vision failed: %s ('%s' kept)", last["error"], self.strategy)
                    return None
                if self.forced:
                    last["error"] = f"'{self.strategy}' gave no answer: the model thought ({FIX_HINT})"
                    log.warning("vision failed: %s", last["error"])
                    return None
                log.warning("vision: '%s' stopped working (no answer, the model thought); "
                            "trying the others", self.strategy)
                self.strategy = None
            # pick a strategy on this photo, but never wait behind another probe (the
            # warm-up, the background retry): the detector describes this one instead
            if not self._lock.acquire(blocking=False):
                last["error"] = "vision is busy picking how to turn thinking off (another probe)"
                log.warning("vision skipped: %s; the detector describes this photo", last["error"])
                return None
            try:
                if self.disabled_until:      # that other probe found nothing works
                    last["error"] = "vision skipped: the model thinks before every answer (" + FIX_HINT + ")"
                    return None
                if self.strategy is not None:    # ... or it just picked one: use it
                    last["attempts"] += 1
                    reply = self._ask(b64, prompt, self.strategy, max(1.0, deadline - time.monotonic()))
                    self._note(last, reply)
                    if not reply.get("answer"):
                        last["error"] = f"'{self.strategy}' gave no answer"
                        log.warning("vision failed: %s", last["error"])
                    return reply.get("answer")
                last["attempts"] += 1
                reply = self._probe(b64, prompt, deadline)
            finally:
                self._lock.release()
            if reply is not None:
                self._note(last, reply)
                last["strategy"] = self.strategy
                return reply["answer"]
            tried = self.probe_results[-1] if self.probe_results else {}
            self._note(last, tried)
            if tried.get("error") or not self.disabled_until:
                last["error"] = tried.get("error") or "no answer from any request"
                log.warning("vision failed: %s", last["error"])
            else:                            # the probe has said it once already
                last["error"] = f"the model thinks before every answer ({FIX_HINT})"
            return None
        except Exception as e:   # vision is best effort: never break take_photo
            if isinstance(e, requests.Timeout):
                last["error"] = f"timed out after {self.timeout_s:.0f} s (llm.vision_timeout_s)"
            else:
                last.setdefault("error", repr(e))
            log.warning("vision failed: %s", last["error"])
            return None
        finally:
            last["elapsed_s"] = round(time.monotonic() - t0, 1)
            last["strategy"] = self.strategy

    @staticmethod
    def _note(last: Dict[str, Any], reply: Dict[str, Any]) -> None:
        for key in ("status", "finish", "reasoning_chars", "content", "seconds"):
            if key in reply:
                last[{"finish": "finish_reason"}.get(key, key)] = reply[key]
