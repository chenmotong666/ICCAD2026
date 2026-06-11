"""
agent/tool_schema.py
====================
LLM tool definitions, system prompt, and tool-subset classification.

Architecture (v3 -compressed + 4-tier)
----------------------------------------
  TOOL_SPECS        -canonical tool definitions (short descriptions)
  _TOOL_REGISTRY     -tool_name ->{method_name, category, history_limit}
                       Single source of truth for dispatch map, history limits,
                       and four-tier tool subsets.

Tiers (by tool count):
  FULL      67 tools -transform/optimisation requests
  ANALYSIS  49 tools -complex analysis (structural, misc)
  MEDIUM    45 tools -basic + structural queries
  BASIC     37 tools -simple info queries (gate count, depth, list, etc.)
"""

from __future__ import annotations


TOOL_SPECS: list[dict] = [
    {"name": "read_design",  "description": "Load Verilog netlist",
     "parameters": {"path": {"type": "string"}}, "required": ["path"]},
    {"name": "write_design", "description": "Write design to Verilog",
     "parameters": {"path": {"type": "string"}}, "required": ["path"]},
    {"name": "design_summary",       "description": "Module, ports, gate count",
     "parameters": {}},
    {"name": "gate_count_breakdown", "description": "Per-type gate counts",
     "parameters": {}},
    {"name": "count_gate_type",      "description": "Count gates of type",
     "parameters": {"gate_type": {"type": "string"}}, "required": ["gate_type"]},
    {"name": "last_operation_count", "description": "Count from last transform",
     "parameters": {"key": {"type": "string"}}, "required": ["key"]},
    {"name": "primary_io_counts",    "description": "PI/PO bit counts",
     "parameters": {}},
    {"name": "largest_output_cone",  "description": "Output with largest cone",
     "parameters": {}},
    {"name": "list_primary_inputs_with_widths",  "description": "List PI names+widths",
     "parameters": {}},
    {"name": "list_primary_outputs_with_widths", "description": "List PO names+widths",
     "parameters": {}},
    {"name": "get_max_depth",     "description": "Max depth from_signal->to_signal",
     "parameters": {"from_signal": {"type": "string"}, "to_signal": {"type": "string"}},
     "required": ["from_signal", "to_signal"]},
    {"name": "max_fanin_depth",   "description": "Max depth of output cone",
     "parameters": {"output_signal": {"type": "string"}}, "required": ["output_signal"]},
    {"name": "max_design_depth",  "description": "Deepest PI-to-PO path",
     "parameters": {}},
    {"name": "optimize_design_depth", "description": "Design-wide depth reduction",
     "parameters": {}},
    {"name": "deepest_output_cone",   "description": "Output with deepest cone",
     "parameters": {}},
    {"name": "count_outputs_depth_gt","description": "Outputs with depth > threshold",
     "parameters": {"threshold": {"type": "integer"}}, "required": ["threshold"]},
    {"name": "max_pi_to_dff_depth",   "description": "Max PI to DFF depth",
     "parameters": {}},
    {"name": "find_path",   "description": "Find path A->B, optional avoid/via",
     "parameters": {"from_signal": {"type": "string"}, "to_signal": {"type": "string"},
                    "avoid": {"type": "string"}, "must_pass": {"type": "string"}},
     "required": ["from_signal", "to_signal"]},
    {"name": "list_paths",  "description": "Complete path enumeration A->B",
     "parameters": {"from_signal": {"type": "string"}, "to_signal": {"type": "string"},
                    "max_paths": {"type": "integer"}},
     "required": ["from_signal", "to_signal"]},
    {"name": "list_register_to_register_paths", "description": "List reg-to-reg paths",
     "parameters": {"limit": {"type": "integer"}}},
    {"name": "all_paths_through", "description": "Check all paths A->B pass through signal",
     "parameters": {"from_signal": {"type": "string"}, "to_signal": {"type": "string"},
                    "through": {"type": "string"}},
     "required": ["from_signal", "to_signal", "through"]},
    {"name": "report_cone_size",    "description": "Gate count in output cone",
     "parameters": {"output_signal": {"type": "string"}}, "required": ["output_signal"]},
    {"name": "cone_gate_breakdown", "description": "Per-type counts in cone",
     "parameters": {"output_signal": {"type": "string"}}, "required": ["output_signal"]},
    {"name": "transitive_fanin",    "description": "List gates in fanin cone",
     "parameters": {"output_signal": {"type": "string"}}, "required": ["output_signal"]},
    {"name": "transitive_fanout",   "description": "List gates reachable from signal",
     "parameters": {"input_signal": {"type": "string"}}, "required": ["input_signal"]},
    {"name": "get_fanout",          "description": "Direct fanout count",
     "parameters": {"net_name": {"type": "string"}}, "required": ["net_name"]},
    {"name": "list_direct_loads",   "description": "List gates driven by signal",
     "parameters": {"name": {"type": "string"}, "limit": {"type": "integer"}},
     "required": ["name"]},
    {"name": "report_large_cones",  "description": "Outputs with cone > threshold",
     "parameters": {"threshold": {"type": "integer"}}, "required": ["threshold"]},
    {"name": "gate_info",       "description": "Gate type, pins, connections",
     "parameters": {"name": {"type": "string"}}, "required": ["name"]},
    {"name": "list_gates_by_type","description": "List gates of primitive type",
     "parameters": {"gate_type": {"type": "string"}, "limit": {"type": "integer"}},
     "required": ["gate_type"]},
    {"name": "report_constant_input_gates", "description": "Gates with const-0/1 input",
     "parameters": {"gate_type": {"type": "string"}, "const_value": {"type": "integer"}},
     "required": ["gate_type"]},
    {"name": "same_clock_domain",  "description": "Check two DFFs share clock",
     "parameters": {"ff1_name": {"type": "string"}, "ff2_name": {"type": "string"}},
     "required": ["ff1_name", "ff2_name"]},
    {"name": "shared_fanin_cones", "description": "Gates shared by two output cones",
     "parameters": {"output_a": {"type": "string"}, "output_b": {"type": "string"}},
     "required": ["output_a", "output_b"]},
    {"name": "direct_pi_po_connections", "description": "List direct PI->PO wires",
     "parameters": {}},
    {"name": "is_cut_between_pi_po",     "description": "Check if wire is cut PI->PO",
     "parameters": {"wire_name": {"type": "string"}}, "required": ["wire_name"]},
    {"name": "internal_signals_equiv",   "description": "Check equiv of two signals",
     "parameters": {"signal_a": {"type": "string"}, "signal_b": {"type": "string"}},
     "required": ["signal_a", "signal_b"]},
    {"name": "is_signal_constant", "description": "Prove signal is constant 0/1",
     "parameters": {"signal_name": {"type": "string"}, "value": {"type": "integer"}},
     "required": ["signal_name", "value"]},
    {"name": "find_nand_pair_for_signal","description": "Find NAND(a,b) equiv to signal",
     "parameters": {"signal_name": {"type": "string"}, "limit": {"type": "integer"}},
     "required": ["signal_name"]},
    {"name": "articulation_points_between","description": "Articulation pts A->B",
     "parameters": {"source": {"type": "string"}, "target": {"type": "string"},
                    "limit": {"type": "integer"}},
     "required": ["source", "target"]},
    {"name": "boolean_expression", "description": "Boolean expr for signal",
     "parameters": {"signal_name": {"type": "string"}, "limit": {"type": "integer"}},
     "required": ["signal_name"]},
    {"name": "rename", "description": "Rename gate/wire (auto-detect)",
     "parameters": {"old_name": {"type": "string"}, "new_name": {"type": "string"}},
     "required": ["old_name", "new_name"]},
    {"name": "list_flipflops_by_clock", "description": "List DFFs by clock signal",
     "parameters": {"clock_name": {"type": "string"}, "limit": {"type": "integer"}},
     "required": ["clock_name"]},
    {"name": "highest_fanout_input",    "description": "PI with highest fanout",
     "parameters": {}},
    {"name": "max_fanout",              "description": "Max fanout in design/signal",
     "parameters": {"name": {"type": "string"}}},
    {"name": "structural_duplicate_merge", "description": "Merge identical gates",
     "parameters": {}},
    {"name": "insert_gate_before", "description": "Insert gate before matching cells",
     "parameters": {"name_pattern": {"type": "string"}, "gate_type": {"type": "string"},
                    "extra_input": {"type": "string"}},
     "required": ["name_pattern", "gate_type", "extra_input"]},
    {"name": "buffer_high_fanout", "description": "Buffer tree on net, limit fanout",
     "parameters": {"net_name": {"type": "string"}, "max_fanout": {"type": "integer"}},
     "required": ["net_name", "max_fanout"]},
    {"name": "buffer_all_high_fanout", "description": "Buffer all high-fanout nets",
     "parameters": {"max_fanout": {"type": "integer"}}, "required": ["max_fanout"]},
    {"name": "buffer_each_load", "description": "Insert BUF per load of net",
     "parameters": {"net_name": {"type": "string"}}, "required": ["net_name"]},
    {"name": "replace_in_cone",  "description": "Replace gate type in output cone",
     "parameters": {"output_signal": {"type": "string"}, "old_type": {"type": "string"},
                    "new_type": {"type": "string"}},
     "required": ["output_signal", "old_type", "new_type"]},
    {"name": "replace_globally", "description": "Replace gate type globally",
     "parameters": {"old_type": {"type": "string"}, "new_type": {"type": "string"}},
     "required": ["old_type", "new_type"]},
    {"name": "replace_or_with_nand_not", "description": "Replace OR->NAND+NOT",
     "parameters": {"output_signal": {"type": "string"}}},
    {"name": "replace_xnor_with_nor",    "description": "Replace XNOR->NOR",
     "parameters": {"output_signal": {"type": "string"}}},
    {"name": "remap_design",     "description": "Remap to nand_not / and_not style",
     "parameters": {"style": {"type": "string"}}, "required": ["style"]},
    {"name": "remove_dangling",  "description": "Remove dangling gates/nets",
     "parameters": {}},
    {"name": "fuse_not_buf",     "description": "Fuse NOT->BUF into single NOT",
     "parameters": {}},
    {"name": "collapse_not_not", "description": "Collapse NOT->NOT into wire",
     "parameters": {}},
    {"name": "simplify_constant_gates", "description": "Propagate constant inputs",
     "parameters": {}},
    {"name": "replace_xor_with_nand",   "description": "Replace XOR->-NAND",
     "parameters": {}},
    {"name": "add_balance_buffers", "description": "Insert BUFs to equalise depth",
     "parameters": {"from_signal": {"type": "string"},
                    "to_signals": {"type": "array", "items": {"type": "string"}}},
     "required": ["from_signal", "to_signals"]},
    {"name": "optimize_cone", "description": "ABC optimize cone (depth/gates)",
     "parameters": {"output_signal": {"type": "string"}, "max_depth": {"type": "integer"},
                    "objective": {"type": "string"}},
     "required": ["output_signal"]},
    {"name": "check_equiv",   "description": "Equiv check two Verilog files",
     "parameters": {"path_a": {"type": "string"}, "path_b": {"type": "string"}},
     "required": ["path_a", "path_b"]},
    {"name": "check_original_equiv", "description": "Check current == original",
     "parameters": {}},
    {"name": "verify_assertion", "description": "Verify signal=1 iff constraints",
     "parameters": {"signal": {"type": "string"},
                    "when_true_signals": {"type": "array", "items": {"type": "string"}},
                    "when_false_signals": {"type": "array", "items": {"type": "string"}}},
     "required": ["signal", "when_true_signals", "when_false_signals"]},
    {"name": "check_signal_symmetry", "description": "Check signal symmetric in inputs",
     "parameters": {"signal_name": {"type": "string"}, "input_a": {"type": "string"},
                    "input_b": {"type": "string"}},
     "required": ["signal_name", "input_a", "input_b"]},
    {"name": "report_floating_signals", "description": "Report floating ports/signals",
     "parameters": {"limit": {"type": "integer"}}},
    {"name": "report_dff_enable_hold",  "description": "Report DFF enable/hold",
     "parameters": {"limit": {"type": "integer"}}},
    {"name": "try_reconnect_input_pin", "description": "Reconnect gate input pin",
     "parameters": {"gate_name": {"type": "string"}, "pin_name": {"type": "string"},
                    "signal_name": {"type": "string"}},
     "required": ["gate_name", "pin_name", "signal_name"]},
]



