"""Fakes shared by the brain tests."""
from __future__ import annotations

import json
from typing import Any, Dict, List

from brain import Brain

PASSPHRASE = "blue pelican seventeen"


class FakeClient:
    """Stands in for BonsaiClient. `reply` is a message dict, or an exception to raise."""

    def __init__(self, reply: Any):
        self.reply = reply
        self.calls: List[Dict[str, Any]] = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def tool_calls(*calls) -> Dict[str, Any]:
    """tool_calls(("speak", {"text": "hi"}), ...) -> an OpenAI-style assistant message."""
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"call_{i}", "type": "function",
         "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
        for i, (name, args) in enumerate(calls)]}


def make_brain(reply: Any = None) -> Brain:
    client = FakeClient(reply) if reply is not None else None
    return Brain(client, PASSPHRASE, recent_lines=10, log=lambda _msg: None)
