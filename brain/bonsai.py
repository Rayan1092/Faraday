"""Tiny client for LM Studio's OpenAI-compatible server.

Stdlib only (urllib), so it behaves the same on Windows and macOS and adds no
dependencies. Every failure (network, timeout, HTTP error, bad JSON, missing
fields) is raised as BonsaiError so the brain can fall back in one place.

Thinking. Bonsai 2 (Qwen 3.5 chat template) thinks before answering unless the
server turns it off. LM Studio turns it off for `reasoning_effort: "none"` on GGUF
models, but the Mac's MLX 9B kept thinking (PR #40): the reply was all reasoning,
the content empty, and the brain fell back after 7 s. So every reply is checked:

- "auto" (default): ask the chat endpoint. If the reply shows thinking, switch this
  client to "raw" for good and ask again once.
- "raw": the /v1/completions endpoint with the prompt built here, ending in an
  empty, closed `<think></think>` block exactly as the model's own template does
  when thinking is off. The model cannot think. Tool calls come back as the
  template's `<tool_call><function=...>` XML and are parsed here.
- "chat": the chat endpoint only (never switches).

Set with the optional `llm.no_think` ("auto", "raw" or "chat").
"""
from __future__ import annotations

import json
import re
import http.client
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

MODES = ("auto", "raw", "chat")

# Qwen 3.5 chat template, tools part (the text the model was trained on).
_TOOLS_HEAD = "# Tools\n\nYou have access to the following functions:\n\n<tools>"
_TOOLS_TAIL = (
    "\n</tools>\n\nIf you choose to call a function ONLY reply in the following format with NO suffix:\n\n"
    "<tool_call>\n<function=example_function_name>\n<parameter=example_parameter_1>\nvalue_1\n"
    "</parameter>\n<parameter=example_parameter_2>\nThis is the value for the second parameter\n"
    "that can span\nmultiple lines\n</parameter>\n</function>\n</tool_call>\n\n<IMPORTANT>\nReminder:\n"
    "- Function calls MUST follow the specified format: an inner <function=...></function> block must "
    "be nested within <tool_call></tool_call> XML tags\n- Required parameters MUST be specified\n"
    "- You may provide optional reasoning for your function call in natural language BEFORE the "
    "function call, but NOT after\n- If there is no function call available, answer the question like "
    "normal with your current knowledge and do not tell the user about function calls\n</IMPORTANT>")
NO_THINK = "<think>\n\n</think>\n\n"

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)
_TOOL_CALL = re.compile(r"<tool_call>\s*<function=([^>\s]+)>(.*?)</function>\s*(?:</tool_call>|$)", re.S)
_PARAM = re.compile(r"<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>", re.S)


class BonsaiError(Exception):
    pass


def render_prompt(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> str:
    """The prompt the model's chat template builds with thinking off (text only)."""
    out: List[str] = []
    msgs = list(messages)
    system = ""
    if msgs and msgs[0].get("role") == "system":
        system = str(msgs.pop(0).get("content") or "").strip()
    if tools:
        head = _TOOLS_HEAD + "".join("\n" + json.dumps(t, ensure_ascii=False) for t in tools) + _TOOLS_TAIL
        out.append("<|im_start|>system\n" + head + ("\n\n" + system if system else "") + "<|im_end|>\n")
    elif system:
        out.append("<|im_start|>system\n" + system + "<|im_end|>\n")
    for m in msgs:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        out.append(f"<|im_start|>{role}\n{str(m.get('content') or '').strip()}<|im_end|>\n")
    out.append("<|im_start|>assistant\n" + NO_THINK)
    return "".join(out)


def _value(text: str, kind: Optional[str] = None) -> Any:
    """A parameter value, typed by the tool schema. The template writes strings raw,
    so a "string" parameter is never parsed (a phone number stays text)."""
    text = text.strip()
    if kind == "string":
        return text
    if kind in ("integer", "number", "boolean", "object", "array") or text[:1] in "[{":
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def _schema_types(tools: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, str]]:
    """{tool name: {parameter: JSON type}} from OpenAI tool definitions."""
    out: Dict[str, Dict[str, str]] = {}
    for tool in tools or []:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            props = (fn.get("parameters") or {}).get("properties") or {}
            out[fn["name"]] = {k: str(v.get("type", "")) for k, v in props.items() if isinstance(v, dict)}
    return out


