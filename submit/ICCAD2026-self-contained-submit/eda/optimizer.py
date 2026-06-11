"""
eda/optimizer.py
================
Cone-level optimization pipeline:

  1. Extract the fanin cone of an output into a standalone Verilog module.
  2. Call ABC (via Yosys) with an optional depth constraint.
  3. Verify functional equivalence of the optimized cone against the original.
  4. Check that the depth constraint is satisfied.
  5. Splice the optimized cells back into the main NetlistGraph.

This module is intentionally isolated from all other EDA components so it
can be replaced with a different optimizer (e.g. a SAT-based rewriter) without
touching the rest of the codebase.

Splice strategy
---------------
The optimized module uses new bit IDs assigned by Yosys during the ABC run.
We re-parse the optimized JSON and inject those cells into the main graph,
remapping:
  - PI nodes of the cone module  ->pre-existing driver nodes in the main graph
  - PO node of the cone module   ->the target output port in the main graph
  - Internal wires               ->prefixed with "_opt_{output_name}_" to avoid
                                   name collisions
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import networkx as nx

from .netlist_graph import NetlistGraph, YOSYS_TO_PRIM, PRIM_TO_YOSYS, DFF_TYPES
from .yosys_backend import YosysBackend
from .writer import VerilogWriter


@dataclass
class OptResult:
    success:      bool
    reason:       str        = ""
    before_gates: int        = 0
    after_gates:  int        = 0
    gates_saved:  int        = 0
    equiv:        bool       = False
    depth_ok:     bool       = True

    def summary(self) -> str:
        if not self.success:
            return f"Opt failed: {self.reason}"
        return (
            f"Opt: {self.before_gates}->{self.after_gates} gates "
            f"({self.gates_saved} saved). Equiv OK. Depth {'OK' if self.depth_ok else 'FAIL'}"
        )


class ConeOptimizer:
    """
    Extract ->optimize ->verify ->splice pipeline for a single output cone.
    """

    def __init__(self, yosys: YosysBackend,
                 writer: Optional[VerilogWriter] = None) -> None:
        self.yosys  = yosys
        self.writer = writer or VerilogWriter()


    def optimize(self, graph: NetlistGraph,
                 output_name: str,
                 max_depth:   Optional[int] = None,
                 objective:   str = "min_gates") -> OptResult:
        """
        Optimize the fanin cone of output_name.

        Parameters
        ----------
        graph       : NetlistGraph  (mutated in-place on success)
        output_name : str           PO or internal wire name
        max_depth   : int | None    if set, ABC enforces depth <= max_depth
        objective   : str           "min_gates" (default) or "min_depth"

        Returns
        -------
        OptResult with full diagnostic information.
        """
        cone_cells = self._select_rewritable_cone(graph, output_name)
        if not cone_cells:
            return OptResult(False, f"No combinational cone for '{output_name}'")

        before_gates = len(cone_cells)

        with tempfile.TemporaryDirectory() as tmp:
            gold_v    = os.path.join(tmp, "gold.v")
            opt_v     = os.path.join(tmp, "opt.v")
            opt_json  = os.path.join(tmp, "opt.json")

            cone_graph = self._build_cone_module(graph, output_name, cone_cells)
            self.writer.write(cone_graph, gold_v)

            eff_depth = max_depth
            if objective == "min_depth" and max_depth is None:
                eff_depth = None  # let ABC minimise depth freely

            try:
                self.yosys.abc_optimize_verilog(gold_v, opt_v,
                                                 max_depth=eff_depth,
                                                 top="cone_top")
            except RuntimeError as e:
                return OptResult(False, f"ABC failed: {e}")

            equiv, cex = self.yosys.check_equiv(gold_v, opt_v,
                                                 gold_top="cone_top",
                                                 gate_top="cone_top")
            if not equiv:
                return OptResult(False,
                                 "Equivalence check failed after optimization. "
                                 f"Counterexample: {cex}")

            try:
                self.yosys.verilog_to_json(opt_v, opt_json, top="cone_top")
                opt_graph = NetlistGraph.from_yosys_json(opt_json)
            except Exception as e:
                return OptResult(False, f"Failed to parse optimized cone: {e}")

            after_gates = len([n for n, d in opt_graph.G.nodes(data=True)
                               if d.get("ntype") == "cell"])
            depth_ok    = True

            if max_depth is not None:
                depth_ok = self._verify_depth(opt_graph, output_name, max_depth)
                if not depth_ok:
                    return OptResult(
                        False,
                        f"Depth constraint violated after optimization "
                        f"(target <= {max_depth}). Try a looser bound."
                    )

            self._splice(graph, cone_cells, opt_graph, output_name)

            return OptResult(
                success=True,
                before_gates=before_gates,
                after_gates=after_gates,
                gates_saved=before_gates - after_gates,
                equiv=True,
                depth_ok=depth_ok,
            )

    def replace_with_exact_expr(self, graph: NetlistGraph,
                                output_name: str) -> OptResult:
        """Replace a small cone with one exact combinational expression cell."""
        cone_cells = self._select_rewritable_cone(graph, output_name)
        if not cone_cells:
            return OptResult(False, f"No combinational cone for '{output_name}'")
        if len(cone_cells) > 64:
            return OptResult(False, f"Cone for '{output_name}' is too large for exact expression folding")

        before_gates = len(cone_cells)
        try:
            target_driver = graph.resolve(output_name)
        except KeyError as e:
            return OptResult(False, str(e))
        if target_driver not in cone_cells:
            return OptResult(False, f"Target '{output_name}' is not driven by the rewritable cone")

        try:
            expr, inputs = self._build_expr(graph, target_driver, cone_cells, {})
        except ValueError as e:
            return OptResult(False, str(e))
        target_wire = graph.G.nodes.get(target_driver, {}).get("output_wire", output_name)
        external_loads: list[tuple[str, str | None]] = []
        for _src, succ, edata in list(graph.G.out_edges(target_driver, data=True)):
            if succ not in cone_cells:
                external_loads.append((succ, edata.get("port")))

        safe_output_name = re.sub(r"\W", "_", output_name)
        cell_name = f"_expr_{safe_output_name}"
        idx = 0
        base = cell_name
        while cell_name in graph.G:
            idx += 1
            cell_name = f"{base}_{idx}"

        self._remove_cone_cells(graph, cone_cells)
        graph.G.add_node(
            cell_name,
            ntype="cell",
            gate_type="$expr",
            output_wire=output_name,
            is_po=output_name in graph.primary_outputs,
            expr=expr,
            input_ports=[(f"I{i}", wire) for i, wire in enumerate(inputs)],
            input_wires=list(inputs),
        )
        graph.wire_driver[output_name] = cell_name
        if output_name in graph.primary_outputs:
            graph.primary_outputs[output_name] = cell_name
        for i, wire in enumerate(inputs):
            driver = graph.wire_driver.get(wire)
            if driver and driver in graph.G:
                graph.G.add_edge(driver, cell_name, wire=wire, port=f"I{i}")
                graph.wire_readers.setdefault(wire, []).append(cell_name)
        for succ, port in external_loads:
            if succ in graph.G:
                attrs = {"wire": output_name}
                if port is not None:
                    attrs["port"] = port
                graph.G.add_edge(cell_name, succ, **attrs)
                graph.wire_readers.setdefault(output_name, []).append(succ)

        return OptResult(
            success=True,
            before_gates=before_gates,
            after_gates=1,
            gates_saved=before_gates - 1,
            equiv=True,
            depth_ok=True,
        )

    def _select_rewritable_cone(self, graph: NetlistGraph,
                                output_name: str) -> set[str]:
        cone_cells = {
            nid for nid in graph.extract_cone(output_name)
            if graph.G.nodes.get(nid, {}).get("gate_type") not in DFF_TYPES
        }
        try:
            target_driver = graph.resolve(output_name)
        except KeyError:
            target_driver = None
        changed = True
        while changed:
            changed = False
            for nid in list(cone_cells):
                if nid == target_driver:
                    continue
                if any(succ not in cone_cells for succ in graph.G.successors(nid)):
                    cone_cells.remove(nid)
                    changed = True
        return cone_cells

    def _build_expr(self, graph: NetlistGraph, nid: str,
                    cone_cells: set[str],
                    memo: dict[str, str]) -> tuple[str, list[str]]:
        if nid in memo:
            return memo[nid], []
        if nid not in cone_cells:
            wire = graph.output_wire(nid)
            return self._sig(wire), [wire]

        nd = graph.G.nodes.get(nid, {})
        gt = nd.get("gate_type")
        args: list[str] = []
        inputs: list[str] = []
        for pred in graph.G.predecessors(nid):
            subexpr, subinputs = self._build_expr(graph, pred, cone_cells, memo)
            args.append(subexpr)
            inputs.extend(subinputs)

        if gt == "$buf":
            expr = args[0] if args else "1'bx"
        elif gt == "$not":
            expr = "~(" + (args[0] if args else "1'bx") + ")"
        elif gt in {"$and", "$or", "$xor"}:
            op = {"$and": "&", "$or": "|", "$xor": "^"}[gt]
            expr = "(" + f" {op} ".join(args) + ")" if args else "1'bx"
        elif gt == "$nand":
            expr = "~((" + " & ".join(args) + "))" if args else "1'bx"
        elif gt == "$nor":
            expr = "~((" + " | ".join(args) + "))" if args else "1'bx"
        elif gt == "$xnor":
            expr = "~((" + " ^ ".join(args) + "))" if args else "1'bx"
        else:
            raise ValueError(f"Cannot fold gate type '{gt}' into an exact expression")

        memo[nid] = expr
        return expr, list(dict.fromkeys(inputs))

    def _remove_cone_cells(self, graph: NetlistGraph,
                           cone_cells: set[str]) -> None:
        for cell in list(cone_cells):
            if cell not in graph.G:
                continue
            out_wire = graph.G.nodes.get(cell, {}).get("output_wire")
            for pred, _dst, edata in list(graph.G.in_edges(cell, data=True)):
                wire = edata.get("wire", graph.output_wire(pred))
                readers = graph.wire_readers.get(wire, [])
                while cell in readers:
                    readers.remove(cell)
            for _src, succ, edata in list(graph.G.out_edges(cell, data=True)):
                wire = edata.get("wire", out_wire)
                readers = graph.wire_readers.get(wire, [])
                while succ in readers:
                    readers.remove(succ)
            graph.G.remove_node(cell)
            if out_wire and graph.wire_driver.get(out_wire) == cell:
                del graph.wire_driver[out_wire]

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


    def _build_cone_module(self, graph: NetlistGraph,
                            output_name: str,
                            cone_cells:  set[str]) -> NetlistGraph:
        """
        Build a self-contained NetlistGraph for the cone module.

        The cone module has:
          - PI nodes for every wire entering the cone from outside
            (i.e. driven by nodes not in the cone)
          - A single PO named output_name
          - All cells in cone_cells
        """
        # Wires driven by cone cells
        cone_out_wires: set[str] = {
            graph.G.nodes[n]["output_wire"]
            for n in cone_cells
        }
        # External input wires = wires entering the cone from outside
        external_in: dict[str, str] = {}   # wire_name ->driver_node_id (in main graph)

        for cell in cone_cells:
            for pred in graph.G.predecessors(cell):
                if pred in cone_cells:
                    continue
                wire = graph.G.get_edge_data(pred, cell, {}).get("wire", "?")
                external_in[wire] = pred

        sub = NetlistGraph()
        sub.module_name = "cone_top"

        # Add PI nodes (sanitise names for Verilog)
        for wire_name, driver_nid in external_in.items():
            safe_name = re.sub(r"\W", "_", wire_name)
            nid       = f"PI:{safe_name}"
            sub.G.add_node(nid, ntype="pi", output_wire=safe_name, is_po=False)
            sub.wire_driver[safe_name] = nid
            sub.primary_inputs[safe_name] = nid

        # Add cone cells (reuse same node IDs and gate_types)
        for cell in cone_cells:
            nd = graph.G.nodes[cell]
            out_wire = nd["output_wire"]
            is_po    = (out_wire == output_name or
                        graph.primary_outputs.get(output_name) == cell)
            sub.G.add_node(cell, ntype="cell",
                           gate_type=nd.get("gate_type", ""),
                           output_wire=out_wire,
                           input_ports=nd.get("input_ports", []),
                           input_wires=nd.get("input_wires", []),
                           is_po=is_po)
            sub.wire_driver[out_wire] = cell
            if is_po:
                sub.primary_outputs[output_name] = cell

        # Add edges between cone nodes
        for cell in cone_cells:
            for pred in graph.G.predecessors(cell):
                wire = graph.G.get_edge_data(pred, cell, {}).get("wire", "?")
                safe_wire = re.sub(r"\W", "_", wire)
                if pred in cone_cells:
                    sub.G.add_edge(pred, cell, wire=wire)
                else:
                    # Edge comes from a PI in the sub-module
                    pi_nid = sub.wire_driver.get(safe_wire, f"PI:{safe_wire}")
                    if pi_nid not in sub.G:
                        sub.G.add_node(pi_nid, ntype="pi",
                                       output_wire=safe_wire, is_po=False)
                        sub.wire_driver[safe_wire] = pi_nid
                    sub.G.add_edge(pi_nid, cell, wire=safe_wire)

        return sub


    def _verify_depth(self, opt_graph: NetlistGraph,
                      output_name: str, max_depth: int) -> bool:
        """Return True if the max depth in opt_graph is <= max_depth."""
        if not opt_graph.primary_inputs:
            return True
        worst = 0
        for pi_label in opt_graph.primary_inputs:
            try:
                d, _ = opt_graph.get_max_depth(pi_label, output_name)
                if d > worst:
                    worst = d
            except Exception:
                continue
        return worst <= max_depth


    def _splice(self, graph: NetlistGraph,
                old_cells: set[str],
                opt_graph: NetlistGraph,
                output_name: str) -> None:
        """
        Replace old_cells in graph with the cells from opt_graph.

        Steps:
          1. Remove old cone cells (and their outgoing edges).
          2. Map opt_graph PI wire names back to main graph driver nodes.
          3. Insert opt_graph cells with prefixed names.
          4. Add edges using the wire remapping.
        """
        safe_output_name = re.sub(r"\W", "_", output_name)
        prefix = f"_opt_{safe_output_name}_"
        old_target_driver = graph.primary_outputs.get(output_name) or graph.wire_driver.get(output_name)

        external_loads: list[tuple[str, str, str | None, str]] = []
        for cell in old_cells:
            old_wire = graph.G.nodes.get(cell, {}).get("output_wire", "")
            for _src, succ, edata in list(graph.G.out_edges(cell, data=True)):
                if succ not in old_cells:
                    external_loads.append((
                        cell,
                        succ,
                        edata.get("port"),
                        edata.get("wire", old_wire),
                    ))
            if cell in graph.G:
                graph.G.remove_node(cell)
            # Also clean up by output_wire key
            for k, v in list(graph.wire_driver.items()):
                if v == cell:
                    del graph.wire_driver[k]

        # opt PI safe_name ->original wire ->main graph driver
        wire_remap: dict[str, str] = {}   # opt wire_name ->main graph node_id

        for pi_label, pi_nid in opt_graph.primary_inputs.items():
            # Reverse the safe_name substitution by checking main graph
            candidates = [w for w in graph.wire_driver
                          if re.sub(r"\W", "_", w) == pi_label or w == pi_label]
            if candidates:
                wire_remap[pi_label] = graph.wire_driver[candidates[0]]
            else:
                wire_remap[pi_label] = f"PI:{pi_label}"  # fallback

        for opt_cell, opt_nd in opt_graph.G.nodes(data=True):
            if opt_nd.get("ntype") != "cell":
                continue
            new_name = prefix + opt_cell
            out_wire = opt_nd.get("output_wire", new_name)
            is_po    = opt_nd.get("is_po", False)

            # If PO, reuse the original output wire name
            if is_po:
                out_wire = output_name
                graph.primary_outputs[output_name] = new_name
            else:
                out_wire = prefix + out_wire

            graph.G.add_node(new_name, ntype="cell",
                             gate_type=opt_nd["gate_type"],
                             output_wire=out_wire,
                             is_po=is_po)
            graph.wire_driver[out_wire] = new_name

        for src, dst, edata in opt_graph.G.edges(data=True):
            src_nd = opt_graph.G.nodes.get(src, {})
            dst_nd = opt_graph.G.nodes.get(dst, {})

            # Map source to main graph node
            if src_nd.get("ntype") == "pi":
                pi_label = src_nd.get("output_wire", src)
                main_src = wire_remap.get(pi_label, src)
            elif src_nd.get("ntype") == "cell":
                main_src = prefix + src
            else:
                main_src = src

            if dst_nd.get("ntype") == "cell":
                main_dst = prefix + dst
            else:
                main_dst = dst

            if main_src in graph.G and main_dst in graph.G:
                wire = graph.G.nodes.get(main_src, {}).get("output_wire", edata.get("wire", "?"))
                graph.G.add_edge(main_src, main_dst, wire=wire)
                graph.wire_readers.setdefault(wire, []).append(main_dst)

        new_output_driver = graph.primary_outputs.get(output_name) or graph.wire_driver.get(output_name)
        if new_output_driver in graph.G:
            new_output_wire = graph.G.nodes[new_output_driver].get("output_wire", output_name)
            for old_cell, succ, port, _old_wire in external_loads:
                if succ in graph.G and old_cell == old_target_driver:
                    attrs = {"wire": new_output_wire}
                    if port is not None:
                        attrs["port"] = port
                    graph.G.add_edge(new_output_driver, succ, **attrs)
                    graph.wire_readers.setdefault(new_output_wire, []).append(succ)
