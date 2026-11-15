"""A model that thinks before answering (the Mac's MLX 9B ignored reasoning_effort
"none", PR #40): the client spots it, switches to the raw no-think prompt and still
returns usable tool calls and lines."""
import json
import unittest

from brain.bonsai import (NO_THINK, BonsaiClient, BonsaiError, clean_reply, render_prompt,
                          thought, tool_calls_in)
from brain.settings import llm_config

TOOLS = [{"type": "function", "function": {"name": "outlet_on", "parameters": {
    "type": "object", "properties": {"outlet": {"type": "string"}}}}}]
MESSAGES = [{"role": "system", "content": "You are Faraday."},
            {"role": "user", "content": 'Heard: "Porch light on."'}]
XML = "<tool_call>\n<function=outlet_on>\n<parameter=outlet>\nporch_light\n</parameter>\n</function>\n</tool_call>"
THINKING = {"message": {"role": "assistant", "content": "", "reasoning_content": "The user wants..."},
            "finish_reason": "length"}
PLAIN = {"message": {"role": "assistant", "content": "OK."}, "finish_reason": "stop"}


class Scripted(BonsaiClient):
    """BonsaiClient with the HTTP call replaced: answers come from a script per endpoint."""

    def __init__(self, chat=(), raw=(), **kw):
        super().__init__("http://x", "m", log=lambda m: None, **kw)
        self.chat_answers, self.raw_answers, self.calls = list(chat), list(raw), []

    def _post(self, url, body, timeout_s=None):
        kind = "raw" if url.endswith("/completions") and not url.endswith("chat/completions") else "chat"
        self.calls.append((kind, body))
        answer = (self.raw_answers if kind == "raw" else self.chat_answers).pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def raw(text):
    return {"text": text, "finish_reason": "stop"}


class Parsing(unittest.TestCase):
    def test_prompt_matches_the_template_with_thinking_off(self):
        p = render_prompt(MESSAGES, TOOLS)
        self.assertTrue(p.startswith("<|im_start|>system\n# Tools\n\nYou have access to the following functions:"))
        self.assertIn('"name": "outlet_on"', p)
        self.assertIn("</IMPORTANT>\n\nYou are Faraday.<|im_end|>\n", p)
        self.assertIn('<|im_start|>user\nHeard: "Porch light on."<|im_end|>\n', p)
        self.assertTrue(p.endswith("<|im_start|>assistant\n" + NO_THINK))
        self.assertEqual(render_prompt(MESSAGES), "<|im_start|>system\nYou are Faraday.<|im_end|>\n"
                         '<|im_start|>user\nHeard: "Porch light on."<|im_end|>\n<|im_start|>assistant\n' + NO_THINK)

    def test_tool_call_xml(self):
        [call] = tool_calls_in(XML)
        self.assertEqual(call["function"]["name"], "outlet_on")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"outlet": "porch_light"})
        two = tool_calls_in("Sure.\n" + XML.replace("outlet_on", "speak").replace("outlet", "text")
                            + "\n<tool_call>\n<function=summarize_log>\n<parameter=minutes>\n60\n"
                              "</parameter>\n</function>\n</tool_call>")
        self.assertEqual([json.loads(c["function"]["arguments"]) for c in two],
                         [{"text": "porch_light"}, {"minutes": "60"}])   # no schema: text

    def test_thinking_is_removed(self):
        self.assertEqual(clean_reply("<think>hmm</think>\n\nPlease leave."), "Please leave.")
        self.assertEqual(clean_reply("they want a line\n</think>\n\nPlease leave."), "Please leave.")
        self.assertEqual(clean_reply("<think>still going and out of tok"), "")
        self.assertEqual(clean_reply("Sure. " + XML), "Sure.")

    def test_thinking_is_spotted(self):
        self.assertTrue(thought(THINKING["message"], "length"))
        self.assertTrue(thought({"content": "<think>a</think>OK"}, "stop"))
        self.assertTrue(thought({"content": "hmm</think>OK"}, "stop"))
        self.assertTrue(thought({"content": ""}, "length"))
        self.assertFalse(thought({"content": "OK."}, "stop"))
        self.assertFalse(thought({"content": "", "tool_calls": [{}]}, "length"))


