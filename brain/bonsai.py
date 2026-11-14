"""Tiny client for LM Studio's OpenAI-compatible chat endpoint.

Stdlib only (urllib), so it behaves the same on Windows and macOS and adds no
dependencies. Every failure (network, timeout, HTTP error, bad JSON, missing
fields) is raised as BonsaiError so the brain can fall back in one place.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


class BonsaiError(Exception):
    pass


class BonsaiClient:
    def __init__(self, base_url: str, model: str, timeout_s: float = 30,
                 max_tokens: int = 200, temperature: float = 0.2,
                 reasoning_effort: Optional[str] = "none"):
        """reasoning_effort "none" turns Bonsai's thinking off. With thinking on it
        spends the whole max_tokens budget reasoning and never calls a tool."""
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        self.url = base + "/chat/completions"
        self.model = model
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "BonsaiClient":
        return cls(cfg["base_url"], cfg["model"], cfg["timeout_s"],
                   cfg["max_tokens"], cfg["temperature"], cfg.get("reasoning_effort", "none"))

    def chat(self, messages: List[Dict[str, Any]],
             tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Return choices[0].message as a dict. Raises BonsaiError on anything odd."""
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:200]
            raise BonsaiError(f"HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            raise BonsaiError(f"connection failed: {e}") from e
        try:
            message = json.loads(raw)["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise BonsaiError(f"unexpected response: {raw[:200]}") from e
        if not isinstance(message, dict):
            raise BonsaiError(f"unexpected message: {message!r}"[:200])
        return message
