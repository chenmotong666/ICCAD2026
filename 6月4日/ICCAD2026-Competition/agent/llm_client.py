"""
agent/llm_client.py
===================
Unified LLM client that supports both OpenAI and Anthropic APIs with
native tool-use (function calling).

Both APIs follow the same pattern:
  1. Send a list of messages + a list of tool definitions.
  2. The model responds with either a final text reply or one or more tool_use blocks.
  3. We execute the tools and send back the results.
  4. Repeat until a final text reply arrives.

This file exposes a single LLMClient class whose interface hides the
provider-specific differences.

Config schema (parsed from the YAML config file):
  provider: "openai" | "anthropic"
  openai:
    api_key: <str>
    base_url:<str>   # optional, for OpenAI-compatible gateways
    model:   <str>   # e.g. "gpt-4o-mini"
  anthropic:
    api_key: <str>
    model:   <str>   # e.g. "claude-haiku-4-5"
  generation:
    temperature:      <float>  default 0.2
    max_output_tokens:<int>    default 4096
"""

from __future__ import annotations

import json
from typing import Any, Optional

# These are imported lazily inside the methods so the module can be loaded
# even when only one SDK is installed.

Message = dict[str, Any]   # role + content, possibly with tool_calls


class LLMClient:
    """
    Provider-agnostic LLM client with native tool-use support.

    Parameters
    ----------
    provider        : "openai" | "anthropic"
    api_key         : str
    base_url        : str    (optional, OpenAI only)
    model           : str
    temperature     : float   (default 0.2)
    max_output_tokens: int    (default 4096)
    """

    def __init__(self, provider: str, api_key: str, model: str,
                 temperature: float = 0.2,
                 max_output_tokens: int = 4096,
                 base_url: str = "") -> None:
        self.provider          = provider.lower()
        self.api_key           = api_key
        self.base_url          = base_url
        self.model             = model
        self.temperature       = temperature
        self.max_output_tokens = max_output_tokens
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self._client           = self._build_client()

    # ── public ────────────────────────────────────────────────────────────────

    def chat(self,
             messages: list[Message],
             tools: list[dict],
             system: str = "") -> tuple[Optional[str], list[dict]]:
        """
        Send a conversation to the LLM.

        Parameters
        ----------
        messages : list of {role, content} dicts (growing conversation history)
        tools    : list of tool definitions (provider-specific format built
                   by tool_schema.py)
        system   : optional system prompt string

        Returns
        -------
        (text_reply, tool_calls)
          text_reply : str | None   — the model's final text, if any
          tool_calls : list[dict]   — zero or more tool invocation dicts,
                                      each with keys: id, name, arguments (dict)
        """
        if self.provider == "openai":
            return self._openai_chat(messages, tools, system)
        elif self.provider == "anthropic":
            return self._anthropic_chat(messages, tools, system)
        else:
            raise ValueError(f"Unknown provider: {self.provider!r}")

    def usage_summary(self) -> dict[str, int]:
        """Return cumulative token usage observed from provider responses."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    def make_tool_result_message(self, tool_call_id: str,
                                  result: str) -> Message:
        """
        Build the provider-specific message that carries a tool's result
        back into the conversation history.
        """
        if self.provider == "openai":
            return {
                "role":         "tool",
                "tool_call_id": tool_call_id,
                "content":      result,
            }
        else:   # anthropic
            return {
                "role": "user",
                "content": [{
                    "type":       "tool_result",
                    "tool_use_id": tool_call_id,
                    "content":    result,
                }],
            }

    def make_assistant_tool_call_message(self,
                                          text: Optional[str],
                                          tool_calls: list[dict]) -> Message:
        """
        Build the assistant message that records its tool calls,
        so the conversation history is self-consistent.
        """
        if self.provider == "openai":
            content = text or ""
            msg: Message = {"role": "assistant", "content": content}
            if tool_calls:
                msg["tool_calls"] = [
                    {
                        "id":       tc["id"],
                        "type":     "function",
                        "function": {
                            "name":      tc["name"],
                            "arguments": json.dumps(tc["arguments"]),
                        },
                    }
                    for tc in tool_calls
                ]
            return msg
        else:   # anthropic
            content_blocks: list[dict] = []
            if text:
                content_blocks.append({"type": "text", "text": text})
            for tc in tool_calls:
                content_blocks.append({
                    "type":  "tool_use",
                    "id":    tc["id"],
                    "name":  tc["name"],
                    "input": tc["arguments"],
                })
            return {"role": "assistant", "content": content_blocks}

    # ── OpenAI implementation ─────────────────────────────────────────────────

    def _openai_chat(self, messages: list[Message], tools: list[dict],
                     system: str) -> tuple[Optional[str], list[dict]]:
        from openai import OpenAI

        all_messages: list[Message] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model":       self.model,
            "messages":    all_messages,
            "temperature": self.temperature,
            "max_tokens":  self.max_output_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        raw_resp = self._client.chat.completions.create(**kwargs)
        resp = self._coerce_jsonish(raw_resp)
        self._record_openai_usage(resp)

        if isinstance(resp, str):
            return (resp or None), []

        choices = self._coerce_jsonish(self._field(resp, "choices") or [])
        if isinstance(choices, str):
            return (choices or None), []
        if not choices:
            text = self._first_text(resp)
            return text, []

        choice = choices[0]
        msg = self._coerce_jsonish(self._field(choice, "message") or choice)

        text = self._field(msg, "content") or self._field(choice, "text") or None
        if isinstance(text, list):
            text = self._text_from_blocks(text)

        tool_calls = []
        raw_tool_calls = self._coerce_jsonish(self._field(msg, "tool_calls") or [])
        if isinstance(raw_tool_calls, list):
            for index, tc in enumerate(raw_tool_calls):
                parsed = self._parse_openai_tool_call(tc, index)
                if parsed is not None:
                    tool_calls.append(parsed)
        return text, tool_calls

    def _parse_openai_tool_call(self, tc: Any, index: int) -> Optional[dict]:
        tc = self._coerce_jsonish(tc)
        function = self._coerce_jsonish(self._field(tc, "function") or {})
        name = (
            self._field(function, "name")
            or self._field(tc, "name")
            or self._field(tc, "tool_name")
        )
        if not name:
            return None
        raw_args = (
            self._field(function, "arguments")
            or self._field(tc, "arguments")
            or self._field(tc, "input")
            or {}
        )
        try:
            arguments = self._parse_arguments(raw_args)
        except (TypeError, ValueError, json.JSONDecodeError):
            # Some OpenAI-compatible endpoints occasionally emit a malformed
            # tool-argument blob. Skip that call so the caller can return any
            # text or prior tool result instead of aborting the whole testcase.
            return None
        return {
            "id": self._field(tc, "id") or f"call_{index}",
            "name": str(name),
            "arguments": arguments,
        }

    def _parse_arguments(self, raw_args: Any) -> dict:
        raw_args = self._coerce_jsonish(raw_args)
        if raw_args is None or raw_args == "":
            return {}
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("tool arguments must decode to an object")

    def _coerce_jsonish(self, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    return value
            return value
        if isinstance(value, (dict, list)) or value is None:
            return value
        try:
            return value.model_dump()
        except Exception:
            return value

    def _field(self, obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _first_text(self, obj: Any) -> Optional[str]:
        for key in ("content", "text", "message", "response", "answer"):
            value = self._field(obj, key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, list):
                text = self._text_from_blocks(value)
                if text:
                    return text
            if isinstance(value, dict):
                text = self._first_text(value)
                if text:
                    return text
        return None

    def _text_from_blocks(self, blocks: list[Any]) -> Optional[str]:
        parts: list[str] = []
        for block in blocks:
            block = self._coerce_jsonish(block)
            if isinstance(block, str):
                parts.append(block)
                continue
            text = self._field(block, "text") or self._field(block, "content")
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(part for part in parts if part) or None

    # ── Anthropic implementation ──────────────────────────────────────────────

    def _anthropic_chat(self, messages: list[Message], tools: list[dict],
                        system: str) -> tuple[Optional[str], list[dict]]:
        import anthropic

        kwargs: dict[str, Any] = {
            "model":      self.model,
            "max_tokens": self.max_output_tokens,
            "messages":   messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        resp = self._client.messages.create(**kwargs)
        self._record_anthropic_usage(resp)

        text: Optional[str] = None
        tool_calls: list[dict] = []

        for block in resp.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id":        block.id,
                    "name":      block.name,
                    "arguments": block.input,
                })
        return text, tool_calls

    # ── client factory ────────────────────────────────────────────────────────

    def _build_client(self) -> Any:
        if self.provider == "openai":
            from openai import OpenAI
            kwargs: dict[str, Any] = {
                "api_key": self.api_key,
                "timeout": 120.0,
                "max_retries": 1,
            }
            if self.base_url:
                kwargs["base_url"] = self.base_url
            return OpenAI(**kwargs)
        elif self.provider == "anthropic":
            import anthropic
            return anthropic.Anthropic(api_key=self.api_key, timeout=120.0)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider!r}")

    def _record_openai_usage(self, resp: Any) -> None:
        usage = self._field(resp, "usage")
        if usage is None:
            return
        prompt = int(
            self._field(usage, "prompt_tokens", 0)
            or self._field(usage, "input_tokens", 0)
            or 0
        )
        completion = int(
            self._field(usage, "completion_tokens", 0)
            or self._field(usage, "output_tokens", 0)
            or 0
        )
        total = int(self._field(usage, "total_tokens", 0) or (prompt + completion))
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total

    def _record_anthropic_usage(self, resp: Any) -> None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        prompt = int(getattr(usage, "input_tokens", 0) or 0)
        completion = int(getattr(usage, "output_tokens", 0) or 0)
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion
