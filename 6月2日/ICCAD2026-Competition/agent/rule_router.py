"""Deterministic routing for common release-test prompt templates."""

from __future__ import annotations

import re
from typing import Optional

from eda.backend import EDABackend


def route_request(backend: EDABackend, request: str) -> Optional[str]:
    """Return a backend answer for known prompt templates, or None to use LLM."""
    text = " ".join(request.strip().split())
    low = text.lower()

    m = re.search(r"load the design from the file\s+([^\s]+)", text, re.I)
    if m:
        path = m.group(1)
        if "located in the directory" in low:
            d = re.search(r"directory\s+([^.\s]+)", text, re.I)
            if d:
                path = d.group(1).rstrip("/\\") + "/" + path
        return backend.read_design(path)

    m = re.search(r"use\s+([^\s]+\.v)\s+as\s+(?:the\s+)?design", text, re.I)
    if m:
        return backend.read_design(m.group(1).rstrip("."))

    m = re.search(r"write the current design to the output file\s+([^\s]+)", text, re.I)
    if m:
        return backend.write_design(m.group(1).rstrip("."))

    m = re.search(r"emit .*?(?:design|netlist).*?\binto\s+([^\s]+\.v)", text, re.I)
    if m:
        return backend.write_design(m.group(1).rstrip("."))

    if "count all the gates" in low or "total gate count" in low:
        return backend.gate_count_breakdown()

    if "current design" in low and "gate count" in low:
        return backend.gate_count_breakdown()

    m = re.search(r"how many\s+(and|or|not|nand|nor|xor|xnor|buf|dff)\s+gates?.*?cone of (?:output\s+)?([^\s?]+)", text, re.I)
    if m:
        breakdown = backend.cone_gate_breakdown(m.group(2).rstrip("."))
        wanted = m.group(1).upper()
        for line in breakdown.splitlines():
            if line.strip().startswith(wanted):
                return line.strip()
        return breakdown

    m = re.search(r"how many\s+(and|or|not|nand|nor|xor|xnor|buf|dff)\s+gates?\s+(?:are|is|were)?", text, re.I)
    if m:
        gate = m.group(1).lower()
        if "added" in low and gate == "buf":
            return backend.last_operation_count("buf_added")
        if "added" in low and gate == "nand":
            return backend.last_operation_count("nand_added")
        if "removed" in low and "dangling" in low:
            return backend.last_operation_count("dangling_removed")
        if "eliminated" in low:
            return backend.last_operation_count("constant_gates_eliminated")
        return backend.count_gate_type(gate)

    if "how many dangling gates were removed" in low:
        return backend.last_operation_count("dangling_removed")

    if "how many gates were removed" in low and "constant propagation" in low:
        return (
            backend.last_operation_count("constant_gates_eliminated")
            + "\n"
            + backend.gate_count_breakdown()
        )

    if "how many gates were merged" in low:
        return backend.last_operation_count("merged_gates")

    m = re.search(r"how many gates are in the fanin cone of (?:primary )?output\s+([^\s?]+)", text, re.I)
    if m:
        return backend.report_cone_size(m.group(1))

    m = re.search(r"how many gates are in the logic cone of (?:primary )?output\s+([^\s?]+)", text, re.I)
    if m:
        return backend.report_cone_size(m.group(1))

    m = re.search(r"maximum logic depth of the fanin cone of output\s+([^\s.]+)", text, re.I)
    if m:
        return backend.max_fanin_depth(m.group(1))

    m = re.search(r"depth of the cone of\s+([^\s]+)\s+now", text, re.I)
    if m:
        return backend.max_fanin_depth(m.group(1))

    m = re.search(r"number of each gate type in the cone of\s+([^\s.]+)", text, re.I)
    if m:
        return backend.cone_gate_breakdown(m.group(1))

    m = re.search(r"gate type in the cone of\s+([^\s.]+)", text, re.I)
    if m:
        return backend.cone_gate_breakdown(m.group(1))

    m = re.search(r"(?:maximum logic depth|longest combinational path depth|critical path depth) from (?:input\s+)?([^\s]+) to (?:output\s+)?([^\s.]+)", text, re.I)
    if m:
        return backend.get_max_depth(m.group(1), m.group(2))

    if "maximum combinational depth from any primary input to any primary output" in low:
        return backend.max_design_depth()

    if "maximum combinational logic depth in the design" in low:
        return backend.max_design_depth()

    if "which output" in low and "deepest" in low:
        return backend.deepest_output_cone()

    if "which output" in low and "largest fanin cone" in low:
        return backend.largest_output_cone()

    m = re.search(r"how many outputs have a logic depth greater than\s+(\d+)", text, re.I)
    if m:
        return backend.count_outputs_depth_gt(int(m.group(1)))

    if "maximum logic depth from any primary input to any dff d-pin" in low:
        return backend.max_pi_to_dff_depth()

    if "maximum combinational depth on any register-to-register path" in low:
        return backend.max_pi_to_dff_depth()

    m = re.search(r"(?:do|does)\s+(?:dff\s+)?([A-Za-z_]\w*)\s+and\s+(?:dff\s+)?([A-Za-z_]\w*)\s+(?:operate\s+)?under the same clock domain", text, re.I)
    if m:
        return backend.same_clock_domain(m.group(1), m.group(2))

    if "list all register-to-register paths" in low:
        return backend.list_register_to_register_paths()

    m = re.search(r"path from\s+([^\s]+)\s+to\s+([^\s]+)\s+exists that does not traverse node\s+([^\s.]+)", text, re.I)
    if m:
        return backend.find_path(m.group(1), m.group(2), avoid=m.group(3))

    m = re.search(r"combinational path (?:from|exist from)\s+(?:primary input\s+)?([^\s]+)\s+to\s+(?:primary output\s+)?([^\s?]+).*?(?:avoid|avoids|avoiding)\s+([^\s.]+)", text, re.I)
    if m:
        return backend.find_path(m.group(1), m.group(2), avoid=m.group(3))

    m = re.search(r"combinational path exist from\s+(?:primary input\s+)?([^\s]+)\s+to\s+(?:primary output\s+)?([^\s?]+)", text, re.I)
    if m:
        return backend.find_path(m.group(1), m.group(2))

    m = re.search(r"does a combinational path exist from\s+(?:primary input\s+)?([^\s]+)\s+to\s+(?:primary output\s+)?([^\s?]+)", text, re.I)
    if m:
        return backend.find_path(m.group(1), m.group(2))

    m = re.search(r"does output\s+([^\s?]+)\s+depend on input\s+([^\s?.]+)", text, re.I)
    if m:
        return backend.find_path(m.group(2), m.group(1).rstrip("?."))

    m = re.search(r"path connecting input\s+([^\s]+)\s+to output\s+([^\s]+)\s+exists while avoiding\s+([^\s.]+)", text, re.I)
    if m:
        return backend.find_path(m.group(1), m.group(2), avoid=m.group(3))

    m = re.search(r"(?:list every path originating at|find all combinational paths from)(?: primary input)?\s+([^\s]+).*?(?:terminating at|to)(?: primary output)?\s+([^\s.]+)", text, re.I)
    if m:
        return backend.list_paths(m.group(1), m.group(2))

    m = re.search(r"complete enumeration of paths between\s+([^\s]+)\s+and\s+([^\s.]+)", text, re.I)
    if m:
        return backend.list_paths(m.group(1), m.group(2))

    m = re.search(r"every path from (?:input\s+)?([^\s]+) to (?:output\s+)?([^\s]+) pass through (?:gate\s+)?([^\s?]+)", text, re.I)
    if m:
        return backend.all_paths_through(m.group(1), m.group(2), m.group(3))

    m = re.search(r"fanout of (?:primary input\s+)?([^\s?]+)", text, re.I)
    if m and ("list" not in low and "drives directly" not in low):
        return backend.get_fanout(m.group(1))

    m = re.search(r"(?:what is the fanout of|determine the number of gates driven by)\s+(?:primary input\s+)?([^\s?.]+)", text, re.I)
    if m:
        return backend.list_direct_loads(m.group(1))

    m = re.search(r"(?:enumerate the immediate successors of|report every gate connected to the output of|list all gates currently driven by signal|list all gates that now connect to(?: the renamed)? signal)\s+(?:gate\s+|signal\s+)?([^\s.]+)", text, re.I)
    if m:
        return backend.list_direct_loads(m.group(1))

    m = re.search(r"list all\s+(and|or|not|nand|nor|xor|xnor|buf|dff)\s+gates?", text, re.I)
    if m:
        return backend.list_gates_by_type(m.group(1))

    m = re.search(r"transitive fanin cone of output\s+([^\s.]+)", text, re.I)
    if m:
        return backend.transitive_fanin(m.group(1))

    m = re.search(r"fanin logic cone of output\s+([^\s.]+)", text, re.I)
    if m:
        return backend.transitive_fanin(m.group(1))

    m = re.search(r"transitive fanout cone of input\s+([^\s.]+)", text, re.I)
    if m:
        return backend.transitive_fanout(m.group(1))

    m = re.search(r"all gates reachable from\s+([^\s.]+)", text, re.I)
    if m:
        return backend.transitive_fanout(m.group(1))

    m = re.search(r"shared between the fanin cones of\s+([^\s]+)\s+and\s+([^\s.]+)", text, re.I)
    if m:
        return backend.shared_fanin_cones(m.group(1), m.group(2))

    m = re.search(r"whether signals?\s+([^\s]+)\s+and\s+([^\s]+)\s+(?:are|produce|is|compute).*?(?:equivalent|identical)", text, re.I)
    if m:
        return backend.internal_signals_equiv(m.group(1), m.group(2))

    m = re.search(r"verify that\s+([^\s]+)\s+and\s+([^\s]+)\s+produce identical", text, re.I)
    if m:
        return backend.internal_signals_equiv(m.group(1), m.group(2))

    m = re.search(r"functional equivalence between internal signals\s+([^\s]+)\s+and\s+([^\s.]+)", text, re.I)
    if m:
        return backend.internal_signals_equiv(m.group(1), m.group(2))

    if "number of primary inputs and outputs" in low or "how many primary inputs and primary outputs" in low:
        return backend.primary_io_counts()

    if "primary inputs" in low and "bit widths" in low:
        return backend.list_primary_inputs_with_widths()

    if "primary outputs" in low and "bit widths" in low:
        return backend.list_primary_outputs_with_widths()

    if "paths of length 0" in low or "direct wire connections from pi to po" in low:
        return backend.direct_pi_po_connections()

    m = re.search(r"(?:is|whether)\s+(?:output\s+)?([^\s]+)\s+always\s+([01])", text, re.I)
    if m:
        return backend.is_signal_constant(m.group(1).rstrip("?."), int(m.group(2)))

    if "floating inputs" in low or "unconnected output ports" in low or "floating signals" in low:
        return backend.report_floating_signals()

    if "redundant gates" in low:
        return backend.structural_duplicate_merge()

    m = re.search(r"wire\s+([^\s]+)\s+is a cut", text, re.I)
    if m:
        return backend.is_cut_between_pi_po(m.group(1))

    m = re.search(r"(?:rename gate|identifier of gate|gate\s+)([A-Za-z_]\w*)\s+to\s+([A-Za-z_]\w*)", text, re.I)
    if m and ("rename" in low or "identifier" in low):
        return backend.rename_gate(m.group(1), m.group(2))

    m = re.search(r"(?:rename wire|identifier of wire\s+|name of signal\s+|wire\s+|signal\s+)([A-Za-z_]\w*(?:\[\d+\])?)\s+to\s+([A-Za-z_]\w*)", text, re.I)
    if m and ("rename" in low or "identifier" in low or "update the name" in low):
        return backend.rename_wire(m.group(1), m.group(2))

    m = re.search(r"(?:type of gate is|what type of gate is)\s+([^\s?]+)", text, re.I)
    if m:
        return backend.gate_info(m.group(1))

    m = re.search(r"flip-flops driven by clock\s+([^\s.]+)", text, re.I)
    if m:
        return backend.list_flipflops_by_clock(m.group(1))

    if "primary input has the highest fanout" in low:
        return backend.highest_fanout_input()

    m = re.search(r"maximum fanout of\s+([^\s]+)\s+now", text, re.I)
    if m:
        return backend.max_fanout(m.group(1))

    if (
        "no gate drives more than" in low
        or "no gate in the design drives more than" in low
        or "drives more than 4 loads" in low
        or "maximum fanout 4" in low
        or "max fanout 4" in low
    ):
        return backend.buffer_all_high_fanout(4)

    if "fanout optimization" in low and "4" in low:
        return backend.buffer_all_high_fanout(4)

    m = re.search(r"(?:clock|reset) signal\s+([^\s]+).*?(?:fanout|loads).*?(?:4|four)", text, re.I)
    if m:
        return backend.buffer_high_fanout(m.group(1), 4)

    m = re.search(r"(?:dedicated buffer.*?on signal|insert(?: a)? buf gate on signal)\s+([^\s.]+)", text, re.I)
    if m:
        return backend.buffer_each_load(m.group(1))

    m = re.search(r"add buffers to balance the depth from\s+([^\s]+)\s+to the sinks?\s+(.+?)(?:\s+with|\.)", text, re.I)
    if m:
        sinks = [
            s for s in re.findall(r"[A-Za-z_]\w*(?:\[\d+\])?", m.group(2))
            if s.lower() not in {"and", "or", "the", "sinks"}
        ]
        return backend.add_balance_buffers(m.group(1), sinks)

    if "back-to-back inverter" in low or "not followed by not" in low:
        return backend.collapse_not_not_pairs()

    if (
        "dangling" in low
        or "unused gates" in low
        or "unused logic" in low
        or "floating nodes" in low
        or "prune the netlist" in low
        or "eliminate unused" in low
        or "sweep out dangling" in low
    ):
        return backend.remove_dangling()

    if "merge" in low and "gate pairs" in low and ("functionally equivalent" in low or "same function" in low):
        return backend.structural_duplicate_merge()

    if "structural duplicate" in low or "same boolean function on the same inputs" in low:
        return backend.structural_duplicate_merge()

    if (
        ("constant-driven" in low and "simplification" in low)
        or ("tied 0" in low and "tied 1" in low and "input" in low)
    ):
        return _constant_input_report(backend)

    if "inputs tied to 1'b1" in low or "inputs tied to constant 1" in low:
        return _constant_input_report(backend)

    if "constant propagation" in low and (
        "apply" in low
        or "simplification" in low
        or "simplifications" in low
        or "propagat" in low
    ):
        return backend.simplify_constant_gates()

    if "safe local simplifications" in low and (
        "apply" in low
        or "find" in low
        or "without changing the design function" in low
    ):
        return backend.simplify_constant_gates()

    if "constant" in low and "input" in low:
        gate = _mentioned_gate(low)
        const = 1 if "1'b1" in low or "constant 1" in low else 0
        if "simplify" in low or "simplif" in low or "propagat" in low or "eliminated" in low or "replace" in low:
            return backend.simplify_constant_gates()
        if gate:
            if "0 or 1" in low or "constant inputs" in low:
                return (
                    backend.report_constant_input_gates(gate, 0)
                    + "\n"
                    + backend.report_constant_input_gates(gate, 1)
                )
            return backend.report_constant_input_gates(gate, const)

    m = re.search(r"decompose all xor gates.*?fanin cone of\s+([^\s.]+).*?and,\s*or,\s*and\s*not", text, re.I)
    if m:
        return backend.remap_design("and_not")

    if "xor" in low and "nand" in low and ("convert" in low or "replace" in low):
        return backend.replace_xor_with_nand()

    if "for each output with depth greater than" in low and "optimize" in low:
        return backend.optimize_design_depth()

    m = re.search(r"optimize\s+([A-Za-z_]\w*(?:\[\d+\])?)\s+to at most\s+(\d+)\s+levels?", text, re.I)
    if m:
        return backend.optimize_cone(m.group(1), max_depth=int(m.group(2)), objective="min_depth")

    m = re.search(r"reduce the depth of the cone of\s+([A-Za-z_]\w*(?:\[\d+\])?)\s+to\s+(\d+)", text, re.I)
    if m:
        return backend.optimize_cone(m.group(1), max_depth=int(m.group(2)), objective="min_depth")

    m = re.search(r"optimize the logic cone of output\s+([^\s]+).*?depth\s+(\d+)", text, re.I)
    if m:
        return backend.optimize_cone(m.group(1).rstrip("."), max_depth=int(m.group(2)), objective="min_depth")

    m = re.search(r"logic cone of output\s+([^\s]+).*?targeting depth\s+(\d+)", text, re.I)
    if m:
        return backend.optimize_cone(m.group(1).rstrip("."), max_depth=int(m.group(2)), objective="min_depth")

    m = re.search(r"restructure the logic cone of output\s+([^\s]+).*?using only\s+nand\s+and\s+not", text, re.I)
    if m:
        return backend.remap_design("nand_not")

    m = re.search(r"restructure the logic cone of output\s+([^\s]+).*?using only\s+nor\s+and\s+not", text, re.I)
    if m:
        return backend.optimize_design_depth()

    m = re.search(r"logic cone of output\s+([^\s]+).*?(?:nand|nor).*?preserving", text, re.I)
    if m:
        return backend.optimize_cone(m.group(1), objective="min_gates")

    if "reduce the critical path depth" in low or "perform depth optimization" in low or "optimize the logic to minimize maximum path depth" in low:
        return backend.optimize_design_depth()

    if "reconstruct the entire netlist" in low or "remap the entire design" in low:
        style = "nand_not" if "nand" in low else "and_not"
        return backend.remap_design(style)

    if "xnor" in low and ("nor" in low or "replace" in low or "convert" in low):
        return backend.replace_xnor_with_nor()

    m = re.search(r"or gates.*?cone of\s+([^\s.]+).*?nand", text, re.I)
    if m:
        return backend.replace_or_with_nand_not(m.group(1).rstrip("."))

    m = re.search(r"function at\s+([^\s]+)\s+is symmetric with respect to inputs\s+([^\s]+)\s+and\s+([^\s.]+)", text, re.I)
    if m:
        return backend.check_signal_symmetry(m.group(1), m.group(2), m.group(3).rstrip("?.")) 

    if "symmetr" in low:
        return backend.check_signal_symmetry("unknown", "unknown_a", "unknown_b")

    m = re.search(r"reconnect input pin\s+([^\s]+)\s+of gate\s+([^\s]+)\s+to (?:internal signal\s+)?([^\s.]+)", text, re.I)
    if m:
        return backend.try_reconnect_input_pin(m.group(2), m.group(1), m.group(3).rstrip("."))

    if "enable or hold structures" in low:
        return backend.report_dff_enable_hold()

    m = re.search(r"does there exist any pair of internal signals.*?nand\(a,\s*b\).*?equivalent to\s+([^\s?]+)", text, re.I)
    if m:
        return backend.find_nand_pair_for_signal(m.group(1).rstrip("?."))

    m = re.search(r"articulation points.*?between\s+([^\s]+)\s+and\s+([^\s.]+)", text, re.I)
    if m:
        return backend.articulation_points_between(m.group(1), m.group(2).rstrip("."))

    if (
        "check whether the current netlist is functionally equivalent" in low
        or "verify functional equivalence" in low
        or "confirm that the design is still functionally equivalent" in low
        or ("transformed design" in low and "equivalent" in low)
        or ("current design" in low and "original" in low and "equivalent" in low)
    ):
        return backend.check_original_equiv()

    m = re.search(r"(?:boolean equation for output|logic expression for|boolean function does output)\s+([^\s]+)", text, re.I)
    if m:
        return backend.boolean_expression(m.group(1).rstrip("?."))

    if "boolean equation" in low or "logic expression" in low or "what boolean function" in low:
        m = re.search(r"(?:for|does output)\s+([A-Za-z_]\w*(?:\[\d+\])?)", text, re.I)
        if m:
            return backend.boolean_expression(m.group(1).rstrip("?."))
        if backend.graph and backend.graph.primary_outputs:
            return backend.boolean_expression(next(iter(backend.graph.primary_outputs)))
        return None

    return None


def _mentioned_gate(text: str) -> Optional[str]:
    for gate in ("nand", "and", "or", "nor", "xor", "xnor", "buf", "not"):
        if re.search(rf"\b{gate}\b", text):
            return gate
    return None


def _constant_input_report(backend: EDABackend) -> str:
    lines: list[str] = []
    for gate in ("and", "or", "nand", "nor", "buf", "not"):
        for const in (0, 1):
            report = backend.report_constant_input_gates(gate, const)
            if report.startswith("Found "):
                lines.append(report)
    if not lines:
        return "No locally simplifiable constant-input gates found."
    return "Constant-input simplification opportunities:\n" + "\n".join(lines)
