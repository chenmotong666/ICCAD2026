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

import traceback
import time
from typing import Any, Optional

from .llm_client import LLMClient
from .rule_router import route_request
from .tool_schema import SYSTEM_PROMPT, get_tools_for_provider, get_tools_for_request, _is_transform_request
from eda.backend import EDABackend

MAX_ROUNDS = 3   # max tool-call iterations per single user request
LLM_RETRIES = 3
HISTORY_CONTENT_LIMIT = 1200

# Per-tool-category truncation limits for history (chars)
_TOOL_CATEGORY_LIMITS: dict[str, int] = {
    # Verification — only need pass/fail
    "check_equiv": 400, "check_original_equiv": 400, "check_signal_symmetry": 400,
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
    Returns the result as a string.  Catches all exceptions so the agent
    can report failures gracefully rather than crashing.
    """
    try:
        dispatch_table: dict[str, Any] = {
            "read_design":         lambda: backend.read_design(**arguments),
            "write_design":        lambda: backend.write_design(**arguments),
            "design_summary":      lambda: backend.design_summary(),
            "gate_count_breakdown": lambda: backend.gate_count_breakdown(),
            "count_gate_type":     lambda: backend.count_gate_type(**arguments),
            "last_operation_count": lambda: backend.last_operation_count(**arguments),
            "primary_io_counts":   lambda: backend.primary_io_counts(),
            "list_primary_inputs_with_widths": lambda: backend.list_primary_inputs_with_widths(),
            "list_primary_outputs_with_widths": lambda: backend.list_primary_outputs_with_widths(),
            "get_max_depth":       lambda: backend.get_max_depth(**arguments),
            "max_fanin_depth":     lambda: backend.max_fanin_depth(**arguments),
            "max_design_depth":    lambda: backend.max_design_depth(),
            "optimize_design_depth": lambda: backend.optimize_design_depth(),
            "deepest_output_cone": lambda: backend.deepest_output_cone(),
            "largest_output_cone": lambda: backend.largest_output_cone(),
            "count_outputs_depth_gt": lambda: backend.count_outputs_depth_gt(**arguments),
            "max_pi_to_dff_depth": lambda: backend.max_pi_to_dff_depth(),
            "find_path":           lambda: backend.find_path(**arguments),
            "list_paths":          lambda: backend.list_paths(**arguments),
            "list_register_to_register_paths": lambda: backend.list_register_to_register_paths(**arguments),
            "all_paths_through":   lambda: backend.all_paths_through(**arguments),
            "report_cone_size":    lambda: backend.report_cone_size(**arguments),
            "cone_gate_breakdown": lambda: backend.cone_gate_breakdown(**arguments),
            "transitive_fanin":    lambda: backend.transitive_fanin(**arguments),
            "transitive_fanout":   lambda: backend.transitive_fanout(**arguments),
            "get_fanout":          lambda: backend.get_fanout(**arguments),
            "list_direct_loads":   lambda: backend.list_direct_loads(**arguments),
            "gate_info":           lambda: backend.gate_info(**arguments),
            "list_gates_by_type":  lambda: backend.list_gates_by_type(**arguments),
            "report_constant_input_gates": lambda: backend.report_constant_input_gates(**arguments),
            "immediate_successors": lambda: backend.immediate_successors(**arguments),
            "report_large_cones":  lambda: backend.report_large_cones(**arguments),
            "same_clock_domain":   lambda: backend.same_clock_domain(**arguments),
            "shared_fanin_cones":  lambda: backend.shared_fanin_cones(**arguments),
            "direct_pi_po_connections": lambda: backend.direct_pi_po_connections(),
            "is_signal_constant": lambda: backend.is_signal_constant(**arguments),
            "is_cut_between_pi_po": lambda: backend.is_cut_between_pi_po(**arguments),
            "internal_signals_equiv": lambda: backend.internal_signals_equiv(**arguments),
            "find_nand_pair_for_signal": lambda: backend.find_nand_pair_for_signal(**arguments),
            "articulation_points_between": lambda: backend.articulation_points_between(**arguments),
            "boolean_expression": lambda: backend.boolean_expression(**arguments),
            "rename_gate":         lambda: backend.rename_gate(**arguments),
            "rename_wire":         lambda: backend.rename_wire(**arguments),
            "list_flipflops_by_clock": lambda: backend.list_flipflops_by_clock(**arguments),
            "highest_fanout_input": lambda: backend.highest_fanout_input(),
            "max_fanout":          lambda: backend.max_fanout(**arguments),
            "structural_duplicate_merge": lambda: backend.structural_duplicate_merge(),
            "insert_gate_before":  lambda: backend.insert_gate_before(**arguments),
            "buffer_high_fanout":  lambda: backend.buffer_high_fanout(**arguments),
            "buffer_all_high_fanout": lambda: backend.buffer_all_high_fanout(**arguments),
            "buffer_each_load":    lambda: backend.buffer_each_load(**arguments),
            "replace_in_cone":     lambda: backend.replace_gate_type_in_cone(**arguments),
            "replace_globally":    lambda: backend.replace_gate_type_globally(**arguments),
            "replace_or_with_nand_not": lambda: backend.replace_or_with_nand_not(**arguments),
            "replace_xnor_with_nor": lambda: backend.replace_xnor_with_nor(**arguments),
            "remap_design":        lambda: backend.remap_design(**arguments),
            "remove_dangling":     lambda: backend.remove_dangling(),
            "fuse_not_buf":        lambda: backend.fuse_not_buf_pairs(),
            "collapse_not_not":    lambda: backend.collapse_not_not_pairs(),
            "simplify_constant_gates": lambda: backend.simplify_constant_gates(),
            "replace_xor_with_nand": lambda: backend.replace_xor_with_nand(),
            "add_balance_buffers": lambda: backend.add_balance_buffers(**arguments),
            "optimize_cone":       lambda: backend.optimize_cone(**arguments),
            "check_equiv":         lambda: backend.check_equiv(**arguments),
            "check_original_equiv": lambda: backend.check_original_equiv(),
            "check_signal_symmetry": lambda: backend.check_signal_symmetry(**arguments),
            "report_floating_signals": lambda: backend.report_floating_signals(**arguments),
            "report_dff_enable_hold": lambda: backend.report_dff_enable_hold(**arguments),
            "try_reconnect_input_pin": lambda: backend.try_reconnect_input_pin(**arguments),
        }
        fn = dispatch_table.get(tool_name)
        if fn is None:
            return f"Unknown tool: '{tool_name}'"
        return fn()
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
        self.tools   = get_tools_for_provider(llm.provider)
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
        last_tool_result: Optional[str] = None
        text: Optional[str] = None
        tools = get_tools_for_request(user_request, self.llm.provider)
        is_transform = _is_transform_request(user_request)

        for round_idx in range(MAX_ROUNDS):
            try:
                text, tool_calls = self._chat_with_retries(tools)
            except Exception as e:
                reply = _standardize_response(last_tool_result or f"LLM request failed: {e}")
                self.history.append({"role": "assistant", "content": reply})
                return reply

            if not tool_calls:
                # Final text answer
                reply = _standardize_response(text or last_tool_result or "(No response from model)")
                self.history.append({"role": "assistant", "content": _compact_for_history(reply)})
                return reply

            # Record assistant's tool-call message in history (transform only)
            if is_transform:
                self.history.append(
                    self.llm.make_assistant_tool_call_message(text, tool_calls)
                )

            # Execute all tool calls and collect results
            results: list[str] = []
            for tc in tool_calls:
                if self.verbose:
                    import sys
                    print(f"[TOOL] {tc['name']}({tc['arguments']})", file=sys.stderr)

                result = _dispatch(self.backend, tc["name"], tc["arguments"])
                last_tool_result = result
                results.append(result)

                if self.verbose:
                    import sys
                    print(f"[RESULT] {result[:200]}", file=sys.stderr)

                if is_transform:
                    tool_msg = self.llm.make_tool_result_message(
                        tc["id"],
                        _compact_for_history(result, tc["name"]),
                    )
                    self.history.append(tool_msg)

            if results:
                reply = _standardize_response("\n\n".join(results))
                self.history.append({"role": "assistant", "content": _compact_for_history(reply)})
                return reply

        # Exceeded MAX_ROUNDS 鈥?return whatever the last text was
        return _standardize_response(text or last_tool_result or "Agent stopped without a final answer.")

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


def _compact_for_history(text: str, tool_name: str = "") -> str:
    text = text or ""
    limit = _limit_for_tool(tool_name) if tool_name else HISTORY_CONTENT_LIMIT
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."
