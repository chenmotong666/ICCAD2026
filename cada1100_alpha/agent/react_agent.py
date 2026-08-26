"""
agent/react_agent.py
====================
ReAct-style LLM agent that drives the EDA backend via tool calls.

Request routing (per request)
-----------------------------
  1. Try deterministic rule-based tool routing for contest prompts.
  2. If no rule applies, make one LLM tool-selection pass.
  3. Execute the selected tool calls and return the combined result.
  4. MAX_ROUNDS is retained as a compatibility guard, but the current
     implementation is intentionally single-pass.

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
from dataclasses import dataclass
from typing import Any, Optional

from .llm_client import LLMClient
from .tool_schema import (
    SYSTEM_PROMPT,
    get_tools_for_request,
    get_dispatch_map_for_backend_tools,
    get_category_limits,
)
from eda.backend import EDABackend


LLM_RETRIES = 3
HISTORY_CONTENT_LIMIT = 140
USER_REQUEST_HISTORY_LIMIT = 160
LLM_REQUEST_CONTENT_LIMIT = 560
STATE_CONTENT_LIMIT = 100
MAX_HISTORY_MESSAGES = 6  # sliding-window cap

# Built once at import time
_DISPATCH_MAP: dict[str, tuple[str, bool]] = get_dispatch_map_for_backend_tools()
_TOOL_CATEGORY_LIMITS: dict[str, int]      = get_category_limits()
_TOOL_LIMIT_DEFAULT = 400
_RULE_CONFIDENCE_THRESHOLD = 0.82
_MIN_REMAINING_TOOL_SEC = 1.0
_SIGNAL_ARGUMENT_KEYS: frozenset[str] = frozenset({
    "name",
    "net_name",
    "output_signal",
    "input_signal",
    "from_signal",
    "to_signal",
    "signal_name",
    "wire_name",
    "clock_name",
    "old_name",
    "new_name",
    "source",
    "target",
    "through",
    "avoid",
    "must_pass",
    "signal_a",
    "signal_b",
    "output_a",
    "output_b",
    "ff1_name",
    "ff2_name",
    "input_a",
    "input_b",
})
_SUSPICIOUS_SIGNAL_WORDS: frozenset[str] = frozenset({
    "a",
    "an",
    "and",
    "any",
    "at",
    "by",
    "cone",
    "current",
    "design",
    "does",
    "from",
    "gate",
    "gates",
    "input",
    "internal",
    "is",
    "logic",
    "net",
    "node",
    "of",
    "or",
    "output",
    "path",
    "primary",
    "signal",
    "the",
    "to",
    "type",
    "what",
    "where",
    "whether",
    "wire",
})
_TRANSFORM_TOOLS: frozenset[str] = frozenset({
    "structural_duplicate_merge",
    "merge_functionally_equivalent_gates",
    "insert_gate_before",
    "buffer_high_fanout",
    "buffer_all_high_fanout",
    "buffer_each_load",
    "replace_in_cone",
    "replace_globally",
    "replace_or_with_nand_not",
    "replace_xnor_with_nor",
    "replace_xor_with_nor",
    "replace_xnor_with_nand",
    "replace_xor_with_and_or_not",
    "replace_xnor_with_and_or_not",
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
    "remap_cone",
    "full_cleanup_optimize",
    "balance_associative_trees",
})
_POST_CHECK_TOOLS: frozenset[str] = frozenset({
    "check_design_style",
    "check_fanout_limit",
})

# State-changing tools = transform tools + read_design + rename
_STATE_CHANGING_TOOLS: frozenset[str] = _TRANSFORM_TOOLS | frozenset({
    "read_design",
    "rename",
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


_SIG_RE = r"([A-Za-z_][A-Za-z0-9_$]*(?:\[\d+\])?)"


def _tool_call(tool_name: str, **arguments) -> dict:
    return {"name": tool_name, "arguments": arguments}


@dataclass(frozen=True)
class _RuleDecision:
    calls: list[dict]
    confidence: float
    reason: str


def _rule_based_decision(user_request: str) -> Optional[_RuleDecision]:
    """Return a generic rule match plus confidence, or None when no rule applies."""
    calls = _rule_based_tool_calls(user_request)
    if not calls:
        return None
    confidence, reason = _score_rule_decision(user_request, calls)
    return _RuleDecision(calls=calls, confidence=confidence, reason=reason)


def _rule_based_tool_calls(user_request: str) -> Optional[list[dict]]:
    """Deterministically handle generic contest prompt intents."""
    text = user_request or ""
    low = text.lower()
    if not text.strip():
        return None

    if _is_read_like(low):
        path = _extract_design_path(text)
        if path:
            return [_tool_call("read_design", path=path)]

    if _is_write_like(low) and ".v" in low:
        path = _extract_output_path(text)
        if path:
            return [_tool_call("write_design", path=path)]

    if _is_gate_breakdown_like(low):
        return [_tool_call("gate_count_breakdown")]

    if "optimization stats" in low or "optimization statistics" in low or "优化统计" in text:
        return [_tool_call("optimization_stats")]

    gate = _gate_type_from_text(low)
    if gate and _is_gate_list_like(low):
        return [_tool_call("list_gates_by_type", gate_type=gate, limit=200)]
    if gate and _is_gate_count_like(low):
        return [_tool_call("count_gate_type", gate_type=gate)]

    if _is_constant_simplify_like(low):
        return [_tool_call("simplify_constant_gates")]

    if _is_constant_report_like(low):
        gate_type = _gate_type_from_text(low) or ""
        const_value = _constant_value_from_text(low)
        args: dict[str, Any] = {"gate_type": gate_type}
        if const_value is not None:
            args["const_value"] = const_value
        return [_tool_call("report_constant_input_gates", **args)]

    if _is_buffer_each_like(low):
        sig = _extract_after_keywords(text, ("signal", "net", "wire", "input")) or _first_signal(text)
        if sig:
            return [_tool_call("buffer_each_load", net_name=sig)]

    if _is_buffer_all_like(low):
        limit = _extract_int_after(low, (
            "more than", "at most", "max fanout", "maximum fanout",
            "greater than", "fanout greater than", "fanout limit",
            "no gate has fanout greater than", "no signal has fanout greater than",
            "no single driver has more than", "drives more than",
        )) or 4
        return [_tool_call("buffer_all_high_fanout", max_fanout=limit)]

    if _is_buffer_net_like(low):
        sig = _extract_after_keywords(text, ("signal", "net", "wire", "reset signal", "clock signal")) or _first_signal(text)
        limit = _extract_int_after(low, (
            "at most", "more than", "max fanout", "maximum fanout",
            "greater than", "fanout limit", "no more than",
        )) or 4
        if sig:
            return [_tool_call("buffer_high_fanout", net_name=sig, max_fanout=limit)]

    if _is_rename_like(low):
        m = re.search(rf"(?:gate|wire|signal|identifier)\s+{_SIG_RE}\s+to\s+({_SIG_RE})", text, re.I)
        if not m:
            m = re.search(rf"rename\s+(?:internal\s+)?(?:gate|wire|signal)?\s*{_SIG_RE}\s+to\s+({_SIG_RE})", text, re.I)
        if not m:
            m = re.search(rf"change\s+the\s+identifier\s+of\s+(?:gate|wire)\s+{_SIG_RE}\s+to\s+({_SIG_RE})", text, re.I)
        if m:
            return [_tool_call("rename", old_name=_clean_signal(m.group(1)), new_name=_clean_signal(m.group(2)))]

    if _is_or_cone_to_nand_like(low):
        out = _extract_cone_signal(text)
        if out:
            # Use remap_cone (ABC-first) instead of per-gate template expansion
            # to avoid depth regression from OR→NAND+NOT template bloat
            return [_tool_call("remap_cone", output_signal=out, style="nand_not")]

    if _is_xor_to_nand_like(low):
        return [_tool_call("replace_xor_with_nand")]

    if _is_xnor_to_nor_like(low):
        return [_tool_call("replace_xnor_with_nor")]

    style = _style_from_text(low)
    if style and _is_style_depth_opt_like(low):
        out = _extract_cone_signal(text)
        if out:
            return [_tool_call("optimize_cone", output_signal=out, objective="min_depth", style=style)]
        # Whole-design ABC remap can violate strict primitive-style constraints;
        # use comprehensive depth optimization as the safe style-preserving fallback.
        return [
            _tool_call("balance_associative_trees"),
            _tool_call("optimize_design_depth"),
        ]

    if style and _is_cone_remap_like(low):
        out = _extract_cone_signal(text)
        if out:
            return [_tool_call("remap_cone", output_signal=out, style=style)]

    if style and _is_design_remap_like(low):
        return [_tool_call("remap_design", style=style)]

    if _is_original_equiv_like(low):
        if any(mark in low for mark in (
            "robust",
            "fallback",
            "per-output",
            "per output",
            "output cone",
            "prove",
            "verify functional equivalence",
            "pre-transformation",
            "pre transformation",
            "transformed design",
        )):
            return [_tool_call("check_original_equiv_robust")]
        return [_tool_call("check_original_equiv")]

    if _is_not_not_like(low):
        return [_tool_call("collapse_not_not")]

    if _is_dangling_like(low):
        return [_tool_call("remove_dangling")]

    if _is_duplicate_merge_like(low):
        structural_only = any(mark in low for mark in (
            "same inputs",
            "same input",
            "on identical inputs",
            "structural duplicate",
        ))
        if (
            not structural_only
            and any(mark in low for mark in (
                "functionally equivalent",
                "same boolean function",
                "produce the same function",
            ))
        ):
            return [
                _tool_call("structural_duplicate_merge"),
                _tool_call("merge_functionally_equivalent_gates"),
            ]
        return [_tool_call("structural_duplicate_merge")]

    if _is_depth_transform_like(low):
        return [
            _tool_call("balance_associative_trees"),
            _tool_call("optimize_design_depth"),
        ]

    if "primary inputs" in low and ("primary outputs" in low or "and outputs" in low) and _has_any_word(low, ("how many", "number of", "determine the number")):
        return [_tool_call("primary_io_counts")]
    if "primary input" in low and "bit width" in low:
        return [_tool_call("list_primary_inputs_with_widths")]
    if "primary output" in low and "bit width" in low:
        return [_tool_call("list_primary_outputs_with_widths")]
    if "length 0" in low or "direct wire connections" in low:
        return [_tool_call("direct_pi_po_connections")]

    if "register-to-register" in low or "register to register" in low:
        if "depth" in low or "maximum" in low:
            return [_tool_call("max_register_to_register_depth")]
        return [_tool_call("list_register_to_register_paths", limit=80)]

    if "dff d-pin" in low or ("primary input" in low and "dff" in low and "depth" in low):
        return [_tool_call("max_pi_to_dff_depth")]

    if "outputs have" in low and "depth greater than" in low:
        n = _extract_int_after(low, ("greater than", "depth >")) or 0
        return [_tool_call("count_outputs_depth_gt", threshold=n)]

    if "depth" in low and ("fanin cone" in low or "depth of the cone" in low):
        sig = _extract_cone_signal(text)
        if sig:
            return [_tool_call("max_fanin_depth", output_signal=sig)]

    if "maximum-depth path" in low or "maximum depth path" in low:
        sig = _extract_after_keywords(text, ("gate",)) or _first_signal(text)
        if sig:
            return [_tool_call("gate_on_max_depth_path", name=sig)]

    if _is_boolean_expr_like(low):
        sig = _extract_output_or_signal(text)
        if sig:
            return [_tool_call("boolean_expression", signal_name=sig)]

    if "enable or hold" in low:
        return [_tool_call("report_dff_enable_hold", limit=120)]

    if _is_last_count_like(low):
        return [_tool_call("last_operation_count", key=_last_count_key_from_text(low))]

    if _is_constant_assertion_like(low):
        sig = _extract_output_or_signal(text)
        val = 1 if "always 1" in low else 0
        if sig:
            return [_tool_call("is_signal_constant", signal_name=sig, value=val)]

    if _is_signal_equiv_like(low):
        pair = _extract_signal_pair(text)
        if pair:
            return [_tool_call("internal_signals_equiv", signal_a=pair[0], signal_b=pair[1])]

    if "nand(" in low or "nand(a, b)" in low or "nand(a,b)" in low:
        sig = _extract_equivalent_target_signal(text) or _extract_output_or_signal(text)
        if sig:
            return [_tool_call("find_nand_pair_for_signal", signal_name=sig, limit=2000)]

    if "symmetric" in low and "with respect to inputs" in low:
        m = re.search(rf"function\s+at\s+{_SIG_RE}.*?inputs?\s+{_SIG_RE}\s+and\s+{_SIG_RE}", text, re.I)
        if m:
            return [_tool_call("check_signal_symmetry", signal_name=_clean_signal(m.group(1)), input_a=_clean_signal(m.group(2)), input_b=_clean_signal(m.group(3)))]

    if "floating" in low or "unconnected" in low:
        return [_tool_call("report_floating_signals", limit=120)]

    if "flip-flop" in low or "flipflop" in low or "flip-flops" in low:
        if "clock" in low and "driven by" in low:
            sig = _extract_after_keywords(text, ("clock",))
            if sig:
                return [_tool_call("list_flipflops_by_clock", clock_name=sig, limit=200)]

    if "largest fanin cone" in low:
        return [_tool_call("largest_output_cone")]
    if "deepest fanin logic cone" in low or "deepest output" in low:
        return [_tool_call("deepest_output_cone")]
    if "maximum combinational" in low and "design" in low and "depth" in low:
        return [_tool_call("max_design_depth")]

    if _is_cone_count_like(low):
        sig = _extract_cone_signal(text)
        if sig:
            use_breakdown = "number of each gate type" in low or bool(_gate_type_from_text(low))
            return [_tool_call("cone_gate_breakdown" if use_breakdown else "report_cone_size", output_signal=sig)]

    path_call = _path_tool_call_from_text(text)
    if path_call:
        return [path_call]

    if _is_fanout_direct_like(low):
        sig = (
            _extract_after_keywords(text, ("primary input", "input", "gate", "signal", "wire", "driven by"))
            or _first_signal(text)
        )
        if sig:
            if "successor" in low:
                return [_tool_call("immediate_successors", name=sig)]
            return [_tool_call("get_fanout", net_name=sig), _tool_call("list_direct_loads", name=sig, limit=200)]

    if "connect to" in low or "connected to" in low:
        sig = _extract_after_keywords(text, ("renamed signal", "signal", "wire", "output of")) or _first_signal(text)
        if sig:
            return [_tool_call("list_direct_loads", name=sig, limit=200)]

    if _is_transitive_fanin_like(low):
        sig = _extract_cone_signal(text) or _extract_output_or_signal(text)
        if sig:
            return [_tool_call("transitive_fanin", output_signal=sig)]

    if _is_transitive_fanout_like(low):
        sig = _extract_after_keywords(text, ("input", "from", "signal")) or _first_signal(text)
        if sig:
            return [_tool_call("transitive_fanout", input_signal=sig)]

    if "highest fanout" in low:
        return [_tool_call("highest_fanout_input")]
    if "maximum fanout" in low or "max fanout" in low:
        sig = _extract_after_keywords(text, ("of",))
        return [_tool_call("max_fanout", name=sig or "")]

    if "cut between" in low:
        sig = _extract_after_keywords(text, ("wire", "signal")) or _first_signal(text)
        if sig:
            return [_tool_call("is_cut_between_pi_po", wire_name=sig)]

    if "articulation" in low:
        pair = _extract_between_pair(text)
        if pair:
            return [_tool_call("articulation_points_between", source=pair[0], target=pair[1], limit=200)]

    if "type of gate" in low or "what type of gate" in low:
        sig = ""
        m = re.search(rf"gate\s+is\s+{_SIG_RE}", text, re.I)
        if not m:
            m = re.search(rf"gate\s+{_SIG_RE}", text, re.I)
        if m and _clean_signal(m.group(1)).lower() != "is":
            sig = _clean_signal(m.group(1))
        if not sig:
            sig = _extract_after_keywords(text, ("named",)) or _first_signal(text)
        if sig:
            return [_tool_call("gate_info", name=sig)]

    if "connected to the output of" in low:
        sig = _extract_after_keywords(text, ("output of",))
        if sig:
            return [_tool_call("list_direct_loads", name=sig, limit=200)]

    return None


def _score_rule_decision(user_request: str, calls: list[dict]) -> tuple[float, str]:
    """High-precision confidence gate for deterministic routing."""
    low = (user_request or "").lower()
    names = [_tool_call_name_args(tc)[0] for tc in calls]
    if not names or any(name not in _DISPATCH_MAP for name in names):
        return 0.0, "unknown rule tool"

    if any(_has_suspicious_signal_argument(tc) for tc in calls):
        return 0.35, "suspicious extracted signal"

    if _has_rule_conflict(low, names):
        return 0.55, "conflicting request intent"

    score = 0.92
    if any(name in _TRANSFORM_TOOLS for name in names):
        score = 0.90 if _has_transform_intent(low) else 0.72
    elif any(name in {"read_design", "write_design"} for name in names):
        score = 0.96
    elif any(name in {"check_original_equiv", "check_equiv"} for name in names):
        score = 0.94

    if any(mark in low for mark in ("maybe", "perhaps", "possibly", "not sure", "unsure")):
        score = min(score, 0.78)

    return score, f"rule confidence {score:.2f}"


def _has_transform_intent(low: str) -> bool:
    return any(mark in low for mark in (
        "add",
        "buffer",
        "collapse",
        "convert",
        "decompose",
        "delete",
        "eliminate",
        "insert",
        "merge",
        "minimi",
        "optimiz",
        "prune",
        "propagat",
        "reconstruct",
        "reduce",
        "remap",
        "remove",
        "replace",
        "restructur",
        "rewrite",
        "simplif",
        "sweep",
        "trim",
        "unused",
        "using only",
        "dangling",
        "floating",
        "do not contribute",
        "same boolean function",
        "structural duplicate",
        "functionally equivalent gates",
    ))


def _has_rule_conflict(low: str, names: list[str]) -> bool:
    tool_set = set(names)
    if "gate_count_breakdown" in tool_set and any(mark in low for mark in ("cost function", "insert buffer", "insert buffers")):
        return True
    if "check_original_equiv" in tool_set and any(mark in low for mark in (
        "already optimal",
        "minimize",
        "minimise",
        "optimize",
        "optimise",
        "reduce",
        "restructure",
    )):
        return True
    if "find_path" in tool_set and any(mark in low for mark in ("all paths", "complete enumeration", "every path", "enumerate every")):
        return True
    if (
        "structural_duplicate_merge" in tool_set
        and _style_from_text(low)
        and _is_design_remap_like(low)
        and _is_style_depth_opt_like(low)
    ):
        return True
    return False


def _has_suspicious_signal_argument(tc: dict) -> bool:
    _, arguments = _tool_call_name_args(tc)
    for key, value in arguments.items():
        if key not in _SIGNAL_ARGUMENT_KEYS:
            continue
        if _value_has_suspicious_signal(value):
            return True
    return False


def _value_has_suspicious_signal(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return any(_value_has_suspicious_signal(v) for v in value)
    if not isinstance(value, str):
        return False
    raw = _clean_signal(value)
    if not raw:
        return False
    norm = raw.lower()
    if norm in _SUSPICIOUS_SIGNAL_WORDS:
        return True
    if norm.endswith((".v", ".log")) or any(ch.isspace() for ch in raw):
        return True
    return False


def _tool_call_name_args(tc: dict) -> tuple[str, dict]:
    fn = tc.get("function")
    raw_name = ""
    raw_args: Any = {}
    if isinstance(fn, dict):
        raw_name = str(fn.get("name", "") or tc.get("name", ""))
        raw_args = fn.get("arguments", tc.get("arguments", {}))
    else:
        raw_name = str(tc.get("name", ""))
        raw_args = tc.get("arguments", {})
    args = dict(raw_args) if isinstance(raw_args, dict) else {}
    return _canonical_tool_name(raw_name), args


def _with_post_checks(tool_calls: list[dict]) -> tuple[list[dict], int]:
    """Append hard checks after transforms that claim a bounded structural result."""
    expanded: list[dict] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = {
        _tool_call_signature(*_tool_call_name_args(tc))
        for tc in tool_calls
    }
    added = 0

    for tc in tool_calls:
        tool_name, arguments = _tool_call_name_args(tc)
        expanded.append(tc)

        post_checks = _post_checks_for_tool(tool_name, arguments)
        for post_name, post_args in post_checks:
            if _append_unique_post_check(expanded, seen, post_name, post_args):
                added += 1

    return expanded, added


def _post_checks_for_tool(tool_name: str, arguments: dict) -> list[tuple[str, dict]]:
    if tool_name == "remap_design" and arguments.get("style"):
        return [("check_design_style", {"style": arguments["style"]})]
    if tool_name in {"remap_cone", "optimize_cone"} and arguments.get("style"):
        args = {"style": arguments["style"]}
        if arguments.get("output_signal"):
            args["output_signal"] = arguments["output_signal"]
        return [("check_design_style", args)]
    if tool_name == "buffer_all_high_fanout" and arguments.get("max_fanout") is not None:
        return [("check_fanout_limit", {"max_fanout": arguments["max_fanout"]})]
    if tool_name == "buffer_high_fanout" and arguments.get("max_fanout") is not None:
        args = {"max_fanout": arguments["max_fanout"]}
        if arguments.get("net_name"):
            args["name"] = arguments["net_name"]
        return [("check_fanout_limit", args)]
    return []


def _append_unique_post_check(
    expanded: list[dict],
    seen: set[tuple[str, tuple[tuple[str, str], ...]]],
    tool_name: str,
    arguments: dict,
) -> bool:
    signature = _tool_call_signature(tool_name, arguments)
    if signature in seen:
        return False
    expanded.append(_tool_call(tool_name, **arguments))
    seen.add(signature)
    return True


def _tool_call_signature(tool_name: str, arguments: dict) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (
        tool_name,
        tuple(sorted((str(k), repr(v)) for k, v in arguments.items())),
    )



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
        self._reset_router_stats()


    def reset(self) -> None:
        """Clear conversation history (call at the start of each testcase)."""
        self.history = []
        self._turn_count = 0
        self._state_summary = ""
        self._last_action_summary = ""
        self._reset_router_stats()

    def _reset_router_stats(self) -> None:
        self._router_stats: dict[str, float] = {
            "turns": 0,
            "rule_matches": 0,
            "rule_direct": 0,
            "rule_fallback": 0,
            "llm_turns": 0,
            "rule_tool_calls": 0,
            "llm_tool_calls": 0,
            "post_checks": 0,
            "tool_failures": 0,
            "rule_confidence_sum": 0.0,
        }

    def router_stats_line(self) -> str:
        stats = self._router_stats
        turns = int(stats.get("turns", 0))
        matches = int(stats.get("rule_matches", 0))
        direct = int(stats.get("rule_direct", 0))
        avg_conf = (stats.get("rule_confidence_sum", 0.0) / matches) if matches else 0.0
        hit_pct = (100.0 * direct / turns) if turns else 0.0
        return (
            "ROUTER_STATS "
            f"turns={turns} "
            f"rule_matches={matches} "
            f"rule_direct={direct} "
            f"rule_fallback={int(stats.get('rule_fallback', 0))} "
            f"llm_turns={int(stats.get('llm_turns', 0))} "
            f"rule_hit_pct={hit_pct:.1f} "
            f"avg_rule_conf={avg_conf:.2f} "
            f"rule_tool_calls={int(stats.get('rule_tool_calls', 0))} "
            f"llm_tool_calls={int(stats.get('llm_tool_calls', 0))} "
            f"post_checks={int(stats.get('post_checks', 0))} "
            f"tool_failures={int(stats.get('tool_failures', 0))}"
        )

    def run(self, user_request: str, budget: Optional[dict[str, Any]] = None) -> str:
        """Process one natural-language request and return the final text answer."""
        deadline = None
        request_kind = "default"
        if isinstance(budget, dict):
            raw_deadline = budget.get("deadline_monotonic")
            if raw_deadline is not None:
                try:
                    deadline = float(raw_deadline)
                except (TypeError, ValueError):
                    deadline = None
            request_kind = str(budget.get("request_kind") or request_kind)
        if deadline is not None:
            self.backend.set_request_deadline(deadline, request_kind)
        else:
            self.backend.clear_request_deadline()
        try:
            return self._run_impl(user_request)
        finally:
            self.backend.clear_request_deadline()

    def _run_impl(self, user_request: str) -> str:
        """Process one natural-language request after the budget has been installed."""
        self._turn_count += 1
        self._router_stats["turns"] += 1
        llm_request = _compact_user_request(
            user_request, limit=LLM_REQUEST_CONTENT_LIMIT)
        user_msg = {"role": "user", "content": llm_request}
        self.history.append(user_msg)
        self._trim_history()

        decision = _rule_based_decision(user_request)
        if decision:
            self._router_stats["rule_matches"] += 1
            self._router_stats["rule_confidence_sum"] += decision.confidence
            if decision.confidence >= _RULE_CONFIDENCE_THRESHOLD:
                self._router_stats["rule_direct"] += 1
                user_msg["content"] = _compact_user_request(user_request)
                return self._execute_tool_calls(
                    decision.calls,
                    user_request,
                    route="rule",
                    confidence=decision.confidence,
                    reason=decision.reason,
                )
            self._router_stats["rule_fallback"] += 1

        tools = get_tools_for_request(user_request, self.llm.provider)
        tools = self._filter_loaded_state_tools(tools)
        self._router_stats["llm_turns"] += 1

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

        return self._execute_tool_calls(tool_calls, user_request, route="llm")


    def _execute_tool_calls(
        self,
        tool_calls: list[dict],
        user_request: str,
        route: str = "llm",
        confidence: Optional[float] = None,
        reason: str = "",
    ) -> str:
        """Execute selected backend tools and store a compact result summary."""
        results: list[str] = []
        original_count = len(tool_calls)
        if route == "rule":
            self._router_stats["rule_tool_calls"] += original_count
        else:
            self._router_stats["llm_tool_calls"] += original_count
        tool_calls, post_check_count = _with_post_checks(tool_calls)
        self._router_stats["post_checks"] += post_check_count

        for tc in tool_calls:
            tool_name, arguments = _tool_call_name_args(tc)
            raw_tool_name = str(tc.get("name", tool_name))
            remaining_fn = getattr(self.backend, "remaining_request_time", None)
            remaining = remaining_fn() if callable(remaining_fn) else float("inf")
            if remaining <= _MIN_REMAINING_TOOL_SEC:
                results.append(
                    f"TIME_BUDGET_EXHAUSTED[{tool_name}]: "
                    f"remaining_request_time={remaining:.2f}s; skipped {len(tool_calls) - len(results)} tool(s)."
                )
                break
            if tool_name not in _DISPATCH_MAP:
                inferred = _infer_tool_call_from_request(user_request, raw_tool_name, arguments)
                if inferred is not None:
                    tool_name, arguments = inferred
            if self.verbose:
                route_bits = [f"route={route}"]
                if confidence is not None:
                    route_bits.append(f"confidence={confidence:.2f}")
                if reason:
                    route_bits.append(f"reason={reason}")
                print(f"[TOOL] {' '.join(route_bits)} {raw_tool_name}({arguments})", file=sys.stderr)

            result = _dispatch(self.backend, tool_name, arguments)
            results.append(result)
            self._update_state_summary(tool_name, result)
            if _looks_like_tool_failure(result):
                self._router_stats["tool_failures"] += 1

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
                if not self._llm_error_is_retryable(e):
                    raise
                if attempt + 1 < LLM_RETRIES:
                    time.sleep(2.0 * (2 ** attempt))  # 2s, 4s, 8s
        assert last_error is not None
        raise last_error

    @staticmethod
    def _llm_error_is_retryable(exc: Exception) -> bool:
        """Distinguish transient network errors from permanent auth/config errors."""
        msg = str(exc).lower()
        if any(kw in msg for kw in (
            "timeout", "timed out", "connection", "ssl", "syscall",
            "reset", "refused", "temporary", "rate limit", "server error",
            "500", "502", "503", "504", "429",
        )):
            return True
        if any(kw in msg for kw in (
            "unauthorized", "authentication", "invalid api key",
            "401", "403", "not found", "404",
        )):
            return False
        return True  # unknown errors default to retryable

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
        "check_original_equiv_robust",
        "check_design_style",
        "check_fanout_limit",
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
        "FAIL[",
        "NotFound:",
        "ERR[",
        "ToolArgErr",
        "ToolErr",
        "Tool error",
        "Error ",
        "Equivalence check error",
        "Unknown tool",
        "UNKNOWN:",
        "UNKNOWN[",
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
    if _is_read_like(low):
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


def _has_any_word(low: str, needles: tuple[str, ...]) -> bool:
    return any(needle in low for needle in needles)


def _is_write_like(low: str) -> bool:
    return any(word in low for word in ("write", "save", "export", "emit"))


def _is_read_like(low: str) -> bool:
    if ".v" not in low:
        return False
    if any(mark in low for mark in (
        "load the design",
        "read the design",
        "read in design",
        "read in the design",
        "load file",
        "load the file",
        "read file",
        "read the file",
    )):
        return True
    return (
        any(word in low for word in ("load", "read", "open"))
        and any(mark in low for mark in ("design", "netlist", "file", "directory", "folder", "from"))
    )


def _is_original_equiv_like(low: str) -> bool:
    return (
        ("equivalent" in low or "equivalence" in low)
        and any(mark in low for mark in (
            "original",
            "loaded from disk",
            "pre-transformation",
            "pre transformation",
            "transformed design",
            "input design",
            "source design",
            "before changes",
            "before transformation",
            "as loaded",
        ))
    )


def _is_gate_breakdown_like(low: str) -> bool:
    if "cost function" in low or "insert buffer" in low or "insert buffers" in low:
        return False
    return (
        "count all the gates" in low
        or "broken down by gate type" in low
        or "total gate count" in low
        or "total count broken down" in low
        or "compute the total gate count" in low
    )


def _gate_type_from_text(low: str) -> str:
    m = re.search(
        r"\b(?:how many|list all|list every|report all|report any|count)\s+"
        r"(xnor|nand|nor|xor|and|or|not|buf|dff)\s+gates?\b",
        low,
    )
    if m:
        return m.group(1)
    for gate in ("xnor", "nand", "nor", "xor", "and", "or", "not", "buf", "dff"):
        if re.search(rf"\b{gate}\b", low):
            return gate
    if "flip-flop" in low or "flipflop" in low:
        return "dff"
    return ""


def _is_gate_list_like(low: str) -> bool:
    return (
        ("list all" in low or "report all" in low or "list every" in low)
        and "gate" in low
        and "constant" not in low
    )


def _is_gate_count_like(low: str) -> bool:
    return (
        ("how many" in low or "currently in the design" in low or "now in the design" in low)
        and "gate" in low
        and "cone" not in low
        and "added" not in low
        and "removed" not in low
        and "eliminated" not in low
    )


def _is_last_count_like(low: str) -> bool:
    if "enable or hold" in low:
        return False
    if not ("how many" in low or "count" in low):
        return False
    return any(word in low for word in (
        "added", "inserted", "removed", "merged", "eliminated",
        "converted", "replaced", "found to have",
    ))


def _last_count_key_from_text(low: str) -> str:
    if "buf" in low or "buffer" in low:
        return "buf_added"
    if "dangling" in low:
        return "dangling_removed"
    if "redundant" in low or "merge" in low or "duplicate" in low:
        return "merged_gates"
    if "constant" in low or "eliminated" in low:
        return "constant_gates_eliminated"
    if "nand" in low and "added" in low:
        return "nand_added"
    if "nor" in low and "added" in low:
        return "nor_added"
    if "xnor" in low:
        return "xnor_converted"
    if "xor" in low:
        return "xor_converted"
    if "inverter" in low or "not" in low:
        return "not_not_collapsed"
    return "last"


def _is_constant_report_like(low: str) -> bool:
    return "constant" in low and any(word in low for word in ("report", "list", "any", "tied to"))


def _constant_value_from_text(low: str) -> Optional[int]:
    if "1'b1" in low or "constant 1" in low or "const=1" in low:
        return 1
    if "1'b0" in low or "constant 0" in low or "const=0" in low:
        return 0
    return None


def _is_constant_simplify_like(low: str) -> bool:
    return (
        any(word in low for word in ("simplify", "propagating", "propagate", "replace"))
        and ("constant" in low or "tied to constant" in low)
    )


def _is_buffer_each_like(low: str) -> bool:
    return "buffer" in low and any(mark in low for mark in ("each load", "dedicated buffer", "per load"))


def _is_buffer_all_like(low: str) -> bool:
    return (
        "buffer" in low
        and any(mark in low for mark in (
            "wherever needed", "no gate drives more than", "no signal drives more than",
            "no gate has fanout greater than", "no single driver has more than",
            "fanout optimization across the netlist", "fanout optimization",
            "perform fanout optimization",
        ))
    )


def _is_buffer_net_like(low: str) -> bool:
    return "buffer" in low and "fanout" in low


def _is_rename_like(low: str) -> bool:
    return any(word in low for word in ("rename", "change the identifier", "update the name"))


def _is_or_cone_to_nand_like(low: str) -> bool:
    return "or gate" in low and "cone" in low and "nand" in low


def _is_xor_to_nand_like(low: str) -> bool:
    return "xor" in low and "xnor" not in low and "nand" in low and any(word in low for word in ("replace", "convert", "realized"))


def _is_xnor_to_nor_like(low: str) -> bool:
    return "xnor" in low and "nor" in low and any(word in low for word in ("replace", "convert", "rewrite"))


def _style_from_text(low: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", " ", low)
    if "nand" in compact and "not" in compact and any(word in compact for word in ("only", "remains", "maintains", "using")):
        return "nand_not"
    if "nor" in compact and "not" in compact and any(word in compact for word in ("only", "remains", "maintains", "using")):
        return "nor_not"
    if "and or and not" in compact or "and or not" in compact:
        return "and_or_not"
    if "and and not" in compact or "and not only" in compact or "only and and not" in compact:
        return "and_not"
    return ""


def _is_style_depth_opt_like(low: str) -> bool:
    return any(word in low for word in ("optimiz", "minimize", "minimise", "reduce", "restructur"))


def _is_cone_remap_like(low: str) -> bool:
    return "cone" in low and any(word in low for word in ("convert", "restructure", "decompose", "using only", "contains only"))


def _is_design_remap_like(low: str) -> bool:
    return any(mark in low for mark in ("entire design", "entire netlist", "whole design", "whole netlist", "remap the entire", "reconstruct the entire"))


def _is_not_not_like(low: str) -> bool:
    return "back-to-back inverter" in low or "not-not" in low or "collapse them into a wire" in low


def _is_dangling_like(low: str) -> bool:
    return any(mark in low for mark in (
        "dangling", "unused", "do not contribute", "not connected to any primary output",
        "floating nodes", "do not affect outputs", "prune the netlist", "sweep out",
        "delete all gates",
    ))


def _is_duplicate_merge_like(low: str) -> bool:
    return any(mark in low for mark in (
        "functionally equivalent gates",
        "gate pairs in the design that are functionally equivalent",
        "same boolean function",
        "structural duplicate",
        "redundant gates",
    ))


def _is_depth_transform_like(low: str) -> bool:
    return "depth" in low and any(word in low for word in ("reduce", "optimiz", "minimize", "minimise", "restructur"))


def _is_boolean_expr_like(low: str) -> bool:
    return any(mark in low for mark in (
        "boolean equation", "boolean expression", "boolean function",
        "logic expression", "what boolean function", "derive the boolean",
    ))


def _is_constant_assertion_like(low: str) -> bool:
    return "always 0" in low or "always 1" in low


def _is_signal_equiv_like(low: str) -> bool:
    return (
        ("functionally equivalent" in low or "identical logic values" in low or "functional equivalence between internal signals" in low)
        and "current design" not in low
        and "original" not in low
    )


def _is_cone_count_like(low: str) -> bool:
    return (
        ("fanin cone" in low or "logic cone" in low or "cone of" in low)
        and (
            any(mark in low for mark in ("how many gates", "number of each gate type", "gate type in the cone", "gates are in"))
            or ("how many" in low and "gate" in low)
        )
    )


def _is_fanout_direct_like(low: str) -> bool:
    return (
        ("fanout of" in low and "transitive" not in low and "maximum" not in low)
        or "drives directly" in low
        or "number of gates driven by" in low
        or "immediate successors" in low
    )


def _is_transitive_fanin_like(low: str) -> bool:
    return "transitive fanin" in low or "fanin logic cone" in low


def _is_transitive_fanout_like(low: str) -> bool:
    return "transitive fanout" in low or "reachable from" in low


def _extract_int_after(low: str, markers: tuple[str, ...]) -> Optional[int]:
    for marker in markers:
        idx = low.find(marker)
        if idx < 0:
            continue
        m = re.search(r"(\d+)", low[idx:])
        if m:
            return int(m.group(1))
    m = re.search(r"(\d+)", low)
    return int(m.group(1)) if m else None


def _extract_after_keywords(text: str, keywords: tuple[str, ...]) -> str:
    for kw in sorted(keywords, key=len, reverse=True):
        m = re.search(rf"\b{re.escape(kw)}\s+{_SIG_RE}", text, re.I)
        if m:
            return _clean_signal(m.group(1))
    return ""


def _first_signal(text: str) -> str:
    m = re.search(_SIG_RE, text)
    return _clean_signal(m.group(1)) if m else ""


def _extract_cone_signal(text: str) -> str:
    patterns = (
        rf"cone\s+of\s+(?:primary\s+)?(?:output\s+)?{_SIG_RE}",
        rf"logic\s+cone\s+of\s+(?:primary\s+)?(?:output\s+)?{_SIG_RE}",
        rf"fanin\s+cone\s+of\s+(?:primary\s+)?(?:output\s+)?{_SIG_RE}",
        rf"output\s+{_SIG_RE}",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return _clean_signal(m.group(1))
    return ""


def _extract_output_or_signal(text: str) -> str:
    stop_words = {
        "a", "an", "and", "any", "does", "determine", "exist", "there",
        "whether", "which", "what", "report", "check", "verify",
    }
    for pat in (
        rf"output\s+{_SIG_RE}",
        rf"signal\s+{_SIG_RE}",
        rf"wire\s+{_SIG_RE}",
        rf"for\s+{_SIG_RE}",
        rf"function\s+at\s+{_SIG_RE}",
    ):
        m = re.search(pat, text, re.I)
        if m:
            sig = _clean_signal(m.group(1))
            if sig.lower() not in stop_words:
                return sig
    sig = _first_signal(text)
    return "" if sig.lower() in stop_words else sig


def _extract_equivalent_target_signal(text: str) -> str:
    for pat in (
        rf"equivalent\s+to\s+{_SIG_RE}",
        rf"same\s+function\s+as\s+{_SIG_RE}",
        rf"matches\s+{_SIG_RE}",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return _clean_signal(m.group(1))
    return ""


def _extract_signal_pair(text: str) -> Optional[tuple[str, str]]:
    patterns = (
        rf"signals?\s+{_SIG_RE}\s+and\s+{_SIG_RE}",
        rf"between\s+internal\s+signals?\s+{_SIG_RE}\s+and\s+{_SIG_RE}",
        rf"that\s+{_SIG_RE}\s+and\s+{_SIG_RE}\s+produce",
        rf"whether\s+{_SIG_RE}\s+and\s+{_SIG_RE}\s+produce",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return _clean_signal(m.group(1)), _clean_signal(m.group(2))
    return None


def _extract_between_pair(text: str) -> Optional[tuple[str, str]]:
    m = re.search(rf"between\s+{_SIG_RE}\s+and\s+{_SIG_RE}", text, re.I)
    if m:
        return _clean_signal(m.group(1)), _clean_signal(m.group(2))
    return None


def _path_tool_call_from_text(text: str) -> Optional[dict]:
    m = re.search(rf"does\s+(?:output\s+)?{_SIG_RE}\s+depend\s+on\s+(?:input\s+)?{_SIG_RE}", text, re.I)
    if m:
        return _tool_call("find_path", from_signal=_clean_signal(m.group(2)), to_signal=_clean_signal(m.group(1)))

    patterns = (
        rf"path\s+from\s+(?:primary\s+)?(?:input\s+)?{_SIG_RE}\s+to\s+(?:primary\s+)?(?:output\s+)?{_SIG_RE}.*?(?:does\s+not\s+traverse|avoid(?:ing|s)?)\s+(?:node\s+|gate\s+)?{_SIG_RE}",
        rf"path\s+connecting\s+(?:input\s+)?{_SIG_RE}\s+to\s+(?:output\s+)?{_SIG_RE}.*?avoid(?:ing)?\s+{_SIG_RE}",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return _tool_call("find_path", from_signal=_clean_signal(m.group(1)), to_signal=_clean_signal(m.group(2)), avoid=_clean_signal(m.group(3)))

    m = re.search(rf"every\s+path\s+from\s+(?:input\s+)?{_SIG_RE}\s+to\s+(?:output\s+)?{_SIG_RE}\s+pass(?:es)?\s+through\s+(?:gate\s+)?{_SIG_RE}", text, re.I)
    if m:
        return _tool_call("all_paths_through", from_signal=_clean_signal(m.group(1)), to_signal=_clean_signal(m.group(2)), through=_clean_signal(m.group(3)))

    for pat in (
        rf"originating\s+at\s+(?:primary\s+)?input\s+{_SIG_RE}\s+and\s+terminating\s+at\s+(?:primary\s+)?output\s+{_SIG_RE}",
        rf"paths?\s+between\s+{_SIG_RE}\s+and\s+{_SIG_RE}",
        rf"paths?\s+from\s+(?:primary\s+)?input\s+{_SIG_RE}\s+to\s+(?:primary\s+)?output\s+{_SIG_RE}",
    ):
        m = re.search(pat, text, re.I)
        if m and any(word in text.lower() for word in ("list", "enumeration", "enumerate", "all paths", "complete")):
            return _tool_call("list_paths", from_signal=_clean_signal(m.group(1)), to_signal=_clean_signal(m.group(2)), max_paths=200)

    for pat in (
        rf"depth\s+from\s+(?:input\s+)?{_SIG_RE}\s+to\s+(?:output\s+)?{_SIG_RE}",
        rf"depth\s+between\s+{_SIG_RE}\s+and\s+{_SIG_RE}",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return _tool_call("get_max_depth", from_signal=_clean_signal(m.group(1)), to_signal=_clean_signal(m.group(2)))

    m = re.search(rf"path\s+exist(?:s)?\s+from\s+(?:primary\s+)?input\s+{_SIG_RE}\s+to\s+(?:primary\s+)?output\s+{_SIG_RE}", text, re.I)
    if m:
        return _tool_call("find_path", from_signal=_clean_signal(m.group(1)), to_signal=_clean_signal(m.group(2)))

    m = re.search(rf"combinational\s+path\s+from\s+{_SIG_RE}\s+to\s+{_SIG_RE}", text, re.I)
    if m:
        return _tool_call("find_path", from_signal=_clean_signal(m.group(1)), to_signal=_clean_signal(m.group(2)))

    return None


def _extract_design_path(text: str) -> str:
    file_match = re.search(r"\bfile\s+([^\s]+\.v)", text, re.I)
    path = _strip_path_token(file_match.group(1)) if file_match else ""
    dir_match = re.search(r"\b(?:directory|folder)\s+([^\s]+)", text, re.I)
    if path and dir_match and not any(sep in path for sep in ("/", "\\")):
        directory = _strip_path_token(dir_match.group(1)).rstrip("/\\.")
        path = os.path.join(directory, path)
    if not path:
        m = re.search(r"([^\s]+\.v)", text, re.I)
        path = _strip_path_token(m.group(1)) if m else ""
    return _strip_path_token(path).rstrip(".")


def _extract_output_path(text: str) -> str:
    m = re.search(r"\boutput\s+file\s+([^\s]+\.v)", text, re.I)
    if not m:
        m = re.search(r"([^\s]+\.v)", text, re.I)
    return _strip_path_token(m.group(1)).rstrip(".") if m else ""


def _strip_path_token(value: str) -> str:
    token = str(value or "").strip()
    token = token.strip("'\"`“”‘’").rstrip(".,;:")
    return token.strip("'\"`“”‘’")


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
