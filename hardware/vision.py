"""Bonsai vision: a short factual description of a photo, via LM Studio image input.

Uses the same llm profile as the brain (base_url + `model`, the 9B on the Mac;
never the 2B). The image is shrunk to 512 px on the long side first (small
prompt, little RAM) and sent as a base64 JPEG data URL.

The description must be factual (owner 2026-11-14): how many people (or
"Nobody is there."), what they hold, face visible or covered, what they're
doing. When the object detector ran, its counts go into the prompt as a hint.

Robust to a model that thinks before answering (the Mac's MLX 9B gave an
empty answer with a 60-token cap -> "Photo saved."):
- thinking is switched off every way LM Studio may honour (reasoning_effort
  "none", chat_template_kwargs enable_thinking false); if the server rejects
  the extra field, the request is retried without it;
- enough tokens (vision_max_tokens, 768): a model that doesn't think stops
  after ~40 tokens anyway; if the answer was still cut off while thinking, one
  retry with 2048;
- reasoning is stripped: <think>...</think>, a reply that only has the closing
  </think>, and a separate reasoning_content field;
- one clear timeout per photo (vision_timeout_s, default 20 s: inside Dell's
  25 s "who's at the door" wait, so a slow vision still gives the detector's
  description in time), named in the log and in `python -m hardware.check vision`.
  The 2048-token retry runs only if there is time left for it.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

log = logging.getLogger("hardware.vision")

PROMPT = ("You are a home security camera at the front door. Describe this photo in one or two "
          "short factual sentences: how many people are there (if nobody, say exactly "
          "\"Nobody is there.\"), what each person holds or carries, whether their face is "
          "visible or covered (mask, hood, helmet, turned away), and what they are doing. "
          "Mention a package or bag on the ground. Only say what you can see. No names, "
          "no guesses about who they are. Answer directly.")
MAX_SIDE = 512
MAX_CHARS = 280
DEFAULT_MAX_TOKENS = 768
RETRY_MAX_TOKENS = 2048
DEFAULT_TIMEOUT_S = 20.0


def _llm(settings: Dict[str, Any]) -> Dict[str, Any]:
    llm = settings.get("llm", {})
    profile = llm.get("profiles", {}).get(llm.get("profile"), {})
    return {"base_url": profile.get("base_url", "http://127.0.0.1:1234"),
            "model": profile.get("model", ""),
            "timeout_s": float(llm.get("vision_timeout_s", DEFAULT_TIMEOUT_S)),
            "max_tokens": int(llm.get("vision_max_tokens", DEFAULT_MAX_TOKENS)),
            "reasoning_effort": llm.get("reasoning_effort", "none")}


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


def strip_reasoning(text: str) -> str:
    """The answer without any thinking: drops <think>...</think>, everything up
    to a lone closing </think>, and an unfinished <think>... (nothing left)."""
    text = text or ""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    text = re.sub(r"<think>.*", "", text, flags=re.S)      # unclosed: still thinking
    return text


def looks_like_thinking(text: str) -> bool:
    """Reasoning printed as plain text, without the closing tag (cut off)."""
    head = (text or "").lstrip()[:40].lower()
    return head.startswith(("<think>", "thinking process", "thinking:", "okay, let me", "let me think"))


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


class Vision:
    def __init__(self, base_url: str, model: str, timeout_s: float = DEFAULT_TIMEOUT_S,
                 reasoning_effort: str = "none", max_tokens: int = DEFAULT_MAX_TOKENS):
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/v1/chat/completions"
        self.model, self.timeout_s, self.reasoning_effort = model, timeout_s, reasoning_effort
        self.max_tokens = max_tokens
        self._extras_ok = True          # chat_template_kwargs accepted by the server
        # What happened on the last describe() (hardware.check vision prints it):
        # elapsed_s, status, finish_reason, content, reasoning_chars, attempts, error
        self.last: Dict[str, Any] = {}

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "Vision":
        c = _llm(settings)
        return cls(c["base_url"], c["model"], c["timeout_s"], c["reasoning_effort"], c["max_tokens"])

    def _body(self, b64: str, prompt: str, max_tokens: int) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self.model, "max_tokens": max_tokens, "temperature": 0.1,
            "reasoning_effort": self.reasoning_effort,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ]}]}
        if self._extras_ok:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return body

    def _post(self, body: Dict[str, Any], timeout: float) -> requests.Response:
        t0 = time.monotonic()
        r = requests.post(self.url, json=body, timeout=timeout)
        if r.status_code == 400 and "chat_template_kwargs" in body:
            # an older server may refuse the extra field: try once without it, and
            # stop sending it only if that worked (a 400 can also mean "no model loaded")
            left = timeout - (time.monotonic() - t0)
            if left <= 1:
                return r
            plain = {k: v for k, v in body.items() if k != "chat_template_kwargs"}
            r2 = requests.post(self.url, json=plain, timeout=left)
            if r2.status_code < 400:
                log.info("vision: server refused chat_template_kwargs (%s); not sending it again",
                         r.text[:120])
                self._extras_ok = False
            return r2
        return r

    def describe(self, path: Path, hint: Optional[str] = None) -> Optional[str]:
        """A short factual description, or None on any failure (the caller keeps
        the photo). `hint` is the object detector's line. The reason for a None
        is logged as a warning and kept in self.last."""
        last: Dict[str, Any] = {"model": self.model, "url": self.url, "attempts": 0}
        self.last = last
        t0 = time.monotonic()
        try:
            b64 = encode_image(Path(path))
            last["image_kb"] = round(len(b64) * 3 / 4 / 1024, 1)
            prompt = PROMPT + (f" Hint: {hint}" if hint else "")
            budget = self.max_tokens
            first_took = 0.0
            while True:
                left = self.timeout_s - (time.monotonic() - t0)
                if left <= 1:
                    raise requests.Timeout()
                last["attempts"] += 1
                started = time.monotonic()
                r = self._post(self._body(b64, prompt, budget), left)
                first_took = first_took or (time.monotonic() - started)
                last["status"] = r.status_code
                if r.status_code >= 400:
                    # LM Studio says why in the body (model not loaded, no image support, ...)
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
                choice = r.json()["choices"][0]
                msg = choice.get("message") or {}
                content = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
                finish = choice.get("finish_reason")
                thought = bool(reasoning) or "</think>" in content or "<think>" in content
                last.update(finish_reason=finish, content=content[:500],
                            reasoning_chars=len(reasoning) + (len(content) - len(strip_reasoning(content))),
                            max_tokens=budget)
                text = short_description(content)
                cut_off = finish == "length" and "</think>" not in content
                if text is not None and (cut_off or looks_like_thinking(strip_reasoning(content))):
                    text = None                # reasoning cut off mid-way is not a description
                    thought = True
                if text is not None:
                    return text
                left = self.timeout_s - (time.monotonic() - t0)
                if finish == "length" and budget < RETRY_MAX_TOKENS and left >= 2 * first_took + 1:
                    log.warning("vision: answer cut off after %d tokens%s; retrying with %d",
                                budget, " of thinking" if thought else "", RETRY_MAX_TOKENS)
                    budget = RETRY_MAX_TOKENS
                    continue
                hint_txt = (" (it spent max_tokens thinking: raise llm.vision_max_tokens)"
                            if thought or finish == "length" else "")
                last["error"] = (f"empty answer, finish_reason={finish}, "
                                 f"{last['reasoning_chars']} chars of reasoning{hint_txt}")
                log.warning("vision gave no description: %s", last["error"])
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

    def warm_up(self) -> Optional[float]:
        """Send one tiny image so LM Studio loads the vision part before the first
        doorbell (the first image request is the slow one). Seconds, or None."""
        import numpy as np
        t0 = time.monotonic()
        try:
            b64 = encode_frame(np.full((64, 64, 3), 128, dtype=np.uint8))
            r = self._post(self._body(b64, "What colour is this image? One word.", 16),
                           self.timeout_s)
            r.raise_for_status()
            return round(time.monotonic() - t0, 1)
        except Exception as e:
            log.warning("vision warm-up failed: %r", e)
            return None
