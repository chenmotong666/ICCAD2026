"""
agent/tool_schema.py
====================
LLM tool definitions, system prompt, and tool-subset classification.

Architecture (v2 — consolidated metadata)
------------------------------------------
  TOOL_SPECS        — canonical tool definitions (LLM-facing)
  _TOOL_REGISTRY     — tool_name → {method_name, category, history_limit}
                       One dict to rule them all.  From this single source we
                       derive the dispatch map, history-truncation limits,
                       and three-tier tool subsets — eliminating four manual
                       registries that previously lived in react_agent.py.

To add a new tool:
  1. Add a TOOL_SPECS entry describing the tool to the LLM.
  2. Add a _TOOL_REGISTRY entry with the backend method name, category, and
     history-truncation limit.
  3. Implement the backend method in eda.backend.EDABackend.

(Future: _TOOL_REGISTRY will be auto-generated from @tool decorators on
EDABackend methods, reducing step 2 to the decorator itself.)
"""

from __future__ import annotations

# ── canonical tool specifications ─────────────────────────────────────────────

TOOL_SPECS: list[dict] = [
    # ── I/O ──
    {
        "name": "read_design",
        "description": "Load a gate-level Verilog netlist. Always call first.",
        "parameters": {"path": {"type": "string"}},
        "required": ["path"],
    },
    {
        "name": "write_design",
        "description": "Write current design to a gate-level Verilog file.",
        "parameters": {"path": {"type": "string"}},
        "required": ["path"],
    },
    # ── summary / counts ──
    {
        "name": "design_summary",
        "description": "Module name, port lists, total gate count.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "gate_count_breakdown",
        "description": "Gate count by type: AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF, DFF.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "count_gate_type",
        "description": "Count of one gate type in the design.",
        "parameters": {"gate_type": {"type": "string"}},
        "required": ["gate_type"],
    },
    {
        "name": "last_operation_count",
        "description": "Count from the last transformation.",
        "parameters": {"key": {"type": "string", "description": "buf_added, dangling_removed, nand_added, nor_added, constant_gates_eliminated, merged_gates."}},
        "required": ["key"],
    },
    {
        "name": "primary_io_counts",
        "description": "Number of primary-input and primary-output bits.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "largest_output_cone",
        "description": "Primary output with the largest fanin cone by gate count.",
        "parameters": {},
        "required": [],
    },
    # ── port / IO listing ──
    {
        "name": "list_primary_inputs_with_widths",
        "description": "List primary input port names and bit widths.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "list_primary_outputs_with_widths",
        "description": "List primary output port names and bit widths.",
        "parameters": {},
        "required": [],
    },
    # ── depth ──
    {
        "name": "get_max_depth",
        "description": "Longest combinational path (gate count) from_signal to to_signal.",
        "parameters": {
            "from_signal": {"type": "string"},
            "to_signal":   {"type": "string"},
        },
        "required": ["from_signal", "to_signal"],
    },
    {
        "name": "max_fanin_depth",
        "description": "Maximum combinational depth of output_signal fanin cone.",
        "parameters": {"output_signal": {"type": "string"}},
        "required": ["output_signal"],
    },
    {
        "name": "max_design_depth",
        "description": "Deepest PI-to-PO combinational path in the design.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "optimize_design_depth",
        "description": "Design-wide depth reduction pass preserving functionality.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "deepest_output_cone",
        "description": "Primary output with the greatest fanin combinational depth.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "count_outputs_depth_gt",
        "description": "Count outputs with max fanin depth exceeding threshold.",
        "parameters": {"threshold": {"type": "integer"}},
        "required": ["threshold"],
    },
    {
        "name": "max_pi_to_dff_depth",
        "description": "Maximum combinational depth from any PI to any DFF D-pin.",
        "parameters": {},
        "required": [],
    },
    # ── path queries ──
    {
        "name": "find_path",
        "description": "Find a combinational path from_signal to to_signal, optionally avoiding or via a waypoint.",
        "parameters": {
            "from_signal": {"type": "string"},
            "to_signal":   {"type": "string"},
            "avoid":       {"type": "string"},
            "must_pass":   {"type": "string"},
        },
        "required": ["from_signal", "to_signal"],
    },
    {
        "name": "list_paths",
        "description": "Enumerate combinational paths from_signal to to_signal, capped at max_paths.",
        "parameters": {
            "from_signal": {"type": "string"},
            "to_signal":   {"type": "string"},
            "max_paths":   {"type": "integer"},
        },
        "required": ["from_signal", "to_signal"],
    },
    {
        "name": "list_register_to_register_paths",
        "description": "List register-to-register combinational paths.",
        "parameters": {"limit": {"type": "integer"}},
        "required": [],
    },
    {
        "name": "all_paths_through",
        "description": "Check whether all combinational paths from_signal to to_signal pass through a given signal.",
        "parameters": {
            "from_signal": {"type": "string"},
            "to_signal":   {"type": "string"},
            "through":     {"type": "string", "description": "Signal/cell every path must pass through."},
        },
        "required": ["from_signal", "to_signal", "through"],
    },
    # ── cone / fanin / fanout ──
    {
        "name": "report_cone_size",
        "description": "Gate count in the fanin cone of output_signal.",
        "parameters": {"output_signal": {"type": "string"}},
        "required": ["output_signal"],
    },
    {
        "name": "cone_gate_breakdown",
        "description": "Gate-type counts in the fanin cone of output_signal.",
        "parameters": {"output_signal": {"type": "string"}},
        "required": ["output_signal"],
    },
    {
        "name": "transitive_fanin",
        "description": "List all gates in the transitive fanin cone of output_signal.",
        "parameters": {"output_signal": {"type": "string"}},
        "required": ["output_signal"],
    },
    {
        "name": "transitive_fanout",
        "description": "List all gates reachable (transitive fanout) from input_signal.",
        "parameters": {"input_signal": {"type": "string"}},
        "required": ["input_signal"],
    },
    {
        "name": "get_fanout",
        "description": "Number of direct readers of a net or cell output.",
        "parameters": {"net_name": {"type": "string"}},
        "required": ["net_name"],
    },
    {
        "name": "list_direct_loads",
        "description": "List direct successor gates driven by a net/port/cell.",
        "parameters": {
            "name": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["name"],
    },
    {
        "name": "report_large_cones",
        "description": "List outputs whose fanin cone exceeds threshold gates.",
        "parameters": {"threshold": {"type": "integer"}},
        "required": ["threshold"],
    },
    # ── gate / signal inspection ──
    {
        "name": "gate_info",
        "description": "Gate type, output wire, and input pin connections of a named cell.",
        "parameters": {"name": {"type": "string"}},
        "required": ["name"],
    },
    {
        "name": "list_gates_by_type",
        "description": "List gates of a primitive type (and, or, not, nand, nor, xor, xnor, buf, dff).",
        "parameters": {
            "gate_type": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["gate_type"],
    },
    {
        "name": "report_constant_input_gates",
        "description": "List gates of type with constant-0 or constant-1 input.",
        "parameters": {
            "gate_type":   {"type": "string"},
            "const_value": {"type": "integer", "description": "0 or 1."},
        },
        "required": ["gate_type", "const_value"],
    },
    # ── structural queries ──
    {
        "name": "same_clock_domain",
        "description": "Check whether two DFF instances share the same clock.",
        "parameters": {
            "ff1_name": {"type": "string"},
            "ff2_name": {"type": "string"},
        },
        "required": ["ff1_name", "ff2_name"],
    },
    {
        "name": "shared_fanin_cones",
        "description": "List gates shared between the fanin cones of two outputs.",
        "parameters": {
            "output_a": {"type": "string"},
            "output_b": {"type": "string"},
        },
        "required": ["output_a", "output_b"],
    },
    {
        "name": "direct_pi_po_connections",
        "description": "List direct PI-to-PO wire connections (depth-0 paths).",
        "parameters": {},
        "required": [],
    },
    {
        "name": "is_cut_between_pi_po",
        "description": "Check whether a wire/cell is a structural cut between PIs and POs.",
        "parameters": {"wire_name": {"type": "string"}},
        "required": ["wire_name"],
    },
    {
        "name": "internal_signals_equiv",
        "description": "Check functional equivalence of two internal signals.",
        "parameters": {
            "signal_a": {"type": "string"},
            "signal_b": {"type": "string"},
        },
        "required": ["signal_a", "signal_b"],
    },
    {
        "name": "find_nand_pair_for_signal",
        "description": "Find an existing signal pair (a,b) where NAND(a,b) equals signal_name.",
        "parameters": {
            "signal_name": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["signal_name"],
    },
    {
        "name": "articulation_points_between",
        "description": "Find articulation points in combinational graph between source and target.",
        "parameters": {
            "source": {"type": "string"},
            "target": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["source", "target"],
    },
    {
        "name": "boolean_expression",
        "description": "Return a Boolean expression for signal_name (small-to-medium cones).",
        "parameters": {
            "signal_name": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["signal_name"],
    },
    # ── renaming ──
    {
        "name": "rename",
        "description": "Rename a gate/cell instance or a wire/signal. Auto-detects the target type.",
        "parameters": {
            "old_name": {"type": "string"},
            "new_name": {"type": "string"},
        },
        "required": ["old_name", "new_name"],
    },
    # ── DFF / clock ──
    {
        "name": "list_flipflops_by_clock",
        "description": "List DFF cells driven by a clock signal.",
        "parameters": {
            "clock_name": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["clock_name"],
    },
    {
        "name": "highest_fanout_input",
        "description": "Primary input with the highest direct fanout.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "max_fanout",
        "description": "Maximum fanout in the design or downstream of a signal.",
        "parameters": {"name": {"type": "string"}},
        "required": [],
    },
    # ── transformations ──
    {
        "name": "structural_duplicate_merge",
        "description": "Merge gates with identical type and input drivers.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "insert_gate_before",
        "description": "Insert a gate before cells matching name_pattern; connect extra_input to second input.",
        "parameters": {
            "name_pattern": {"type": "string"},
            "gate_type":    {"type": "string"},
            "extra_input":  {"type": "string"},
        },
        "required": ["name_pattern", "gate_type", "extra_input"],
    },
    {
        "name": "buffer_high_fanout",
        "description": "Insert buffer tree on net_name to limit fanout.",
        "parameters": {
            "net_name":   {"type": "string"},
            "max_fanout": {"type": "integer"},
        },
        "required": ["net_name", "max_fanout"],
    },
    {
        "name": "buffer_all_high_fanout",
        "description": "Insert buffer trees design-wide for max_fanout limit.",
        "parameters": {"max_fanout": {"type": "integer"}},
        "required": ["max_fanout"],
    },
    {
        "name": "buffer_each_load",
        "description": "Insert one dedicated BUF per current load of net_name.",
        "parameters": {"net_name": {"type": "string"}},
        "required": ["net_name"],
    },
    {
        "name": "replace_in_cone",
        "description": "Replace all gates of old_type with new_type in the fanin cone of output_signal.",
        "parameters": {
            "output_signal": {"type": "string"},
            "old_type":      {"type": "string"},
            "new_type":      {"type": "string"},
        },
        "required": ["output_signal", "old_type", "new_type"],
    },
    {
        "name": "replace_globally",
        "description": "Replace all gates of old_type with new_type across the entire design.",
        "parameters": {
            "old_type": {"type": "string"},
            "new_type": {"type": "string"},
        },
        "required": ["old_type", "new_type"],
    },
    {
        "name": "replace_or_with_nand_not",
        "description": "Replace OR gates with equivalent NAND/NOT logic.",
        "parameters": {"output_signal": {"type": "string"}},
        "required": [],
    },
    {
        "name": "replace_xnor_with_nor",
        "description": "Replace XNOR gates with equivalent NOR-only logic.",
        "parameters": {"output_signal": {"type": "string"}},
        "required": [],
    },
    {
        "name": "remap_design",
        "description": "Remap combinational gates to nand_not or and_not style.",
        "parameters": {"style": {"type": "string", "description": "'nand_not' or 'and_not'."}},
        "required": ["style"],
    },
    {
        "name": "remove_dangling",
        "description": "Remove gates/nets not contributing to any primary output.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "fuse_not_buf",
        "description": "Fuse NOT-BUF cascades into single inverter. Not for NOT-NOT pairs.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "collapse_not_not",
        "description": "Collapse back-to-back NOT-NOT inverter pairs into a wire.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "simplify_constant_gates",
        "description": "Propagate constant inputs through all gate types.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "replace_xor_with_nand",
        "description": "Convert every 2-input XOR into 4-NAND implementation.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "add_balance_buffers",
        "description": "Insert BUF chains to equalise depth from from_signal to each sink in to_signals.",
        "parameters": {
            "from_signal": {"type": "string"},
            "to_signals":  {"type": "array", "items": {"type": "string"}},
        },
        "required": ["from_signal", "to_signals"],
    },
    # ── cone optimisation / verification ──
    {
        "name": "optimize_cone",
        "description": "ABC logic optimisation of fanin cone of output_signal with optional depth constraint (min_gates or min_depth).",
        "parameters": {
            "output_signal": {"type": "string"},
            "max_depth":     {"type": "integer"},
            "objective":     {"type": "string", "description": "'min_gates' or 'min_depth'."},
        },
        "required": ["output_signal"],
    },
    {
        "name": "check_equiv",
        "description": "Check functional equivalence between two Verilog files. For current vs original use check_original_equiv.",
        "parameters": {
            "path_a": {"type": "string"},
            "path_b": {"type": "string"},
        },
        "required": ["path_a", "path_b"],
    },
    {
        "name": "check_original_equiv",
        "description": "Check current design is functionally equivalent to the original loaded netlist.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "verify_assertion",
        "description": "Verify signal=1 only when specified signals are 1 and others are 0. Returns PASS or counterexample.",
        "parameters": {
            "signal": {"type": "string", "description": "Signal to check."},
            "when_true_signals": {"type": "array", "items": {"type": "string"}},
            "when_false_signals": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["signal", "when_true_signals", "when_false_signals"],
    },
    # ── misc analysis ──
    {
        "name": "check_signal_symmetry",
        "description": "Check whether signal_name is symmetric with respect to two inputs.",
        "parameters": {
            "signal_name": {"type": "string"},
            "input_a":     {"type": "string"},
            "input_b":     {"type": "string"},
        },
        "required": ["signal_name", "input_a", "input_b"],
    },
    {
        "name": "report_floating_signals",
        "description": "Report unconnected inputs or undriven outputs in the design.",
        "parameters": {"limit": {"type": "integer"}},
        "required": [],
    },
    {
        "name": "report_dff_enable_hold",
        "description": "Report DFF enable/hold structures from feedback paths.",
        "parameters": {"limit": {"type": "integer"}},
        "required": [],
    },
    {
        "name": "try_reconnect_input_pin",
        "description": "Reconnect an input pin of a gate to a different internal signal.",
        "parameters": {
            "gate_name":    {"type": "string"},
            "pin_name":     {"type": "string"},
            "signal_name":  {"type": "string"},
        },
        "required": ["gate_name", "pin_name", "signal_name"],
    },
]


# ── tool registry (single source of truth for dispatch/limits/subsets) ─────
#
# Each entry: {method_name, category, history_limit}
#   method_name   — EDABackend method to call (usually same as tool name)
#   category      — ToolCategory value (used for three-tier subsets)
#   history_limit — max chars to keep in conversation history
#
# When adding a new tool, add one entry here + the TOOL_SPECS entry above.

from eda.constants import ToolCategory as _TC

_TOOL_REGISTRY: dict[str, dict] = {
    # ── I/O ──
    "read_design":               {"method_name": "read_design",               "category": _TC.IO,        "history_limit": 600},
    "write_design":              {"method_name": "write_design",              "category": _TC.IO,        "history_limit": 600},
    # ── summary / counts ──
    "design_summary":            {"method_name": "design_summary",            "category": _TC.SUMMARY,   "history_limit": 600},
    "gate_count_breakdown":      {"method_name": "gate_count_breakdown",      "category": _TC.SUMMARY,   "history_limit": 600},
    "count_gate_type":           {"method_name": "count_gate_type",           "category": _TC.SUMMARY,   "history_limit": 600},
    "last_operation_count":      {"method_name": "last_operation_count",      "category": _TC.SUMMARY,   "history_limit": 600},
    "primary_io_counts":         {"method_name": "primary_io_counts",         "category": _TC.SUMMARY,   "history_limit": 600},
    "largest_output_cone":       {"method_name": "largest_output_cone",       "category": _TC.SUMMARY,   "history_limit": 600},
    "list_primary_inputs_with_widths":  {"method_name": "list_primary_inputs_with_widths",  "category": _TC.SUMMARY, "history_limit": 600},
    "list_primary_outputs_with_widths": {"method_name": "list_primary_outputs_with_widths", "category": _TC.SUMMARY, "history_limit": 600},
    # ── depth ──
    "get_max_depth":             {"method_name": "get_max_depth",             "category": _TC.DEPTH,     "history_limit": 600},
    "max_fanin_depth":           {"method_name": "max_fanin_depth",           "category": _TC.DEPTH,     "history_limit": 600},
    "max_design_depth":          {"method_name": "max_design_depth",          "category": _TC.DEPTH,     "history_limit": 600},
    "optimize_design_depth":     {"method_name": "optimize_design_depth",     "category": _TC.OPTIMIZE,  "history_limit": 1200},
    "deepest_output_cone":       {"method_name": "deepest_output_cone",       "category": _TC.DEPTH,     "history_limit": 600},
    "count_outputs_depth_gt":    {"method_name": "count_outputs_depth_gt",    "category": _TC.DEPTH,     "history_limit": 600},
    "max_pi_to_dff_depth":       {"method_name": "max_pi_to_dff_depth",       "category": _TC.DEPTH,     "history_limit": 600},
    # ── path queries ──
    "find_path":                 {"method_name": "find_path",                 "category": _TC.PATH,      "history_limit": 800},
    "list_paths":                {"method_name": "list_paths",                "category": _TC.PATH,      "history_limit": 800},
    "list_register_to_register_paths": {"method_name": "list_register_to_register_paths", "category": _TC.PATH, "history_limit": 800},
    "all_paths_through":         {"method_name": "all_paths_through",         "category": _TC.PATH,      "history_limit": 800},
    # ── cone / fanin / fanout ──
    "report_cone_size":          {"method_name": "report_cone_size",          "category": _TC.CONE,      "history_limit": 800},
    "cone_gate_breakdown":       {"method_name": "cone_gate_breakdown",       "category": _TC.CONE,      "history_limit": 800},
    "transitive_fanin":          {"method_name": "transitive_fanin",          "category": _TC.CONE,      "history_limit": 800},
    "transitive_fanout":         {"method_name": "transitive_fanout",         "category": _TC.CONE,      "history_limit": 800},
    "get_fanout":                {"method_name": "get_fanout",                "category": _TC.CONE,      "history_limit": 600},
    "list_direct_loads":         {"method_name": "list_direct_loads",         "category": _TC.CONE,      "history_limit": 600},
    "report_large_cones":        {"method_name": "report_large_cones",        "category": _TC.CONE,      "history_limit": 800},
    # ── gate / signal inspection ──
    "gate_info":                 {"method_name": "gate_info",                 "category": _TC.GATE,      "history_limit": 600},
    "list_gates_by_type":        {"method_name": "list_gates_by_type",        "category": _TC.GATE,      "history_limit": 600},
    "report_constant_input_gates": {"method_name": "report_constant_input_gates", "category": _TC.GATE,  "history_limit": 600},
    # ── structural queries ──
    "same_clock_domain":         {"method_name": "same_clock_domain",         "category": _TC.STRUCTURAL, "history_limit": 400},
    "shared_fanin_cones":        {"method_name": "shared_fanin_cones",        "category": _TC.STRUCTURAL, "history_limit": 800},
    "direct_pi_po_connections":  {"method_name": "direct_pi_po_connections",  "category": _TC.STRUCTURAL, "history_limit": 400},
    "is_cut_between_pi_po":     {"method_name": "is_cut_between_pi_po",     "category": _TC.STRUCTURAL, "history_limit": 400},
    "internal_signals_equiv":    {"method_name": "internal_signals_equiv",    "category": _TC.STRUCTURAL, "history_limit": 400},
    "find_nand_pair_for_signal": {"method_name": "find_nand_pair_for_signal", "category": _TC.STRUCTURAL, "history_limit": 600},
    "articulation_points_between": {"method_name": "articulation_points_between", "category": _TC.STRUCTURAL, "history_limit": 800},
    "boolean_expression":        {"method_name": "boolean_expression",        "category": _TC.STRUCTURAL, "history_limit": 600},
    # ── renaming ──
    "rename":                    {"method_name": "rename",                    "category": _TC.RENAME,    "history_limit": 600},
    # ── DFF / clock ──
    "list_flipflops_by_clock":   {"method_name": "list_flipflops_by_clock",   "category": _TC.DFF_CLOCK, "history_limit": 600},
    "highest_fanout_input":      {"method_name": "highest_fanout_input",      "category": _TC.DFF_CLOCK, "history_limit": 600},
    "max_fanout":                {"method_name": "max_fanout",                "category": _TC.DFF_CLOCK, "history_limit": 600},
    # ── transformations ──
    "structural_duplicate_merge": {"method_name": "structural_duplicate_merge", "category": _TC.TRANSFORM, "history_limit": 1200},
    "insert_gate_before":        {"method_name": "insert_gate_before",        "category": _TC.TRANSFORM, "history_limit": 1200},
    "buffer_high_fanout":        {"method_name": "buffer_high_fanout",        "category": _TC.TRANSFORM, "history_limit": 1200},
    "buffer_all_high_fanout":    {"method_name": "buffer_all_high_fanout",    "category": _TC.TRANSFORM, "history_limit": 1200},
    "buffer_each_load":          {"method_name": "buffer_each_load",          "category": _TC.TRANSFORM, "history_limit": 1200},
    "replace_in_cone":           {"method_name": "replace_gate_type_in_cone", "category": _TC.TRANSFORM, "history_limit": 1200},
    "replace_globally":          {"method_name": "replace_gate_type_globally","category": _TC.TRANSFORM, "history_limit": 1200},
    "replace_or_with_nand_not":  {"method_name": "replace_or_with_nand_not",  "category": _TC.TRANSFORM, "history_limit": 1200},
    "replace_xnor_with_nor":     {"method_name": "replace_xnor_with_nor",     "category": _TC.TRANSFORM, "history_limit": 1200},
    "remap_design":              {"method_name": "remap_design",              "category": _TC.TRANSFORM, "history_limit": 1200},
    "remove_dangling":           {"method_name": "remove_dangling",           "category": _TC.TRANSFORM, "history_limit": 1200},
    "fuse_not_buf":              {"method_name": "fuse_not_buf_pairs",        "category": _TC.TRANSFORM, "history_limit": 1200},
    "collapse_not_not":          {"method_name": "collapse_not_not_pairs",    "category": _TC.TRANSFORM, "history_limit": 1200},
    "simplify_constant_gates":   {"method_name": "simplify_constant_gates",   "category": _TC.TRANSFORM, "history_limit": 1200},
    "replace_xor_with_nand":     {"method_name": "replace_xor_with_nand",     "category": _TC.TRANSFORM, "history_limit": 1200},
    "add_balance_buffers":       {"method_name": "add_balance_buffers",       "category": _TC.TRANSFORM, "history_limit": 1200},
    "try_reconnect_input_pin":   {"method_name": "try_reconnect_input_pin",   "category": _TC.TRANSFORM, "history_limit": 600},
    # ── cone optimisation / verification ──
    "optimize_cone":             {"method_name": "optimize_cone",             "category": _TC.OPTIMIZE,  "history_limit": 1200},
    "check_equiv":               {"method_name": "check_equiv",               "category": _TC.VERIFY,    "history_limit": 400},
    "check_original_equiv":      {"method_name": "check_original_equiv",      "category": _TC.VERIFY,    "history_limit": 400},
    "verify_assertion":          {"method_name": "verify_assertion",          "category": _TC.VERIFY,    "history_limit": 600},
    # ── misc analysis ──
    "check_signal_symmetry":     {"method_name": "check_signal_symmetry",     "category": _TC.MISC,      "history_limit": 400},
    "report_floating_signals":   {"method_name": "report_floating_signals",   "category": _TC.MISC,      "history_limit": 600},
    "report_dff_enable_hold":    {"method_name": "report_dff_enable_hold",    "category": _TC.MISC,      "history_limit": 600},
}


# ── derived registries (auto-generated from _TOOL_REGISTRY) ─────────────────

def _build_dispatch_map() -> dict[str, tuple[str, bool]]:
    """Derive dispatch map from _TOOL_REGISTRY.

    Returns {tool_name: (method_name, takes_kwargs)}.
    ``takes_kwargs`` is inferred from the TOOL_SPECS parameters dict.
    """
    spec_map = {s["name"]: s for s in TOOL_SPECS}
    result: dict[str, tuple[str, bool]] = {}
    for tool_name, meta in _TOOL_REGISTRY.items():
        spec = spec_map.get(tool_name, {})
        takes_kwargs = len(spec.get("parameters", {})) > 0
        result[tool_name] = (meta["method_name"], takes_kwargs)
    return result


def _build_category_limits() -> dict[str, int]:
    """Derive per-tool history-truncation limits from _TOOL_REGISTRY."""
    return {name: meta["history_limit"] for name, meta in _TOOL_REGISTRY.items()}


def _build_analysis_only_set() -> set[str]:
    """Tools safe for analysis-only requests (excludes transform/optimize)."""
    from eda.constants import ANALYSIS_CATEGORIES
    return {
        name for name, meta in _TOOL_REGISTRY.items()
        if meta["category"] in ANALYSIS_CATEGORIES
    }


def _build_basic_set() -> set[str]:
    """Tools for basic informational requests."""
    from eda.constants import BASIC_CATEGORIES
    return {
        name for name, meta in _TOOL_REGISTRY.items()
        if meta["category"] in BASIC_CATEGORIES
    }


# Build at import time
_DISPATCH_MAP: dict[str, tuple[str, bool]] = _build_dispatch_map()
_TOOL_CATEGORY_LIMITS: dict[str, int]          = _build_category_limits()
_ANALYSIS_ONLY_TOOLS: set[str]                 = _build_analysis_only_set()
_BASIC_TOOLS: set[str]                         = _build_basic_set()


# ── request classification ────────────────────────────────────────────────────

_TRANSFORM_KEYWORDS: tuple[str, ...] = (
    "transform", "replace", "convert", "insert", "buffer",
    "remove", "prune", "merge", "collapse", "fuse",
    "simplif", "propagat",
    "remap", "restructure", "optimiz",
    "write the current design", "write out",
    "reconnect",
    "eliminate", "dangling", "unused",
    "minimi depth", "minimi gate",
)

_BASIC_KEYWORDS: tuple[str, ...] = (
    "load", "read", "write", "list",
    "gate count", "how many", "total",
    "what is", "what are", "maximum", "deepest", "largest",
    "design summary", "summarize", "summarise", "describe",
    "breakdown", "fanout",
    "primary input", "primary output",
    "which output", "highest",
)


def _is_transform_request(text: str) -> bool:
    """Conservative check: True when the request likely needs transform/optimisation tools."""
    low = text.lower()
    for kw in _TRANSFORM_KEYWORDS:
        if kw in low:
            return True
    return False


def _is_basic_request(text: str) -> bool:
    """True for simple informational requests that only need basic tools."""
    low = text.lower()
    for kw in _BASIC_KEYWORDS:
        if kw in low:
            return True
    return False


# ── public API ─────────────────────────────────────────────────────────────────

def get_tools_for_request(text: str, provider: str) -> list[dict]:
    """Return tool definitions appropriate for the given request text.

    Three tiers:
      - transform/optimisation → full tool set
      - basic informational → ~18 basic tools
      - other analysis → ~50 analysis-only tools
    """
    if _is_transform_request(text):
        specs = TOOL_SPECS
    elif _is_basic_request(text):
        specs = [t for t in TOOL_SPECS if t["name"] in _BASIC_TOOLS]
    else:
        specs = [t for t in TOOL_SPECS if t["name"] in _ANALYSIS_ONLY_TOOLS]
    return _build_tools(specs, provider)


def _build_tools(specs: list[dict], provider: str) -> list[dict]:
    """Build provider-specific tool definitions from canonical specs."""
    if provider == "openai":
        return openai_tools(specs)
    elif provider == "anthropic":
        return anthropic_tools(specs)
    else:
        raise ValueError(f"Unknown provider: {provider!r}")


def get_dispatch_map_for_backend_tools() -> dict[str, tuple[str, bool]]:
    """Return {tool_name: (method_name, takes_kwargs)} for all registered tools.

    This replaces the manually-maintained _DISPATCH_MAP in react_agent.py.
    The agent layer should call this once at import time.
    """
    return dict(_DISPATCH_MAP)


def get_category_limits() -> dict[str, int]:
    """Return {tool_name: history_limit} for all registered tools.

    This replaces the manually-maintained _TOOL_CATEGORY_LIMITS in react_agent.py.
    """
    return dict(_TOOL_CATEGORY_LIMITS)


# ── provider-specific format builders ─────────────────────────────────────────

def _build_param_properties(spec: dict) -> dict:
    """Extract parameter properties from a TOOL_SPECS entry (shared by both formats)."""
    properties = {}
    for param_name, param_info in spec["parameters"].items():
        prop: dict = {"type": param_info.get("type", "string")}
        if "description" in param_info:
            prop["description"] = param_info["description"]
        if "items" in param_info:
            prop["items"] = param_info["items"]
        properties[param_name] = prop
    return properties


def openai_tools(specs: list[dict] = TOOL_SPECS) -> list[dict]:
    """Convert TOOL_SPECS to OpenAI function-calling format."""
    result = []
    for spec in specs:
        properties = _build_param_properties(spec)
        params: dict = {"type": "object", "properties": properties}
        if spec.get("required"):
            params["required"] = spec["required"]
        result.append({
            "type": "function",
            "function": {
                "name":        spec["name"],
                "description": spec["description"],
                "parameters": params,
            },
        })
    return result


def anthropic_tools(specs: list[dict] = TOOL_SPECS) -> list[dict]:
    """Convert TOOL_SPECS to Anthropic tool-use format."""
    result = []
    for spec in specs:
        properties = _build_param_properties(spec)
        schema: dict = {"type": "object", "properties": properties}
        if spec.get("required"):
            schema["required"] = spec["required"]
        result.append({
            "name":         spec["name"],
            "description":  spec["description"],
            "input_schema": schema,
        })
    return result


def get_tools_for_provider(provider: str) -> list[dict]:
    if provider.lower() == "openai":
        return openai_tools()
    elif provider.lower() == "anthropic":
        return anthropic_tools()
    else:
        raise ValueError(f"Unknown provider: {provider!r}")


# ── system prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """EDA netlist assistant. Use tools. Be concise.
Primitives: and/or/nand/nor/xor/xnor (2-in/1-out), not/buf (1-in/1-out), dff.
Call read_design first. Transform proactively.
"""