from eda.constants import ToolCategory as _TC

_TOOL_REGISTRY: dict[str, dict] = {
    "read_design":   {"method_name": "read_design",   "category": _TC.IO,      "history_limit": 300},
    "write_design":  {"method_name": "write_design",  "category": _TC.IO,      "history_limit": 300},
    "design_summary":            {"method_name": "design_summary",            "category": _TC.SUMMARY, "history_limit": 300},
    "gate_count_breakdown":      {"method_name": "gate_count_breakdown",      "category": _TC.SUMMARY, "history_limit": 300},
    "count_gate_type":           {"method_name": "count_gate_type",           "category": _TC.SUMMARY, "history_limit": 300},
    "last_operation_count":      {"method_name": "last_operation_count",      "category": _TC.SUMMARY, "history_limit": 300},
    "primary_io_counts":         {"method_name": "primary_io_counts",         "category": _TC.SUMMARY, "history_limit": 300},
    "largest_output_cone":       {"method_name": "largest_output_cone",       "category": _TC.SUMMARY, "history_limit": 300},
    "list_primary_inputs_with_widths":  {"method_name": "list_primary_inputs_with_widths",  "category": _TC.SUMMARY, "history_limit": 300},
    "list_primary_outputs_with_widths": {"method_name": "list_primary_outputs_with_widths", "category": _TC.SUMMARY, "history_limit": 300},
    "get_max_depth":          {"method_name": "get_max_depth",          "category": _TC.DEPTH,   "history_limit": 300},
    "max_fanin_depth":        {"method_name": "max_fanin_depth",        "category": _TC.DEPTH,   "history_limit": 300},
    "max_design_depth":       {"method_name": "max_design_depth",       "category": _TC.DEPTH,   "history_limit": 300},
    "optimize_design_depth":  {"method_name": "optimize_design_depth",  "category": _TC.OPTIMIZE,"history_limit": 600},
    "deepest_output_cone":    {"method_name": "deepest_output_cone",    "category": _TC.DEPTH,   "history_limit": 300},
    "count_outputs_depth_gt": {"method_name": "count_outputs_depth_gt", "category": _TC.DEPTH,   "history_limit": 300},
    "max_pi_to_dff_depth":    {"method_name": "max_pi_to_dff_depth",    "category": _TC.DEPTH,   "history_limit": 300},
    "find_path":                 {"method_name": "find_path",                 "category": _TC.PATH, "history_limit": 400},
    "list_paths":                {"method_name": "list_paths",                "category": _TC.PATH, "history_limit": 400},
    "list_register_to_register_paths": {"method_name": "list_register_to_register_paths", "category": _TC.PATH, "history_limit": 400},
    "all_paths_through":         {"method_name": "all_paths_through",         "category": _TC.PATH, "history_limit": 400},
    "report_cone_size":    {"method_name": "report_cone_size",    "category": _TC.CONE, "history_limit": 400},
    "cone_gate_breakdown": {"method_name": "cone_gate_breakdown", "category": _TC.CONE, "history_limit": 400},
    "transitive_fanin":    {"method_name": "transitive_fanin",    "category": _TC.CONE, "history_limit": 400},
    "transitive_fanout":   {"method_name": "transitive_fanout",   "category": _TC.CONE, "history_limit": 400},
    "get_fanout":          {"method_name": "get_fanout",          "category": _TC.CONE, "history_limit": 300},
    "list_direct_loads":   {"method_name": "list_direct_loads",   "category": _TC.CONE, "history_limit": 300},
    "report_large_cones":  {"method_name": "report_large_cones",  "category": _TC.CONE, "history_limit": 400},
    "gate_info":                 {"method_name": "gate_info",                 "category": _TC.GATE, "history_limit": 300},
    "list_gates_by_type":        {"method_name": "list_gates_by_type",        "category": _TC.GATE, "history_limit": 300},
    "report_constant_input_gates": {"method_name": "report_constant_input_gates", "category": _TC.GATE, "history_limit": 300},
    "same_clock_domain":         {"method_name": "same_clock_domain",         "category": _TC.STRUCTURAL, "history_limit": 200},
    "shared_fanin_cones":        {"method_name": "shared_fanin_cones",        "category": _TC.STRUCTURAL, "history_limit": 400},
    "direct_pi_po_connections":  {"method_name": "direct_pi_po_connections",  "category": _TC.STRUCTURAL, "history_limit": 200},
    "is_cut_between_pi_po":     {"method_name": "is_cut_between_pi_po",     "category": _TC.STRUCTURAL, "history_limit": 200},
    "internal_signals_equiv":    {"method_name": "internal_signals_equiv",    "category": _TC.STRUCTURAL, "history_limit": 200},
    "is_signal_constant":        {"method_name": "is_signal_constant",        "category": _TC.VERIFY,     "history_limit": 200},
    "find_nand_pair_for_signal": {"method_name": "find_nand_pair_for_signal", "category": _TC.STRUCTURAL, "history_limit": 300},
    "articulation_points_between": {"method_name": "articulation_points_between", "category": _TC.STRUCTURAL, "history_limit": 400},
    "boolean_expression":        {"method_name": "boolean_expression",        "category": _TC.STRUCTURAL, "history_limit": 300},
    "rename": {"method_name": "rename", "category": _TC.RENAME, "history_limit": 300},
    "list_flipflops_by_clock": {"method_name": "list_flipflops_by_clock", "category": _TC.DFF_CLOCK, "history_limit": 300},
    "highest_fanout_input":    {"method_name": "highest_fanout_input",    "category": _TC.DFF_CLOCK, "history_limit": 300},
    "max_fanout":              {"method_name": "max_fanout",              "category": _TC.DFF_CLOCK, "history_limit": 300},
    "structural_duplicate_merge": {"method_name": "structural_duplicate_merge", "category": _TC.TRANSFORM, "history_limit": 600},
    "insert_gate_before":        {"method_name": "insert_gate_before",        "category": _TC.TRANSFORM, "history_limit": 600},
    "buffer_high_fanout":        {"method_name": "buffer_high_fanout",        "category": _TC.TRANSFORM, "history_limit": 600},
    "buffer_all_high_fanout":    {"method_name": "buffer_all_high_fanout",    "category": _TC.TRANSFORM, "history_limit": 600},
    "buffer_each_load":          {"method_name": "buffer_each_load",          "category": _TC.TRANSFORM, "history_limit": 600},
    "replace_in_cone":           {"method_name": "replace_gate_type_in_cone", "category": _TC.TRANSFORM, "history_limit": 600},
    "replace_globally":          {"method_name": "replace_gate_type_globally","category": _TC.TRANSFORM, "history_limit": 600},
    "replace_or_with_nand_not":  {"method_name": "replace_or_with_nand_not",  "category": _TC.TRANSFORM, "history_limit": 600},
    "replace_xnor_with_nor":     {"method_name": "replace_xnor_with_nor",     "category": _TC.TRANSFORM, "history_limit": 600},
    "remap_design":              {"method_name": "remap_design",              "category": _TC.TRANSFORM, "history_limit": 600},
    "remove_dangling":           {"method_name": "remove_dangling",           "category": _TC.TRANSFORM, "history_limit": 600},
    "fuse_not_buf":              {"method_name": "fuse_not_buf_pairs",        "category": _TC.TRANSFORM, "history_limit": 600},
    "collapse_not_not":          {"method_name": "collapse_not_not_pairs",    "category": _TC.TRANSFORM, "history_limit": 600},
    "simplify_constant_gates":   {"method_name": "simplify_constant_gates",   "category": _TC.TRANSFORM, "history_limit": 600},
    "replace_xor_with_nand":     {"method_name": "replace_xor_with_nand",     "category": _TC.TRANSFORM, "history_limit": 600},
    "add_balance_buffers":       {"method_name": "add_balance_buffers",       "category": _TC.TRANSFORM, "history_limit": 600},
    "try_reconnect_input_pin":   {"method_name": "try_reconnect_input_pin",   "category": _TC.TRANSFORM, "history_limit": 300},
    "optimize_cone":        {"method_name": "optimize_cone",        "category": _TC.OPTIMIZE, "history_limit": 600},
    "check_equiv":          {"method_name": "check_equiv",          "category": _TC.VERIFY,   "history_limit": 200},
    "check_original_equiv": {"method_name": "check_original_equiv", "category": _TC.VERIFY,   "history_limit": 200},
    "verify_assertion":     {"method_name": "verify_assertion",     "category": _TC.VERIFY,   "history_limit": 300},
    "check_signal_symmetry":  {"method_name": "check_signal_symmetry",  "category": _TC.MISC, "history_limit": 200},
    "report_floating_signals":{"method_name": "report_floating_signals","category": _TC.MISC, "history_limit": 300},
    "report_dff_enable_hold": {"method_name": "report_dff_enable_hold", "category": _TC.MISC, "history_limit": 300},
}



def _build_dispatch_map() -> dict[str, tuple[str, bool]]:
    spec_map = {s["name"]: s for s in TOOL_SPECS}
    result: dict[str, tuple[str, bool]] = {}
    for tool_name, meta in _TOOL_REGISTRY.items():
        spec = spec_map.get(tool_name, {})
        takes_kwargs = len(spec.get("parameters", {})) > 0
        result[tool_name] = (meta["method_name"], takes_kwargs)
    return result


def _build_category_limits() -> dict[str, int]:
    return {name: meta["history_limit"] for name, meta in _TOOL_REGISTRY.items()}


def _build_tool_set(categories: frozenset[str]) -> set[str]:
    return {
        name for name, meta in _TOOL_REGISTRY.items()
        if meta["category"] in categories
    }


# Build at import time
_DISPATCH_MAP: dict[str, tuple[str, bool]] = _build_dispatch_map()
_TOOL_CATEGORY_LIMITS: dict[str, int]      = _build_category_limits()

from eda.constants import BASIC_CATEGORIES, MEDIUM_CATEGORIES, ANALYSIS_CATEGORIES

_BASIC_TOOLS:    set[str] = _build_tool_set(BASIC_CATEGORIES)
_MEDIUM_TOOLS:   set[str] = _build_tool_set(MEDIUM_CATEGORIES)
_ANALYSIS_TOOLS: set[str] = _build_tool_set(ANALYSIS_CATEGORIES)

_READ_TOOLS = frozenset({"read_design"})
_WRITE_TOOLS = frozenset({"write_design"})

_COUNT_TOOLS = frozenset({
    "read_design", "design_summary", "gate_count_breakdown",
    "count_gate_type", "last_operation_count", "primary_io_counts",
})

_DEPTH_TOOLS = frozenset({
    "read_design", "get_max_depth", "max_fanin_depth", "max_design_depth",
    "deepest_output_cone", "count_outputs_depth_gt", "max_pi_to_dff_depth",
})

_CONE_TOOLS = frozenset({
    "read_design", "report_cone_size", "cone_gate_breakdown",
    "transitive_fanin", "transitive_fanout", "report_large_cones",
    "largest_output_cone", "shared_fanin_cones",
})

_FANOUT_TOOLS = frozenset({
    "read_design", "get_fanout", "max_fanout", "highest_fanout_input",
    "list_direct_loads", "transitive_fanout", "list_flipflops_by_clock",
})

_PATH_TOOLS = frozenset({
    "read_design", "find_path", "list_paths", "all_paths_through",
    "list_register_to_register_paths", "direct_pi_po_connections",
})

_GATE_TOOLS = frozenset({
    "read_design", "gate_info", "list_gates_by_type",
    "report_constant_input_gates", "count_gate_type",
    "gate_count_breakdown",
})

_GATE_COUNT_TOOLS = frozenset({
    "read_design", "count_gate_type", "gate_count_breakdown",
    "last_operation_count",
})

_GATE_BREAKDOWN_TOOLS = frozenset({
    "read_design", "gate_count_breakdown",
})

_GATE_TYPE_COUNT_TOOLS = frozenset({
    "read_design", "count_gate_type",
})

_LAST_COUNT_TOOLS = frozenset({
    "read_design", "last_operation_count", "count_gate_type",
})

_GATE_LIST_TOOLS = frozenset({
    "read_design", "list_gates_by_type", "gate_info", "count_gate_type",
})

_CONST_REPORT_TOOLS = frozenset({
    "read_design", "report_constant_input_gates", "count_gate_type",
})

_SUMMARY_TOOLS = frozenset({
    "read_design", "design_summary", "gate_count_breakdown",
    "primary_io_counts", "largest_output_cone", "deepest_output_cone",
})

_POST_CLEANUP_REPORT_TOOLS = frozenset({
    "read_design", "max_design_depth", "gate_count_breakdown",
})

_CONE_SIZE_TOOLS = frozenset({
    "read_design", "largest_output_cone", "report_cone_size",
    "cone_gate_breakdown", "report_large_cones",
})

_CONE_COUNT_TOOLS = frozenset({
    "read_design", "report_cone_size", "cone_gate_breakdown",
})

_CONE_LARGE_TOOLS = frozenset({
    "read_design", "report_large_cones", "largest_output_cone",
})

_CONE_LIST_TOOLS = frozenset({
    "read_design", "transitive_fanin", "transitive_fanout",
    "shared_fanin_cones",
})

_PATH_EXISTS_TOOLS = frozenset({
    "read_design", "find_path",
})

_PATH_LIST_TOOLS = frozenset({
    "read_design", "list_paths",
})

_PATH_THROUGH_TOOLS = frozenset({
    "read_design", "find_path", "all_paths_through",
})

_REG_PATH_TOOLS = frozenset({
    "read_design", "list_register_to_register_paths",
})

_DEPTH_BETWEEN_TOOLS = frozenset({
    "read_design", "get_max_depth",
})

_DEPTH_OUTPUT_TOOLS = frozenset({
    "read_design", "max_fanin_depth",
})

_DEPTH_DESIGN_TOOLS = frozenset({
    "read_design", "max_design_depth", "deepest_output_cone",
})

_DEPTH_THRESHOLD_TOOLS = frozenset({
    "read_design", "count_outputs_depth_gt",
})

_DEPTH_PI_DFF_TOOLS = frozenset({
    "read_design", "max_pi_to_dff_depth",
})

_FANOUT_DIRECT_TOOLS = frozenset({
    "read_design", "get_fanout", "list_direct_loads",
})

_FANOUT_MAX_TOOLS = frozenset({
    "read_design", "max_fanout", "highest_fanout_input",
})

_FANOUT_TRANSITIVE_TOOLS = frozenset({
    "read_design", "transitive_fanout",
})

_IO_TOOLS = frozenset({
    "read_design", "design_summary", "primary_io_counts",
    "list_primary_inputs_with_widths", "list_primary_outputs_with_widths",
    "direct_pi_po_connections",
})

_IO_COUNT_TOOLS = frozenset({
    "read_design", "primary_io_counts",
})

_PI_WIDTH_TOOLS = frozenset({
    "read_design", "list_primary_inputs_with_widths",
})

_PO_WIDTH_TOOLS = frozenset({
    "read_design", "list_primary_outputs_with_widths",
})

_PI_PO_TOOLS = frozenset({
    "read_design", "direct_pi_po_connections", "primary_io_counts",
})

_CLOCK_DOMAIN_TOOLS = frozenset({
    "read_design", "same_clock_domain", "list_flipflops_by_clock",
})

_DFF_CLOCK_LIST_TOOLS = frozenset({
    "read_design", "list_flipflops_by_clock",
})

_ARTICULATION_TOOLS = frozenset({
    "read_design", "articulation_points_between", "is_cut_between_pi_po",
})

_RENAME_TOOLS = frozenset({
    "read_design", "rename", "gate_info", "list_direct_loads",
})

_VERIFY_TOOLS = frozenset({
    "read_design", "check_equiv", "check_original_equiv",
    "verify_assertion", "internal_signals_equiv", "check_signal_symmetry",
    "is_signal_constant",
})

_DESIGN_EQUIV_TOOLS = frozenset({
    "read_design", "check_original_equiv", "check_equiv",
})

_SIGNAL_EQUIV_TOOLS = frozenset({
    "read_design", "internal_signals_equiv",
})

_ASSERTION_TOOLS = frozenset({
    "read_design", "is_signal_constant", "verify_assertion",
})

_BOOLEAN_EXPR_TOOLS = frozenset({
    "read_design", "boolean_expression",
})

_MISC_TOOLS = frozenset({
    "read_design", "check_signal_symmetry", "report_floating_signals",
    "report_dff_enable_hold", "boolean_expression", "gate_info",
    "list_flipflops_by_clock",
})

_CONST_CLEANUP_TOOLS = frozenset({
    "read_design", "simplify_constant_gates", "report_constant_input_gates",
    "remove_dangling", "last_operation_count", "gate_count_breakdown",
    "count_gate_type", "check_original_equiv",
})

_BUFFER_TOOLS = frozenset({
    "read_design", "buffer_high_fanout", "buffer_all_high_fanout",
    "buffer_each_load", "add_balance_buffers", "get_fanout", "max_fanout",
    "list_direct_loads", "highest_fanout_input", "last_operation_count",
    "check_original_equiv",
})

_BUFFER_ALL_TOOLS = frozenset({
    "read_design", "buffer_all_high_fanout", "max_fanout",
    "last_operation_count", "check_original_equiv",
})

_BUFFER_EACH_TOOLS = frozenset({
    "read_design", "buffer_each_load", "list_direct_loads",
    "last_operation_count", "check_original_equiv",
})

_BUFFER_BALANCE_TOOLS = frozenset({
    "read_design", "add_balance_buffers", "last_operation_count",
    "check_original_equiv",
})

_BUFFER_NET_TOOLS = frozenset({
    "read_design", "buffer_high_fanout", "get_fanout", "max_fanout",
    "last_operation_count", "check_original_equiv",
})

_REPLACE_REMAP_TOOLS = frozenset({
    "read_design", "replace_in_cone", "replace_globally",
    "replace_xor_with_nand", "replace_xnor_with_nor",
    "replace_or_with_nand_not", "remap_design", "optimize_cone",
    "remove_dangling", "gate_count_breakdown", "count_gate_type",
    "last_operation_count", "check_original_equiv",
})

_XOR_REPLACE_TOOLS = frozenset({
    "read_design", "replace_xor_with_nand", "last_operation_count",
    "count_gate_type", "check_original_equiv",
})

_XNOR_REPLACE_TOOLS = frozenset({
    "read_design", "replace_xnor_with_nor", "last_operation_count",
    "count_gate_type", "check_original_equiv",
})

_OR_CONE_REPLACE_TOOLS = frozenset({
    "read_design", "replace_or_with_nand_not", "last_operation_count",
    "gate_count_breakdown", "check_original_equiv",
})

_REMAP_TOOLS = frozenset({
    "read_design", "remap_design", "gate_count_breakdown",
    "check_original_equiv",
})

_CONE_RESTRUCTURE_TOOLS = frozenset({
    "read_design", "optimize_cone", "replace_in_cone",
    "gate_count_breakdown", "check_original_equiv",
})

_DEPTH_OPT_TOOLS = frozenset({
    "read_design", "optimize_design_depth", "optimize_cone",
    "max_design_depth", "max_fanin_depth", "get_max_depth",
    "deepest_output_cone", "count_outputs_depth_gt",
    "gate_count_breakdown", "check_original_equiv",
})

_DEPTH_REDUCE_TOOLS = frozenset({
    "read_design", "optimize_design_depth", "max_design_depth",
    "gate_count_breakdown", "check_original_equiv",
})

_DEPTH_CONE_OPT_TOOLS = frozenset({
    "read_design", "optimize_cone", "max_fanin_depth",
    "gate_count_breakdown", "check_original_equiv",
})

_STRUCT_CLEANUP_TOOLS = frozenset({
    "read_design", "structural_duplicate_merge", "remove_dangling",
    "fuse_not_buf", "collapse_not_not", "simplify_constant_gates",
    "last_operation_count", "gate_count_breakdown", "check_original_equiv",
})

