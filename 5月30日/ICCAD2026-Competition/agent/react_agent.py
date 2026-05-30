"""
agent/react_agent.py
====================
ReAct-style LLM agent that drives the EDA backend via tool calls.

ReAct loop (per request)
-------------------------
  1. Send user request + conversation history to LLM.
  2. If model returns tool_calls 鈫?execute each tool 鈫?append results 鈫?go to 1.
  3. If model returns a text reply 鈫?return it as the final answer.
  4. Hard limit of MAX_ROUNDS per request to prevent runaway loops.

Tool dispatch
-------------
  Tool names map directly to EDABackend methods via TOOL_DISPATCH.
  Arguments are passed as **kwargs from the model's JSON output.

Conversation history
--------------------
  A rolling list of {role, content} dicts is maintained per testcase.
  The history is intentionally kept flat (no summarisation) because testcases
  are bounded in length by the contest spec.  If future cases are very long,
  add a sliding-window trim here.
"""

from __future__ import annotations

import sys
import traceback
import time
from typing import Any, Optional

from .llm_client import LLMClient
from .rule_router import route_request
from .tool_schema import SYSTEM_PROMPT, get_tools_for_request, _is_transform_request
from eda.backend import EDABackend

LLM_RETRIES = 3
HISTORY_CONTENT_LIMIT = 1200

# Per-tool-category truncation limits for history (chars)
_TOOL_CATEGORY_LIMITS: dict[str, int] = {
    # Verification — only need pass/fail
    "check_equiv": 400, "check_original_equiv": 400, "verify_assertion": 600, "check_signal_symmetry": 400,
    "is_cut_between_pi_po": 400, "is_signal_constant": 400,
    "same_clock_domain": 400, "internal_signals_equiv": 400,
    "direct_pi_po_connections": 400,
    # Queries — short factual answers
    "gate_info": 600, "get_fanout": 600, "list_direct_loads": 600,
    "design_summary": 600, "gate_count_breakdown": 600, "count_gate_type": 600,
    "primary_io_counts": 600, "last_operation_count": 600,
    "list_primary_inputs_with_widths": 600, "list_primary_outputs_with_widths": 600,
    "list_flipflops_by_clock": 600, "highest_fanout_input": 600, "max_fanout": 600,
    "immediate_successors": 600, "list_gates_by_type": 600,
    "report_constant_input_gates": 600, "report_floating_signals": 600,
    "report_dff_enable_hold": 600,
    "max_fanin_depth": 600, "max_design_depth": 600,
    "deepest_output_cone": 600, "largest_output_cone": 600,
    "count_outputs_depth_gt": 600, "max_pi_to_dff_depth": 600,
    "boolean_expression": 600, "find_nand_pair_for_signal": 600,
    "rename_gate": 600, "rename_wire": 600,
    "read_design": 600, "write_design": 600,
    "try_reconnect_input_pin": 600,
    # Paths — may need longer traces
    "find_path": 800, "list_paths": 800, "list_register_to_register_paths": 800,
    "all_paths_through": 800, "articulation_points_between": 800,
    # Cones — medium detail
    "report_cone_size": 800, "cone_gate_breakdown": 800,
    "transitive_fanin": 800, "transitive_fanout": 800,
    "shared_fanin_cones": 800, "report_large_cones": 800,
    # Transforms — keep full detail
    "insert_gate_before": 1200, "buffer_high_fanout": 1200,
    "buffer_all_high_fanout": 1200, "buffer_each_load": 1200,
    "add_balance_buffers": 1200, "replace_in_cone": 1200, "replace_globally": 1200,
    "replace_or_with_nand_not": 1200, "replace_xnor_with_nor": 1200,
    "replace_xor_with_nand": 1200, "remap_design": 1200,
    "remove_dangling": 1200, "fuse_not_buf": 1200, "collapse_not_not": 1200,
    "simplify_constant_gates": 1200, "structural_duplicate_merge": 1200,
    "optimize_cone": 1200, "optimize_design_depth": 1200,
}

_TOOL_LIMIT_DEFAULT = 800


