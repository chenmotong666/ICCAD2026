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
    base_url:<str>   # optional, for Anthropic-compatible gateways
    model:   <str>   # e.g. "claude-haiku-4-5"
  generation:
    temperature:      <float>  default 0.2
    max_output_tokens:<int>    default 4096
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# These are imported lazily inside the methods so the module can be loaded
# even when only one SDK is installed.

Message = dict[str, Any]   # role + content, possibly with tool_calls


def _curl_run(cmd: list[str], body: str, timeout: float) -> subprocess.CompletedProcess:
    """Run curl in a new session and SIGKILL the group on timeout."""
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        out, err = proc.communicate(input=body, timeout=max(2.0, timeout + 1.0))
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        try:
            proc.kill()
        except Exception:
            pass
        try:
            out, err = proc.communicate(timeout=2.0)
        except Exception:
            out, err = "", ""
        raise
    return subprocess.CompletedProcess(cmd, int(proc.returncode or 0), out, err)


class LLMClient:
    """
    Provider-agnostic LLM client with native tool-use support.

    Parameters
    ----------
    provider        : "openai" | "anthropic"
    api_key         : str
    base_url        : str    (optional gateway URL)
    model           : str
    temperature     : float   (default 0.2)
    max_output_tokens: int    (default 4096)
    """

    def __init__(self, provider: str, api_key: str, model: str,
                 temperature: float = 0.2,
                 max_output_tokens: int = 4096,
                 base_url: str = "",
                 fallback_provider: str = "",
                 fallback_api_key: str = "",
                 fallback_base_url: str = "",
                 fallback_model: str = "") -> None:
        self.provider          = provider.lower()
        self.api_key           = api_key
        self.base_url          = base_url
        self.model             = model
        self.temperature       = temperature
        self.max_output_tokens = max_output_tokens
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self._client           = self._build_client()
        # Fallback provider (used when primary fails)
        self._fallback_client = None
        if fallback_provider and fallback_api_key:
            self._fallback_client = LLMClient(
                provider=fallback_provider,
                api_key=fallback_api_key,
                base_url=fallback_base_url or "",
                model=fallback_model or model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )


    def chat(self,
             messages: list[Message],
             tools: list[dict],
             system: str = "",
             timeout_sec: Optional[float] = None) -> tuple[Optional[str], list[dict]]:
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
          text_reply : str | None   -the model's final text, if any
          tool_calls : list[dict]   -zero or more tool invocation dicts,
                                      each with keys: id, name, arguments (dict)
        """
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        started = time.monotonic()
        budget = self._effective_timeout(timeout_sec)
        try:
            if self.provider == "openai":
                return self._openai_chat(messages, tools, system, budget)
            elif self.provider == "anthropic":
                return self._anthropic_chat(messages, tools, system, budget)
            else:
                raise ValueError(f"Unknown provider: {self.provider!r}")
        except Exception as e:
            if self._fallback_client is not None:
                leftover = self._remaining_attempt_timeout(started, budget)
                if leftover is None:
                    raise
                try:
                    return self._fallback_client.chat(
                        messages, tools, system, timeout_sec=leftover)
                except Exception:
                    pass  # fallback also failed, raise original error
            raise

    def usage_summary(self) -> dict[str, int]:
        """Return cumulative token usage observed from provider responses."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    def last_usage_summary(self) -> dict[str, int]:
        """Return token usage from the most recent provider response."""
        return {
            "prompt_tokens": self.last_prompt_tokens,
            "completion_tokens": self.last_completion_tokens,
            "total_tokens": self.last_total_tokens,
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


    def _openai_chat(self, messages: list[Message], tools: list[dict],
                     system: str,
                     timeout_sec: Optional[float] = None) -> tuple[Optional[str], list[dict]]:
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

        timeout = self._effective_timeout(timeout_sec)
        started = time.monotonic()
        try:
            client = self._client.with_options(timeout=timeout, max_retries=0)
            raw_resp = client.chat.completions.create(**kwargs)
        except Exception:
            if not self.base_url:
                raise
            leftover = self._remaining_attempt_timeout(started, timeout)
            if leftover is None:
                raise
            raw_resp = self._openai_chat_via_curl(kwargs, leftover)
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
            # tool-argument blob.  R15 (F-02/C4): do NOT silently drop the
            # call — keep it with empty arguments so dispatch produces a
            # visible tool error that triggers the replan loop; a silent
            # drop made the agent answer from stale context instead.
            logger.warning(
                "Malformed tool arguments for %s; kept call with empty args",
                name,
            )
            arguments = {}
        return {
            "id": self._field(tc, "id") or f"call_{index}",
            "name": str(name),
            "arguments": arguments,
        }

    def _openai_chat_via_curl(self, payload: dict[str, Any], timeout: float) -> Any:
        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        curl = "curl.exe" if os.name == "nt" else "curl"
        cmd = [
            curl,
            "-sS",
            "-k",
            "-L",
            "--max-time", str(max(1, int(timeout))),
            "-X", "POST",
            endpoint,
            "-H", "Content-Type: application/json",
            "-H", f"Authorization: Bearer {self.api_key}",
            "--data-binary", "@-",
            "-w", "\n__HTTP_STATUS__:%{http_code}",
        ]
        if os.name == "nt":
            cmd.insert(2, "--ssl-no-revoke")
        body = json.dumps(payload, ensure_ascii=False)
        proc = _curl_run(cmd, body, timeout)
        combined = (proc.stdout or "") + (proc.stderr or "")
        if "__HTTP_STATUS__:" in combined:
            response_text, _, status_text = combined.rpartition("__HTTP_STATUS__:")
            status_text = status_text.strip()
        else:
            response_text = proc.stdout or ""
            status_text = "000"
        try:
            status = int(status_text[:3])
        except ValueError:
            status = 0
        if proc.returncode != 0 or status < 200 or status >= 300:
            detail = self._scrub_sensitive((proc.stderr or "") + response_text)
            raise RuntimeError(
                f"curl OpenAI request failed status={status} rc={proc.returncode}: {detail[:500]}"
            )
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            detail = self._scrub_sensitive(response_text)
            raise RuntimeError(f"curl OpenAI response was not JSON: {detail[:500]}") from e

    def _scrub_sensitive(self, text: str) -> str:
        text = re.sub(r"sk-[A-Za-z0-9_-]+", "<key>", str(text or ""))
        text = re.sub(r"https?://[^\s\"')>]+", "<url>", text)
        return text

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


    def _anthropic_chat(self, messages: list[Message], tools: list[dict],
                        system: str,
                        timeout_sec: Optional[float] = None) -> tuple[Optional[str], list[dict]]:
        import anthropic

        kwargs: dict[str, Any] = {
            "model":      self.model,
            "max_tokens": self.max_output_tokens,
            "messages":   messages,
            "temperature": self.temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        timeout = self._effective_timeout(timeout_sec)
        started = time.monotonic()
        try:
            client = self._client.with_options(timeout=timeout, max_retries=0)
            resp = client.messages.create(**kwargs)
        except Exception:
            if not self.base_url:
                raise
            leftover = self._remaining_attempt_timeout(started, timeout)
            if leftover is None:
                raise
            resp = self._anthropic_chat_via_curl(kwargs, leftover)
        self._record_anthropic_usage(resp)

        text: Optional[str] = None
        tool_calls: list[dict] = []

        for block in self._field(resp, "content", []) or []:
            block = self._coerce_jsonish(block)
            block_type = self._field(block, "type")
            if block_type == "text":
                text = self._field(block, "text")
            elif block_type == "tool_use":
                tool_calls.append({
                    "id":        self._field(block, "id"),
                    "name":      self._field(block, "name"),
                    "arguments": self._field(block, "input", {}),
                })
        return text, tool_calls

    def _anthropic_chat_via_curl(self, payload: dict[str, Any], timeout: float) -> Any:
        endpoint = self.base_url.rstrip("/") + "/v1/messages"
        curl = "curl.exe" if os.name == "nt" else "curl"
        cmd = [
            curl,
            "-sS",
            "-k",
            "-L",
            "--max-time", str(max(1, int(timeout))),
            "-X", "POST",
            endpoint,
            "-H", "Content-Type: application/json",
            "-H", f"x-api-key: {self.api_key}",
            "-H", "anthropic-version: 2023-06-01",
            "--data-binary", "@-",
            "-w", "\n__HTTP_STATUS__:%{http_code}",
        ]
        if os.name == "nt":
            cmd.insert(2, "--ssl-no-revoke")
        body = json.dumps(payload, ensure_ascii=False)
        proc = _curl_run(cmd, body, timeout)
        combined = (proc.stdout or "") + (proc.stderr or "")
        if "__HTTP_STATUS__:" in combined:
            response_text, _, status_text = combined.rpartition("__HTTP_STATUS__:")
            status_text = status_text.strip()
        else:
            response_text = proc.stdout or ""
            status_text = "000"
        try:
            status = int(status_text[:3])
        except ValueError:
            status = 0
        if proc.returncode != 0 or status < 200 or status >= 300:
            detail = self._scrub_sensitive((proc.stderr or "") + response_text)
            raise RuntimeError(
                f"curl Anthropic request failed status={status} rc={proc.returncode}: {detail[:500]}"
            )
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            detail = self._scrub_sensitive(response_text)
            raise RuntimeError(f"curl Anthropic response was not JSON: {detail[:500]}") from e


    def _build_client(self) -> Any:
        if self.provider == "openai":
            from openai import OpenAI
            kwargs: dict[str, Any] = {
                "api_key": self.api_key,
                "timeout": 120.0,
                "max_retries": 0,
            }
            if self.base_url:
                kwargs["base_url"] = self.base_url
            return OpenAI(**kwargs)
        elif self.provider == "anthropic":
            import anthropic
            kwargs = {
                "api_key": self.api_key,
                "timeout": 120.0,
                "max_retries": 0,
            }
            if self.base_url:
                kwargs["base_url"] = self.base_url
            return anthropic.Anthropic(**kwargs)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider!r}")

    def _build_client_for(self, provider: str, api_key: str,
                          base_url: str, model: str) -> Any:
        """Build a client for an alternate provider (used for fallback)."""
        if provider == "openai":
            from openai import OpenAI
            kwargs: dict[str, Any] = {
                "api_key": api_key,
                "timeout": 120.0,
                "max_retries": 0,
            }
            if base_url:
                kwargs["base_url"] = base_url
            return OpenAI(**kwargs)
        elif provider == "anthropic":
            import anthropic
            kwargs = {
                "api_key": api_key,
                "timeout": 120.0,
                "max_retries": 0,
            }
            if base_url:
                kwargs["base_url"] = base_url
            return anthropic.Anthropic(**kwargs)
        else:
            raise ValueError(f"Unknown fallback provider: {provider!r}")

    @staticmethod
    def _effective_timeout(timeout_sec: Optional[float]) -> float:
        """Clamp one provider call so it cannot consume the whole request budget."""
        if timeout_sec is None:
            return 120.0
        return max(1.0, min(120.0, float(timeout_sec)))

    @staticmethod
    def _remaining_attempt_timeout(started: float, timeout: float) -> Optional[float]:
        """Seconds left in one attempt budget; None if the next stage would be useless."""
        leftover = float(timeout) - (time.monotonic() - started)
        if leftover <= 2.0:
            return None
        return leftover

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
        self.last_prompt_tokens = prompt
        self.last_completion_tokens = completion
        self.last_total_tokens = total
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total

    def _record_anthropic_usage(self, resp: Any) -> None:
        usage = self._field(resp, "usage")
        if usage is None:
            return
        prompt = int(self._field(usage, "input_tokens", 0) or 0)
        completion = int(self._field(usage, "output_tokens", 0) or 0)
        self.last_prompt_tokens = prompt
        self.last_completion_tokens = completion
        self.last_total_tokens = prompt + completion
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion
