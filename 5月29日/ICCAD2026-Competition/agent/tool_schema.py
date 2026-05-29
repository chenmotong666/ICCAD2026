"""
agent/tool_schema.py
====================
LLM tool definitions and the system prompt injected at the start of every
conversation turn.

The tool definitions are emitted in both OpenAI and Anthropic formats because
the two APIs use slightly different schemas.  The canonical definition is the
TOOL_SPECS list; the two format functions derive provider-specific lists from it.

Tool call → EDABackend method mapping
--------------------------------------
  read_design           → backend.read_design(path)
  write_design          → backend.write_design(path)
  design_summary        → backend.design_summary()
  get_max_depth         → backend.get_max_depth(from_signal, to_signal)
  find_path             → backend.find_path(from_signal, to_signal, avoid, must_pass)
  all_paths_through     → backend.all_paths_through(from_signal, to_signal, through)
  report_cone_size      → backend.report_cone_size(output_signal)
  get_fanout            → backend.get_fanout(net_name)
  report_large_cones    → backend.report_large_cones(threshold)
  same_clock_domain     → backend.same_clock_domain(ff1_name, ff2_name)
  insert_gate_before    → backend.insert_gate_before(name_pattern, gate_type, extra_input)
  buffer_high_fanout    → backend.buffer_high_fanout(net_name, max_fanout)
  replace_in_cone       → backend.replace_gate_type_in_cone(output_signal, old_type, new_type)
  replace_globally      → backend.replace_gate_type_globally(old_type, new_type)
  remove_dangling       → backend.remove_dangling()
  fuse_not_buf          → backend.fuse_not_buf_pairs()
  add_balance_buffers   → backend.add_balance_buffers(from_signal, to_signals)
  optimize_cone         → backend.optimize_cone(output_signal, max_depth, objective)
  check_equiv           → backend.check_equiv(path_a, path_b)
"""

from __future__ import annotations

# ── canonical tool specifications ─────────────────────────────────────────────