def _limit_for_tool(tool_name: str) -> int:
    return _TOOL_CATEGORY_LIMITS.get(tool_name, _TOOL_LIMIT_DEFAULT)


# 鈹€鈹€ tool dispatcher 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def _dispatch(backend: EDABackend, tool_name: str,
              arguments: dict) -> str:
    """
    Map a tool name + arguments dict to the corresponding EDABackend method.
    Uses the module-level _DISPATCH_MAP built once at import time.
    """
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
    if stripped.startswith(("OK:", "FAIL[", "UNKNOWN[")):
        return stripped
    failure_markers = (
        "Error:",
        "Tool error",
        "Unexpected error",
        "LLM request failed",
        "Configuration error",
        "Equivalence check error",
    )
    if stripped.startswith(failure_markers):
        return f"FAIL[RUNTIME]: {stripped}"
    if "not supported" in stripped.lower() or "unsupported" in stripped.lower():
        return f"FAIL[UNSUPPORTED]: {stripped}"
    return f"OK: {stripped}"


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲

class ReactAgent:
    """
    Stateful ReAct agent.  One instance is created at contest startup and
    reused across all requests in the session.  Conversation history is
    reset at the beginning of each testcase (call reset()).

    Parameters
    ----------
    llm     : LLMClient   鈥?pre-configured LLM client
    backend : EDABackend  鈥?pre-configured EDA backend
    verbose : bool        鈥?if True, print tool calls to stderr for debugging
    """

    def __init__(self, llm: LLMClient,
                 backend: EDABackend,
                 verbose: bool = False) -> None:
        self.llm     = llm
        self.backend = backend
        self.verbose = verbose
        self.enable_rule_router = True
        self.history: list[dict] = []   # conversation history for this testcase

    # 鈹€鈹€ public interface 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def reset(self) -> None:
        """Clear conversation history (call at the start of each testcase)."""
        self.history = []

    def run(self, user_request: str) -> str:
        """
        Process one natural-language request and return the final text answer.

        The method mutates self.history to include the exchange so that
        subsequent calls have full context.
        """
        if self.enable_rule_router:
            routed = route_request(self.backend, user_request)
            if routed is not None:
                return _standardize_response(routed)

        self.history.append({"role": "user", "content": user_request})
        tools = get_tools_for_request(user_request, self.llm.provider)
        is_transform = _is_transform_request(user_request)

        try:
            text, tool_calls = self._chat_with_retries(tools)
        except Exception as e:
            reply = _standardize_response(f"LLM request failed: {e}")
            self.history.append({"role": "assistant", "content": reply})
            return reply

        if not tool_calls:
            reply = _standardize_response(text or "(No response from model)")
            self.history.append({"role": "assistant", "content": _compact_for_history(reply)})
            return reply

        # Record tool-call in history (transform only)
        if is_transform:
            self.history.append(
                self.llm.make_assistant_tool_call_message(text, tool_calls)
            )

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
                self.history.append(tool_msg)

        reply = _standardize_response("\n\n".join(results))
        self.history.append({"role": "assistant", "content": _compact_for_history(reply)})
        return reply

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