_DANGLING_CLEANUP_TOOLS = frozenset({
    "read_design", "remove_dangling", "last_operation_count",
    "gate_count_breakdown", "check_original_equiv",
})

_NOT_NOT_CLEANUP_TOOLS = frozenset({
    "read_design", "collapse_not_not", "last_operation_count",
    "gate_count_breakdown", "check_original_equiv",
})

_DUP_CLEANUP_TOOLS = frozenset({
    "read_design", "structural_duplicate_merge", "last_operation_count",
    "gate_count_breakdown", "check_original_equiv",
})

_INSERT_RECONNECT_TOOLS = frozenset({
    "read_design", "insert_gate_before", "try_reconnect_input_pin",
    "gate_info", "list_direct_loads", "internal_signals_equiv",
    "check_original_equiv",
})



def _build_provider_tools(specs: list[dict], provider: str) -> list[dict]:
    if provider == "openai":
        return _openai_tools(specs)
    elif provider == "anthropic":
        return _anthropic_tools(specs)
    raise ValueError(f"Unknown provider: {provider!r}")


# Keyed by (provider, tier): "openai_full", "anthropic_basic", etc.
_TOOL_CACHE: dict[str, list[dict]] = {}

def _cached_tools(provider: str, tier: str, specs: list[dict]) -> list[dict]:
    key = f"{provider}_{tier}"
    if key not in _TOOL_CACHE:
        _TOOL_CACHE[key] = _build_provider_tools(specs, provider)
    return _TOOL_CACHE[key]


def _specs_for_names(names: frozenset[str]) -> list[dict]:
    return [t for t in TOOL_SPECS if t["name"] in names]


def _cached_named_tools(provider: str, tier: str, names: frozenset[str]) -> list[dict]:
    return _cached_tools(provider, tier, _specs_for_names(names))



_TRANSFORM_KEYWORDS: tuple[str, ...] = (
    "transform", "replace", "convert", "insert", "buffer",
    "remove", "delete", "prune", "merge", "collapse", "fuse",
    "simplif", "propagat",
    "remap", "restructure", "decompose", "optimiz", "reduce",
    "rewrite", "reconstruct", "cleanup", "rename",
    "write the current design", "write out",
    "reconnect",
    "eliminate", "dangling", "unused",
    "minimi depth", "minimi gate",
)

_STRUCTURAL_KEYWORDS: tuple[str, ...] = (
    "articulation", "cut between", "structural",
    "boolean expression", "boolean equation", "boolean function", "logic expression",
    "equivalent", "equiv", "symmetr",
    "depend on", "dependency",
    "shared between", "shared fanin",
    "nand pair",
    "enable", "hold structure",
    "floating", "unconnected",
    "clock domain", "same clock",
)

_MISC_KEYWORDS: tuple[str, ...] = (
    "symmetr", "floating", "unconnected",
    "enable or hold", "enable/hold", "hold structure",
)

_BROAD_ANALYSIS_KEYWORDS: tuple[str, ...] = (
    "analyze", "analyse", "analysis", "inspect the design",
    "diagnose", "investigate",
)

_VERIFY_KEYWORDS: tuple[str, ...] = (
    "verify", "equivalent", "equiv", "identical",
    "assertion", "always 0", "always 1",
)

_SIGNAL_EQUIV_KEYWORDS: tuple[str, ...] = (
    "same local function", "structurally identical",
    "functionally identical", "internal signals", "same function",
    "functionally equivalent",
)

_DESIGN_EQUIV_KEYWORDS: tuple[str, ...] = (
    "pre-transformation", "pre transformation", "original netlist",
    "original design", "transformed design is equivalent",
)

_DEPTH_KEYWORDS: tuple[str, ...] = (
    "depth", "deepest", "critical path", "longest combinational",
)

_CONE_KEYWORDS: tuple[str, ...] = (
    "fanin cone", "logic cone", "output cone",
    "large cone", "largest cone", "shared fanin",
    "fanin logic", "amount of fanin", "how large is that cone",
)

_FANOUT_KEYWORDS: tuple[str, ...] = (
    "fanout", "direct loads", "driven by",
    "drives directly", "driven directly",
    "reachable from", "immediate successors", "connected to the output of",
)

_PATH_KEYWORDS: tuple[str, ...] = (
    "path", "paths", "depend on", "dependency",
    "direct wire connections", "pi to po", "pi->po",
    "pi-to-po", "primary-input to primary-output",
)

_GATE_KEYWORDS: tuple[str, ...] = (
    "gate type", "what type of gate", "list all",
    "constant input", "constant-driven", "tied 0", "tied 1",
)

_COUNT_KEYWORDS: tuple[str, ...] = (
    "gate count", "count all the gates", "how many gates",
    "how many and", "how many or", "how many not", "how many nand",
    "how many nor", "how many xor", "how many xnor", "how many buf",
    "how many dff", "number of each gate type",
)

_SUMMARY_KEYWORDS: tuple[str, ...] = (
    "summary", "summarize", "summarise", "overview",
    "current design", "module", "cell count", "cells",
    "compact inventory", "inventory of the netlist",
    "total primitive instances", "mix of primitive", "visible input/output",
)

_IO_KEYWORDS: tuple[str, ...] = (
    "primary input", "primary output", "bit width", "bit widths",
)

_RENAME_KEYWORDS: tuple[str, ...] = (
    "rename", "identifier", "update the name",
)

_CONST_CLEANUP_KEYWORDS: tuple[str, ...] = (
    "constant", "constant-driven", "tied 0", "tied 1", "1'b0", "1'b1",
    "simplif", "propagat", "safe local",
)

_BUFFER_KEYWORDS: tuple[str, ...] = (
    "buffer", "fanout", "loads per driver", "load per driver",
    "drives more than", "balance the depth", "balance buffers",
)

_REPLACE_REMAP_KEYWORDS: tuple[str, ...] = (
    "replace", "convert", "decompose", "remap", "restructure", "reconstruct",
    "nand", "nor", "xor", "xnor", "and_not", "nand_not", "or gates",
)

_DEPTH_OPT_KEYWORDS: tuple[str, ...] = (
    "optimiz", "reduce", "depth", "critical path", "levels",
    "maximum path", "minimi depth",
)

_STRUCT_CLEANUP_KEYWORDS: tuple[str, ...] = (
    "dangling", "unused", "prune", "remove", "delete", "cleanup", "merge",
    "duplicate", "redundant", "collapse", "fuse", "back-to-back",
    "do not contribute", "floating nodes", "not affect outputs",
)

_INSERT_RECONNECT_KEYWORDS: tuple[str, ...] = (
    "insert gate", "insert a gate", "insert an", "before matching",
    "reconnect", "input pin",
)

_CLOCK_DOMAIN_KEYWORDS: tuple[str, ...] = (
    "same clock domain", "under the same clock domain", "share clock",
    "same clock",
)

_ARTICULATION_KEYWORDS: tuple[str, ...] = (
    "articulation point", "articulation points", "cut between",
)

_CONST_REPORT_MARKERS: tuple[str, ...] = (
    "report", "list", "find", "which",
)

_READ_MARKERS: tuple[str, ...] = (
    "load", "read", "open", "opening", "bring", "work on",
)

_READ_CONTEXT_MARKERS: tuple[str, ...] = (
    "into memory", "into the system", "current state", "current design",
    "netlist located at", "stored at", "stored in", "from testcase",
)

_WRITE_MARKERS: tuple[str, ...] = (
    "write", "save", "store", "export", "emit",
)

_LAST_COUNT_MARKERS: tuple[str, ...] = (
    "were added", "were inserted", "were removed", "were merged",
    "were converted", "were replaced", "was added", "was removed",
    "were eliminated", "was eliminated", "added by", "removed by",
    "eliminated by", "merged as", "converted by",
    "after the buffer insertion", "by the buffer insertion",
    "constant propagation",
)

_PATH_EXISTS_MARKERS: tuple[str, ...] = (
    "path connecting", "path exist", "path exists",
    "whether a path", "whether any path", "does a path",
    "determine whether a combinational path",
)

_PATH_LIST_MARKERS: tuple[str, ...] = (
    "list every path", "find all combinational paths",
    "complete enumeration of paths", "enumerate paths",
    "enumeration of paths", "all paths from",
)

_DEPTH_QUERY_MARKERS: tuple[str, ...] = (
    "compute", "calculate", "what is", "how many", "determine",
)

_TRANSFORM_ACTION_MARKERS: tuple[str, ...] = (
    "replace", "convert", "insert", "buffer", "remove", "prune",
    "merge", "collapse", "fuse", "simplif", "propagat", "remap",
    "restructure", "optimiz", "reduce", "rewrite", "cleanup",
    "reconnect", "eliminate",
)


def _is_transform_request(text: str) -> bool:
    low = text.lower()
    for kw in _TRANSFORM_KEYWORDS:
        if kw in low:
            return True
    return False


def _is_structural_request(text: str) -> bool:
    low = text.lower()
    for kw in _STRUCTURAL_KEYWORDS:
        if kw in low:
            return True
    return False


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in text for kw in keywords)


def _is_read_request(low: str) -> bool:
    if ".v" not in low:
        return False
    return (
        "load the design from" in low
        or "read the design from" in low
        or ("use " in low and ".v" in low and "design" in low)
        or (
            _has_any(low, _READ_MARKERS)
            and _has_any(low, _READ_CONTEXT_MARKERS)
        )
    )


def _is_write_request(low: str) -> bool:
    if ".v" not in low:
        return False
    return (
        "write the current design" in low
        or "write out" in low
        or ("output file" in low and ".v" in low)
        or ("emit" in low and ".v" in low)
        or _has_any(low, _WRITE_MARKERS)
    )


def _is_design_equiv_request(low: str) -> bool:
    if not _has_any(low, ("equivalent", "equiv")):
        return False
    return (
        _has_any(low, _DESIGN_EQUIV_KEYWORDS)
        or (
            _has_any(low, ("prove", "verify", "check"))
            and _has_any(low, ("transformed design", "pre-transformation", "original"))
        )
    )


def _is_signal_equiv_request(low: str) -> bool:
    return (
        (
            _has_any(low, _SIGNAL_EQUIV_KEYWORDS)
            or ("signal" in low and _has_any(low, ("equivalent", "equiv", "identical")))
            or "produce identical logic values" in low
        )
        and _has_any(low, ("identical", "equivalent", "equiv", "same"))
    )


def _is_const_report_request(low: str) -> bool:
    return (
        _has_any(low, _CONST_REPORT_MARKERS)
        and _has_any(low, ("constant", " const ", "tied 0", "tied 1", "1'b0", "1'b1"))
        and _has_any(low, ("gate", "gates", "and", "or", "nand", "nor", "xor", "xnor"))
    )


def _is_gate_list_request(low: str) -> bool:
    return (
        _has_any(low, ("list", "report all", "show all"))
        and _has_any(low, ("and gate", "or gate", "nand", "nor", "xor", "xnor", "buf", "not gate"))
    )


def _is_pi_po_direct_request(low: str) -> bool:
    return (
        _has_any(low, ("direct", "passthrough", "pass-through", "wire connection"))
        and (
            _has_any(low, ("pi to po", "pi-to-po", "pi->po"))
            or (
                _has_any(low, ("primary input", "primary-input"))
                and _has_any(low, ("primary output", "primary-output"))
            )
        )
    )


def _is_cone_size_request(low: str) -> bool:
    return (
        _has_any(low, ("largest", "largest amount", "how large", "size", "count", "breakdown", "how many gates"))
        and _has_any(low, ("cone", "fanin logic", "amount of fanin"))
    )


def _is_cone_count_request(low: str) -> bool:
    return (
        _has_any(low, ("how many gates", "gate count", "breakdown"))
        and _has_any(low, ("fanin cone", "logic cone", "output cone", "cone of output"))
    )


def _is_cone_large_request(low: str) -> bool:
    return (
        _has_any(low, ("contains more than", "larger than", "greater than"))
        and "cone" in low
    )


def _is_cone_list_request(low: str) -> bool:
    return (
        _has_any(low, ("list", "enumerate", "transitive", "shared fanin", "shared between", "reused by both"))
        and _has_any(low, ("fanin", "cone", "shared fanin"))
    )


def _is_path_exists_request(low: str) -> bool:
    return (
        _has_any(low, _PATH_EXISTS_MARKERS)
        or (
            "verify whether" in low
            and "path" in low
            and _has_any(low, ("avoid", "connecting", "from", "to"))
        )
    )


def _is_path_list_request(low: str) -> bool:
    return _has_any(low, _PATH_LIST_MARKERS)


def _is_path_through_request(low: str) -> bool:
    return (
        "path" in low
        and _has_any(low, ("pass through", "through gate", "all paths through"))
    )


def _is_reg_path_request(low: str) -> bool:
    return (
        _has_any(low, ("register-to-register", "register to register"))
        or ("dff" in low and "path" in low and "dff" in low)
    )


def _is_depth_transform_phrase(low: str) -> bool:
    return _has_any(low, ("reduce", "optimiz", "restructure", "minimi"))


def _is_depth_query(low: str) -> bool:
    return _has_any(low, _DEPTH_KEYWORDS) and not _is_depth_transform_phrase(low)


def _is_depth_threshold_request(low: str) -> bool:
    return _is_depth_query(low) and _has_any(low, ("greater than", "more than", "depth >", "threshold"))


def _is_depth_pi_dff_request(low: str) -> bool:
    return _is_depth_query(low) and "dff" in low


def _is_depth_output_request(low: str) -> bool:
    return _is_depth_query(low) and _has_any(low, ("fanin cone of output", "of output"))


def _is_depth_design_request(low: str) -> bool:
    return (
        _is_depth_query(low)
        and _has_any(low, ("any primary input to any primary output", "entire design", "whole design", "deepest"))
    )


def _is_depth_between_request(low: str) -> bool:
    return (
        _is_depth_query(low)
        and (
            _has_any(low, (" from ", " between "))
            or _has_any(low, _DEPTH_QUERY_MARKERS)
        )
    )


def _is_post_cleanup_report_request(low: str) -> bool:
    return (
        _has_any(low, ("after cleanup", "after the cleanup", "after this cleanup", "after the cleanup pass"))
        and _has_any(low, ("report", "current", "what is", "how many"))
        and (
            _has_any(low, ("depth", "critical path", "maximum combinational"))
            or _has_any(low, ("gate breakdown", "gate count", "total gate"))
        )
    )


def _is_clock_list_request(low: str) -> bool:
    return (
        _has_any(low, ("flip-flop", "flipflop", "flip flops", "dff"))
        and "clock" in low
        and _has_any(low, ("list", "driven by clock", "clock "))
        and "same clock" not in low
    )


def _is_direct_loads_request(low: str) -> bool:
    return (
        _has_any(low, ("direct loads", "drives directly", "directly drives", "driven by", "number of gates driven by", "immediate successors"))
        or ("fanout of" in low and "transitive" not in low and "maximum" not in low)
    )


def _is_max_fanout_request(low: str) -> bool:
    return _has_any(low, ("highest fanout", "max fanout", "maximum fanout", "largest fanout"))


def _is_transitive_fanout_request(low: str) -> bool:
    return _has_any(low, ("transitive fanout", "reachable downstream", "gates reachable from", "reachable from"))


def _is_last_count_request(low: str) -> bool:
    return (
        _has_any(low, _LAST_COUNT_MARKERS)
        or (
            _has_any(low, ("how many", "count"))
            and _has_any(low, ("added", "inserted", "removed", "merged", "converted", "replaced"))
        )
    )


def _is_gate_breakdown_request(low: str) -> bool:
    return (
        _has_any(low, ("count all the gates", "broken down by gate type", "number of each gate type"))
        or _has_any(low, ("total gate count", "total count broken down", "gate count breakdown"))
    )


def _is_gate_type_count_request(low: str) -> bool:
    return (
        _has_any(low, ("how many", "report the total", "currently in the design", "now in the design"))
        and _has_any(low, ("and gate", "or gate", "not gate", "nand", "nor", "xor", "xnor", "buf", "dff"))
    )


def _is_assertion_request(low: str) -> bool:
    return _has_any(low, ("always 0", "always 1", "assertion"))


