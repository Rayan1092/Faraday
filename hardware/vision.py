"""Bonsai vision: one short sentence about a photo, via LM Studio image input.

Uses the same llm profile as the brain (base_url + model from settings). The
image is shrunk to 512 px on the long side first: small prompt, little RAM.
Checked on the NV laptop: ternary-bonsai-2-4b@q2_0 accepts images (~7 s).
"""
from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

import requests

log = logging.getLogger("hardware.vision")

PROMPT = ("You are a home security camera. Describe this photo in one short sentence: "
          "who or what is there, what they carry, where. No guessing names.")
MAX_SIDE = 512
MAX_CHARS = 200


def _llm(settings: Dict[str, Any]) -> Dict[str, Any]:
    llm = settings.get("llm", {})
    profile = llm.get("profiles", {}).get(llm.get("profile"), {})
    return {"base_url": profile.get("base_url", "http://127.0.0.1:1234"),
            "model": profile.get("model", ""),
            "timeout_s": float(llm.get("vision_timeout_s", llm.get("timeout_s", 30))),
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
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip().strip('"').strip()
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
                 reasoning_effort: str = "none"):
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.model, self.timeout_s, self.reasoning_effort = model, timeout_s, reasoning_effort

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "Vision":
        c = _llm(settings)
        return cls(c["base_url"], c["model"], c["timeout_s"], c["reasoning_effort"])

    def describe(self, path: Path) -> Optional[str]:
        """One sentence, or None on any failure (caller keeps the photo)."""
        try:
            b64 = encode_image(Path(path))
            body = {"model": self.model, "max_tokens": 60, "temperature": 0.2,
                    "reasoning_effort": self.reasoning_effort,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                    ]}]}
            r = requests.post(self.url, json=body, timeout=self.timeout_s)
            r.raise_for_status()
            return one_sentence(r.json()["choices"][0]["message"].get("content") or "")
        except Exception as e:   # vision is best effort: never break take_photo
            log.warning("vision failed: %r", e)
            return None
