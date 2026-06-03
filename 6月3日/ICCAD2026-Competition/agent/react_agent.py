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

Token-optimised conversation history
-------------------------------------
  - Non-transform tool results are NOT stored in history.
  - Transform results are aggressively truncated before storage.
  - Sliding window trims at user-message boundaries, keeping complete
    tool_call/tool_result pairs intact.
"""

from __future__ import annotations

import re
import sys
import time
from typing import Any, Optional

from .llm_client import LLMClient
from .tool_schema import (
    SYSTEM_PROMPT,
    get_tools_for_request,
    get_dispatch_map_for_backend_tools,
    get_category_limits,
)
from eda.backend import EDABackend

# ── constants ─────────────────────────────────────────────────────────────────

LLM_RETRIES = 2
HISTORY_CONTENT_LIMIT = 600
USER_REQUEST_HISTORY_LIMIT = 320
MAX_HISTORY_MESSAGES = 24  # sliding-window cap

# Built once at import time
_DISPATCH_MAP: dict[str, tuple[str, bool]] = get_dispatch_map_for_backend_tools()
_TOOL_CATEGORY_LIMITS: dict[str, int]      = get_category_limits()
_TOOL_LIMIT_DEFAULT = 400

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

_REQUEST_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bensure (?:the )?design functionality does not change\.?", re.I),
    re.compile(r"\bensure functionality does not change\.?", re.I),
    re.compile(r"\bpreserve (?:the )?(?:design )?functionality\.?", re.I),
    re.compile(r"\bwithout changing (?:the )?(?:design )?functionality\.?", re.I),
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
        import traceback
        return f"Tool err '{tool_name}': {traceback.format_exc(limit=3)}"


def _standardize_response(text: str) -> str:
    """Normalize success/failure prefixes."""
    stripped = (text or "").strip()
    if stripped.startswith(("FAIL[", "UNKNOWN[")):
        return stripped
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
    """Stateful ReAct agent. One instance per contest session.

    Conversation history is reset at the beginning of each testcase (call reset()).
    """

    def __init__(self, llm: LLMClient,
                 backend: EDABackend,
                 verbose: bool = False) -> None:
        self.llm     = llm
        self.backend = backend
        self.verbose = verbose
        self.history: list[dict] = []
        self._turn_count: int = 0  # tracks how many user turns in this testcase

    # ── public interface ──────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear conversation history (call at the start of each testcase)."""
        self.history = []
        self._turn_count = 0

    def run(self, user_request: str) -> str:
        """Process one natural-language request and return the final text answer."""
        self._turn_count += 1
        user_msg = {"role": "user", "content": user_request}
        self.history.append(user_msg)
        self._trim_history()
        tools = get_tools_for_request(user_request, self.llm.provider)

        try:
            text, tool_calls = self._chat_with_retries(tools)
        except Exception as e:
            user_msg["content"] = _compact_user_request(user_request)
            reply = _standardize_response(f"LLM request failed: {e}")
            self._append_history_safe({"role": "assistant", "content": reply})
            return reply

        user_msg["content"] = _compact_user_request(user_request)

        if not tool_calls:
            reply = _standardize_response(text or "(No response)")
            self._append_history_safe(
                {"role": "assistant", "content": _compact_for_history(reply)})
            return reply

        # Execute tool calls and keep only the final compact answer in future
        # history. There is no second model round, so replaying tool_call/tool
        # messages in later requests only spends prompt tokens.
        results: list[str] = []
        for tc in tool_calls:
            if self.verbose:
                print(f"[TOOL] {tc['name']}({tc['arguments']})", file=sys.stderr)

            result = _dispatch(self.backend, tc["name"], tc["arguments"])
            results.append(result)

            if self.verbose:
                print(f"[RESULT] {result[:200]}", file=sys.stderr)

        reply = _standardize_response("\n\n".join(results))
        self._append_history_safe(
            {"role": "assistant", "content": _compact_tool_reply_for_history(reply, tool_calls)})
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
        """Append a message, then trim history at user-message boundaries."""
        self.history.append(msg)
        self._trim_history()

    def _trim_history(self) -> None:
        """Sliding-window: keep the last N/2 complete user-turn groups.

        A "user turn" starts at a message with role='user' and includes all
        subsequent assistant/tool messages until the next user message.
        This preserves tool_call/tool_result pairing.
        """
        if len(self.history) <= MAX_HISTORY_MESSAGES:
            return

        # Walk backward to find the start of the Nth-last user turn
        max_turns = MAX_HISTORY_MESSAGES // 2
        user_indices = [
            i for i, m in enumerate(self.history)
            if m.get("role") == "user"
        ]
        if len(user_indices) <= max_turns:
            return

        keep_from = user_indices[-max_turns]
        self.history = self.history[keep_from:]


def _compact_for_history(text: str, tool_name: str = "") -> str:
    text = text or ""
    limit = _limit_for_tool(tool_name) if tool_name else HISTORY_CONTENT_LIMIT
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def _compact_tool_reply_for_history(text: str, tool_calls: list[dict]) -> str:
    text = text or ""
    if not tool_calls:
        return _compact_for_history(text)
    limit = max(_limit_for_tool(str(tc.get("name", ""))) for tc in tool_calls)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def _compact_user_request(text: str) -> str:
    compact = " ".join((text or "").split())
    for pattern in _REQUEST_BOILERPLATE_PATTERNS:
        compact = pattern.sub("", compact)
    compact = re.sub(r"\s+", " ", compact).strip()
    if len(compact) <= USER_REQUEST_HISTORY_LIMIT:
        return compact
    return compact[:USER_REQUEST_HISTORY_LIMIT] + "..."
