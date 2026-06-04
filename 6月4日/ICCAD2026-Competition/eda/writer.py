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

import re
from pathlib import Path
from typing import Optional

import networkx as nx

from .netlist_graph import NetlistGraph, YOSYS_TO_PRIM, DFF_TYPES


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
        "$and":  "and   {n} ({y}, {a}, {b});",
        "$or":   "or    {n} ({y}, {a}, {b});",
        "$nand": "nand  {n} ({y}, {a}, {b});",
        "$nor":  "nor   {n} ({y}, {a}, {b});",
        "$xor":  "xor   {n} ({y}, {a}, {b});",
        "$xnor": "xnor  {n} ({y}, {a}, {b});",
        "$not":  "not   {n} ({y}, {a});",
        "$buf":  "buf   {n} ({y}, {a});",
    }

    def write(self, graph: NetlistGraph, path: str) -> None:
        """
        Write graph to a Verilog file at path.

        Parameters
        ----------
        graph : NetlistGraph
        path  : str  destination file path
        """
        lines = self._build_lines(graph)
        Path(path).write_text("\n".join(lines))

    def to_string(self, graph: NetlistGraph) -> str:
        """Return the Verilog as a string without writing to disk."""
        return "\n".join(self._build_lines(graph))

    # ── internal ──────────────────────────────────────────────────────────────

    def _build_lines(self, g: NetlistGraph) -> list[str]:
        lines: list[str] = []
        output_overrides = self._duplicate_output_overrides(g)

        # ── port list ─────────────────────────────────────────────────────────
        pi_ports = self._scalar_port_names(g, "pi")
        po_ports = self._port_base_names(g.primary_outputs.keys())
        all_ports = pi_ports + po_ports
        lines.append(f"module {self._ident(g.module_name)} (")
        for i, p in enumerate(all_ports):
            sep = "," if i < len(all_ports) - 1 else ""
            lines.append(f"    {self._ident(p)}{sep}")
        lines.append(");")
        lines.append("")

        # ── input declarations ────────────────────────────────────────────────
        for port_name in pi_ports:
            width = g.port_widths.get(port_name, self._bus_width(g, port_name, "pi"))
            if width > 1:
                lines.append(f"  input [{width-1}:0] {self._ident(port_name)};")
            else:
                lines.append(f"  input {self._ident(port_name)};")

        # ── output declarations ───────────────────────────────────────────────
        for port_name in po_ports:
            width = g.port_widths.get(port_name, self._label_width(g.primary_outputs.keys(), port_name))
            if width > 1:
                lines.append(f"  output [{width-1}:0] {self._ident(port_name)};")
            else:
                lines.append(f"  output {self._ident(port_name)};")

        lines.append("")

        # ── internal wire declarations ────────────────────────────────────────
        pi_wires = {d["output_wire"]
                    for _, d in g.G.nodes(data=True) if d.get("ntype") == "pi"}
        port_wires = set(g.primary_inputs) | set(g.primary_outputs)
        internal = sorted(
            d["output_wire"]
            for _, d in g.G.nodes(data=True)
            if d.get("ntype") == "cell"
            and not d.get("is_po")
            and d.get("output_wire", "").startswith("1'b") is False
            and d.get("output_wire") not in port_wires
        )
        internal.extend(output_overrides.values())
        internal = sorted(dict.fromkeys(internal))
        if internal:
            lines.append(f"  wire {', '.join(self._sig(w) for w in internal)};")
            lines.append("")

        # ── cell instantiations in topological order ──────────────────────────
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
        if any(d.get("gate_type") in DFF_TYPES for _, d in g.G.nodes(data=True)):
            lines += [
                "(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);",
                "endmodule",
                "",
            ]
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
            expr = nd.get("expr", "1'bx")
            return f"assign {out} = {expr};"

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
            ordered = [p for p in ("RN", "SN", "CK", "D") if p in port_map]
            ordered.extend(p for p in port_map if p not in ordered)
            ports_str = ", ".join(f".{p}({port_map[p]})" for p in ordered)
            return f"dff {self._ident(nid)} (.Q({out}), {ports_str});"

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
            ports = [
                (str(edata.get("port", "")), g.output_wire(pred))
                for pred, _dst, edata in g.G.in_edges(nid, data=True)
            ]
        return {str(port): self._sig(wire) for port, wire in ports if str(port)}

    def _valid_input_ports(self, nid: str, nd: dict, g: NetlistGraph) -> list[tuple[str, str]]:
        ports = list(nd.get("input_ports") or [])
        if not ports:
            return []
        valid: list[tuple[str, str]] = []
        for port, wire in ports:
            driver = g.wire_driver.get(wire)
            if driver is None or not g.G.has_edge(driver, nid):
                return []
            edata = g.G.get_edge_data(driver, nid, {})
            if edata.get("wire") != wire:
                return []
            valid.append((str(port), wire))
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
        return list(seen.keys())

    def _port_base_names(self, labels) -> list[str]:
        seen: dict[str, None] = {}
        for label in labels:
            base = str(label).split("[")[0]
            seen.setdefault(base, None)
        return list(seen.keys())

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
            src = g.output_wire(driver)
            if src == out_label:
                continue
            assigns.append(f"assign {self._sig(out_label)} = {self._sig(src)};")
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
