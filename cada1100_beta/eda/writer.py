"""
eda/writer.py
=============
Emit clean gate-level Verilog from a NetlistGraph.

The writer emits cells in topological order so the output is immediately
usable by downstream tools without re-sorting.

Supported gate types: and, or, nand, nor, xor, xnor, not, buf, dff.
Bus ports (input [N:0] x) are handled via the PI node set.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Optional

import networkx as nx

from .netlist_graph import NetlistGraph, YOSYS_TO_PRIM, DFF_TYPES, CONST_X, CONST_Z


class VerilogWriter:
    """
    Convert a NetlistGraph to a gate-level Verilog file.

    Usage
    -----
        writer = VerilogWriter()
        writer.write(graph, "/path/to/output.v")
    """

    # Verilog instantiation templates.
    # {n}=cell name, {y}=output wire, {a}/{b}=input wires.
    _GATE_TMPL: dict[str, str] = {
        "$and":  "and {n}({y}, {a}, {b});",
        "$or":   "or {n}({y}, {a}, {b});",
        "$nand": "nand {n}({y}, {a}, {b});",
        "$nor":  "nor {n}({y}, {a}, {b});",
        "$xor":  "xor {n}({y}, {a}, {b});",
        "$xnor": "xnor {n}({y}, {a}, {b});",
        "$not":  "not {n}({y}, {a});",
        "$buf":  "buf {n}({y}, {a});",
    }

    def write(self, graph: NetlistGraph, path: str) -> None:
        """
        Write graph to a Verilog file at path.

        Parameters
        ----------
        graph : NetlistGraph
        path  : str  destination file path
        """
        lines = self._build_lines(self.prepare_serialization_graph(graph))
        Path(path).write_text("\n".join(lines))

    def to_string(self, graph: NetlistGraph) -> str:
        """Return the Verilog as a string without writing to disk."""
        return "\n".join(self._build_lines(self.prepare_serialization_graph(graph)))

    def prepare_serialization_graph(self, graph: NetlistGraph) -> NetlistGraph:
        """Return the exact graph that will be emitted, including PO aliases."""
        prepared = copy.deepcopy(graph)

        # Some synthesized graphs retain dead cells whose output attribute
        # reuses a live wire name.  Give every non-canonical duplicate an
        # explicit private wire here, so cost evaluation and final emission
        # operate on the same single-driver graph.
        for nid, fresh_wire in self._duplicate_output_overrides(prepared).items():
            prepared.G.nodes[nid]["output_wire"] = fresh_wire
            prepared.G.nodes[nid]["origin_wire"] = fresh_wire
            prepared.wire_driver[fresh_wire] = nid
            prepared.wire_readers.setdefault(fresh_wire, [])

        # Prefer a wire rename when one cell exclusively drives one output.
        outputs_by_driver: dict[str, list[str]] = {}
        for out_label, driver in prepared.primary_outputs.items():
            outputs_by_driver.setdefault(driver, []).append(str(out_label))
        from .transformer import NetlistTransformer
        tx = NetlistTransformer(prepared)
        for driver, labels in outputs_by_driver.items():
            if driver not in prepared.G or len(labels) != 1:
                continue
            out_label = labels[0]
            source_wire = prepared.output_wire(driver)
            if (
                prepared.G.nodes[driver].get("ntype") == "cell"
                and source_wire != out_label
                and source_wire not in prepared.primary_outputs
                and out_label not in prepared.wire_driver
            ):
                tx.rename_wire(source_wire, out_label)

        # Remaining aliases are materialized as two NOT gates.  Doing this in
        # the graph makes writer cost, depth, fanout and emitted Verilog agree.
        # Skip genuinely undriven outputs (CONST_X / CONST_Z drivers): they
        # must stay unconnected rather than gain spurious NOT pairs.
        alias_rows = [
            (str(out_label), driver)
            for out_label, driver in prepared.primary_outputs.items()
            if driver in prepared.G
            and prepared.output_wire(driver) != out_label
            and driver not in (CONST_X, CONST_Z)
        ]
        for index, (out_label, driver) in enumerate(alias_rows):
            src_wire = prepared.output_wire(driver)
            inv0 = f"__po_alias_inv0_{index}"
            inv1 = f"__po_alias_inv1_{index}"
            mid = f"__po_alias_wire_{index}"
            suffix = 0
            while inv0 in prepared.G or inv1 in prepared.G or mid in prepared.wire_driver:
                suffix += 1
                inv0 = f"__po_alias_inv0_{index}_{suffix}"
                inv1 = f"__po_alias_inv1_{index}_{suffix}"
                mid = f"__po_alias_wire_{index}_{suffix}"
            prepared.G.add_node(
                inv0, ntype="cell", gate_type="$not", output_wire=mid,
                input_ports=[("A", src_wire)], input_wires=[src_wire],
                is_po=False, origin_id=inv0, origin_wire=mid,
            )
            prepared.G.add_node(
                inv1, ntype="cell", gate_type="$not", output_wire=out_label,
                input_ports=[("A", mid)], input_wires=[mid],
                is_po=True, origin_id=inv1, origin_wire=out_label,
            )
            prepared.G.add_edge(driver, inv0, wire=src_wire, port="A")
            prepared.G.add_edge(inv0, inv1, wire=mid, port="A")
            prepared.wire_driver[mid] = inv0
            prepared.wire_driver[out_label] = inv1
            prepared.wire_readers.setdefault(src_wire, []).append(inv0)
            prepared.wire_readers.setdefault(mid, []).append(inv1)
            prepared.primary_outputs[out_label] = inv1
            if prepared.G.nodes[driver].get("ntype") == "cell":
                prepared.G.nodes[driver]["is_po"] = any(
                    value == driver for value in prepared.primary_outputs.values()
                )

        # A newly materialized output alias may intentionally take over a
        # port wire that a now-dead cell used to name.  Canonicalize that last
        # duplicate too, making this preparation pass idempotent.
        for nid, fresh_wire in self._duplicate_output_overrides(prepared).items():
            prepared.G.nodes[nid]["output_wire"] = fresh_wire
            prepared.G.nodes[nid]["origin_wire"] = fresh_wire
            prepared.wire_driver[fresh_wire] = nid
            prepared.wire_readers.setdefault(fresh_wire, [])
        return prepared


    def _build_lines(self, g: NetlistGraph) -> list[str]:
        lines: list[str] = []
        output_overrides = self._duplicate_output_overrides(g)

        pi_ports = self._scalar_port_names(g, "pi")
        po_ports = self._port_base_names(g.primary_outputs.keys())
        all_ports = pi_ports + po_ports
        lines.append(f"module {self._ident(g.module_name)} (")
        for i, p in enumerate(all_ports):
            sep = "," if i < len(all_ports) - 1 else ""
            lines.append(f"    {self._ident(p)}{sep}")
        lines.append(");")
        lines.append("")

        pi_widths = self._width_map(
            d.get("output_wire", "")
            for _nid, d in g.G.nodes(data=True)
            if d.get("ntype") == "pi"
        )
        po_widths = self._width_map(g.primary_outputs.keys())
        for port_name in pi_ports:
            width = g.port_widths.get(port_name)
            if width is None:
                width = pi_widths.get(port_name, 1)
            range_text = self._range_text(g, port_name, width)
            lines.append(f"  input {range_text}{self._ident(port_name)};")

        for port_name in po_ports:
            width = g.port_widths.get(port_name)
            if width is None:
                width = po_widths.get(port_name, 1)
            range_text = self._range_text(g, port_name, width)
            lines.append(f"  output {range_text}{self._ident(port_name)};")

        lines.append("")

        pi_wires = {d["output_wire"]
                    for _, d in g.G.nodes(data=True) if d.get("ntype") == "pi"}
        port_wires = set(g.primary_inputs) | set(g.primary_outputs)
        internal = sorted(
            d["output_wire"]
            for _, d in g.G.nodes(data=True)
            if d.get("ntype") == "cell"
            and d.get("output_wire", "").startswith("1'b") is False
            and d.get("output_wire") not in port_wires
        )
        internal.extend(output_overrides.values())
        internal = sorted(dict.fromkeys(internal))
        if internal:
            scalar_wires: list[str] = []
            bus_indices: dict[str, set[int]] = {}
            for wire in internal:
                match = re.fullmatch(r"(.+)\[(\d+)\]", str(wire))
                if match:
                    bus_indices.setdefault(match.group(1), set()).add(int(match.group(2)))
                else:
                    scalar_wires.append(str(wire))
            if scalar_wires:
                lines.append(
                    f"  wire {', '.join(self._sig(w) for w in scalar_wires)};"
                )
            for base in sorted(bus_indices):
                declared = g.signal_ranges.get(base)
                if declared is None:
                    indices = bus_indices[base]
                    declared = (max(indices), min(indices))
                left, right = declared
                lines.append(f"  wire [{left}:{right}] {self._ident(base)};")
            lines.append("")

        try:
            topo = list(nx.topological_sort(g.G))
        except nx.NetworkXUnfeasible:
            topo = list(g.G.nodes)

        for nid in topo:
            nd = g.G.nodes.get(nid, {})
            if nd.get("ntype") != "cell":
                continue
            line = self._emit_cell(nid, nd, g, output_overrides)
            if line:
                lines.append(f"  {line}")

        assigns = self._output_alias_assigns(g)
        if assigns:
            lines.append("")
            lines.extend(f"  {line}" for line in assigns)

        lines += ["", "endmodule", ""]
        return lines

    def _emit_cell(
        self,
        nid: str,
        nd: dict,
        g: NetlistGraph,
        output_overrides: dict[str, str],
    ) -> Optional[str]:
        gt  = nd.get("gate_type", "")
        out = self._sig(output_overrides.get(nid, nd.get("output_wire", nid)))
        input_wires = self._cell_input_wires(nid, nd, g)
        preds = list(g.G.predecessors(nid))

        if gt == "$expr":
            raise ValueError(
                f"cell {nid!r} is an unresolved expression; lower it to "
                "2-input contest primitives before serialization"
            )

        tmpl = self._GATE_TMPL.get(gt)
        if tmpl:
            a = self._sig(input_wires[0]) if len(input_wires) > 0 else "1'bx"
            b = self._sig(input_wires[1]) if len(input_wires) > 1 else "1'bx"
            return tmpl.format(n=self._ident(nid), y=out, a=a, b=b)

        if gt in DFF_TYPES:
            # Named port instantiation for DFF
            port_map = self._cell_port_map(nid, nd, g)
            if not port_map:
                for pred in preds:
                    edge = g.G.get_edge_data(pred, nid, {})
                    port = edge.get("port", edge.get("wire", "?"))
                    port_map[port] = self._owire(pred, g)
            uses_pdf_ports = any(p in port_map for p in ("clk", "rst_n", "d"))
            output_port = "q" if uses_pdf_ports else "Q"
            ordered_base = ("clk", "rst_n", "d") if uses_pdf_ports else ("RN", "SN", "CK", "D")
            ordered = [p for p in ordered_base if p in port_map]
            ordered.extend(p for p in port_map if p not in ordered)
            ports_str = ", ".join(f".{p}({port_map[p]})" for p in ordered)
            if ports_str:
                return f"dff {self._ident(nid)} (.{output_port}({out}), {ports_str});"
            return f"dff {self._ident(nid)} (.{output_port}({out}));"

        # Fallback: positional instantiation
        prim  = YOSYS_TO_PRIM.get(gt, gt.lstrip("$"))
        inp   = ", ".join(self._sig(w) for w in input_wires)
        return f"{prim} {self._ident(nid)} ({out}, {inp});"

    def _cell_input_wires(self, nid: str, nd: dict, g: NetlistGraph) -> list[str]:
        ports = self._valid_input_ports(nid, nd, g)
        if ports:
            return [wire for _port, wire in ports]
        edges = []
        for pred, _dst, edata in g.G.in_edges(nid, data=True):
            port = str(edata.get("port", ""))
            edges.append((self._port_sort_key(port), g.output_wire(pred)))
        if edges:
            return [wire for _key, wire in sorted(edges)]
        return list(nd.get("input_wires") or [])

    def _cell_port_map(self, nid: str, nd: dict, g: NetlistGraph) -> dict[str, str]:
        ports = self._valid_input_ports(nid, nd, g)
        if not ports:
            declared = list(nd.get("input_ports") or [])
            ports = []
            for pred, _dst, edata in g.G.in_edges(nid, data=True):
                port = str(edata.get("port", "") or "")
                canonical = g.output_wire(pred)
                if not port:
                    # from_yosys_json DFF pins can lose the edge port
                    # attribute; recover it from the declared input_ports
                    # row whose wire (canonical name or retained alias)
                    # matches this edge.  Without a port name the pin would
                    # be dropped and the emitted DFF left dangling.
                    edge_wire = str(edata.get("wire", "") or "")
                    for dport, dwire in declared:
                        dw = str(dwire)
                        if dw in (edge_wire, canonical) or (
                            g.signal_aliases.get(dw) in (edge_wire, canonical)
                        ):
                            port = str(dport)
                            break
                if port:
                    ports.append((port, canonical))
        return {str(port): self._sig(wire) for port, wire in ports if str(port)}

    def _valid_input_ports(self, nid: str, nd: dict, g: NetlistGraph) -> list[tuple[str, str]]:
        ports = list(nd.get("input_ports") or [])
        if not ports:
            return []
        valid: list[tuple[str, str]] = []
        for port, wire in ports:
            driver = g.wire_driver.get(wire)
            if driver is None:
                # The declared pin wire may be a retained alias netname
                # (from_yosys_json keeps several netnames per bit); resolve
                # it to the canonical driven wire before giving up.
                alias = g.signal_aliases.get(str(wire))
                if alias:
                    driver = g.wire_driver.get(alias)
            if driver is None or not g.G.has_edge(driver, nid):
                return []
            # JSON may retain several netnames for the same bit.  Emit the
            # canonical wire actually driven by the predecessor; otherwise a
            # DFF/input pin can reference an alias that has no declaration or
            # driver in the serialized netlist.
            valid.append((str(port), g.output_wire(driver)))
        return sorted(valid, key=lambda item: self._port_sort_key(item[0]))

    def _port_sort_key(self, port: str) -> tuple[int, str]:
        order = {"A": 0, "I0": 0, "\\A": 0, "B": 1, "I1": 1, "\\B": 1}
        p = str(port).upper().lstrip("\\")
        if p in order:
            return (order[p], p)
        m = re.fullmatch(r"[A-Z]*(\d+)", p)
        if m:
            return (int(m.group(1)), p)
        return (99, p)

    def _owire(self, nid: str, g: NetlistGraph) -> str:
        return self._sig(g.G.nodes.get(nid, {}).get("output_wire", nid))

    def _sig(self, name: str) -> str:
        if name.startswith("1'b"):
            return name
        m = re.fullmatch(r"(.+)\[(\d+)\]", name)
        if m:
            return f"{self._ident(m.group(1))}[{m.group(2)}]"
        return self._ident(name)

    def _range_text(self, g: NetlistGraph, name: str, width: int) -> str:
        declared = g.signal_ranges.get(name)
        if declared is not None:
            return f"[{declared[0]}:{declared[1]}] "
        if width > 1:
            return f"[{width - 1}:0] "
        return ""

    def _ident(self, name: str) -> str:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
            return name
        return "\\" + name.replace("\\", "\\\\").replace(" ", "_") + " "

    def _scalar_port_names(self, g: NetlistGraph, ntype: str) -> list[str]:
        """Return deduplicated scalar port base names for PI or PO nodes."""
        seen: dict[str, int] = {}
        for _, d in g.G.nodes(data=True):
            if d.get("ntype") != ntype:
                continue
            w    = d.get("output_wire", "")
            base = w.split("[")[0]
            seen[base] = seen.get(base, 0) + 1
        # AIG/ABC CEC pairs ports positionally after lowering.  Keep module
        # interfaces deterministic even when Yosys inserted graph nodes in a
        # different order for an equivalent candidate.
        return sorted(seen.keys())

    def _port_base_names(self, labels) -> list[str]:
        seen: dict[str, None] = {}
        for label in labels:
            base = str(label).split("[")[0]
            seen.setdefault(base, None)
        return sorted(seen.keys())

    def _width_map(self, labels) -> dict[str, int]:
        """Compute all scalar/bus widths in one pass."""
        result: dict[str, int] = {}
        counts: dict[str, int] = {}
        for raw in labels:
            label = str(raw)
            match = re.fullmatch(r"(.+)\[(\d+)\]", label)
            if match:
                base = match.group(1)
                result[base] = max(result.get(base, 1), int(match.group(2)) + 1)
                counts[base] = counts.get(base, 0) + 1
            else:
                result[label] = max(result.get(label, 1), 1)
                counts[label] = counts.get(label, 0) + 1
        for base, count in counts.items():
            result[base] = max(result.get(base, 1), count)
        return result

    def _label_width(self, labels, port_name: str) -> int:
        count = 0
        max_index = -1
        for label in labels:
            label = str(label)
            if label == port_name:
                count += 1
                max_index = max(max_index, 0)
                continue
            m = re.fullmatch(rf"{re.escape(port_name)}\[(\d+)\]", label)
            if m:
                count += 1
                max_index = max(max_index, int(m.group(1)))
        return max(max_index + 1, count, 1)

    def _output_alias_assigns(self, g: NetlistGraph) -> list[str]:
        assigns: list[str] = []
        for out_label, driver in g.primary_outputs.items():
            if driver not in g.G:
                continue
            # Skip undriven outputs (CONST_X / CONST_Z) — do not fabricate
            # double-NOT gates for ports that have no real driver.
            if driver in (CONST_X, CONST_Z):
                continue
            src = g.output_wire(driver)
            if src == out_label:
                continue
            # Keep the emitted design strictly primitive gate-level.  Two
            # NOTs preserve the alias while remaining legal in every contest
            # gate-style used by this project (including strict AND/NOT,
            # NAND/NOT and NOR/NOT outputs).
            index = len(assigns) // 2
            mid = self._sig(f"__po_alias_wire_{index}")
            assigns.append(
                f"not {self._ident(f'__po_alias_inv0_{index}')}"
                f"({mid}, {self._sig(src)});"
            )
            assigns.append(
                f"not {self._ident(f'__po_alias_inv1_{index}')}"
                f"({self._sig(out_label)}, {mid});"
            )
        return assigns

    def _duplicate_output_overrides(self, g: NetlistGraph) -> dict[str, str]:
        by_wire: dict[str, list[str]] = {}
        used: set[str] = set(g.primary_inputs) | set(g.primary_outputs)
        for nid, nd in g.G.nodes(data=True):
            out = nd.get("output_wire")
            if not out or str(out).startswith("1'b"):
                continue
            used.add(out)
            if nd.get("ntype") == "cell":
                by_wire.setdefault(out, []).append(nid)

        overrides: dict[str, str] = {}
        for wire, drivers in by_wire.items():
            if len(drivers) <= 1:
                continue
            canonical = g.wire_driver.get(wire)
            if canonical not in drivers:
                canonical = drivers[-1]
            for nid in drivers:
                if nid == canonical:
                    continue
                fresh = self._fresh_duplicate_wire(wire, nid, used)
                used.add(fresh)
                overrides[nid] = fresh
        return overrides

    def _fresh_duplicate_wire(self, wire: str, nid: str, used: set[str]) -> str:
        base = re.sub(r"[^A-Za-z0-9_$]+", "_", f"dup_{wire}_{nid}").strip("_")
        if not base or not re.match(r"[A-Za-z_]", base):
            base = "dup_wire"
        candidate = f"__{base}__"
        idx = 0
        while candidate in used:
            idx += 1
            candidate = f"__{base}_{idx}__"
        return candidate

    def _bus_width(self, g: NetlistGraph, port_name: str, ntype: str) -> int:
        """Count how many PI/PO nodes share this port base name (= bus width)."""
        count = 0
        max_index = -1
        for _, d in g.G.nodes(data=True):
            if ntype == "po":
                if d.get("ntype") != "cell" or not d.get("is_po"):
                    continue
            elif d.get("ntype") != ntype:
                continue
            w = d.get("output_wire", "")
            if w == port_name:
                count += 1
                max_index = max(max_index, 0)
                continue
            m = re.fullmatch(rf"{re.escape(port_name)}\[(\d+)\]", w)
            if m:
                count += 1
                max_index = max(max_index, int(m.group(1)))
        return max(max_index + 1, count, 1)
