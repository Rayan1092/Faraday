"""Bonsai vision: one short sentence about a photo, via LM Studio image input.

Uses the same llm profile as the brain (base_url + model from settings). The
image is shrunk to 512 px on the long side first: small prompt, little RAM.
Checked on the NV laptop: ternary-bonsai-2-4b@q2_0 accepts images (~7 s).
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

PROMPT = ("You are a home security camera. Describe this photo in one short sentence: "
          "who or what is there, what they carry, where. No guessing names.")
MAX_SIDE = 512
MAX_CHARS = 200
# Room for a model that thinks before answering even with reasoning_effort "none"
# (a 60-token cap gave an empty answer -> "Photo saved."). The answer itself is
# one sentence, so a model that doesn't think stops long before this.
DEFAULT_MAX_TOKENS = 300


def _llm(settings: Dict[str, Any]) -> Dict[str, Any]:
    llm = settings.get("llm", {})
    profile = llm.get("profiles", {}).get(llm.get("profile"), {})
    return {"base_url": profile.get("base_url", "http://127.0.0.1:1234"),
            "model": profile.get("model", ""),
            "timeout_s": float(llm.get("vision_timeout_s", llm.get("timeout_s", 30))),
            "max_tokens": int(llm.get("vision_max_tokens", DEFAULT_MAX_TOKENS)),
            "reasoning_effort": llm.get("reasoning_effort", "none")}


def encode_image(path: Path, max_side: int = MAX_SIDE) -> str:
    """JPEG bytes as base64, shrunk so the long side is at most max_side."""
    import cv2
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"cannot read image {path}")
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise ValueError("jpeg encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def one_sentence(text: str) -> Optional[str]:
    """Clean model output down to one short sentence, or None if empty."""
    text = re.sub(r"<think>.*?(</think>|$)", "", text or "", flags=re.S).strip().strip('"').strip()
    text = " ".join(text.split())
    if not text:
        return None
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    sentence = m.group(1) if m else text
    if len(sentence) > MAX_CHARS:
        sentence = sentence[:MAX_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return sentence


class Vision:
    def __init__(self, base_url: str, model: str, timeout_s: float = 30,
                 reasoning_effort: str = "none", max_tokens: int = DEFAULT_MAX_TOKENS):
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/v1/chat/completions"
        self.model, self.timeout_s, self.reasoning_effort = model, timeout_s, reasoning_effort
        self.max_tokens = max_tokens
        # What happened on the last describe() (hardware.check vision prints it):
        # elapsed_s, status, finish_reason, content, reasoning_chars, error
        self.last: Dict[str, Any] = {}

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "Vision":
        c = _llm(settings)
        return cls(c["base_url"], c["model"], c["timeout_s"], c["reasoning_effort"], c["max_tokens"])

    def describe(self, path: Path) -> Optional[str]:
        """One sentence, or None on any failure (caller keeps the photo). The
        reason for a None is logged as a warning and kept in self.last."""
        last: Dict[str, Any] = {"model": self.model, "url": self.url}
        self.last = last
        t0 = time.monotonic()
        try:
            b64 = encode_image(Path(path))
            last["image_kb"] = round(len(b64) * 3 / 4 / 1024, 1)
            body = {"model": self.model, "max_tokens": self.max_tokens, "temperature": 0.2,
                    "reasoning_effort": self.reasoning_effort,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                    ]}]}
            r = requests.post(self.url, json=body, timeout=self.timeout_s)
            last["status"] = r.status_code
            if r.status_code >= 400:
                # LM Studio says why in the body (model not loaded, no image support, ...)
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            choice = r.json()["choices"][0]
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
            last.update(finish_reason=choice.get("finish_reason"), content=content[:500],
                        reasoning_chars=len(reasoning))
            text = one_sentence(content)
            if text is None:
                hint = (" (it spent max_tokens thinking: raise llm.vision_max_tokens)"
                        if reasoning or "<think>" in content or choice.get("finish_reason") == "length"
                        else "")
                last["error"] = (f"empty answer, finish_reason={choice.get('finish_reason')}, "
                                 f"{len(reasoning)} chars of reasoning{hint}")
                log.warning("vision gave no description: %s", last["error"])
            return text
        except Exception as e:   # vision is best effort: never break take_photo
            if isinstance(e, requests.Timeout):
                last["error"] = f"timed out after {self.timeout_s:.0f} s (llm.vision_timeout_s)"
            else:
                last.setdefault("error", repr(e))
            log.warning("vision failed: %s", last["error"])
            return None
        finally:
            last["elapsed_s"] = round(time.monotonic() - t0, 1)