TOOL_SPECS: list[dict] = [
    # ── I/O ──
    {
        "name": "read_design",
        "description": "Load a gate-level Verilog file into the internal design state. Call this first before any analysis or transformation.",
        "parameters": {"path": {"type": "string"}},
        "required": ["path"],
    },
    {
        "name": "write_design",
        "description": "Write the current design to a gate-level Verilog file.",
        "parameters": {"path": {"type": "string"}},
        "required": ["path"],
    },
    # ── summary / counts ──
    {
        "name": "design_summary",
        "description": "Summarise current design: module name, port lists, gate count.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "gate_count_breakdown",
        "description": "Total gate count broken down by AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF, DFF.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "count_gate_type",
        "description": "Count of one primitive gate type in the current design.",
        "parameters": {"gate_type": {"type": "string"}},
        "required": ["gate_type"],
    },
    {
        "name": "last_operation_count",
        "description": "Saved count from the most recent transformation. Keys: buf_added, nand_added, nor_added, dangling_removed, constant_gates_eliminated, merged_gates.",
        "parameters": {"key": {"type": "string", "description": "buf_added, dangling_removed, nand_added, nor_added, constant_gates_eliminated, merged_gates."}},
        "required": ["key"],
    },
    {
        "name": "primary_io_counts",
        "description": "Number of primary-input bits and primary-output bits.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "largest_output_cone",
        "description": "Report the primary output with the largest fanin cone by gate count.",
        "parameters": {},
        "required": [],
    },
    # ── port / IO listing ──
    {
        "name": "list_primary_inputs_with_widths",
        "description": "List primary input names and bit widths.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "list_primary_outputs_with_widths",
        "description": "List primary output names and bit widths.",
        "parameters": {},
        "required": [],
    },
    # ── depth ──
    {
        "name": "get_max_depth",
        "description": "Longest combinational path (gate levels) from from_signal to to_signal, with example path.",
        "parameters": {
            "from_signal": {"type": "string"},
            "to_signal":   {"type": "string"},
        },
        "required": ["from_signal", "to_signal"],
    },
    {
        "name": "max_fanin_depth",
        "description": "Maximum combinational depth of the fanin cone of output_signal.",
        "parameters": {"output_signal": {"type": "string"}},
        "required": ["output_signal"],
    },
    {
        "name": "max_design_depth",
        "description": "Deepest combinational path from any PI to any PO in the whole design.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "optimize_design_depth",
        "description": "Whole-design depth reduction pass while preserving functionality. Use this for design-wide depth optimization.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "deepest_output_cone",
        "description": "Primary output whose fanin cone has the greatest combinational depth.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "count_outputs_depth_gt",
        "description": "Count primary outputs whose max fanin depth exceeds threshold.",
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
        "description": "Find a combinational path from from_signal to to_signal, optionally avoiding or requiring waypoints.",
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
        "description": "Enumerate combinational paths from from_signal to to_signal, capped by max_paths (default 100).",
        "parameters": {
            "from_signal": {"type": "string"},
            "to_signal":   {"type": "string"},
            "max_paths":   {"type": "integer"},
        },
        "required": ["from_signal", "to_signal"],
    },
    {
        "name": "list_register_to_register_paths",
        "description": "List register-to-register combinational paths, capped by limit (default 80).",
        "parameters": {"limit": {"type": "integer"}},
        "required": [],
    },
    {
        "name": "all_paths_through",
        "description": "Check whether ALL combinational paths from from_signal to to_signal pass through a given signal/cell.",
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
        "description": "Gate count in the combinational fanin cone of output_signal.",
        "parameters": {"output_signal": {"type": "string"}},
        "required": ["output_signal"],
    },
    {
        "name": "cone_gate_breakdown",
        "description": "Gate-type counts within the fanin cone of output_signal.",
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
        "description": "List direct successor gates driven by a net, port, or cell output.",
        "parameters": {
            "name": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["name"],
    },
    {
        "name": "report_large_cones",
        "description": "List primary outputs whose fanin cone exceeds threshold gates.",
        "parameters": {"threshold": {"type": "integer"}},
        "required": ["threshold"],
    },
    # ── gate / signal inspection ──
    {
        "name": "gate_info",
        "description": "Report gate type, output wire, and input pin connections for a named cell.",
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
        "description": "Report gates of gate_type that have a constant-0 or constant-1 input.",
        "parameters": {
            "gate_type":   {"type": "string"},
            "const_value": {"type": "integer", "description": "0 or 1."},
        },
        "required": ["gate_type", "const_value"],
    },
    {
        "name": "immediate_successors",
        "description": "List immediate successor gates driven by a signal or port.",
        "parameters": {"name": {"type": "string"}},
        "required": ["name"],
    },
    # ── structural queries ──
    {
        "name": "same_clock_domain",
        "description": "Check whether two DFF instances share the same clock domain.",
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
        "description": "List direct primary-input to primary-output wire connections (depth-0 paths).",
        "parameters": {},
        "required": [],
    },
    {
        "name": "is_cut_between_pi_po",
        "description": "Check whether a wire/cell is a cut between any PI and any PO.",
        "parameters": {"wire_name": {"type": "string"}},
        "required": ["wire_name"],
    },
    {
        "name": "internal_signals_equiv",
        "description": "Check functional equivalence of two internal signals (structural + simulation).",
        "parameters": {
            "signal_a": {"type": "string"},
            "signal_b": {"type": "string"},
        },
        "required": ["signal_a", "signal_b"],
    },
    {
        "name": "find_nand_pair_for_signal",
        "description": "Search for an existing signal pair (a, b) whose NAND(a, b) is equivalent to signal_name.",
        "parameters": {
            "signal_name": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["signal_name"],
    },
    {
        "name": "articulation_points_between",
        "description": "Find articulation points in the combinational graph between source and target.",
        "parameters": {
            "source": {"type": "string"},
            "target": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["source", "target"],
    },
    {
        "name": "boolean_expression",
        "description": "Return a compact Boolean expression for signal_name (small-to-medium cones only).",
        "parameters": {
            "signal_name": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["signal_name"],
    },
    # ── renaming ──
    {
        "name": "rename_gate",
        "description": "Rename a gate/cell instance; preserve connectivity and all references.",
        "parameters": {
            "old_name": {"type": "string"},
            "new_name": {"type": "string"},
        },
        "required": ["old_name", "new_name"],
    },
    {
        "name": "rename_wire",
        "description": "Rename a driven wire/signal and update all references.",
        "parameters": {
            "old_name": {"type": "string"},
            "new_name": {"type": "string"},
        },
        "required": ["old_name", "new_name"],
    },
    # ── DFF / clock ──
    {
        "name": "list_flipflops_by_clock",
        "description": "List DFF cells directly driven by a clock signal.",
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
        "description": "Maximum fanout in the design, or downstream of a named signal.",
        "parameters": {"name": {"type": "string"}},
        "required": [],
    },
    # ── transformations ──
    {
        "name": "structural_duplicate_merge",
        "description": "Merge gates that have identical primitive type and identical input drivers.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "insert_gate_before",
        "description": "Insert a gate before every cell whose instance name contains name_pattern; connect extra_input to its second input.",
        "parameters": {
            "name_pattern": {"type": "string"},
            "gate_type":    {"type": "string"},
            "extra_input":  {"type": "string"},
        },
        "required": ["name_pattern", "gate_type", "extra_input"],
    },
    {
        "name": "buffer_high_fanout",
        "description": "Insert a buffer tree on net_name so no driver exceeds max_fanout loads.",
        "parameters": {
            "net_name":   {"type": "string"},
            "max_fanout": {"type": "integer"},
        },
        "required": ["net_name", "max_fanout"],
    },
    {
        "name": "buffer_all_high_fanout",
        "description": "Insert buffer trees design-wide so every driver has fanout <= max_fanout. Use this for design-wide fanout limits.",
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
        "description": "Replace all gates of old_type with new_type inside the fanin cone of output_signal.",
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
        "description": "Replace OR gates with equivalent NAND/NOT logic in a cone or whole design.",
        "parameters": {"output_signal": {"type": "string"}},
        "required": [],
    },
    {
        "name": "replace_xnor_with_nor",
        "description": "Replace XNOR gates with equivalent NOR-only implementations in a cone or whole design.",
        "parameters": {"output_signal": {"type": "string"}},
        "required": [],
    },
    {
        "name": "remap_design",
        "description": "Remap combinational gates to a target style: 'nand_not' or 'and_not'.",
        "parameters": {"style": {"type": "string", "description": "'nand_not' or 'and_not'."}},
        "required": ["style"],
    },
    {
        "name": "remove_dangling",
        "description": "Remove all gates/nets that do not contribute to any primary output. Use this to eliminate unused logic, prune dead gates, or cleanup dangling nets.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "fuse_not_buf",
        "description": "Fuse NOT-followed-by-BUF cascades into a single inverter. Do NOT use for NOT→NOT (back-to-back inverter) pairs.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "collapse_not_not",
        "description": "Collapse back-to-back inverter (NOT→NOT) pairs into a wire. Use this to eliminate double-inverter chains.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "simplify_constant_gates",
        "description": "Propagate constant inputs through AND/OR/NAND/NOR/XOR/XNOR/NOT/BUF gates. Use report_constant_input_gates first; check result with last_operation_count('constant_gates_eliminated').",
        "parameters": {},
        "required": [],
    },
    {
        "name": "replace_xor_with_nand",
        "description": "Convert every 2-input XOR into an equivalent 4-NAND implementation.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "add_balance_buffers",
        "description": "Insert BUF chains to equalise combinational depth from from_signal to each sink in to_signals.",
        "parameters": {
            "from_signal": {"type": "string"},
            "to_signals":  {"type": "array", "items": {"type": "string"}},
        },
        "required": ["from_signal", "to_signals"],
    },
    # ── cone optimisation / verification ──
    {
        "name": "optimize_cone",
        "description": "ABC logic optimisation of the fanin cone of output_signal, with optional depth constraint and objective (min_gates or min_depth). Verify constraint is met after optimization.",
        "parameters": {
            "output_signal": {"type": "string"},
            "max_depth":     {"type": "integer"},
            "objective":     {"type": "string", "description": "'min_gates' or 'min_depth'."},
        },
        "required": ["output_signal"],
    },
    {
        "name": "check_equiv",
        "description": "Check functional equivalence between two Verilog files via Yosys. For current vs original loaded design use check_original_equiv instead.",
        "parameters": {
            "path_a": {"type": "string"},
            "path_b": {"type": "string"},
        },
        "required": ["path_a", "path_b"],
    },
    {
        "name": "check_original_equiv",
        "description": "Check that the current in-memory design is functionally equivalent to the original loaded netlist.",
        "parameters": {},
        "required": [],
    },
    # ── misc analysis ──
    {
        "name": "check_signal_symmetry",
        "description": "Check whether signal_name is symmetric with respect to two named inputs.",
        "parameters": {
            "signal_name": {"type": "string"},
            "input_a":     {"type": "string"},
            "input_b":     {"type": "string"},
        },
        "required": ["signal_name", "input_a", "input_b"],
    },
    {
        "name": "report_floating_signals",
        "description": "Report unconnected inputs or undriven output ports in the design.",
        "parameters": {"limit": {"type": "integer"}},
        "required": [],
    },
    {
        "name": "report_dff_enable_hold",
        "description": "Report enable/hold structures inferred from DFF feedback paths.",
        "parameters": {"limit": {"type": "integer"}},
        "required": [],
    },
    {
        "name": "try_reconnect_input_pin",
        "description": "Reconnect an input pin of a named gate to a different internal signal.",
        "parameters": {
            "gate_name":    {"type": "string"},
            "pin_name":     {"type": "string"},
            "signal_name":  {"type": "string"},
        },
        "required": ["gate_name", "pin_name", "signal_name"],
    },
]


# ── conservative tool-subset classification ──────────────────────────────────

# Tools that are safe for analysis-only requests (excludes heavy transforms
# and optimisation tools).  When in doubt the agent sends the full set.
_ANALYSIS_ONLY_TOOLS: set[str] = {
    "read_design", "write_design", "design_summary",
    "gate_count_breakdown", "count_gate_type", "last_operation_count",
    "primary_io_counts", "list_primary_inputs_with_widths", "list_primary_outputs_with_widths",
    "largest_output_cone",
    "get_max_depth", "max_fanin_depth", "max_design_depth", "deepest_output_cone",
    "count_outputs_depth_gt", "max_pi_to_dff_depth",
    "find_path", "list_paths", "list_register_to_register_paths", "all_paths_through",
    "report_cone_size", "cone_gate_breakdown", "transitive_fanin", "transitive_fanout",
    "get_fanout", "list_direct_loads", "immediate_successors", "report_large_cones",
    "gate_info", "list_gates_by_type", "report_constant_input_gates",
    "same_clock_domain", "shared_fanin_cones", "direct_pi_po_connections",
    "is_cut_between_pi_po",
    "internal_signals_equiv", "find_nand_pair_for_signal",
    "articulation_points_between", "boolean_expression",
    "rename_gate", "rename_wire",
    "list_flipflops_by_clock", "highest_fanout_input", "max_fanout",
    "check_signal_symmetry", "report_floating_signals", "report_dff_enable_hold",
    "check_equiv", "check_original_equiv",
}

_TRANSFORM_KEYWORDS: tuple[str, ...] = (
    "transform", "replace", "convert", "insert", "buffer",
    "remove", "prune", "merge", "collapse", "fuse",
    "simplify", "simplif", "propagat",
    "remap", "restructure", "optimi", "reduc", "minimi",
    "write the current design", "write out",
    "verify", "check equivalence", "check the current netlist",
    "prove", "reconnect", "equivalen",
    "eliminate", "dangling", "unused",
)


def _is_transform_request(text: str) -> bool:
    """Conservative check: True when the request likely needs transform/optimisation tools."""
    low = text.lower()
    for kw in _TRANSFORM_KEYWORDS:
        if kw in low:
            return True
    return False


def get_tools_for_request(text: str, provider: str) -> list[dict]:
    """Return tool definitions appropriate for the given request text.

    Purely analytical requests get a reduced subset (~50 tools);
    anything that smells like a transform/optimisation gets the full set.
    """
    if _is_transform_request(text):
        specs = TOOL_SPECS
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


# ── provider-specific format builders ─────────────────────────────────────────

def openai_tools(specs: list[dict] = TOOL_SPECS) -> list[dict]:
    """Convert TOOL_SPECS to OpenAI function-calling format."""
    result = []
    for spec in specs:
        properties = {}
        for param_name, param_info in spec["parameters"].items():
            prop: dict = {"type": param_info.get("type", "string")}
            if "description" in param_info:
                prop["description"] = param_info["description"]
            if "items" in param_info:
                prop["items"] = param_info["items"]
            properties[param_name] = prop

        result.append({
            "type": "function",
            "function": {
                "name":        spec["name"],
                "description": spec["description"],
                "parameters": {
                    "type":       "object",
                    "properties": properties,
                    "required":   spec.get("required", []),
                },
            },
        })
    return result


def anthropic_tools(specs: list[dict] = TOOL_SPECS) -> list[dict]:
    """Convert TOOL_SPECS to Anthropic tool-use format."""
    result = []
    for spec in specs:
        properties = {}
        for param_name, param_info in spec["parameters"].items():
            prop: dict = {"type": param_info.get("type", "string")}
            if "description" in param_info:
                prop["description"] = param_info["description"]
            if "items" in param_info:
                prop["items"] = param_info["items"]
            properties[param_name] = prop

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

SYSTEM_PROMPT = """\
You are an EDA assistant for gate-level netlist analysis and transformation.
For each natural-language request: identify the needed operation, call the appropriate tool(s), and give a concise factual answer.

Gate primitives: and, or, nand, nor, xor, xnor (2 in / 1 out); not, buf (1 in / 1 out); dff (clk, rst_n, d, q).
State persists across requests within a testcase.

Rules:
- Call read_design first before any analysis or transformation.
- When asked to eliminate/remove/insert/buffer/optimize, perform the action proactively.
- For post-transformation counts use last_operation_count, but only after performing the corresponding transformation.
- Do not do exhaustive searches or full Boolean expansion on large cones; trust the tool's cap/limit.
"""

# Convenience export for the agent
TOOL_DEFINITIONS = TOOL_SPECS   # re-exported; agent uses get_tools_for_provider()
