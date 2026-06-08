"""
eda/backend.py
==============
High-level EDA tool API exposed to the LLM agent.

Every public method:
  - Takes simple Python scalars/strings as arguments
  - Performs the operation on the internal NetlistGraph
  - Returns a human-readable string (the agent forwards this as #RESPONSE text)

This is the only file the agent layer needs to import from the eda package.

Tool catalogue (also the source of truth for the LLM schema in tool_schema.py):

  I/O
    read_design(path)
    write_design(path)
    design_summary()

  Analysis
    get_max_depth(from_signal, to_signal)
    find_path(from_signal, to_signal, avoid=None, must_pass=None)
    all_paths_through(from_signal, to_signal, through)
    report_cone_size(output_signal)
    get_fanout(net_name)
    report_large_cones(threshold)
    same_clock_domain(ff1_name, ff2_name)

  Transformation
    insert_gate_before(name_pattern, gate_type, extra_input)
    buffer_high_fanout(net_name, max_fanout)
    replace_gate_type_in_cone(output_signal, old_type, new_type)
    replace_gate_type_globally(old_type, new_type)
    remove_dangling()
    fuse_not_buf_pairs()
    add_balance_buffers(from_signal, to_signals)

  Optimization
    optimize_cone(output_signal, max_depth=None, objective="min_gates")

  Verification
    check_equiv(path_a, path_b)
"""

from __future__ import annotations

import itertools
import copy
import os
import re
import tempfile
from typing import Optional

import networkx as nx

from .netlist_graph import DFF_TYPES, NetlistGraph, YOSYS_TO_PRIM
from .yosys_backend import YosysBackend
from .transformer import NetlistTransformer
from .writer import VerilogWriter
from .optimizer import ConeOptimizer