def _is_boolean_expression_request(low: str) -> bool:
    return _has_any(low, (
        "boolean expression", "boolean equation", "boolean function",
        "logic expression", "derive the boolean", "in terms of its primary inputs",
        "in terms of primary inputs",
    ))


def _is_io_count_request(low: str) -> bool:
    return (
        _has_any(low, ("how many", "number of", "determine the number"))
        and "primary input" in low
        and "primary output" in low
    )


def _is_pi_width_request(low: str) -> bool:
    return "primary input" in low and _has_any(low, ("bit width", "bit widths"))


def _is_po_width_request(low: str) -> bool:
    return "primary output" in low and _has_any(low, ("bit width", "bit widths"))


def _is_buffer_each_request(low: str) -> bool:
    return _has_any(low, ("each load", "per load", "dedicated buffer"))


def _is_buffer_balance_request(low: str) -> bool:
    return _has_any(low, ("balance the depth", "balance buffers", "add buffers to balance"))


def _is_buffer_all_request(low: str) -> bool:
    return (
        _has_any(low, ("wherever needed", "across the netlist", "fanout optimization"))
        or (_has_any(low, ("no gate drives more than", "maximum fanout", "max fanout")) and "buffer" in low)
    )


def _is_buffer_net_request(low: str) -> bool:
    return (
        "buffer" in low
        and _has_any(low, ("clock signal", "reset signal", "signal "))
        and _has_any(low, ("fanout", "loads per driver", "at most"))
    )


def _is_xor_replace_request(low: str) -> bool:
    return "xor" in low and "xnor" not in low and _has_any(low, ("replace", "convert"))


def _is_xnor_replace_request(low: str) -> bool:
    return "xnor" in low and _has_any(low, ("replace", "convert", "rewrite"))


def _is_or_cone_replace_request(low: str) -> bool:
    return "or" in low and "nand" in low and "cone" in low


def _is_remap_request(low: str) -> bool:
    return (
        "remap" in low
        or _has_any(low, ("using only and and not", "only and and not", "and and not gates"))
    )


def _is_cone_restructure_request(low: str) -> bool:
    return (
        ("cone" in low and _has_any(low, ("convert", "restructure", "target depth", "optimiz")))
        or "target depth" in low
    )


def _is_dangling_cleanup_request(low: str) -> bool:
    return _has_any(low, ("dangling", "unused", "do not contribute", "floating nodes", "not affect outputs"))


def _is_not_not_cleanup_request(low: str) -> bool:
    return _has_any(low, ("back-to-back inverter", "not-not", "collapse them into a wire"))


def _is_duplicate_cleanup_request(low: str) -> bool:
    return _has_any(low, ("structural duplicate", "same boolean function", "compute the same"))


def _transform_tools_for_request(low: str, provider: str) -> list[dict]:
    if _has_any(low, _CONST_CLEANUP_KEYWORDS):
        return _cached_named_tools(provider, "const_cleanup", _CONST_CLEANUP_TOOLS)
    if _has_any(low, _BUFFER_KEYWORDS):
        if _is_buffer_each_request(low):
            return _cached_named_tools(provider, "buffer_each", _BUFFER_EACH_TOOLS)
        if _is_buffer_balance_request(low):
            return _cached_named_tools(provider, "buffer_balance", _BUFFER_BALANCE_TOOLS)
        if _is_buffer_all_request(low):
            return _cached_named_tools(provider, "buffer_all", _BUFFER_ALL_TOOLS)
        if _is_buffer_net_request(low):
            return _cached_named_tools(provider, "buffer_net", _BUFFER_NET_TOOLS)
        return _cached_named_tools(provider, "buffer_transform", _BUFFER_TOOLS)
    if _has_any(low, _INSERT_RECONNECT_KEYWORDS):
        return _cached_named_tools(provider, "insert_reconnect", _INSERT_RECONNECT_TOOLS)
    if _has_any(low, _STRUCT_CLEANUP_KEYWORDS):
        if _is_not_not_cleanup_request(low):
            return _cached_named_tools(provider, "not_not_cleanup", _NOT_NOT_CLEANUP_TOOLS)
        if _is_duplicate_cleanup_request(low):
            return _cached_named_tools(provider, "dup_cleanup", _DUP_CLEANUP_TOOLS)
        if _is_dangling_cleanup_request(low):
            return _cached_named_tools(provider, "dangling_cleanup", _DANGLING_CLEANUP_TOOLS)
        return _cached_named_tools(provider, "struct_cleanup", _STRUCT_CLEANUP_TOOLS)
    if _has_any(low, _REPLACE_REMAP_KEYWORDS):
        if _is_xnor_replace_request(low):
            return _cached_named_tools(provider, "xnor_replace", _XNOR_REPLACE_TOOLS)
        if _is_xor_replace_request(low):
            return _cached_named_tools(provider, "xor_replace", _XOR_REPLACE_TOOLS)
        if _is_or_cone_replace_request(low):
            return _cached_named_tools(provider, "or_cone_replace", _OR_CONE_REPLACE_TOOLS)
        if _is_remap_request(low):
            return _cached_named_tools(provider, "remap", _REMAP_TOOLS)
        if _is_cone_restructure_request(low):
            return _cached_named_tools(provider, "cone_restructure", _CONE_RESTRUCTURE_TOOLS)
        return _cached_named_tools(provider, "replace_remap", _REPLACE_REMAP_TOOLS)
    if _has_any(low, _DEPTH_OPT_KEYWORDS):
        if _has_any(low, ("levels deep", "target depth", "at most")) and "design" not in low:
            return _cached_named_tools(provider, "depth_cone_opt", _DEPTH_CONE_OPT_TOOLS)
        if _has_any(low, ("reduce", "critical path", "restructure", "depth")):
            return _cached_named_tools(provider, "depth_reduce", _DEPTH_REDUCE_TOOLS)
        return _cached_named_tools(provider, "depth_opt", _DEPTH_OPT_TOOLS)
    return _cached_tools(provider, "full", TOOL_SPECS)



def _get_tools_for_request_legacy(text: str, provider: str) -> list[dict]:
    """Return tool definitions for the given request text.

    Four tiers:
      - transform  ->full set (67 tools)
      - structural ->medium set (45 tools: basic + structural)
      - analysis   ->analysis set (49 tools: all non-transform)
      - default    ->basic set (37 tools: info queries)
    """
    if _is_transform_request(text):
        return _cached_tools(provider, "full", TOOL_SPECS)
    elif _is_structural_request(text):
        specs = [t for t in TOOL_SPECS if t["name"] in _MEDIUM_TOOLS]
        return _cached_tools(provider, "medium", specs)
    else:
        # Default to basic -safest, sends fewest tools for unknown queries
        specs = [t for t in TOOL_SPECS if t["name"] in _BASIC_TOOLS]
        return _cached_tools(provider, "basic", specs)


