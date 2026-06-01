"""
agent/react_agent.py
====================
ReAct-style LLM agent that drives the EDA backend via tool calls.

ReAct loop (per request)
-------------------------
  1. Send user request + conversation history to LLM.
  2. If model returns tool_calls → execute each tool → append results → go to 1.
  3. If model returns a text reply → return it as the final answer.
  4. Hard limit of MAX_ROUNDS per request to prevent runaway loops.

Tool dispatch
-------------
  The dispatch map and category limits are auto-derived from the consolidated
  _TOOL_REGISTRY in tool_schema.py — no manual duplication of tool names.

Conversation history
--------------------
  A rolling list of {role, content} dicts is maintained per testcase.
  A sliding-window trim caps total messages to prevent context overflow.
"""

from __future__ import annotations

import inspect
import sys
import traceback
import time
from typing import Any, Optional

from .llm_client import LLMClient
from .tool_schema import (
    SYSTEM_PROMPT,
    get_tools_for_request,
    get_dispatch_map_for_backend_tools,
    get_category_limits,
    _is_transform_request,
)
from eda.backend import EDABackend

# ── constants ─────────────────────────────────────────────────────────────────

LLM_RETRIES = 3
HISTORY_CONTENT_LIMIT = 1200
MAX_HISTORY_MESSAGES = 40  # sliding-window cap (system prompt + last N messages)

# Built once at import time from the consolidated tool registry
_DISPATCH_MAP: dict[str, tuple[str, bool]] = get_dispatch_map_for_backend_tools()
_TOOL_CATEGORY_LIMITS: dict[str, int]          = get_category_limits()
_TOOL_LIMIT_DEFAULT = 800

# Known failure prefixes for response standardisation
_FAILURE_PREFIXES: tuple[str, ...] = (
    "Error:",
    "Tool error",
    "Unexpected error",
    "LLM request failed",
    "Configuration error",
    "Equivalence check error",
    "UNKNOWN[",
)


def _limit_for_tool(tool_name: str) -> int:
    return _TOOL_CATEGORY_LIMITS.get(tool_name, _TOOL_LIMIT_DEFAULT)


# ── tool dispatcher ────────────────────────────────────────────────────────────

def _dispatch(backend: EDABackend, tool_name: str,
              arguments: dict) -> str:
    """Map a tool name + arguments dict to the corresponding EDABackend method."""
    try:
        entry = _DISPATCH_MAP.get(tool_name)
        if entry is None:
            return f"Unknown tool: '{tool_name}'"
        method_name, takes_kwargs = entry
        fn = getattr(backend, method_name)
        return fn(**arguments) if takes_kwargs else fn()
    except RuntimeError as e:
        return f"Tool error ({tool_name}): {e}"
    except Exception:
        return f"Unexpected error in tool '{tool_name}':\n{traceback.format_exc(limit=5)}"


def _standardize_response(text: str) -> str:
    """Normalize success/failure prefixes without changing the substantive answer."""
    stripped = (text or "").strip()
    # Preserve already-prefixed responses (FAIL/UNKNOWN from tool errors)
    if stripped.startswith(("FAIL[", "UNKNOWN[")):
        return stripped
    # Re-wrap OK-prefixed responses without the prefix
    if stripped.startswith("OK:"):
        return stripped[3:].strip()
    if stripped.startswith(_FAILURE_PREFIXES):
        return f"FAIL[RUNTIME]: {stripped}"
    first_line = stripped.split("\n")[0].lower()
    if "not supported" in first_line or "unsupported" in first_line:
        return f"FAIL[UNSUPPORTED]: {stripped}"
    return stripped


# ── ReAct agent ────────────────────────────────────────────────────────────────

class ReactAgent:
    """Stateful ReAct agent.  One instance is created at contest startup and
    reused across all requests in the session.  Conversation history is
    reset at the beginning of each testcase (call reset()).

    Parameters
    ----------
    llm     : LLMClient   — pre-configured LLM client
    backend : EDABackend  — pre-configured EDA backend
    verbose : bool        — if True, print tool calls to stderr for debugging
    """

    def __init__(self, llm: LLMClient,
                 backend: EDABackend,
                 verbose: bool = False) -> None:
        self.llm     = llm
        self.backend = backend
        self.verbose = verbose
        self.history: list[dict] = []

    # ── public interface ──────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear conversation history (call at the start of each testcase)."""
        self.history = []

    def run(self, user_request: str) -> str:
        """Process one natural-language request and return the final text answer."""
        self.history.append({"role": "user", "content": user_request})
        tools = get_tools_for_request(user_request, self.llm.provider)
        is_transform = _is_transform_request(user_request)

        try:
            text, tool_calls = self._chat_with_retries(tools)
        except Exception as e:
            reply = _standardize_response(f"LLM request failed: {e}")
            self._append_history_safe({"role": "assistant", "content": reply})
            return reply

        if not tool_calls:
            reply = _standardize_response(text or "(No response from model)")
            self._append_history_safe(
                {"role": "assistant", "content": _compact_for_history(reply)})
            return reply

        # Record tool-call in history (transform only — saves tokens)
        if is_transform:
            self._append_history_safe(
                self.llm.make_assistant_tool_call_message(text, tool_calls))

        # Execute tool calls and collect results
        results: list[str] = []
        for tc in tool_calls:
            if self.verbose:
                print(f"[TOOL] {tc['name']}({tc['arguments']})", file=sys.stderr)

            result = _dispatch(self.backend, tc["name"], tc["arguments"])
            results.append(result)

            if self.verbose:
                print(f"[RESULT] {result[:200]}", file=sys.stderr)

            if is_transform:
                tool_msg = self.llm.make_tool_result_message(
                    tc["id"],
                    _compact_for_history(result, tc["name"]),
                )
                self._append_history_safe(tool_msg)

        reply = _standardize_response("\n\n".join(results))
        self._append_history_safe(
            {"role": "assistant", "content": _compact_for_history(reply)})
        return reply

    # ── internal ──────────────────────────────────────────────────────────

    def _chat_with_retries(self, tools: list[dict]) -> tuple[Optional[str], list[dict]]:
        last_error: Optional[Exception] = None
        for attempt in range(LLM_RETRIES):
            try:
                return self.llm.chat(
                    messages=self.history,
                    tools=tools,
                    system=SYSTEM_PROMPT,
                )
            except Exception as e:
                last_error = e
                if attempt + 1 < LLM_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _append_history_safe(self, msg: dict) -> None:
        """Append a message to conversation history.

        NOTE: Sliding-window trimming is intentionally NOT implemented here
        because naive truncation breaks tool_call/tool_result message pairing,
        causing API 400 errors ("messages with role 'tool' must be a response
        to a preceeding message with 'tool_calls'").

        The contest testcases are bounded in length by spec; if very long
        sessions are needed in the future, trim at user-message boundaries
        (keeping complete request-response-tool_call-tool_result groups).
        """
        self.history.append(msg)


def _compact_for_history(text: str, tool_name: str = "") -> str:
    text = text or ""
    limit = _limit_for_tool(tool_name) if tool_name else HISTORY_CONTENT_LIMIT
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."