def tool_calls_in(text: str, tools: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """`<tool_call><function=name><parameter=p>v</parameter>...` -> OpenAI tool_calls."""
    calls = []
    types = _schema_types(tools)
    for i, m in enumerate(_TOOL_CALL.finditer(text or "")):
        kinds = types.get(m.group(1), {})
        args = {p.group(1): _value(p.group(2), kinds.get(p.group(1)))
                for p in _PARAM.finditer(m.group(2))}
        calls.append({"id": f"call_{i}", "type": "function",
                      "function": {"name": m.group(1), "arguments": json.dumps(args)}})
    return calls


def strip_thinking(content: str) -> str:
    """Content without thinking. An unclosed <think> means the model was still
    thinking when it hit max_tokens: nothing usable."""
    content = _THINK_BLOCK.sub("", content or "")
    if "</think>" in content:          # the template opened <think>: only its end shows
        content = content.rsplit("</think>", 1)[1]
    if "<think>" in content:
        content = content.split("<think>")[0]
    return content


def clean_reply(content: str) -> str:
    """Content without thinking or tool-call XML."""
    return _TOOL_CALL.sub("", strip_thinking(content)).strip()


def thought(message: Dict[str, Any], finish_reason: Optional[str]) -> int:
    """How many characters of thinking a chat reply carries (0 = none)."""
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    content = message.get("content") or ""
    chars = len(reasoning) if isinstance(reasoning, str) else 0
    if isinstance(content, str) and "<think>" in content:
        m = re.search(r"<think>(.*?)(</think>|$)", content, re.S)
        chars += len(m.group(1).strip()) if m else 0
    elif isinstance(content, str) and "</think>" in content:
        chars += len(content.split("</think>")[0].strip()) or 1
    if not chars and finish_reason == "length" and not (content or "").strip() \
            and not message.get("tool_calls"):
        chars = 1   # ran out of tokens without a word: thinking we can't see
    return chars


class BonsaiClient:
    def __init__(self, base_url: str, model: str, timeout_s: float = 30,
                 max_tokens: int = 200, temperature: float = 0.2,
                 reasoning_effort: Optional[str] = "none", no_think: str = "auto",
                 log: Any = print):
        """reasoning_effort "none" asks the server to turn thinking off. no_think says
        what to do when it doesn't (see the module docstring)."""
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        self.url = base + "/chat/completions"
        self.raw_url = base + "/completions"
        self.model = model
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        if no_think not in MODES:
            raise ValueError(f"llm.no_think must be one of {MODES}, not {no_think!r}")
        self.no_think = no_think
        self.mode = "raw" if no_think == "raw" else "chat"   # what the next call uses
        self.log = log
        # The last call (the bench and logs print it): endpoint, elapsed_s,
        # finish_reason, thought_chars, retried.
        self.last: Dict[str, Any] = {}
        self.thinking_seen = 0     # chat replies that carried thinking
        self.probed = False        # "auto": has probe() decided chat or raw yet?
        self.probe_result: Optional[str] = None
        self._probe_lock = threading.Lock()

    def warm_up(self) -> None:
        """Probe now, in a background thread, so the first real call doesn't wait."""
        if self.no_think == "auto" and not self.probed:
            threading.Thread(target=self._probe_once, name="bonsai-probe", daemon=True).start()

    def _probe_once(self) -> None:
        with self._probe_lock:          # a call made meanwhile waits for this answer
            if not self.probed:
                self.probe()

    def probe(self) -> Optional[str]:
        """Ask "Say OK." with 16 tokens, once. A model that answers straight away
        stops after a word; one that thinks runs out of tokens or shows think tags.
        Picks "raw" or "chat" for every later call, so no real request pays for the
        thinking. If the probe fails, chat stays and thinking is spotted in the
        replies instead (never probe twice: a dead server must not double the wait)."""
        started = time.perf_counter()
        body: Dict[str, Any] = {"model": self.model, "max_tokens": 16, "temperature": 0,
                                "stream": False,
                                "messages": [{"role": "user", "content": "Say OK."}]}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        try:
            choice = self._post(self.url, body, min(self.timeout_s, 10))
        except BonsaiError as e:
            self.log(f"[bonsai] thinking probe failed ({e}); spotting thinking in replies instead")
            return None
        finally:
            # Set only now: a call made while the probe runs waits on the lock for it.
            self.probed = True
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        finish = choice.get("finish_reason")
        thinks = bool(thought(message, finish)) or finish == "length"
        self.mode = "raw" if thinks else "chat"
        self.probe_result = self.mode
        self.log(f"[bonsai] {self.model}: " + (
            "thinks despite reasoning_effort, using no-think prompts" if thinks
            else "answers without thinking, using chat") +
            f" (probe {time.perf_counter() - started:.2f}s)")
        return self.mode

    @classmethod
    def from_config(cls, cfg: Dict[str, Any], log: Any = print) -> "BonsaiClient":
        return cls(cfg["base_url"], cfg["model"], cfg["timeout_s"],
                   cfg["max_tokens"], cfg["temperature"], cfg.get("reasoning_effort", "none"),
                   cfg.get("no_think", "auto"), log)

    def chat(self, messages: List[Dict[str, Any]],
             tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Return choices[0].message as a dict, thinking removed and tool-call XML
        parsed. Raises BonsaiError on anything odd."""
        started = time.perf_counter()
        if self.no_think == "auto" and not self.probed:
            self._probe_once()
        if self.mode == "raw":
            try:
                message = self._raw(messages, tools)
            except BonsaiError as e:
                if self.no_think != "auto" or not str(e).startswith("HTTP 4"):
                    raise
                # This server has no raw completions for the model: chat it is.
                self.log(f"[bonsai] no-think prompt refused ({e}); using chat")
                self.mode = "chat"
            else:
                self.last = {"endpoint": "raw", "thought_chars": 0, "retried": False,
                             "model": self.model,
                             "elapsed_s": round(time.perf_counter() - started, 2)}
                return message
        message, finish, chars = self._chat(messages, tools)
        last = {"endpoint": "chat", "finish_reason": finish, "thought_chars": chars,
                "retried": False, "model": self.model}
        self.last = last
        if chars:
            self.thinking_seen += 1
            usable = finish != "length" and bool(message.get("tool_calls") or message.get("content"))
            if self.no_think == "auto":
                self.log(f"[bonsai] {self.model} thought before answering ({chars} chars, "
                         f"finish_reason {finish}) despite reasoning_effort "
                         f"{self.reasoning_effort!r}: switching to no-think prompts")
                self.mode = "raw"
                if not usable:      # a finished answer is kept; only later calls change
                    try:
                        message = self._raw(messages, tools)
                    except BonsaiError as e:
                        if str(e).startswith("HTTP 4"):    # this server has no raw completions
                            self.log(f"[bonsai] no-think prompt refused ({e}); back to chat")
                            self.mode = "chat"
                        else:                              # busy or slow: raw stays for next time
                            self.log(f"[bonsai] no-think retry failed ({e})")
                    else:
                        last.update(endpoint="raw", retried=True)
        last["elapsed_s"] = round(time.perf_counter() - started, 2)
        return message

    # ---- the two endpoints ---------------------------------------------------

    def _post(self, url: str, body: Dict[str, Any], timeout_s: Optional[float] = None) -> Dict[str, Any]:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s or self.timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:200]
            raise BonsaiError(f"HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError,
                http.client.HTTPException) as e:
            raise BonsaiError(f"connection failed: {e}") from e
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise BonsaiError(f"unexpected response: {raw[:200]}") from e
        if not isinstance(choice, dict):
            raise BonsaiError(f"unexpected choice: {choice!r}"[:200])
        return choice

    def _chat(self, messages, tools):
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
        choice = self._post(self.url, body)
        message = choice.get("message")
        if not isinstance(message, dict):
            raise BonsaiError(f"unexpected message: {message!r}"[:200])
        message = dict(message)
        finish = choice.get("finish_reason")
        chars = thought(message, finish)   # before cleaning, so inline <think> tags count
        content = message.get("content")
        if isinstance(content, str):
            if not message.get("tool_calls"):
                calls = tool_calls_in(strip_thinking(content), tools)
                if calls:
                    message["tool_calls"] = calls
            message["content"] = clean_reply(content)
        return message, finish, chars

    def _raw(self, messages, tools) -> Dict[str, Any]:
        body = {"model": self.model, "prompt": render_prompt(messages, tools),
                "temperature": self.temperature, "max_tokens": self.max_tokens,
                "stream": False, "stop": ["<|im_end|>", "<|endoftext|>"]}
        choice = self._post(self.raw_url, body)
        text = choice.get("text")
        if not isinstance(text, str):
            raise BonsaiError(f"unexpected completion: {choice!r}"[:200])
        message: Dict[str, Any] = {"role": "assistant", "content": clean_reply(text)}
        calls = tool_calls_in(text, tools)
        if calls:
            message["tool_calls"] = calls
        return message