class Modes(unittest.TestCase):
    def test_probe_finds_thinking_so_every_call_is_raw(self):
        c = Scripted(chat=[THINKING], raw=[raw(XML), raw("Please leave now.")])
        m = c.chat(MESSAGES, TOOLS)
        self.assertEqual(json.loads(m["tool_calls"][0]["function"]["arguments"]), {"outlet": "porch_light"})
        self.assertEqual(c.chat(MESSAGES)["content"], "Please leave now.")
        self.assertEqual([k for k, _ in c.calls], ["chat", "raw", "raw"])
        probe = c.calls[0][1]
        self.assertEqual((probe["max_tokens"], probe["reasoning_effort"]), (16, "none"))
        self.assertTrue(c.calls[1][1]["prompt"].endswith(NO_THINK))
        self.assertEqual(c.calls[1][1]["stop"][0], "<|im_end|>")
        self.assertEqual(c.probe_result, "raw")

    def test_probe_finds_no_thinking_so_chat_stays(self):
        answer = {"message": {"role": "assistant", "tool_calls": [{"function": {"name": "outlet_on"}}]},
                  "finish_reason": "tool_calls"}
        c = Scripted(chat=[PLAIN, answer])
        self.assertTrue(c.chat(MESSAGES, TOOLS)["tool_calls"])
        self.assertEqual([k for k, _ in c.calls], ["chat", "chat"])
        self.assertEqual(c.last["endpoint"], "chat")

    def test_thinking_later_switches_and_retries_once(self):
        c = Scripted(chat=[PLAIN, THINKING], raw=[raw("Please leave.")])
        self.assertEqual(c.chat(MESSAGES)["content"], "Please leave.")
        self.assertEqual((c.mode, c.last["retried"], c.thinking_seen), ("raw", True, 1))

    def test_raw_refused_falls_back_to_chat(self):
        c = Scripted(chat=[THINKING, PLAIN], raw=[BonsaiError("HTTP 404: no completions")])
        self.assertEqual(c.chat(MESSAGES)["content"], "OK.")
        self.assertEqual(c.mode, "chat")

    def test_probe_failure_never_doubles_the_wait(self):
        c = Scripted(chat=[BonsaiError("connection failed"), BonsaiError("connection failed"), THINKING],
                     raw=[raw("Please leave.")])
        with self.assertRaises(BonsaiError):
            c.chat(MESSAGES)
        self.assertEqual(len(c.calls), 2)          # one probe, then the real call; no second probe
        self.assertEqual(c.chat(MESSAGES)["content"], "Please leave.")   # thinking spotted in the reply
        self.assertEqual([k for k, _ in c.calls], ["chat", "chat", "chat", "raw"])

    def test_forced_modes(self):
        c = Scripted(raw=[raw("Hi.")], no_think="raw")
        self.assertEqual(c.chat(MESSAGES)["content"], "Hi.")
        self.assertEqual([k for k, _ in c.calls], ["raw"])
        c = Scripted(chat=[THINKING], no_think="chat")
        self.assertEqual(c.chat(MESSAGES)["content"], "")
        self.assertEqual(([k for k, _ in c.calls], c.mode), (["chat"], "chat"))
        with self.assertRaises(ValueError):
            Scripted(no_think="maybe")

    def test_xml_tool_calls_in_chat_content_are_parsed(self):
        c = Scripted(chat=[PLAIN, {"message": {"content": "<think>x</think>" + XML}, "finish_reason": "stop"}])
        m = c.chat(MESSAGES, TOOLS)
        self.assertEqual(m["tool_calls"][0]["function"]["name"], "outlet_on")


class ReviewFixes(unittest.TestCase):
    """From the review of PR #42."""

    def test_inline_thinking_is_seen_and_a_finished_answer_kept(self):
        inline = {"message": {"content": "<think>they want the light</think>\n" + XML}, "finish_reason": "stop"}
        c = Scripted(chat=[PLAIN, inline], raw=[raw("Hi.")])
        m = c.chat(MESSAGES, TOOLS)
        self.assertEqual(m["tool_calls"][0]["function"]["name"], "outlet_on")   # kept, no retry
        self.assertEqual((c.mode, c.thinking_seen, c.last["thought_chars"] > 0), ("raw", 1, True))
        self.assertEqual([k for k, _ in c.calls], ["chat", "chat"])
        self.assertEqual(c.chat(MESSAGES)["content"], "Hi.")                     # later calls: raw

    def test_a_call_waits_for_the_probe_in_flight(self):
        import threading
        import time as _t
        gate = threading.Event()

        class Slow(Scripted):
            def _post(self, url, body, timeout_s=None):
                if body.get("max_tokens") == 16:
                    gate.wait(2)
                return super()._post(url, body, timeout_s)

        c = Slow(chat=[THINKING], raw=[raw("Please leave.")])
        c.warm_up()
        _t.sleep(0.05)
        threading.Timer(0.2, gate.set).start()
        self.assertEqual(c.chat(MESSAGES)["content"], "Please leave.")
        self.assertEqual([k for k, _ in c.calls], ["chat", "raw"])                # probe, then raw only

    def test_a_slow_retry_keeps_raw_for_next_time(self):
        c = Scripted(chat=[PLAIN, THINKING], raw=[BonsaiError("connection failed: timed out"), raw("Hi.")])
        self.assertEqual(c.chat(MESSAGES)["content"], "")
        self.assertEqual(c.mode, "raw")
        self.assertEqual(c.chat(MESSAGES)["content"], "Hi.")

    def test_values_typed_by_the_schema(self):
        tools = [{"type": "function", "function": {"name": "save_message", "parameters": {"properties": {
            "text": {"type": "string"}, "sender": {"type": "string"}}}}},
                 {"type": "function", "function": {"name": "summarize_log", "parameters": {"properties": {
                     "minutes": {"type": "integer"}}}}}]
        xml = ("<tool_call>\n<function=save_message>\n<parameter=text>\n5551234567\n</parameter>\n"
               "<parameter=sender>\ntrue\n</parameter>\n</function>\n</tool_call>\n"
               "<tool_call>\n<function=summarize_log>\n<parameter=minutes>\n60\n</parameter>\n"
               "</function>\n</tool_call>")
        a, b = [json.loads(c["function"]["arguments"]) for c in tool_calls_in(xml, tools)]
        self.assertEqual(a, {"text": "5551234567", "sender": "true"})
        self.assertEqual(b, {"minutes": 60})

    def test_null_setting_means_auto(self):
        llm = {"profile": "mac", "profiles": {"mac": {"base_url": "u", "model": "m", "no_think": None}}}
        self.assertEqual(llm_config({"llm": llm})["no_think"], "auto")


class Settings(unittest.TestCase):
    def test_no_think_from_settings(self):
        llm = {"profile": "mac", "profiles": {"mac": {"base_url": "u", "model": "m"}}}
        self.assertEqual(llm_config({"llm": llm})["no_think"], "auto")
        self.assertEqual(llm_config({"llm": {**llm, "no_think": "chat"}})["no_think"], "chat")
        llm["profiles"]["mac"]["no_think"] = "raw"
        self.assertEqual(llm_config({"llm": {**llm, "no_think": "chat"}})["no_think"], "raw")


if __name__ == "__main__":
    unittest.main()