_DISPATCH_MAP: dict[str, tuple[str, bool]] = {
    "read_design":                ("read_design",                True),
    "write_design":               ("write_design",               True),
    "design_summary":             ("design_summary",             False),
    "gate_count_breakdown":       ("gate_count_breakdown",       False),
    "count_gate_type":            ("count_gate_type",            True),
    "last_operation_count":       ("last_operation_count",       True),
    "primary_io_counts":          ("primary_io_counts",          False),
    "list_primary_inputs_with_widths":  ("list_primary_inputs_with_widths",  False),
    "list_primary_outputs_with_widths": ("list_primary_outputs_with_widths", False),
    "get_max_depth":              ("get_max_depth",              True),
    "max_fanin_depth":            ("max_fanin_depth",            True),
    "max_design_depth":           ("max_design_depth",           False),
    "optimize_design_depth":      ("optimize_design_depth",      False),
    "deepest_output_cone":        ("deepest_output_cone",        False),
    "largest_output_cone":        ("largest_output_cone",        False),
    "count_outputs_depth_gt":     ("count_outputs_depth_gt",     True),
    "max_pi_to_dff_depth":        ("max_pi_to_dff_depth",        False),
    "find_path":                  ("find_path",                  True),
    "list_paths":                 ("list_paths",                 True),
    "list_register_to_register_paths": ("list_register_to_register_paths", True),
    "all_paths_through":          ("all_paths_through",          True),
    "report_cone_size":           ("report_cone_size",           True),
    "cone_gate_breakdown":        ("cone_gate_breakdown",        True),
    "transitive_fanin":           ("transitive_fanin",           True),
    "transitive_fanout":          ("transitive_fanout",          True),
    "get_fanout":                 ("get_fanout",                 True),
    "list_direct_loads":          ("list_direct_loads",          True),
    "gate_info":                  ("gate_info",                  True),
    "list_gates_by_type":         ("list_gates_by_type",         True),
    "report_constant_input_gates":("report_constant_input_gates",True),
    "immediate_successors":       ("immediate_successors",       True),
    "report_large_cones":         ("report_large_cones",         True),
    "same_clock_domain":          ("same_clock_domain",          True),
    "shared_fanin_cones":         ("shared_fanin_cones",         True),
    "direct_pi_po_connections":   ("direct_pi_po_connections",   False),
    "is_signal_constant":         ("is_signal_constant",         True),
    "is_cut_between_pi_po":       ("is_cut_between_pi_po",       True),
    "internal_signals_equiv":     ("internal_signals_equiv",     True),
    "find_nand_pair_for_signal":  ("find_nand_pair_for_signal",  True),
    "articulation_points_between":("articulation_points_between",True),
    "boolean_expression":         ("boolean_expression",         True),
    "rename":                     ("rename",                     True),
    "rename_gate":                ("rename_gate",                True),
    "rename_wire":                ("rename_wire",                True),
    "list_flipflops_by_clock":    ("list_flipflops_by_clock",    True),
    "highest_fanout_input":       ("highest_fanout_input",       False),
    "max_fanout":                 ("max_fanout",                 True),
    "structural_duplicate_merge": ("structural_duplicate_merge", False),
    "insert_gate_before":         ("insert_gate_before",         True),
    "buffer_high_fanout":         ("buffer_high_fanout",         True),
    "buffer_all_high_fanout":     ("buffer_all_high_fanout",     True),
    "buffer_each_load":           ("buffer_each_load",           True),
    "replace_in_cone":            ("replace_gate_type_in_cone",  True),
    "replace_globally":           ("replace_gate_type_globally", True),
    "replace_or_with_nand_not":   ("replace_or_with_nand_not",   True),
    "replace_xnor_with_nor":      ("replace_xnor_with_nor",      True),
    "remap_design":               ("remap_design",               True),
    "remove_dangling":            ("remove_dangling",            False),
    "fuse_not_buf":               ("fuse_not_buf_pairs",         False),
    "collapse_not_not":           ("collapse_not_not_pairs",     False),
    "simplify_constant_gates":    ("simplify_constant_gates",    False),
    "replace_xor_with_nand":      ("replace_xor_with_nand",      False),
    "add_balance_buffers":        ("add_balance_buffers",        True),
    "optimize_cone":              ("optimize_cone",              True),
    "check_equiv":                ("check_equiv",                True),
    "check_original_equiv":       ("check_original_equiv",       False),
    "check_signal_symmetry":      ("check_signal_symmetry",      True),
    "verify_assertion":           ("verify_assertion",           True),
    "report_floating_signals":    ("report_floating_signals",    True),
    "report_dff_enable_hold":     ("report_dff_enable_hold",     True),
    "try_reconnect_input_pin":    ("try_reconnect_input_pin",    True),
}


def _compact_for_history(text: str, tool_name: str = "") -> str:
    text = text or ""
    limit = _limit_for_tool(tool_name) if tool_name else HISTORY_CONTENT_LIMIT
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."