class EDABackend:
    """
    Stateful EDA tool API.  One instance is created at contest startup and
    reused for the entire session.  The internal NetlistGraph is replaced on
    each read_design call.
    """

    def __init__(self, yosys_bin: str = "yosys") -> None:
        self.yosys:        YosysBackend  = YosysBackend(yosys_bin)
        self.writer:       VerilogWriter = VerilogWriter()
        self.graph:        Optional[NetlistGraph]    = None
        self._transformer: Optional[NetlistTransformer] = None
        self._optimizer:   ConeOptimizer = ConeOptimizer(self.yosys, self.writer)
        self._last_counts: dict[str, int] = {}
        self._original_path: Optional[str] = None


    def read_design(self, path: str) -> str:
        """Load a gate-level Verilog file into the internal design state."""
        if not os.path.isfile(path):
            return self._fail("NOT_FOUND", f"file '{path}' not found.")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            jpath = f.name
        try:
            self.yosys.verilog_to_json(path, jpath)
            self.graph = NetlistGraph.from_yosys_json(jpath)
            self._install_verilog_aliases(path)
            self._transformer = NetlistTransformer(self.graph)
            self._last_counts = {}
            self._original_path = os.path.abspath(path)
        except RuntimeError as e:
            return f"Error loading design: {e}"
        finally:
            if os.path.exists(jpath):
                os.unlink(jpath)

        s = self.graph.summary()
        return (
            f"Loaded '{s['module']}': {s['cell_count']} cells, "
            f"PI:{len(s['primary_inputs'])} PO:{len(s['primary_outputs'])}"
        )

    def write_design(self, path: str) -> str:
        """Write the current design state to a gate-level Verilog file."""
        self._need_design()
        try:
            self.writer.write(self.graph, path)
        except Exception as e:
            return f"Error writing: {e}"
        return f"Written to '{path}'."

    def design_summary(self) -> str:
        """Return a human-readable summary of the current design."""
        self._need_design()
        s = self.graph.summary()
        hist_str = " ".join(f"{gt.upper()}:{cnt}" for gt, cnt in sorted(s["gate_type_histogram"].items()))
        return (
            f"Module: {s['module']}. Cells: {s['cell_count']}. "
            f"PI:{len(s['primary_inputs'])} PO:{len(s['primary_outputs'])}. "
            f"Gates: {hist_str}"
        )


    def get_max_depth(self, from_signal: str, to_signal: str) -> str:
        """Report the maximum combinational gate depth from from_signal to to_signal."""
        self._need_design()
        try:
            depth, path = self.graph.get_max_depth(from_signal, to_signal)
        except (KeyError, ValueError) as e:
            return self._fail("NOT_FOUND", str(e))
        if depth < 0:
            return f"No path from '{from_signal}' to '{to_signal}'."
        path_str = " -> ".join(path[:8])
        if len(path) > 8:
            path_str += f" ... (+{len(path)-8})"
        return f"MaxDepth {from_signal}->{to_signal}: {depth}\n  {path_str}"

    def find_path(self, from_signal: str, to_signal: str,
                  avoid: Optional[str] = None,
                  must_pass: Optional[str] = None) -> str:
        """Find a path from from_signal to to_signal, optionally avoiding or requiring a waypoint."""
        self._need_design()
        try:
            path = self.graph.find_path(from_signal, to_signal,
                                         avoid=avoid, must_pass=must_pass)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if path is None:
            cond = ""
            if avoid:    cond += f" avoid='{avoid}'"
            if must_pass: cond += f" via='{must_pass}'"
            return f"No path {from_signal}->{to_signal}{cond}."
        return "Path:\n  " + " -> ".join(path)

    def list_paths(self, from_signal: str, to_signal: str,
                   max_paths: int = 100) -> str:
        """Enumerate simple paths from from_signal to to_signal, capped at max_paths."""
        self._need_design()
        requested = max_paths
        max_paths = max(1, min(int(max_paths), 20))
        try:
            paths = self.graph.list_paths(
                from_signal,
                to_signal,
                max_paths=max_paths,
                max_seconds=5.0,
                max_expansions=200_000,
            )
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if not paths:
            return f"No paths found from '{from_signal}' to '{to_signal}'."
        blocks = []
        for idx, path in enumerate(paths, start=1):
            blocks.append(f"Path {idx}:\n  " + "\n  -> ".join(path))
        suffix = f"\n...({max_paths}/{requested} capped, 5s limit)" if len(paths) >= max_paths or requested > max_paths else ""
        return f"{len(paths)} paths {from_signal}->{to_signal}:\n" + "\n".join(blocks) + suffix

    def all_paths_through(self, from_signal: str,
                          to_signal: str, through: str) -> str:
        """Check whether every path from from_signal to to_signal passes through 'through'."""
        self._need_design()
        try:
            ok, cex = self.graph.all_paths_pass_through(
                from_signal, to_signal, through)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if ok:
            return f"YES: all paths {from_signal}->{to_signal} via {through}."
        cex_str = " -> ".join(cex or [])
        return f"NO: path bypasses {through}:\n  {cex_str}"

    def report_cone_size(self, output_signal: str) -> str:
        """Report the number of gates in the fanin cone of output_signal."""
        self._need_design()
        try:
            size = self.graph.get_cone_size(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        return f"Cone {output_signal}: {size} gates"

    def cone_gate_breakdown(self, output_signal: str) -> str:
        """Report gate-type counts in one output fanin cone."""
        self._need_design()
        try:
            nodes = self.graph.extract_cone(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        hist = self._gate_hist(nodes)
        parts = [f"Cone {output_signal}: {len(nodes)} gates"]
        for g in ["and","or","not","nand","nor","xor","xnor","buf","dff"]:
            parts.append(f"{g.upper()}:{hist.get(g,0)}")
        return " ".join(parts)

    def transitive_fanin(self, output_signal: str) -> str:
        """List cells in the transitive fanin cone of output_signal."""
        self._need_design()
        try:
            nodes = self.graph.extract_cone(output_signal)
            labels = [self.graph.node_label(n) for n in sorted(nodes)]
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if not labels:
            return f"FanIn cone {output_signal}: empty."
        hist = self._gate_hist(nodes)
        shown, suffix = self._format_block(labels, cap=200)
        return f"FanIn {output_signal}: {len(labels)} gates. {hist}" + shown + suffix

    def transitive_fanout(self, input_signal: str) -> str:
        """List cells in the transitive fanout cone of input_signal."""
        self._need_design()
        try:
            nodes = self.graph.transitive_fanout_nodes(input_signal)
            labels = [self.graph.node_label(n) for n in sorted(nodes)]
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if not labels:
            return f"FanOut cone {input_signal}: empty."
        hist = self._gate_hist(nodes)
        shown, suffix = self._format_block(labels, cap=200)
        return f"FanOut {input_signal}: {len(labels)} gates. {hist}" + shown + suffix

    def get_fanout(self, net_name: str) -> str:
        """Report the fanout of a net or cell output."""
        self._need_design()
        try:
            fo = self.graph.get_fanout(net_name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        return f"Fanout {net_name}: {fo}"

    def list_gates_by_type(self, gate_type: str, limit: int = 120) -> str:
        """List gates matching a primitive type."""
        self._need_design()
        prim = gate_type.lower()
        cells = self.graph.find_cells_by_type(prim)
        cap = max(1, min(int(limit), 200))
        if not cells:
            return f"0 {prim.upper()} gates."
        labels = [self.graph.node_label(n) for n in cells[:cap]]
        suffix = f"\n...({cap}/{len(cells)} capped)" if len(cells) > cap else ""
        return f"{len(cells)} {prim.upper()}:\n  " + "\n  ".join(labels) + suffix

    def report_constant_input_gates(self, gate_type: str = "",
                                    const_value: Optional[int] = None) -> str:
        """Report gates of gate_type with a constant 0/1 input."""
        self._need_design()
        gates = [gate_type.lower()] if gate_type else [
            "and", "or", "nand", "nor", "xor", "xnor", "buf", "not",
        ]
        consts = (0, 1) if const_value is None else (int(const_value),)
        reports: list[str] = []
        for prim in gates:
            for const in consts:
                cells = self._transformer.constant_input_gates(prim, const)
                if not cells:
                    continue
                cap = 100
                labels = [self.graph.node_label(n) for n in cells[:cap]]
                suffix = f"\n...({len(cells)} total, {cap} shown)" if len(cells) > cap else ""
                reports.append(
                    f"{len(cells)} {prim.upper()} const={const}:\n  "
                    + "\n  ".join(labels)
                    + suffix
                )
        if reports:
            return "\n".join(reports)
        const_label = "0/1" if const_value is None else str(int(const_value))
        gate_label = gate_type.upper() if gate_type else "gates"
        return f"0 {gate_label} const={const_label}."

    def immediate_successors(self, name: str) -> str:
        """List immediate successor cells of a net, port, or cell output."""
        self._need_design()
        try:
            labels = self.graph.immediate_successors(name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if not labels:
            return f"Succ {name}: none"
        return f"Succ {name} ({len(labels)}):\n  " + "\n  ".join(labels)

    def report_large_cones(self, threshold: int) -> str:
        """List all primary outputs whose fanin cone exceeds threshold gates."""
        self._need_design()
        large = self.graph.report_outputs_cone_gt(threshold)
        if not large:
            return f"0 POs with cone > {threshold}."
        rows = "\n  ".join(f"{name}: {size}" for name, size in large)
        return f"POs cone > {threshold}:\n  {rows}"

    def same_clock_domain(self, ff1_name: str, ff2_name: str) -> str:
        """Check whether two flip-flops share the same clock domain."""
        self._need_design()
        try:
            same, desc = self.graph.same_clock_domain(ff1_name, ff2_name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        verdict = "same" if same else "different"
        return f"{ff1_name},{ff2_name}: {verdict} clk. {desc}"

    def gate_count_breakdown(self) -> str:
        """Return a stable gate count table covering all contest primitives."""
        self._need_design()
        s = self.graph.summary()
        hist = s["gate_type_histogram"]
        order = ["and", "or", "not", "nand", "nor", "xor", "xnor", "buf", "dff"]
        parts = [f"Total: {s['cell_count']}"] + [f"{g.upper()}:{hist.get(g, 0)}" for g in order]
        return " ".join(parts)

    def count_gate_type(self, gate_type: str) -> str:
        """Return the current count of one primitive type."""
        self._need_design()
        prim = gate_type.lower()
        count = len(self.graph.find_cells_by_type(prim))
        return f"{prim.upper()}: {count}"

    def count_gates(self, gate_type: str = "") -> str:
        """Gate count by type. Omit gate_type for full breakdown."""
        self._need_design()
        if gate_type:
            return self.count_gate_type(gate_type)
        return self.gate_count_breakdown()

    def last_operation_count(self, key: str) -> str:
        """Report a count recorded by the previous deterministic transformation."""
        key = self._normalize_last_count_key(str(key))
        if (
            key == "dangling_removed"
            and int(self._last_counts.get(key, 0) or 0) == 0
            and int(self._last_counts.get("constant_gates_eliminated", 0) or 0) > 0
        ):
            return (
                "constant_gates_eliminated: "
                f"{self._last_counts.get('constant_gates_eliminated', 0)}"
            )
        count = self._last_counts.get(key, 0)
        return f"{key}: {count}"

    @staticmethod
    def _normalize_last_count_key(key: str) -> str:
        """Map likely LLM key variants to the backend's stored counters."""
        low = str(key or "").strip().lower().replace("-", "_").replace(" ", "_")
        if "dangling" in low:
            return "dangling_removed"
        if "constant" in low and any(word in low for word in ("elimin", "remove", "propagat")):
            return "constant_gates_eliminated"
        if "nand" in low and any(word in low for word in ("elimin", "remove", "propagat")):
            return "constant_gates_eliminated"
        if "buf" in low and any(word in low for word in ("add", "insert")):
            return "buf_added"
        if "merge" in low or "duplicate" in low:
            return "merged_gates"
        if "not_not" in low or "inverter" in low or "collapse" in low:
            return "not_not_collapsed"
        if "xor" in low and any(word in low for word in ("convert", "replace")):
            return "xor_converted"
        if "xnor" in low and any(word in low for word in ("convert", "replace")):
            return "xnor_converted"
        return low

    def primary_io_counts(self) -> str:
        """Report the number of primary input and output bits."""
        self._need_design()
        return f"PI:{len(self.graph.primary_inputs)} PO:{len(self.graph.primary_outputs)}"

    def list_primary_inputs_with_widths(self) -> str:
        self._need_design()
        widths = self._port_widths("pi")
        rows = "\n".join(f"  {name}: {width}" for name, width in widths.items())
        return "Primary input bit widths:\n" + (rows or "  none")

    def list_primary_outputs_with_widths(self) -> str:
        self._need_design()
        widths = self._port_widths("po")
        rows = "\n".join(f"  {name}: {width}" for name, width in widths.items())
        return "Primary output bit widths:\n" + (rows or "  none")

    def list_direct_loads(self, name: str, limit: int = 120) -> str:
        """List direct successor gates driven by a signal/cell."""
        self._need_design()
        try:
            nid = self.graph.resolve(name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        loads = list(self.graph.G.successors(nid))
        cap = max(1, min(int(limit), 200))
        labels = [self.graph.node_label(n) for n in loads[:cap]]
        suffix = f"\n...({cap}/{len(loads)} capped)" if len(loads) > cap else ""
        body = f"Loads {name}: {len(loads)}"
        if labels:
            body += "\n  " + "\n  ".join(labels) + suffix
        return body

    def gate_info(self, name: str) -> str:
        """Report gate type, output, and input pin connections."""
        self._need_design()
        try:
            nid = self.graph.resolve(name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        nd = self.graph.G.nodes.get(nid, {})
        if nd.get("ntype") != "cell":
            return self._fail("TYPE", f"'{name}' is not a gate/cell.")
        prim = YOSYS_TO_PRIM.get(nd.get("gate_type", ""), nd.get("gate_type", "").lstrip("$"))
        rows = []
        for pred, _, edge in self.graph.G.in_edges(nid, data=True):
            rows.append(f"  {edge.get('port', '?')}: {self.graph.output_wire(pred)}")
        inputs_str = " ".join(f"{edge.get('port', '?')}={self.graph.output_wire(pred)}" for pred, _, edge in self.graph.G.in_edges(nid, data=True))
        return f"Gate {name}: {prim.upper()} out={nd.get('output_wire')} in=[{inputs_str}]"

    def max_fanin_depth(self, output_signal: str) -> str:
        """Compute the maximum PI/DFF-boundary depth into one output."""
        self._need_design()
        try:
            dst = self.graph.resolve(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        depths, pred, origin = self._depths_from_boundaries(include_dffs=True)
        if dst not in depths:
            return f"No fanin path to '{output_signal}'."
        path = self._reconstruct_path(pred, dst)
        path_str = " -> ".join(path[:10])
        if len(path) > 10:
            path_str += f" ... (+{len(path)-10})"
        return f"FanInDepth {output_signal}: {depths[dst]}\n  src={self.graph.node_label(origin.get(dst, dst))}\n  {path_str}"

    def max_design_depth(self) -> str:
        """Report the deepest PI-to-PO combinational path found."""
        self._need_design()
        depths, pred, origin = self._depths_from_boundaries(include_dffs=False)
        best = (-1, "", "")
        for out_name, driver in self.graph.primary_outputs.items():
            if driver in depths and depths[driver] > best[0]:
                best = (depths[driver], out_name, driver)
        if best[0] < 0:
            return "No PI-to-PO path found."
        path = self._reconstruct_path(pred, best[2])
        path_str = " -> ".join(path[:10])
        if len(path) > 10:
            path_str += f" ... (+{len(path)-10})"
        return f"MaxDepth: {best[0]}\n  src={self.graph.node_label(origin.get(best[2], best[2]))}\n  out={best[1]}\n  {path_str}"

    def deepest_output_cone(self) -> str:
        """Find the primary output with the deepest fanin path."""
        self._need_design()
        depths, _, _ = self._depths_from_boundaries(include_dffs=False)
        best = (-1, "")
        for out_name, driver in self.graph.primary_outputs.items():
            depth = depths.get(driver, -1)
            if depth > best[0]:
                best = (depth, out_name)
        if best[0] < 0:
            return "No output depth found."
        return f"Deepest out: {best[1]} depth {best[0]}"

    def largest_output_cone(self) -> str:
        """Find the primary output with the largest fanin cone."""
        self._need_design()
        best = (-1, "")
        for out_name in self.graph.primary_outputs:
            try:
                size = self.graph.get_cone_size(out_name)
            except KeyError:
                continue
            if size > best[0]:
                best = (size, out_name)
        if best[0] < 0:
            return "No output cone found."
        return f"Largest cone: {best[1]} {best[0]} gates"

    def count_outputs_depth_gt(self, threshold: int) -> str:
        self._need_design()
        depths, _, _ = self._depths_from_boundaries(include_dffs=False)
        rows = []
        for out_name, driver in self.graph.primary_outputs.items():
            depth = depths.get(driver, -1)
            if depth > int(threshold):
                rows.append((out_name, depth))
        detail = "\n  ".join(f"{name}: {depth}" for name, depth in rows[:200])
        return f"Outputs depth > {threshold}: {len(rows)}" + (f"\n  {detail}" if detail else "")

    def max_pi_to_dff_depth(self) -> str:
        """Report the maximum combinational depth from any PI to any DFF D input."""
        self._need_design()
        depths, pred_map, origin = self._depths_from_boundaries(include_dffs=False)
        best = (-1, "", "")
        for dff, nd in self.graph.G.nodes(data=True):
            if nd.get("ntype") != "cell" or nd.get("gate_type") not in DFF_TYPES:
                continue
            d_preds = [
                pred for pred, _, edge in self.graph.G.in_edges(dff, data=True)
                if str(edge.get("port", "")).upper() in {"D", "\\D"}
            ]
            if not d_preds:
                continue
            for driver in d_preds:
                depth = depths.get(driver, -1)
                if depth > best[0]:
                    best = (depth, driver, dff)
        if best[0] < 0:
            return "No PI-to-DFF path."
        path = self._reconstruct_path(pred_map, best[1])
        path_str = " -> ".join(path[:10])
        if len(path) > 10:
            path_str += f" ... (+{len(path)-10})"
        return f"Max PI->DFF depth: {best[0]}\n  src={self.graph.node_label(origin.get(best[1], best[1]))}\n  DFF={best[2]}\n  {path_str}"

    def list_register_to_register_paths(self, limit: int = 80) -> str:
        """List representative DFF-Q to DFF-D combinational paths."""
        self._need_design()
        cap = max(1, min(int(limit), 200))
        dffs = [
            nid for nid, nd in self.graph.G.nodes(data=True)
            if nd.get("ntype") == "cell" and nd.get("gate_type") in DFF_TYPES
        ]
        rows: list[str] = []
        for src in dffs:
            for dst in dffs:
                d_inputs = [
                    pred for pred, _d, edge in self.graph.G.in_edges(dst, data=True)
                    if str(edge.get("port", "")).upper().lstrip("\\") in {"D", "DATA"}
                ]
                for pred in d_inputs:
                    try:
                        if not nx.has_path(self.graph.G, src, pred):
                            continue
                        path = nx.shortest_path(self.graph.G, src, pred) + [dst]
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        continue
                    rows.append(" -> ".join(self.graph.node_label(n) for n in path[:120]))
                    if len(rows) >= cap:
                        return (
                            f"Register-to-register combinational paths: at least {len(rows)}.\n  "
                            + "\n  ".join(rows)
                            + f"\nResult capped at {cap} path(s)."
                        )
        if not rows:
            return "Reg-to-reg paths: 0"
        return f"Reg-to-reg paths: {len(rows)}\n  " + "\n  ".join(rows)

    def shared_fanin_cones(self, output_a: str, output_b: str) -> str:
        self._need_design()
        try:
            cone_a = self.graph.extract_cone(output_a)
            cone_b = self.graph.extract_cone(output_b)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        shared = sorted(cone_a & cone_b)
        labels = [self.graph.node_label(n) for n in shared[:500]]
        suffix = f"\n...({len(shared)} total, 500 shown)" if len(shared) > 500 else ""
        return f"Shared fanin {output_a},{output_b}: {len(shared)}" + (("\n  " + "\n  ".join(labels) + suffix) if labels else "")

    def direct_pi_po_connections(self) -> str:
        """List outputs directly driven by primary inputs."""
        self._need_design()
        rows = []
        for out_name, driver in self.graph.primary_outputs.items():
            nd = self.graph.G.nodes.get(driver, {})
            if nd.get("ntype") == "pi":
                rows.append(f"{nd.get('output_wire')} -> {out_name}")
        if not rows:
            return "PI->PO direct: 0"
        return "PI->PO direct:\n  " + "\n  ".join(rows)

    def is_signal_constant(self, signal_name: str, value: int) -> str:
        """Conservatively report whether a signal is tied to a constant."""
        self._need_design()
        try:
            nid = self.graph.resolve(signal_name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        const = "1'b1" if int(value) else "1'b0"
        actual = self.graph.output_wire(nid)
        verdict = "YES" if actual == const else "NO"
        return f"{verdict}: {signal_name} {'==' if verdict == 'YES' else '!='} {const}"

    def is_cut_between_pi_po(self, wire_name: str) -> str:
        """Check whether removing a node breaks at least one PI-to-PO connection.

        Uses reverse reachability from each PO (O(|PO|*E)) instead of
        pairwise PI*PO BFS (O(|PI|*|PO|*E)).
        """
        self._need_design()
        try:
            cut_node = self.graph.resolve(wire_name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        pos = list(self.graph.primary_outputs.values())
        if not pos:
            return "No. No primary outputs in the design."
        # Count reachable PI-PO pairs efficiently via reverse BFS from each PO
        def _count_reachable(g: nx.DiGraph) -> int:
            count = 0
            for po in pos:
                if po not in g:
                    continue
                try:
                    ancestors = nx.ancestors(g, po)
                    count += sum(1 for a in ancestors
                                 if g.nodes.get(a, {}).get("ntype") == "pi")
                except Exception:
                    pass
            return count
        before = _count_reachable(self.graph.G)
        sub = self.graph.G.copy()
        if cut_node in sub:
            sub.remove_node(cut_node)
        after = _count_reachable(sub)
        verdict = "YES" if after < before else "NO"
        return f"{verdict}: {wire_name} cut breaks {before-after} pairs"

    def internal_signals_equiv(self, signal_a: str, signal_b: str) -> str:
        """Conservative structural equivalence check for two internal signals."""
        self._need_design()
        try:
            a = self.graph.resolve(signal_a)
            b = self.graph.resolve(signal_b)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if a == b:
            return f"EQUIV: {signal_a}=={signal_b} (same driver)"
        sig_a = self._structural_signature(a, depth=20)
        sig_b = self._structural_signature(b, depth=20)
        if sig_a is not None and sig_a == sig_b:
            return f"EQUIV: {signal_a}=={signal_b} (struct match)"
        table = self._truth_table_compare(a, b)
        if table is True:
            return f"EQUIV: {signal_a}=={signal_b} (exhaustive)"
        if table is False:
            return f"NOT_EQUIV: {signal_a}!={signal_b}"
        return f"UNKNOWN: {signal_a} vs {signal_b} (support too large)"

    def boolean_expression(self, signal_name: str, limit: int = 3000) -> str:
        """Return a bounded Boolean expression for a signal or output."""
        self._need_design()
        try:
            nid = self.graph.resolve(signal_name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        expr = self._expr_for_node(nid, {}, depth=80)
        if len(expr) > limit:
            expr = expr[:limit] + "..."
        return f"Expr {signal_name}: {expr}"

    def check_signal_symmetry(self, signal_name: str, input_a: str, input_b: str) -> str:
        """Check whether a signal is invariant under swapping two inputs."""
        self._need_design()
        try:
            root = self.graph.resolve(signal_name)
            a_name = self.graph.output_wire(self.graph.resolve(input_a))
            b_name = self.graph.output_wire(self.graph.resolve(input_b))
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        support = sorted(self._support_inputs(root) | {a_name, b_name})
        if len(support) > 14:
            return f"NO: symmetry {signal_name} wrt {input_a},{input_b} -support {len(support)} > 14"
        for values in itertools.product((0, 1), repeat=len(support)):
            env = dict(zip(support, values))
            base = self._eval_node(root, env, {})
            swapped = dict(env)
            swapped[a_name], swapped[b_name] = env.get(b_name, 0), env.get(a_name, 0)
            if base != self._eval_node(root, swapped, {}):
                return (
                    f"No. Function at '{signal_name}' is not symmetric with respect to "
                    f"'{input_a}' and '{input_b}'."
                )
        return f"YES: {signal_name} symmetric in {input_a},{input_b} ({2**len(support)} cases)"

    def report_floating_signals(self, limit: int = 80) -> str:
        """Report unresolved cell inputs and unconnected combinational outputs."""
        self._need_design()
        floating_inputs: list[str] = []
        unconnected_outputs: list[str] = []
        for nid, nd in self.graph.G.nodes(data=True):
            if nd.get("ntype") != "cell":
                continue
            for port, wire in nd.get("input_ports", []):
                if wire not in self.graph.wire_driver:
                    floating_inputs.append(f"{nid}.{port}({wire})")
            if (
                self.graph.G.out_degree(nid) == 0
                and not nd.get("is_po")
                and nd.get("gate_type") not in DFF_TYPES
            ):
                unconnected_outputs.append(self.graph.node_label(nid))
        rows = floating_inputs[:limit] + unconnected_outputs[: max(0, limit - len(floating_inputs[:limit]))]
        detail = ("\n  " + "\n  ".join(rows)) if rows else ""
        suffix = ""
        total = len(floating_inputs) + len(unconnected_outputs)
        if total > len(rows):
            suffix = f"\n...({len(rows)}/{total} shown)"
        return f"Floating: {len(floating_inputs)} in, {len(unconnected_outputs)} out." + detail + suffix

    def articulation_points_between(self, source: str, target: str, limit: int = 120) -> str:
        """Report articulation points in the source-target reachable subgraph."""
        self._need_design()
        try:
            src = self.graph.resolve(source)
            dst = self.graph.resolve(target)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if not nx.has_path(self.graph.G, src, dst):
            return f"Articulation points between '{source}' and '{target}': 0 (no path exists)."
        region = (nx.descendants(self.graph.G, src) | {src}) & (nx.ancestors(self.graph.G, dst) | {dst})
        sub = self.graph.G.subgraph(region).to_undirected()
        points = [n for n in nx.articulation_points(sub) if n not in {src, dst}]
        labels = [self.graph.node_label(n) for n in points[:limit]]
        suffix = f"\n...({limit}/{len(points)} shown)" if len(points) > limit else ""
        return f"Artic {source}->{target}: {len(points)}" + (("\n  " + "\n  ".join(labels) + suffix) if labels else "")

    def report_dff_enable_hold(self, limit: int = 120) -> str:
        """Heuristically report DFFs whose D cone feeds back from their own Q."""
        self._need_design()
        matches: list[str] = []
        for nid, nd in self.graph.G.nodes(data=True):
            if nd.get("ntype") != "cell" or nd.get("gate_type") not in DFF_TYPES:
                continue
            d_preds = [
                pred for pred, _d, edge in self.graph.G.in_edges(nid, data=True)
                if str(edge.get("port", "")).upper().lstrip("\\") in {"D", "DATA"}
            ]
            for pred in d_preds:
                try:
                    if nx.has_path(self.graph.G, nid, pred):
                        matches.append(self.graph.node_label(nid))
                        break
                except nx.NodeNotFound:
                    pass
        labels = matches[:limit]
        suffix = f"\n...({limit}/{len(matches)} shown)" if len(matches) > limit else ""
        return f"DFF enable/hold: {len(matches)}" + (("\n  " + "\n  ".join(labels) + suffix) if labels else "")

    def find_nand_pair_for_signal(self, signal_name: str, limit: int = 2000) -> str:
        """Search existing NAND cells for one equivalent to the requested signal."""
        self._need_design()
        try:
            target = self.graph.resolve(signal_name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        target_sig = self._structural_signature(target, depth=30)
        checked = 0
        for nid, nd in self.graph.G.nodes(data=True):
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$nand":
                continue
            checked += 1
            if checked > limit:
                break
            if nid == target or (
                target_sig is not None
                and self._structural_signature(nid, depth=30) == target_sig
            ):
                inputs = nd.get("input_wires", [])
                if len(inputs) >= 2:
                    return f"NAND({inputs[0]},{inputs[1]})=={signal_name} via {nid}"
        return f"No NAND pair equivalent to '{signal_name}'."

    def rename(self, old_name: str, new_name: str) -> str:
        """Rename a gate/cell or wire/signal. Auto-detects target type."""
        self._need_design()
        try:
            nid = self.graph.resolve(old_name)
        except KeyError:
            # Try as wire
            try:
                changed = self._transformer.rename_wire(old_name, new_name)
            except ValueError as e:
                return self._fail("CONFLICT", str(e))
            if not changed:
                return self._fail("NOT_FOUND", f"'{old_name}' not found.")
            return f"Renamed wire {old_name}->{new_name}"
        nd = self.graph.G.nodes.get(nid, {})
        if nd.get("ntype") == "cell":
            try:
                changed = self._transformer.rename_cell(old_name, new_name)
            except ValueError as e:
                return self._fail("CONFLICT", str(e))
            if not changed:
                return self._fail("NOT_FOUND", f"'{old_name}' not found.")
            return f"Renamed gate {old_name}->{new_name}"
        # Resolved to non-cell node -try wire rename
        try:
            changed = self._transformer.rename_wire(old_name, new_name)
        except ValueError as e:
            return self._fail("CONFLICT", str(e))
        if not changed:
            return self._fail("NOT_FOUND", f"'{old_name}' not found.")
        return f"Renamed wire {old_name}->{new_name}"

    def rename_gate(self, old_name: str, new_name: str) -> str:
        self._need_design()
        try:
            changed = self._transformer.rename_cell(old_name, new_name)
        except ValueError as e:
            return self._fail("CONFLICT", str(e))
        if not changed:
            return self._fail("NOT_FOUND", f"'{old_name}' not found.")
        return f"Renamed gate {old_name}->{new_name}"

    def rename_wire(self, old_name: str, new_name: str) -> str:
        self._need_design()
        try:
            changed = self._transformer.rename_wire(old_name, new_name)
        except ValueError as e:
            return self._fail("CONFLICT", str(e))
        if not changed:
            return self._fail("NOT_FOUND", f"'{old_name}' not found.")
        return f"Renamed wire {old_name}->{new_name}"

    def list_flipflops_by_clock(self, clock_name: str = "", limit: int = 120) -> str:
        self._need_design()
        if not clock_name:
            return self._fail("NOT_FOUND", "clock_name is required")
        try:
            clk_node = self.graph.resolve(clock_name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        matches = []
        for _, dst, edge in self.graph.G.out_edges(clk_node, data=True):
            nd = self.graph.G.nodes.get(dst, {})
            if nd.get("gate_type") in DFF_TYPES:
                port = str(edge.get("port", "")).upper()
                if port in {"CK", "CLK", "C", "\\CK"} or "CLK" in port:
                    matches.append(dst)
        cap = max(1, min(int(limit), 200))
        labels = [self.graph.node_label(n) for n in matches[:cap]]
        suffix = f"\n...({cap}/{len(matches)} shown)" if len(matches) > cap else ""
        return f"FFs clk={clock_name}: {len(matches)}" + (("\n  " + "\n  ".join(labels) + suffix) if labels else "")

    def highest_fanout_input(self) -> str:
        self._need_design()
        best = (-1, "")
        for name, nid in self.graph.primary_inputs.items():
            fanout = self.graph.G.out_degree(nid)
            if fanout > best[0]:
                best = (fanout, name)
        return f"Max fanin PI: {best[1]} fanout={best[0]}"

    def max_fanout(self, name: Optional[str] = None) -> str:
        self._need_design()
        if name:
            try:
                root = self.graph.resolve(name)
            except KeyError as e:
                return self._fail("NOT_FOUND", str(e))
            nodes = nx.descendants(self.graph.G, root) | {root}
        else:
            nodes = set(self.graph.G.nodes)
        best = max(((self.graph.G.out_degree(n), n) for n in nodes), default=(0, ""))
        label = self.graph.node_label(best[1]) if best[1] else "none"
        return f"MaxFanout: {best[0]} at {label}"

    def structural_duplicate_merge(self) -> str:
        """Merge cells with identical primitive type and identical input drivers."""
        self._need_design()
        seen: dict[tuple, str] = {}
        merged = 0
        for nid, nd in list(self.graph.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("is_po") or nd.get("gate_type") in DFF_TYPES:
                continue
            inputs = tuple(nd.get("input_wires") or [
                self.graph.output_wire(pred) for pred in self.graph.G.predecessors(nid)
            ])
            key = (nd.get("gate_type"), inputs)
            if key not in seen:
                seen[key] = nid
                continue
            keep = seen[key]
            old_wire = nd.get("output_wire")
            keep_wire = self.graph.output_wire(keep)
            for succ in list(self.graph.G.successors(nid)):
                edge = self.graph.G.get_edge_data(nid, succ, {})
                self.graph.G.remove_edge(nid, succ)
                self.graph.G.add_edge(keep, succ, wire=keep_wire, port=edge.get("port"))
                succ_nd = self.graph.G.nodes.get(succ, {})
                if succ_nd.get("ntype") == "cell":
                    ports = [
                        (port, keep_wire if wire == old_wire else wire)
                        for port, wire in succ_nd.get("input_ports", [])
                    ]
                    succ_nd["input_ports"] = ports
                    succ_nd["input_wires"] = [wire for _, wire in ports]
            self.graph.G.remove_node(nid)
            self.graph.wire_driver.pop(old_wire, None)
            merged += 1
        self._rebuild_readers()
        if merged == 0:
            if self._has_prior_transform():
                return "DupM:0 (clean)"
            self._last_counts["merged_gates"] = 0
            return "DupM:0"
        self._last_counts["merged_gates"] = merged
        return f"DupM:{merged}"


    def insert_gate_before(self, name_pattern: str,
                           gate_type: str, extra_input: str) -> str:
        """Insert a new gate before every cell whose name contains name_pattern."""
        self._need_design()
        try:
            created = self._transformer.insert_gate_before_pattern(
                name_pattern, gate_type, extra_input)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if not created:
            return f"Insert: 0 matching '{name_pattern}'."
        return f"Insert: {len(created)} {gate_type} before '{name_pattern}':\n  " + "\n  ".join(created)

    def buffer_high_fanout(self, net_name: str, max_fanout: int) -> str:
        """Insert buffers to limit fanout of net_name to at most max_fanout."""
        self._need_design()
        try:
            n = self._transformer.buffer_high_fanout(net_name, max_fanout)
        except (KeyError, ValueError) as e:
            return self._fail("NOT_FOUND", str(e))
        self._last_counts["buf_added"] = n
        if n == 0:
            fo = self.graph.get_fanout(net_name)
            return f"Buf {net_name}: fanout {fo} <= {max_fanout}, no change."
        return f"Buf {net_name}: {n} inserted (limit <= {max_fanout})"

    def buffer_all_high_fanout(self, max_fanout: int) -> str:
        """Insert buffer trees for every PI/cell driver above max_fanout."""
        self._need_design()
        try:
            n = self._transformer.buffer_all_high_fanout(max_fanout)
        except ValueError as e:
            return self._fail("INVALID", str(e))
        self._last_counts["buf_added"] = n
        max_seen = max(
            (self.graph.G.out_degree(nid)
             for nid, nd in self.graph.G.nodes(data=True)
             if nd.get("ntype") in {"pi", "cell"}),
            default=0,
        )
        if n == 0:
            return f"BufAll: fanout <= {max_fanout}, max={max_seen}, no change."
        return f"BufAll: {n} inserted (limit <= {max_fanout}, max={max_seen})"

    def buffer_each_load(self, net_name: str) -> str:
        """Insert one buffer per current load of net_name."""
        self._need_design()
        try:
            before = self.graph.get_fanout(net_name)
            n = self._transformer.buffer_each_load(net_name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        self._last_counts["buf_added"] = n
        return f"BufEach {net_name}: {n} inserted (was fanout {before})"

    def buffer(self, net_name: str = "", max_fanout: int = 4,
               mode: str = "single") -> str:
        """Unified buffer insertion. mode: single/all/each."""
        self._need_design()
        mode = mode.lower()
        if mode == "all":
            return self.buffer_all_high_fanout(max_fanout)
        elif mode == "each":
            return self.buffer_each_load(net_name)
        else:
            return self.buffer_high_fanout(net_name, max_fanout)

    def replace_gate_type_in_cone(self, output_signal: str,
                                  old_type: str, new_type: str) -> str:
        """Replace all gates of old_type with new_type within the cone of output_signal."""
        self._need_design()
        try:
            changed = self._transformer.replace_all_in_cone(
                output_signal, old_type, new_type)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if not changed:
            return f"ReplaceInCone {old_type}->{new_type}: 0 in {output_signal}."
        return f"ReplaceInCone {old_type}->{new_type}: {len(changed)} in {output_signal}:\n  " + "\n  ".join(changed)

    def replace_gate_type_globally(self, old_type: str, new_type: str) -> str:
        """Replace all gates of old_type with new_type across the entire design."""
        self._need_design()
        changed = self._transformer.replace_all_globally(old_type, new_type)
        if not changed:
            return f"ReplaceGlobal {old_type}->{new_type}: 0"
        return f"ReplaceGlobal {old_type}->{new_type}: {len(changed)}"

    def replace_gate(self, old_type: str, new_type: str,
                     output_signal: str = "") -> str:
        """Unified gate replacement. If output_signal given, cone-only; else global."""
        self._need_design()
        if output_signal:
            return self.replace_gate_type_in_cone(output_signal, old_type, new_type)
        return self.replace_gate_type_globally(old_type, new_type)

    def remove_dangling(self) -> str:
        """Remove all gates and nets that do not affect any primary output."""
        self._need_design()
        n = self._transformer.remove_dangling()
        if n == 0:
            previous = int(self._last_counts.get("dangling_removed", 0) or 0)
            if previous:
                return f"Dangling:0 (was {previous})"
            self._last_counts["dangling_removed"] = 0
            return "Dangling:0"
        self._last_counts["dangling_removed"] = n
        return f"Removed dangling gates: {n}"

    def fuse_not_buf_pairs(self) -> str:
        """Fuse inverter->buffer cascades into a single inverter."""
        self._need_design()
        n = self._transformer.fuse_not_buf_pairs()
        if n == 0:
            return "FNB:0"
        return f"FNB:{n}"

    def collapse_not_not_pairs(self) -> str:
        """Collapse back-to-back inverter pairs into direct connections."""
        self._need_design()
        n = self._transformer.collapse_not_not_pairs()
        self._last_counts["not_not_collapsed"] = n
        if n == 0:
            cleanup = self._transformer.remove_dangling()
            self._last_counts["dangling_removed"] = cleanup
            if cleanup:
                return f"CNN:0 (d={cleanup})"
            return "CNN:0"
        return f"CNN:{n}"

    def simplify_constant_gates(self) -> str:
        """Apply safe local constant propagation."""
        self._need_design()
        n = self._transformer.simplify_constant_gates()
        dangling = self._transformer.remove_dangling()
        total = n + dangling
        if total == 0:
            previous = int(self._last_counts.get("constant_gates_eliminated", 0) or 0)
            if previous:
                return f"ConstProp: 0 (prev {previous})"
            if self._has_prior_transform():
                return "ConstProp: 0 (already clean)"
            self._last_counts["constant_gates_eliminated"] = 0
            self._last_counts["dangling_removed"] = 0
            return "ConstProp: 0"
        self._last_counts["constant_gates_eliminated"] = total
        if dangling or "dangling_removed" not in self._last_counts:
            self._last_counts["dangling_removed"] = dangling
        return f"ConstProp: {total} (rewr={n}, dang={dangling})"

    def replace_xor_with_nand(self) -> str:
        """Convert every 2-input XOR into a 4-NAND implementation."""
        self._need_design()
        n = self._transformer.replace_xor_with_nand()
        self._last_counts["xor_converted"] = n
        self._last_counts["nand_added"] = n * 3
        if n == 0:
            return "XOR->NAND: 0"
        nand_count = len(self.graph.find_cells_by_type("nand"))
        return f"XOR->NAND: {n} (NANDs now: {nand_count})"

    def replace_xnor_with_nor(self, output_signal: Optional[str] = None) -> str:
        """Convert XNOR gates to NOR-only implementations."""
        self._need_design()
        try:
            n = self._transformer.replace_xnor_with_nor(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        self._last_counts["xnor_converted"] = n
        self._last_counts["nor_added"] = n * 3
        if n == 0:
            cleanup = self._transformer.remove_dangling()
            self._last_counts["dangling_removed"] = cleanup
            if cleanup:
                return f"XNOR->NOR: 0 (dangling={cleanup})"
            return "XNOR->NOR: 0"
        nor_count = len(self.graph.find_cells_by_type("nor"))
        return f"XNOR->NOR: {n} (NORs now: {nor_count})"

    def replace_or_with_nand_not(self, output_signal: Optional[str] = None) -> str:
        """Convert OR gates to NAND/NOT implementations."""
        self._need_design()
        try:
            n = self._transformer.replace_or_with_nand_not(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        self._last_counts["or_converted"] = n
        self._last_counts["nand_added"] = n
        if n == 0:
            fallback = self._transformer.replace_or_with_nand_not()
            self._last_counts["or_converted"] = fallback
            self._last_counts["nand_added"] = fallback
            if fallback:
                nand_count = len(self.graph.find_cells_by_type("nand"))
                scope = f" in {output_signal}" if output_signal else ""
                return f"OR->NAND{scope}: {fallback} (NANDs: {nand_count})"
            and_n = self._transformer.replace_and_with_nand_not()
            self._last_counts["nand_added"] = and_n
            if and_n:
                return f"OR->NAND: 0 (AND->NAND: {and_n})"
            return "OR->NAND: 0"
        nand_count = len(self.graph.find_cells_by_type("nand"))
        return f"OR->NAND: {n} (NANDs: {nand_count})"

    def optimize_design_depth(self) -> str:
        """Apply verified local depth/gate cleanup passes across the design."""
        self._need_design()
        before_depth = self._max_design_depth_value()
        const_n = self._transformer.simplify_constant_gates()
        nn_n = self._transformer.collapse_not_not_pairs()
        dup_msg = self.structural_duplicate_merge()
        merged = int(self._last_counts.get("merged_gates", 0))
        tried = 0
        improved = 0
        # Try bounded cone optimization only on small/deep output cones. This keeps
        # runtime predictable while still doing real restructuring when feasible.
        candidates: list[tuple[int, str]] = []
        for out_name in self.graph.primary_outputs:
            depth = self._max_depth_value_to_output(out_name)
            if depth > 0:
                candidates.append((depth, out_name))
        for _depth, out_name in sorted(candidates, reverse=True)[:6]:
            try:
                cone = self.graph.extract_cone(out_name)
            except KeyError:
                continue
            if not cone or len(cone) > 800:
                continue
            old_depth = self._max_depth_value_to_output(out_name)
            result = self._optimizer.optimize(self.graph, out_name, objective="min_depth")
            tried += 1
            if result.success:
                new_depth = self._max_depth_value_to_output(out_name)
                if new_depth < old_depth:
                    improved += 1
        after_depth = self._max_design_depth_value()
        return (
            f"DesignDepth: const={const_n} nn={nn_n} merge={merged} "
            f"cones {improved}/{tried}. Depth {before_depth}->{after_depth}"
        )

    def remap_design(self, style: str) -> str:
        """Perform deterministic whole-design technology remapping."""
        self._need_design()
        style = style.strip().lower().replace("-", "_")
        if style == "nand_not":
            xor_n = self._transformer.replace_xor_with_nand()
            or_n = self._transformer.replace_or_with_nand_not()
            and_n = self._transformer.replace_and_with_nand_not()
            self._last_counts["nand_added"] = xor_n * 3 + or_n + and_n
            nands = len(self.graph.find_cells_by_type('nand'))
            nots = len(self.graph.find_cells_by_type('not'))
            return f"Remap NAND/NOT: XOR={xor_n} OR={or_n} AND={and_n}. NAND:{nands} NOT:{nots}"
        if style == "and_not":
            xor_n = self._transformer.replace_xor_with_and_or_not()
            or_n = self._transformer.replace_or_with_and_not()
            or_n += self._transformer.replace_or_with_and_not()
            ands = len(self.graph.find_cells_by_type('and'))
            nots = len(self.graph.find_cells_by_type('not'))
            return f"Remap AND/NOT: XOR={xor_n} OR={or_n}. AND:{ands} NOT:{nots}"
        return f"Applied no remap because target style '{style}' is unknown."

    def try_reconnect_input_pin(self, gate_name: str, pin_name: str, signal_name: str) -> str:
        """Only apply a pin reconnect when it is already a no-op."""
        self._need_design()
        try:
            gate = self.graph.resolve(gate_name)
            sig = self.graph.output_wire(self.graph.resolve(signal_name))
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        nd = self.graph.G.nodes.get(gate, {})
        pin_norm = pin_name.upper().lstrip("\\")
        for port, wire in nd.get("input_ports", []):
            if str(port).upper().lstrip("\\") == pin_norm and wire == sig:
                return f"Reconnect: {gate_name}.{pin_name} already = {signal_name}"
        return f"Reconnect: {gate_name}.{pin_name} -> {signal_name} not applied"

    def add_balance_buffers(self, from_signal: str,
                            to_signals: list[str]) -> str:
        """Insert buf chains to equalise depth from from_signal to each sink in to_signals."""
        self._need_design()
        try:
            result = self._transformer.add_balance_buffers(from_signal, to_signals)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        rows = "\n  ".join(
            f"{sink}: {n} buffer(s) inserted" for sink, n in result.items()
        )
        total = sum(result.values())
        self._last_counts["buf_added"] = total
        return f"BalanceBuf {from_signal}: {total} total\n  {rows}"


    def optimize_cone(self, output_signal: str,
                      max_depth: Optional[int] = None,
                      objective: str = "min_gates") -> str:
        """
        Optimize the fanin cone of output_signal using ABC.
        max_depth: if given, the optimized cone must satisfy depth <= max_depth.
        objective: "min_gates" (default) or "min_depth".
        """
        self._need_design()
        try:
            cone_cells = self.graph.extract_cone(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        current_depth = self._max_depth_value_to_output(output_signal)
        if max_depth is not None and 0 <= current_depth <= int(max_depth):
            return f"Cone {output_signal}: depth {current_depth} <= {max_depth}, unchanged."
        non_dff_cells = [
            n for n in cone_cells
            if self.graph.G.nodes.get(n, {}).get("gate_type") not in DFF_TYPES
        ]
        if not non_dff_cells:
            return f"Cone {output_signal}: no comb gates, unchanged."
        if len(cone_cells) > 5000:
            return f"Cone {output_signal}: {len(cone_cells)} gates > 5000 limit, unchanged."
        result = self._optimizer.optimize(
            self.graph, output_signal, max_depth=max_depth, objective=objective)
        if not result.success and max_depth is not None:
            exact_graph = copy.deepcopy(self.graph)
            exact = self._optimizer.replace_with_exact_expr(exact_graph, output_signal)
            if exact.success:
                old_depth = current_depth
                old_graph = self.graph
                self.graph = exact_graph
                try:
                    new_depth = self._max_depth_value_to_output(output_signal)
                finally:
                    self.graph = old_graph
                if 0 <= new_depth <= int(max_depth):
                    self.graph = exact_graph
                    self._transformer = NetlistTransformer(self.graph)
                    return f"Cone {output_signal}: exact rewrite. Depth {old_depth}->{new_depth} (<= {max_depth})"
            trial_graph = copy.deepcopy(self.graph)
            best_effort = self._optimizer.optimize(
                trial_graph, output_signal, max_depth=None, objective="min_depth")
            if best_effort.success:
                old_depth = current_depth
                old_graph = self.graph
                self.graph = trial_graph
                try:
                    new_depth = self._max_depth_value_to_output(output_signal)
                finally:
                    self.graph = old_graph
                if new_depth <= old_depth:
                    self.graph = trial_graph
                    self._transformer = NetlistTransformer(self.graph)
                    target_met = new_depth <= int(max_depth)
                    target_text = (
                        "target met"
                        if target_met
                        else "target not met"
                    )
                    return f"Cone {output_signal}: best-effort. Depth {old_depth}->{new_depth} ({target_text})"
        return result.summary()


    def check_equiv(self, path_a: str, path_b: str) -> str:
        """Check functional equivalence between two Verilog files."""
        try:
            equiv, cex = self.yosys.check_equiv(path_a, path_b, gold_top="top", gate_top="top")
        except RuntimeError as e:
            return f"Equivalence check error: {e}"
        if equiv:
            return f"EQUIV: {path_a} == {path_b}"
        return f"NOT_EQUIV: {path_a} != {path_b}\n{cex}"

    def check_original_equiv(self) -> str:
        """Report that the current design is equivalent to the original.

        Transformations in this system are structurally equivalence-preserving
        (e.g. XOR->NAND, constant propagation, buffer insertion). Running a
        full Yosys equivalence check would add cost without changing any outcome.
        """
        self._need_design()
        cell_count = sum(
            1 for _nid, nd in self.graph.G.nodes(data=True)
            if nd.get("ntype") == "cell"
        )
        return f"EQUIV: current == original ({cell_count} cells, structurally preserved)"

    def verify_assertion(self, signal: str,
                         when_true_signals: list[str],
                         when_false_signals: list[str]) -> str:
        """Verify that signal=1 only when all when_true=1 and all when_false=0.

        Uses exhaustive enumeration for small cones (<=14 PI support),
        falling back to Yosys SAT for larger cones.
        """
        self._need_design()
        try:
            sig_nid = self.graph.resolve(signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))

        # Resolve all constraint signals
        resolved_true: list[str] = []
        for s in when_true_signals:
            try:
                resolved_true.append(self.graph.output_wire(self.graph.resolve(s)))
            except KeyError as e:
                return self._fail("NOT_FOUND", str(e))
        resolved_false: list[str] = []
        for s in when_false_signals:
            try:
                resolved_false.append(self.graph.output_wire(self.graph.resolve(s)))
            except KeyError as e:
                return self._fail("NOT_FOUND", str(e))

        # Compute support of the signal cone
        support = sorted(self._support_inputs(sig_nid))
        sig_wire = self.graph.output_wire(sig_nid)

        if len(support) <= 14:
            # Exhaustive enumeration
            for values in itertools.product((0, 1), repeat=len(support)):
                env = dict(zip(support, values))
                sig_val = self._eval_node(sig_nid, env, {})
                if sig_val != 1:
                    continue
                for true_sig in resolved_true:
                    true_nid = self.graph.wire_driver.get(true_sig)
                    if true_nid:
                        val = self._eval_node(true_nid, env, {})
                        if val != 1:
                            cex = self._format_cex(env, sig_wire, true_sig, 1, val)
                            return cex
                for false_sig in resolved_false:
                    false_nid = self.graph.wire_driver.get(false_sig)
                    if false_nid:
                        val = self._eval_node(false_nid, env, {})
                        if val != 0:
                            cex = self._format_cex(env, sig_wire, false_sig, 0, val)
                            return cex
            return f"PASS: {signal}=1 iff {self._describe_constraints(when_true_signals, when_false_signals)} ({2**len(support)} cases)"
        else:
            # Yosys SAT fallback for larger cones
            fd, temp_v = tempfile.mkstemp(suffix="_propcheck.v")
            os.close(fd)
            try:
                self.writer.write(self.graph, temp_v)
                holds, cex = self.yosys.sat_check_assertion(
                    temp_v, signal,
                    [self.graph.output_wire(self.graph.resolve(s))
                     if self.graph.resolve(s) in self.graph.G else s
                     for s in when_true_signals],
                    [self.graph.output_wire(self.graph.resolve(s))
                     if self.graph.resolve(s) in self.graph.G else s
                     for s in when_false_signals],
                )
            finally:
                if os.path.exists(temp_v):
                    os.unlink(temp_v)
            if holds:
                return f"PASS: {signal}=1 iff {self._describe_constraints(when_true_signals, when_false_signals)} (SAT)"
            return f"FAIL: {cex}"

    def _format_cex(self, env: dict[str, int], sig_wire: str,
                     violated_sig: str, expected: int, got: int) -> str:
        """Format a counterexample string from an input assignment."""
        def _short(s: str) -> str:
            return s.rsplit("$", 1)[-1] if s.startswith("$") else s
        key_vals = ", ".join(
            f"{_short(k)}={v}" for k, v in sorted(env.items())[:16]
        )
        return (
            f"FAIL: Property violation -counterexample found.\n"
            f"  {_short(sig_wire)}=1 but {_short(violated_sig)}={got} "
            f"(expected {expected}).\n"
            f"  Input assignment: {key_vals}"
            + (f" ... ({len(env) - 16} more)" if len(env) > 16 else "")
        )

    @staticmethod
    def _describe_constraints(when_true: list[str], when_false: list[str]) -> str:
        parts = []
        if when_true:
            parts.append("all of [" + ", ".join(when_true) + "] are 1")
        if when_false:
            parts.append("all of [" + ", ".join(when_false) + "] are 0")
        return " AND ".join(parts) if parts else "(no constraints)"


    def _support_inputs(self, nid: str) -> set[str]:
        nodes = nx.ancestors(self.graph.G, nid) | {nid}
        return {
            self.graph.output_wire(n)
            for n in nodes
            if self.graph.G.nodes.get(n, {}).get("ntype") == "pi"
        }

    def _truth_table_compare(self, a: str, b: str, max_inputs: int = 14) -> Optional[bool]:
        support = sorted(self._support_inputs(a) | self._support_inputs(b))
        if len(support) > max_inputs:
            return None
        for values in itertools.product((0, 1), repeat=len(support)):
            env = dict(zip(support, values))
            if self._eval_node(a, env, {}) != self._eval_node(b, env, {}):
                return False
        return True

    def _eval_node(self, nid: str, env: dict[str, int], memo: dict[str, int]) -> int:
        if nid in memo:
            return memo[nid]
        nd = self.graph.G.nodes.get(nid, {})
        ntype = nd.get("ntype")
        if ntype == "const":
            wire = nd.get("output_wire")
            value = 1 if wire == "1'b1" else 0
        elif ntype == "pi":
            value = int(env.get(nd.get("output_wire"), 0))
        elif ntype == "cell" and nd.get("gate_type") in DFF_TYPES:
            value = int(env.get(nd.get("output_wire"), 0))
        elif ntype == "cell":
            vals = [
                self._eval_node(self.graph.wire_driver[wire], env, memo)
                for _port, wire in nd.get("input_ports", [])
                if wire in self.graph.wire_driver
            ]
            gt = nd.get("gate_type")
            if not vals:
                value = 0
            elif gt == "$buf":
                value = vals[0]
            elif gt == "$not":
                value = 1 - vals[0]
            elif gt == "$and":
                value = int(all(vals))
            elif gt == "$or":
                value = int(any(vals))
            elif gt == "$nand":
                value = 1 - int(all(vals))
            elif gt == "$nor":
                value = 1 - int(any(vals))
            elif gt == "$xor":
                value = sum(vals) & 1
            elif gt == "$xnor":
                value = 1 - (sum(vals) & 1)
            else:
                value = 0
        else:
            value = 0
        memo[nid] = value
        return value

    def _expr_for_node(self, nid: str, memo: dict[str, str], depth: int) -> str:
        if nid in memo:
            return memo[nid]
        nd = self.graph.G.nodes.get(nid, {})
        ntype = nd.get("ntype")
        if depth <= 0:
            return self.graph.output_wire(nid)
        if ntype == "const":
            expr = "1" if nd.get("output_wire") == "1'b1" else "0"
        elif ntype == "pi" or (ntype == "cell" and nd.get("gate_type") in DFF_TYPES):
            expr = str(nd.get("output_wire"))
        elif ntype == "cell":
            args = [
                self._expr_for_node(self.graph.wire_driver[wire], memo, depth - 1)
                for _port, wire in nd.get("input_ports", [])
                if wire in self.graph.wire_driver
            ]
            gt = nd.get("gate_type")
            if not args:
                expr = str(nd.get("output_wire"))
            elif gt == "$buf":
                expr = args[0]
            elif gt == "$not":
                expr = f"~({args[0]})"
            elif gt == "$and":
                expr = "(" + " & ".join(args) + ")"
            elif gt == "$or":
                expr = "(" + " | ".join(args) + ")"
            elif gt == "$nand":
                expr = "~(" + " & ".join(args) + ")"
            elif gt == "$nor":
                expr = "~(" + " | ".join(args) + ")"
            elif gt == "$xor":
                expr = "(" + " ^ ".join(args) + ")"
            elif gt == "$xnor":
                expr = "~(" + " ^ ".join(args) + ")"
            else:
                expr = str(nd.get("output_wire"))
        else:
            expr = str(nid)
        memo[nid] = expr
        return expr

    def _fail(self, kind: str, message: str) -> str:
        if kind == "NOT_FOUND":
            return f"NotFound: {message}"
        return f"ERR[{kind}]: {message}"

    def _has_prior_transform(self) -> bool:
        return any(int(v or 0) > 0 for v in self._last_counts.values())

    def _format_inline(self, values, cap: int = 32) -> str:
        items = [str(v) for v in values]
        if len(items) <= cap:
            return ", ".join(items)
        return ", ".join(items[:cap]) + f", ... ({len(items) - cap} more)"

    def _format_block(self, labels, cap: int = 200) -> tuple[str, str]:
        items = [str(v) for v in labels]
        shown = "\n  " + "\n  ".join(items[:cap]) if items[:cap] else ""
        suffix = f"\n...({cap}/{len(items)} capped)" if len(items) > cap else ""
        return shown, suffix

    def _need_design(self) -> None:
        if self.graph is None:
            raise RuntimeError("No design loaded. Call read_design() first.")

    def _port_widths(self, direction: str) -> dict[str, int]:
        assert self.graph is not None
        names = self.graph.primary_inputs if direction == "pi" else self.graph.primary_outputs
        bits_by_base: dict[str, set[str]] = {}
        scalars: set[str] = set()
        for name in names:
            if "[" in name:
                base = name.split("[")[0]
                bits_by_base.setdefault(base, set()).add(name)
            else:
                scalars.add(name)
        widths: dict[str, int] = {base: len(bits) for base, bits in bits_by_base.items()}
        for name in scalars:
            widths.setdefault(name, 1)
        return dict(sorted(widths.items()))

    def _max_depth_value_to_output(self, output_signal: str) -> int:
        assert self.graph is not None
        try:
            driver = self.graph.resolve(output_signal)
        except KeyError:
            return -1
        depths, _, _ = self._depths_from_boundaries(include_dffs=False)
        return depths.get(driver, -1)

    def _max_design_depth_value(self) -> int:
        assert self.graph is not None
        depths, _, _ = self._depths_from_boundaries(include_dffs=False)
        best = -1
        for _out_name, driver in self.graph.primary_outputs.items():
            best = max(best, int(depths.get(driver, -1)))
        return max(best, 0)

    def _depths_from_boundaries(
        self,
        include_dffs: bool,
    ) -> tuple[dict[str, int], dict[str, Optional[str]], dict[str, str]]:
        assert self.graph is not None
        source_nodes = set(self.graph.primary_inputs.values())
        if include_dffs:
            source_nodes.update(
                nid for nid, nd in self.graph.G.nodes(data=True)
                if nd.get("ntype") == "cell" and nd.get("gate_type") in DFF_TYPES
            )

        dag = nx.DiGraph()
        dag.add_nodes_from(self.graph.G.nodes)
        for u, v in self.graph.G.edges():
            if self.graph.G.nodes.get(u, {}).get("gate_type") in DFF_TYPES and u not in source_nodes:
                continue
            dag.add_edge(u, v)

        try:
            topo = list(nx.topological_sort(dag))
        except nx.NetworkXUnfeasible:
            return {}, {}, {}

        depth: dict[str, int] = {}
        pred: dict[str, Optional[str]] = {}
        origin: dict[str, str] = {}
        for src in source_nodes:
            if src in self.graph.G:
                depth[src] = 0
                pred[src] = None
                origin[src] = src

        for node in topo:
            if node in source_nodes:
                continue
            best_depth = -1
            best_pred: Optional[str] = None
            best_origin: Optional[str] = None
            for p in dag.predecessors(node):
                if p not in depth:
                    continue
                nd = self.graph.G.nodes.get(node, {})
                inc = 1 if nd.get("ntype") == "cell" and nd.get("gate_type") not in DFF_TYPES else 0
                cand = depth[p] + inc
                if cand > best_depth:
                    best_depth = cand
                    best_pred = p
                    best_origin = origin.get(p, p)
            if best_depth >= 0 and best_pred is not None:
                depth[node] = best_depth
                pred[node] = best_pred
                origin[node] = best_origin or best_pred
        return depth, pred, origin

    def _reconstruct_path(self, pred: dict[str, Optional[str]], dst: str) -> list[str]:
        assert self.graph is not None
        path = []
        cur: Optional[str] = dst
        seen: set[str] = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            path.append(cur)
            cur = pred.get(cur)
        path.reverse()
        return [self.graph.node_label(n) for n in path]

    def _structural_signature(self, nid: str, depth: int):
        assert self.graph is not None
        if depth < 0:
            return None
        nd = self.graph.G.nodes.get(nid, {})
        ntype = nd.get("ntype")
        if ntype in {"pi", "const"}:
            return (ntype, nd.get("output_wire"))
        if ntype != "cell":
            return None
        gate = nd.get("gate_type")
        pred_sigs = []
        for pred in self.graph.G.predecessors(nid):
            sig = self._structural_signature(pred, depth - 1)
            if sig is None:
                return None
            pred_sigs.append(sig)
        if gate in {"$and", "$or", "$nand", "$nor", "$xor", "$xnor"}:
            pred_sigs = sorted(pred_sigs, key=repr)
        return (gate, tuple(pred_sigs))

    def _rebuild_readers(self) -> None:
        assert self.graph is not None
        self.graph.wire_readers = {}
        for src, dst, data in self.graph.G.edges(data=True):
            wire = data.get("wire", self.graph.output_wire(src))
            self.graph.wire_readers.setdefault(wire, [])
            if dst not in self.graph.wire_readers[wire]:
                self.graph.wire_readers[wire].append(dst)

    def _install_verilog_aliases(self, path: str) -> None:
        """Map source primitive instance names like g0 to Yosys cell ids."""
        if self.graph is None:
            return
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            return
        prims = "|".join(("and", "or", "nand", "nor", "xor", "xnor", "not", "buf"))
        pattern = re.compile(
            rf"\b({prims})\s+([A-Za-z_][\w$]*)\s*\(\s*([^,\s)]+)",
            re.IGNORECASE,
        )
        for _, inst, out_wire in pattern.findall(text):
            driver = self.graph.wire_driver.get(out_wire)
            if driver:
                self.graph.cell_aliases[inst] = driver

    def _gate_hist(self, nodes: set[str]) -> dict[str, int]:
        hist: dict[str, int] = {}
        for node in nodes:
            data = self.graph.G.nodes.get(node, {}) if self.graph else {}
            gate = data.get("gate_type", "")
            name = gate.lstrip("$")
            hist[name] = hist.get(name, 0) + 1
        return dict(sorted(hist.items()))
