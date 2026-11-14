"""describe_photo: camera JPG -> photo_described event (PLAN.md seam 4).

integration/ calls this after hardware/camera saves the photo for a take_photo
action, then hands the returned event to pipeline.handle().

It asks Bonsai to describe the image (OpenAI-style image message, which LM
Studio accepts for vision-capable models). If the loaded model can't take
images, or the call fails, it still returns a valid event with an honest
"no description" text, so the doorbell flow never stalls. A model that refuses
images (HTTP 4xx) is not asked again until restart.
Which Bonsai build does vision is PLAN.md Open Question 2.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Optional, Union

from shared import Event, EventType, Source, make_event

from .bonsai import BonsaiError
from .core import _clean_line
from .settings import REPO_ROOT

NO_DESCRIPTION = "Photo taken. No description available."
VISION_PROMPT = ("You are a home guard camera. Describe who or what is at the front door in "
                 "one short sentence. Mention clothing and anything they carry. No guessing names.")
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
MAX_IMAGE_BYTES = 4_000_000


def repo_path(path: Union[str, Path]) -> str:
    """captures/photos/x.jpg style: relative to the repo root, forward slashes."""
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(REPO_ROOT)
        except ValueError:
            pass
    return p.as_posix()


class Describer:
    def __init__(self, client: Optional[Any], model: Optional[str] = None,
                 log=print):
        self.client = client
        self.model = model
        self.log = log
        self.disabled_reason: Optional[str] = None

    @classmethod
    def from_settings(cls, settings: Optional[dict] = None, log=print) -> "Describer":
        """Uses the llm profile's model, or llm.vision_model if that optional key is set."""
        from .bonsai import BonsaiClient
        from .settings import llm_config, load_settings
        settings = settings or load_settings()
        cfg = dict(llm_config(settings))
        cfg["model"] = settings.get("llm", {}).get("vision_model") or cfg["model"]
        return cls(BonsaiClient.from_config(cfg), cfg["model"], log)

    def describe(self, path: Union[str, Path]) -> Optional[str]:
        """One sentence about the photo, or None."""
        if self.client is None or self.disabled_reason:
            return None
        p = Path(path)
        p = p if p.is_absolute() else REPO_ROOT / p
        mime = IMAGE_TYPES.get(p.suffix.lower())
        try:
            data = p.read_bytes() if mime else b""
        except OSError as e:
            self.log(f"[vision] can't read {p.name}: {e}")
            return None
        if not data or len(data) > MAX_IMAGE_BYTES:
            self.log(f"[vision] skipped {p.name}: not a .jpg/.png under 4 MB")
            return None
        url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        messages = [{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": url}}]}]
        started = time.perf_counter()
        try:
            reply = self.client.chat(messages, None)
        except BonsaiError as e:
            if str(e).startswith("HTTP 4"):
                self.disabled_reason = str(e)[:160]
                self.log(f"[vision] model refused images, not asking again: {self.disabled_reason}")
            else:
                self.log(f"[vision] failed after {time.perf_counter() - started:.2f}s: {e}")
            return None
        except Exception as e:
            self.log(f"[vision] crashed: {e!r}")
            return None
        line = _clean_line(reply.get("content") if isinstance(reply, dict) else None)
        self.log(f"[vision] Bonsai took {time.perf_counter() - started:.2f}s"
                 + ("" if line else ": unusable reply"))
        return line


def describe_photo(path: Union[str, Path], caused_by: Optional[str],
                   describer: Optional[Describer] = None) -> Event:
    """Always returns a valid photo_described event (source "vision")."""
    text = describer.describe(path) if describer is not None else None
    return make_event(EventType.PHOTO_DESCRIBED,
                      {"photo_path": repo_path(path), "description": text or NO_DESCRIPTION,
                       "model": describer.model if (describer and text) else None},
                      Source.VISION, caused_by=caused_by)