def get_tools_for_request(text: str, provider: str) -> list[dict]:
    """Return compact tool definitions for the given request text."""
    low = text.lower()
    if _is_read_request(low):
        return _cached_named_tools(provider, "read", _READ_TOOLS)
    if _is_write_request(low):
        return _cached_named_tools(provider, "write", _WRITE_TOOLS)
    if _has_any(low, _RENAME_KEYWORDS):
        return _cached_named_tools(provider, "rename", _RENAME_TOOLS)
    if _is_design_equiv_request(low):
        return _cached_named_tools(provider, "design_equiv", _DESIGN_EQUIV_TOOLS)
    if _is_signal_equiv_request(low):
        return _cached_named_tools(provider, "signal_equiv", _SIGNAL_EQUIV_TOOLS)
    if _is_const_report_request(low):
        return _cached_named_tools(provider, "const_report", _CONST_REPORT_TOOLS)
    if _is_path_exists_request(low):
        return _cached_named_tools(provider, "path_exists", _PATH_EXISTS_TOOLS)
    if _is_path_through_request(low):
        return _cached_named_tools(provider, "path_through", _PATH_THROUGH_TOOLS)
    if _is_path_list_request(low):
        return _cached_named_tools(provider, "path_list", _PATH_LIST_TOOLS)
    if _is_reg_path_request(low):
        return _cached_named_tools(provider, "reg_path", _REG_PATH_TOOLS)
    if _is_post_cleanup_report_request(low):
        return _cached_named_tools(provider, "post_cleanup_report", _POST_CLEANUP_REPORT_TOOLS)
    if _is_depth_threshold_request(low):
        return _cached_named_tools(provider, "depth_threshold", _DEPTH_THRESHOLD_TOOLS)
    if _is_depth_pi_dff_request(low):
        return _cached_named_tools(provider, "depth_pi_dff", _DEPTH_PI_DFF_TOOLS)
    if _is_depth_output_request(low):
        return _cached_named_tools(provider, "depth_output", _DEPTH_OUTPUT_TOOLS)
    if _is_depth_design_request(low):
        return _cached_named_tools(provider, "depth_design", _DEPTH_DESIGN_TOOLS)
    if _is_depth_between_request(low):
        return _cached_named_tools(provider, "depth_between", _DEPTH_BETWEEN_TOOLS)
    if _is_cone_count_request(low):
        return _cached_named_tools(provider, "cone_count", _CONE_COUNT_TOOLS)
    if _is_cone_large_request(low):
        return _cached_named_tools(provider, "cone_large", _CONE_LARGE_TOOLS)
    if _is_cone_size_request(low):
        return _cached_named_tools(provider, "cone_size", _CONE_SIZE_TOOLS)
    if _is_cone_list_request(low):
        return _cached_named_tools(provider, "cone_list", _CONE_LIST_TOOLS)
    if _is_clock_list_request(low):
        return _cached_named_tools(provider, "dff_clock_list", _DFF_CLOCK_LIST_TOOLS)
    if _is_transitive_fanout_request(low):
        return _cached_named_tools(provider, "fanout_transitive", _FANOUT_TRANSITIVE_TOOLS)
    if _is_max_fanout_request(low):
        return _cached_named_tools(provider, "fanout_max", _FANOUT_MAX_TOOLS)
    if _is_direct_loads_request(low):
        return _cached_named_tools(provider, "fanout_direct", _FANOUT_DIRECT_TOOLS)
    if _is_last_count_request(low):
        return _cached_named_tools(provider, "last_count", _LAST_COUNT_TOOLS)
    if _is_gate_breakdown_request(low):
        return _cached_named_tools(provider, "gate_breakdown", _GATE_BREAKDOWN_TOOLS)
    if _is_gate_type_count_request(low):
        return _cached_named_tools(provider, "gate_type_count", _GATE_TYPE_COUNT_TOOLS)
    if _is_assertion_request(low):
        return _cached_named_tools(provider, "assertion", _ASSERTION_TOOLS)
    if _is_boolean_expression_request(low):
        return _cached_named_tools(provider, "boolean_expr", _BOOLEAN_EXPR_TOOLS)
    if _is_io_count_request(low):
        return _cached_named_tools(provider, "io_count", _IO_COUNT_TOOLS)
    if _is_pi_width_request(low):
        return _cached_named_tools(provider, "pi_widths", _PI_WIDTH_TOOLS)
    if _is_po_width_request(low):
        return _cached_named_tools(provider, "po_widths", _PO_WIDTH_TOOLS)
    if _is_transform_request(text):
        return _transform_tools_for_request(low, provider)
    if _has_any(low, _MISC_KEYWORDS):
        return _cached_named_tools(provider, "misc", _MISC_TOOLS)
    if _has_any(low, _VERIFY_KEYWORDS):
        return _cached_named_tools(provider, "verify", _VERIFY_TOOLS)
    if _has_any(low, _BROAD_ANALYSIS_KEYWORDS):
        specs = [t for t in TOOL_SPECS if t["name"] in _ANALYSIS_TOOLS]
        return _cached_tools(provider, "analysis", specs)
    if _has_any(low, _CLOCK_DOMAIN_KEYWORDS):
        return _cached_named_tools(provider, "clock_domain", _CLOCK_DOMAIN_TOOLS)
    if _has_any(low, _ARTICULATION_KEYWORDS):
        return _cached_named_tools(provider, "articulation", _ARTICULATION_TOOLS)
    if _has_any(low, _DEPTH_KEYWORDS):
        return _cached_named_tools(provider, "depth", _DEPTH_TOOLS)
    if _has_any(low, _CONE_KEYWORDS):
        return _cached_named_tools(provider, "cone", _CONE_TOOLS)
    if _has_any(low, _FANOUT_KEYWORDS):
        return _cached_named_tools(provider, "fanout", _FANOUT_TOOLS)
    if _is_pi_po_direct_request(low):
        return _cached_named_tools(provider, "pi_po", _PI_PO_TOOLS)
    if _has_any(low, _PATH_KEYWORDS):
        return _cached_named_tools(provider, "path", _PATH_TOOLS)
    if _has_any(low, _IO_KEYWORDS):
        return _cached_named_tools(provider, "io", _IO_TOOLS)
    if _has_any(low, _COUNT_KEYWORDS):
        return _cached_named_tools(provider, "gate_count", _GATE_COUNT_TOOLS)
    if _is_gate_list_request(low):
        return _cached_named_tools(provider, "gate_list", _GATE_LIST_TOOLS)
    if _has_any(low, _GATE_KEYWORDS):
        return _cached_named_tools(provider, "gate", _GATE_TOOLS)
    if _has_any(low, _SUMMARY_KEYWORDS):
        return _cached_named_tools(provider, "summary", _SUMMARY_TOOLS)
    if _is_structural_request(text):
        specs = [t for t in TOOL_SPECS if t["name"] in _MEDIUM_TOOLS]
        return _cached_tools(provider, "medium", specs)
    specs = [t for t in TOOL_SPECS if t["name"] in _BASIC_TOOLS]
    return _cached_tools(provider, "basic", specs)


def get_dispatch_map_for_backend_tools() -> dict[str, tuple[str, bool]]:
    return dict(_DISPATCH_MAP)


def get_category_limits() -> dict[str, int]:
    return dict(_TOOL_CATEGORY_LIMITS)



def _build_param_properties(spec: dict) -> dict:
    """Extract parameter properties (no descriptions -saves tokens)."""
    properties = {}
    for param_name, param_info in spec["parameters"].items():
        prop: dict = {"type": param_info.get("type", "string")}
        if "items" in param_info:
            prop["items"] = param_info["items"]
        properties[param_name] = prop
    return properties


def _openai_tools(specs: list[dict]) -> list[dict]:
    """Convert TOOL_SPECS to OpenAI function-calling format."""
    result = []
    for spec in specs:
        properties = _build_param_properties(spec)
        params: dict = {"type": "object", "properties": properties}
        required = spec.get("required")
        if required:
            params["required"] = required
        result.append({
            "type": "function",
            "function": {
                "name":        spec["name"],
                "description": spec["description"],
                "parameters": params,
            },
        })
    return result


def _anthropic_tools(specs: list[dict]) -> list[dict]:
    """Convert TOOL_SPECS to Anthropic tool-use format."""
    result = []
    for spec in specs:
        properties = _build_param_properties(spec)
        schema: dict = {"type": "object", "properties": properties}
        required = spec.get("required")
        if required:
            schema["required"] = required
        result.append({
            "name":         spec["name"],
            "description":  spec["description"],
            "input_schema": schema,
        })
    return result


# Legacy public builders (for external callers that pass a custom spec list)
def openai_tools(specs: list[dict] = TOOL_SPECS) -> list[dict]:
    return _openai_tools(specs)


def anthropic_tools(specs: list[dict] = TOOL_SPECS) -> list[dict]:
    return _anthropic_tools(specs)


def get_tools_for_provider(provider: str) -> list[dict]:
    if provider.lower() == "openai":
        return _openai_tools(TOOL_SPECS)
    elif provider.lower() == "anthropic":
        return _anthropic_tools(TOOL_SPECS)
    else:
        raise ValueError(f"Unknown provider: {provider!r}")



SYSTEM_PROMPT = """EDA netlist assistant. Use tools. Be concise.
Primitives: and/or/nand/nor/xor/xnor (2-in/1-out), not/buf (1-in/1-out), dff.
Call read_design before analysis when no design is loaded. Transform proactively.
For always-0/always-1 questions, use is_signal_constant; DFF initial state is 0.
For complete large lists, use the file path returned by the tool.
"""
