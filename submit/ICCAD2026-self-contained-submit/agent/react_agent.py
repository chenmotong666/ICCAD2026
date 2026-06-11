"""
agent/react_agent.py
====================
ReAct-style LLM agent that drives the EDA backend via tool calls.

ReAct loop (per request)
-------------------------
  1. Send user request + conversation history to LLM.
  2. If model returns tool_calls ->execute each tool ->append results ->go to 1.
  3. If model returns a text reply ->return it as the final answer.
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
import os
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


LLM_RETRIES = 2
HISTORY_CONTENT_LIMIT = 140
USER_REQUEST_HISTORY_LIMIT = 160
LLM_REQUEST_CONTENT_LIMIT = 560
STATE_CONTENT_LIMIT = 100
MAX_HISTORY_MESSAGES = 6  # sliding-window cap

# Built once at import time
_DISPATCH_MAP: dict[str, tuple[str, bool]] = get_dispatch_map_for_backend_tools()
_TOOL_CATEGORY_LIMITS: dict[str, int]      = get_category_limits()
_TOOL_LIMIT_DEFAULT = 400

_STATE_CHANGING_TOOLS: frozenset[str] = frozenset({
    "read_design",
    "rename",
    "structural_duplicate_merge",
    "insert_gate_before",
    "buffer_high_fanout",
    "buffer_all_high_fanout",
    "buffer_each_load",
    "replace_in_cone",
    "replace_globally",
    "replace_or_with_nand_not",
    "replace_xnor_with_nor",
    "remap_design",
    "remove_dangling",
    "fuse_not_buf",
    "collapse_not_not",
    "simplify_constant_gates",
    "replace_xor_with_nand",
    "add_balance_buffers",
    "try_reconnect_input_pin",
    "optimize_design_depth",
    "optimize_cone",
})

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
    re.compile(r"\bensure (?:the )?(?:design )?function(?:ality)? remains unchanged\.?", re.I),
    re.compile(r"\bpreserve (?:the )?(?:design )?functionality\.?", re.I),
    re.compile(r"\bwhile preserving functional equivalence\.?", re.I),
    re.compile(r"\bwhile preserving (?:the )?(?:design )?function(?:ality)?\.?", re.I),
    re.compile(r"\bwithout changing (?:the )?(?:design )?functionality\.?", re.I),
    re.compile(r"\bwithout altering (?:the )?(?:design )?(?:behavior|behaviour|functionality)\.?", re.I),
    re.compile(r"\bwithout changing (?:its|the )?(?:behavior|behaviour)\.?", re.I),
    re.compile(r"\bmake sure nothing changes functionally\.?", re.I),
    re.compile(r"\bmake sure (?:the )?(?:design )?function(?:ality)? is preserved\.?", re.I),
    re.compile(r"\bmake sure (?:the )?(?:current )?design remains functionally equivalent\.?", re.I),
    re.compile(r"\bwithout changing (?:the )?(?:design )?function\.?", re.I),
    re.compile(r"\bno functional change\.?", re.I),
)


def _limit_for_tool(tool_name: str) -> int:
    return _TOOL_CATEGORY_LIMITS.get(tool_name, _TOOL_LIMIT_DEFAULT)


def _canonical_tool_name(tool_name: str) -> str:
    """Accept exact tool names and common PascalCase/camelCase gateway variants."""
    raw = str(tool_name or "").strip()
    if raw in _DISPATCH_MAP:
        return raw
    name = re.sub(r"[^0-9A-Za-z]+", "_", raw)
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    name = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    name = name.strip("_").lower()
    return name if name in _DISPATCH_MAP else raw



def _dispatch(backend: EDABackend, tool_name: str,
              arguments: dict) -> str:
    """Map a tool name + arguments dict to the corresponding EDABackend method."""
    try:
        canonical_name = _canonical_tool_name(tool_name)
        entry = _DISPATCH_MAP.get(canonical_name)
        if entry is None:
            return f"Unknown tool: '{tool_name}'"
        method_name, takes_kwargs = entry
        fn = getattr(backend, method_name)
        return fn(**arguments) if takes_kwargs else fn()
    except RuntimeError as e:
        return f"Tool error ({tool_name}): {e}"
    except TypeError as e:
        return f"ToolArgErr {tool_name}: {e}"
    except Exception:
        return f"ToolErr {tool_name}: unexpected failure"


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
        self._state_summary: str = ""
        self._last_action_summary: str = ""


    def reset(self) -> None:
        """Clear conversation history (call at the start of each testcase)."""
        self.history = []
        self._turn_count = 0
        self._state_summary = ""
        self._last_action_summary = ""

    def run(self, user_request: str) -> str:
        """Process one natural-language request and return the final text answer."""
        self._turn_count += 1
        llm_request = _compact_user_request(
            user_request, limit=LLM_REQUEST_CONTENT_LIMIT)
        user_msg = {"role": "user", "content": llm_request}
        self.history.append(user_msg)
        self._trim_history()
        tools = get_tools_for_request(user_request, self.llm.provider)
        tools = self._filter_loaded_state_tools(tools)

        try:
            text, tool_calls = self._chat_with_retries(tools)
        except Exception as e:
            user_msg["content"] = _compact_user_request(user_request)
            reply = _standardize_response(f"LLM request failed: {e}")
            self._append_history_safe({"role": "assistant", "content": reply})
            return reply

        if self.verbose:
            usage_fn = getattr(self.llm, "last_usage_summary", None)
            usage = usage_fn() if callable(usage_fn) else {}
            tool_names = [
                str(t.get("function", {}).get("name", t.get("name", "")))
                for t in tools
            ]
            print(
                " ".join((
                    f"[LLM] turn={self._turn_count}",
                    f"prompt={usage.get('prompt_tokens', 0)}",
                    f"completion={usage.get('completion_tokens', 0)}",
                    f"total={usage.get('total_tokens', 0)}",
                    f"tools={len(tool_names)}",
                    f"calls={len(tool_calls)}",
                    f"text_len={len(text or '')}",
                    f"tool_names={','.join(tool_names)}",
                )),
                file=sys.stderr,
            )

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
            raw_tool_name = str(tc.get("name", ""))
            tool_name = _canonical_tool_name(raw_tool_name)
            arguments = tc["arguments"]
            if tool_name not in _DISPATCH_MAP:
                inferred = _infer_tool_call_from_request(user_request, raw_tool_name, arguments)
                if inferred is not None:
                    tool_name, arguments = inferred
            if self.verbose:
                print(f"[TOOL] {raw_tool_name}({arguments})", file=sys.stderr)

            result = _dispatch(self.backend, tool_name, arguments)
            results.append(result)
            self._update_state_summary(tool_name, result)

            if self.verbose:
                print(f"[RESULT] {result[:200]}", file=sys.stderr)

        reply = _standardize_response("\n\n".join(results))
        self._append_history_safe(
            {"role": "assistant", "content": _compact_tool_reply_for_history(reply, tool_calls)})
        return reply


    def _chat_with_retries(self, tools: list[dict]) -> tuple[Optional[str], list[dict]]:
        last_error: Optional[Exception] = None
        for attempt in range(LLM_RETRIES):
            try:
                return self.llm.chat(
                    messages=self._messages_for_llm(),
                    tools=tools,
                    system=SYSTEM_PROMPT,
                )
            except Exception as e:
                last_error = e
                if attempt + 1 < LLM_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _filter_loaded_state_tools(self, tools: list[dict]) -> list[dict]:
        """Drop read_design after a design is already loaded."""
        if not self._state_summary:
            return tools
        filtered = [
            tool for tool in tools
            if _tool_name(tool) != "read_design"
        ]
        return filtered or tools

    def _append_history_safe(self, msg: dict) -> None:
        """Append a message, then trim history at user-message boundaries."""
        self.history.append(msg)
        self._trim_history()

    def _messages_for_llm(self) -> list[dict]:
        state_context = self._state_context()
        if not state_context:
            return self.history
        return [
            {"role": "assistant", "content": state_context},
            *self.history,
        ]

    def _state_context(self) -> str:
        if self._last_action_summary:
            return f"Last: {self._last_action_summary}"
        return ""

    def _update_state_summary(self, tool_name: str, result: str) -> None:
        compact = _compact_inline(result, STATE_CONTENT_LIMIT)
        if tool_name == "read_design" and compact.startswith("Loaded"):
            self._state_summary = compact
            self._last_action_summary = ""
            return
        if tool_name in _STATE_CHANGING_TOOLS and not _looks_like_tool_failure(compact):
            self._last_action_summary = compact

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
    sections = _split_tool_result_sections(text, len(tool_calls))
    summaries = [
        _summarize_tool_result(str(tc.get("name", "")), section)
        for tc, section in zip(tool_calls, sections)
    ]
    summary = " | ".join(s for s in summaries if s)
    if summary:
        text = summary
    limit = min(
        HISTORY_CONTENT_LIMIT,
        max(_limit_for_tool(str(tc.get("name", ""))) for tc in tool_calls),
    )
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def _tool_name(tool: dict) -> str:
    fn = tool.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name", ""))
    return str(tool.get("name", ""))


def _split_tool_result_sections(text: str, count: int) -> list[str]:
    if count <= 1:
        return [text]
    sections = re.split(r"\n\s*\n", text or "", maxsplit=count - 1)
    if len(sections) < count:
        sections.extend([""] * (count - len(sections)))
    return sections[:count]


def _summarize_tool_result(tool_name: str, text: str) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return ""
    first = compact.split(" | ")[0]
    line = (text or "").strip().splitlines()[0].strip() if text else compact

    if tool_name == "read_design":
        return _compact_inline(line, 80)
    if tool_name == "write_design":
        return _compact_inline(line, 60)
    if tool_name in _STATE_CHANGING_TOOLS:
        return _compact_inline(line, 100)
    if tool_name in {
        "find_path",
        "list_paths",
        "list_register_to_register_paths",
        "all_paths_through",
        "transitive_fanin",
        "transitive_fanout",
        "list_direct_loads",
        "list_gates_by_type",
        "list_flipflops_by_clock",
        "report_dff_enable_hold",
        "report_floating_signals",
        "articulation_points_between",
        "boolean_expression",
        "internal_signals_equiv",
        "check_equiv",
        "check_original_equiv",
        "verify_assertion",
    }:
        return _compact_inline(line, 90)
    return _compact_inline(first, 110)


def _compact_inline(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _looks_like_tool_failure(text: str) -> bool:
    return text.startswith((
        "NotFound:",
        "ERR[",
        "ToolArgErr",
        "ToolErr",
        "Tool error",
        "Error ",
        "Equivalence check error",
    ))


def _infer_tool_call_from_request(
    user_request: str,
    raw_tool_name: str,
    arguments: dict,
) -> Optional[tuple[str, dict]]:
    """Recover from model-emitted generic tool names such as Read, Bash, or Glob."""
    low_name = str(raw_tool_name or "").strip().lower()
    if low_name not in {"read", "bash", "shell", "glob"}:
        return None

    text = user_request or ""
    low = text.lower()
    if "load the design" in low or "read the design" in low:
        path = _extract_design_path(text)
        if path:
            return "read_design", {"path": path}
    if any(word in low for word in ("write", "save", "export", "emit")) and ".v" in low:
        path = _extract_output_path(text)
        if path:
            return "write_design", {"path": path}
    if "count all the gates" in low or "gate count" in low:
        return "gate_count_breakdown", {}
    m = re.search(
        r"path\s+from\s+(?:primary\s+)?(?:input\s+)?([A-Za-z0-9_.$\[\]\\]+)\s+"
        r"to\s+(?:primary\s+)?(?:output\s+)?([A-Za-z0-9_.$\[\]\\]+)\s+"
        r"pass(?:es)?\s+through\s+(?:gate\s+|node\s+)?([A-Za-z0-9_.$\[\]\\]+)",
        text,
        re.I,
    )
    if m:
        return "all_paths_through", {
            "from_signal": _clean_signal(m.group(1)),
            "to_signal": _clean_signal(m.group(2)),
            "through": _clean_signal(m.group(3)),
        }
    m = re.search(
        r"between\s+internal\s+signals?\s+([A-Za-z0-9_.$\[\]\\]+)\s+and\s+([A-Za-z0-9_.$\[\]\\]+)",
        text,
        re.I,
    )
    if m:
        return "internal_signals_equiv", {
            "signal_a": _clean_signal(m.group(1)),
            "signal_b": _clean_signal(m.group(2)),
        }
    return None


def _extract_design_path(text: str) -> str:
    file_match = re.search(r"\bfile\s+([^\s'\"`]+\.v)", text, re.I)
    path = file_match.group(1) if file_match else ""
    dir_match = re.search(r"\bdirectory\s+([^\s'\"`]+)", text, re.I)
    if path and dir_match and not any(sep in path for sep in ("/", "\\")):
        directory = dir_match.group(1).rstrip("/\\.")
        path = os.path.join(directory, path)
    if not path:
        m = re.search(r"([^\s'\"`]+\.v)", text, re.I)
        path = m.group(1) if m else ""
    return path.rstrip(".")


def _extract_output_path(text: str) -> str:
    m = re.search(r"\boutput\s+file\s+([^\s'\"`]+\.v)", text, re.I)
    if not m:
        m = re.search(r"([^\s'\"`]+\.v)", text, re.I)
    return m.group(1).rstrip(".") if m else ""


def _clean_signal(value: str) -> str:
    return str(value or "").strip().strip("'\"`").rstrip("?.,;:")


def _compact_user_request(text: str, limit: int = USER_REQUEST_HISTORY_LIMIT) -> str:
    compact = " ".join((text or "").split())
    for pattern in _REQUEST_BOILERPLATE_PATTERNS:
        compact = pattern.sub("", compact)
    compact = re.sub(r"\s+", " ", compact).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."
