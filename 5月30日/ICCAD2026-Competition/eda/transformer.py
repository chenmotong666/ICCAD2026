"""
eda/transformer.py
==================
Structural mutations on a NetlistGraph in-place.

Design principles
-----------------
1. Every public method leaves the graph in a valid, consistent state
   (wire_driver / wire_readers caches stay correct).

2. Mutations only touch graph edges and node attributes — they never
   re-parse Verilog or call Yosys.  Yosys is called only for optimization
   and equivalence checks (see optimizer.py).

3. Fresh wire names are generated deterministically from a prefix + counter
   so that repeated runs produce the same output.

4. All methods return structured data (counts, lists of names) that the
   EDABackend layer formats into human-readable strings for the agent.
"""

from __future__ import annotations

import math
import re
from typing import Optional

import networkx as nx

from .netlist_graph import (
    NetlistGraph, CONST_0, CONST_1,
    YOSYS_TO_PRIM, PRIM_TO_YOSYS, DFF_TYPES,
)


class NetlistTransformer:
    """
    Applies structural mutations to a NetlistGraph.
    Instantiated once per EDABackend and reused across all operations.
    """

    def __init__(self, graph: NetlistGraph) -> None:
        self.ng   = graph
        self._ctr: dict[str, int] = {}   # prefix → next counter value

    # ── helpers ───────────────────────────────────────────────────────────────

    def _fresh_name(self, prefix: str) -> str:
        """Generate the next unique cell name for a given prefix."""
        i = self._ctr.get(prefix, 0)
        while True:
            name = f"{prefix}_{i}"
            if name not in self.ng.G:
                self._ctr[prefix] = i + 1
                return name
            i += 1

    def _fresh_wire(self, hint: str) -> str:
        """Generate a unique internal wire name."""
        base = re.sub(r"\W", "_", hint)
        i    = self._ctr.get(f"wire_{base}", 0)
        while True:
            name = f"__{base}_{i}__"
            if name not in self.ng.wire_driver:
                self._ctr[f"wire_{base}"] = i + 1
                return name
            i += 1

    def _add_cell(self, name: str, gate_type: str,
                  output_wire: str, is_po: bool = False) -> None:
        """Register a new cell node in the graph."""
        ytype = PRIM_TO_YOSYS.get(gate_type, f"${gate_type}")
        self.ng.G.add_node(name, ntype="cell", gate_type=ytype,
                           output_wire=output_wire, is_po=is_po,
                           input_ports=[], input_wires=[])
        self.ng.wire_driver[output_wire] = name

    def _add_edge(self, src: str, dst: str, wire: str,
                  port: Optional[str] = None) -> None:
        """Add a directed edge and update wire_readers cache."""
        attrs = {"wire": wire}
        if port is not None:
            attrs["port"] = port
        self.ng.G.add_edge(src, dst, **attrs)
        self.ng.wire_readers.setdefault(wire, [])
        if dst not in self.ng.wire_readers[wire]:
            self.ng.wire_readers[wire].append(dst)
        self._record_input(dst, wire, port)

    def _remove_edge(self, src: str, dst: str, wire: str) -> None:
        """Remove a directed edge and update wire_readers cache."""
        if self.ng.G.has_edge(src, dst):
            self.ng.G.remove_edge(src, dst)
        readers = self.ng.wire_readers.get(wire, [])
        if dst in readers:
            readers.remove(dst)
        self._forget_input(dst, wire)

    def _record_input(self, dst: str, wire: str, port: Optional[str]) -> None:
        nd = self.ng.G.nodes.get(dst, {})
        if nd.get("ntype") != "cell":
            return
        port_name = port or f"I{len(nd.get('input_ports', []))}"
        ports = list(nd.get("input_ports", []))
        replaced = False
        if port is not None:
            for idx, (existing_port, _existing_wire) in enumerate(ports):
                if existing_port == port:
                    ports[idx] = (port_name, wire)
                    replaced = True
                    break
        if not replaced:
            ports.append((port_name, wire))
        nd["input_ports"] = ports
        nd["input_wires"] = [w for _, w in ports]

    def _forget_input(self, dst: str, wire: str) -> None:
        nd = self.ng.G.nodes.get(dst, {})
        if nd.get("ntype") != "cell":
            return
        ports = list(nd.get("input_ports", []))
        for idx, (_port, existing_wire) in enumerate(ports):
            if existing_wire == wire:
                del ports[idx]
                break
        nd["input_ports"] = ports
        nd["input_wires"] = [w for _, w in ports]

    def _remove_cell(self, nid: str) -> None:
        """Remove a cell node and its wire_driver entry."""
        out_wire = self.ng.G.nodes.get(nid, {}).get("output_wire")
        if out_wire:
            for succ in list(self.ng.G.successors(nid)):
                self._forget_input(succ, out_wire)
            self.ng.wire_readers.pop(out_wire, None)
        if nid in self.ng.G:
            self.ng.G.remove_node(nid)
        if out_wire and self.ng.wire_driver.get(out_wire) == nid:
            del self.ng.wire_driver[out_wire]
        # Remove from primary_outputs if applicable
        for port, driver in list(self.ng.primary_outputs.items()):
            if driver == nid:
                del self.ng.primary_outputs[port]

    def _cell_input_drivers(self, nid: str) -> list[tuple[str, str, str]]:
        """Return (port, driver_node, wire) tuples using the preserved port map."""
        nd = self.ng.G.nodes.get(nid, {})
        rows: list[tuple[str, str, str]] = []
        for idx, (port, wire) in enumerate(nd.get("input_ports", [])):
            driver = self.ng.wire_driver.get(wire)
            if driver is None:
                continue
            rows.append((port or f"I{idx}", driver, wire))
        if rows:
            return rows
        for idx, (pred, _dst, edge) in enumerate(self.ng.G.in_edges(nid, data=True)):
            wire = edge.get("wire", self.ng.output_wire(pred))
            rows.append((edge.get("port") or f"I{idx}", pred, wire))
        return rows

    def _clear_cell_inputs(self, nid: str) -> None:
        """Detach all current input edges and clear preserved input port metadata."""
        nd = self.ng.G.nodes.get(nid, {})
        for _port, wire in list(nd.get("input_ports", [])):
            readers = self.ng.wire_readers.get(wire, [])
            while nid in readers:
                readers.remove(nid)
        for pred in list(self.ng.G.predecessors(nid)):
            if self.ng.G.has_edge(pred, nid):
                self.ng.G.remove_edge(pred, nid)
        nd["input_ports"] = []
        nd["input_wires"] = []

    def _replace_cell_output_with_driver(self, cell: str, driver: str) -> None:
        old_wire = self.ng.G.nodes[cell].get("output_wire")
        new_wire = self.ng.G.nodes[driver].get("output_wire")
        for succ in list(self.ng.G.successors(cell)):
            edge_data = self.ng.G.get_edge_data(cell, succ, {})
            self._remove_edge(cell, succ, edge_data.get("wire", old_wire))
            self._add_edge(driver, succ, new_wire, edge_data.get("port"))
        for port, port_driver in list(self.ng.primary_outputs.items()):
            if port_driver == cell:
                self.ng.primary_outputs[port] = driver
        self._remove_cell(cell)

    def _rewrite_cell_as_unary(self, cell: str, gate_type: str, driver: str) -> None:
        nd = self.ng.G.nodes[cell]
        self._clear_cell_inputs(cell)
        nd["gate_type"] = PRIM_TO_YOSYS.get(gate_type, f"${gate_type}")
        self._add_edge(driver, cell, self.ng.output_wire(driver), "A")

    # ── remove dangling ───────────────────────────────────────────────────────

    def remove_dangling(self) -> int:
        """
        Iteratively remove all cells that do not contribute to any primary output.

        A cell is dangling if it cannot reach any PO node via forward edges.
        Uses reverse reachability: compute all nodes reachable *backward* from
        PO-flagged nodes, and remove anything outside that set.

        Returns
        -------
        int : number of cells removed
        """
        removed = 0
        changed = True
        while changed:
            changed = False
            # Preserve logic that reaches primary outputs or sequential state.
            # DFF cells are roots because their D/R/S/CLK fanin affects future cycles.
            po_drivers = {nid for nid, d in self.ng.G.nodes(data=True)
                          if d.get("is_po") and d.get("ntype") == "cell"}
            dff_cells = {nid for nid, d in self.ng.G.nodes(data=True)
                         if d.get("ntype") == "cell"
                         and d.get("gate_type", "") in DFF_TYPES}
            roots = po_drivers | dff_cells
            if not roots:
                break
            reachable: set[str] = set()
            stack = list(roots)
            while stack:
                nid = stack.pop()
                if nid in reachable:
                    continue
                reachable.add(nid)
                stack.extend(self.ng.G.predecessors(nid))

            to_remove = [
                nid for nid, d in self.ng.G.nodes(data=True)
                if d.get("ntype") == "cell" and nid not in reachable
            ]
            for nid in to_remove:
                self._remove_cell(nid)
                removed += 1
                changed = True

        return removed

    # ── insert gate before matching cells ────────────────────────────────────

    def insert_gate_before_pattern(self, name_pattern: str,
                                   new_gate: str,
                                   extra_input_name: str) -> list[str]:
        """
        For every cell whose name contains name_pattern, insert a new gate
        between that cell's first input driver and the cell itself.

        Before:  driver ──(old_wire)──► target_cell
        After:   driver ──(old_wire)──► [new_gate] ──(mid_wire)──► target_cell
                 extra  ────────────────────────────────────────┘

        Parameters
        ----------
        name_pattern   : substring to match against cell instance names
        new_gate       : primitive type of the new gate (e.g. "and")
        extra_input_name: signal/port/wire name to connect as the second input

        Returns
        -------
        list[str] : names of newly created gate cells
        """
        extra_nid  = self.ng.resolve(extra_input_name)
        extra_wire = self.ng.G.nodes[extra_nid]["output_wire"]
        targets    = self.ng.find_cells_by_pattern(name_pattern)
        created: list[str] = []

        for target in targets:
            in_edges = list(self.ng.G.in_edges(target, data=True))
            if not in_edges:
                continue
            old_driver, _, edata = in_edges[0]
            old_wire = edata.get("wire", "?")

            mid_wire = self._fresh_wire(f"ins_{target}")
            new_name = self._fresh_name(f"ins_{new_gate}")

            self._add_cell(new_name, new_gate, mid_wire)
            self._add_edge(old_driver, new_name, old_wire)
            self._add_edge(extra_nid,  new_name, extra_wire)
            self._remove_edge(old_driver, target, old_wire)
            self._add_edge(new_name, target, mid_wire)

            created.append(new_name)

        return created

    # ── replace gate type ─────────────────────────────────────────────────────

    def replace_gate_type(self, cell_name: str, new_prim: str) -> bool:
        """
        Change the gate type of a single cell.
        Connectivity is preserved; only the gate_type attribute changes.

        Returns True if the cell was found and updated.
        """
        if cell_name not in self.ng.G:
            return False
        nd = self.ng.G.nodes[cell_name]
        if nd.get("ntype") != "cell":
            return False
        nd["gate_type"] = PRIM_TO_YOSYS.get(new_prim, f"${new_prim}")
        return True

    def rename_cell(self, old_name: str, new_name: str) -> bool:
        """Rename a cell node and preserve all connectivity/cache entries."""
        try:
            nid = self.ng.resolve(old_name)
        except KeyError:
            return False
        if nid not in self.ng.G or self.ng.G.nodes[nid].get("ntype") != "cell":
            return False
        if new_name in self.ng.G:
            raise ValueError(f"target cell name '{new_name}' already exists")
        nx.relabel_nodes(self.ng.G, {nid: new_name}, copy=False)
        for wire, driver in list(self.ng.wire_driver.items()):
            if driver == nid:
                self.ng.wire_driver[wire] = new_name
        for wire, readers in list(self.ng.wire_readers.items()):
            self.ng.wire_readers[wire] = [
                new_name if reader == nid else reader for reader in readers
            ]
        for port, driver in list(self.ng.primary_outputs.items()):
            if driver == nid:
                self.ng.primary_outputs[port] = new_name
        for alias, target in list(self.ng.cell_aliases.items()):
            if target == nid:
                self.ng.cell_aliases[alias] = new_name
        return True

    def rename_wire(self, old_name: str, new_name: str) -> bool:
        """Rename a driven wire and update edge labels and port maps."""
        if new_name in self.ng.wire_driver:
            raise ValueError(f"target wire name '{new_name}' already exists")
        driver = self.ng.wire_driver.get(old_name)
        if driver is None:
            try:
                driver = self.ng.resolve(old_name)
                old_name = self.ng.G.nodes[driver].get("output_wire", old_name)
            except KeyError:
                return False
        self.ng.G.nodes[driver]["output_wire"] = new_name
        self.ng.wire_driver[new_name] = driver
        self.ng.wire_driver.pop(old_name, None)
        if old_name in self.ng.wire_readers:
            self.ng.wire_readers[new_name] = self.ng.wire_readers.pop(old_name)
        for _, _, data in self.ng.G.out_edges(driver, data=True):
            if data.get("wire") == old_name:
                data["wire"] = new_name
        for _, nd in self.ng.G.nodes(data=True):
            if nd.get("ntype") != "cell":
                continue
            ports = [
                (port, new_name if wire == old_name else wire)
                for port, wire in nd.get("input_ports", [])
            ]
            nd["input_ports"] = ports
            nd["input_wires"] = [wire for _, wire in ports]
        if old_name in self.ng.primary_inputs:
            self.ng.primary_inputs[new_name] = self.ng.primary_inputs.pop(old_name)
        for port, port_driver in list(self.ng.primary_outputs.items()):
            if port_driver == driver and port == old_name:
                self.ng.primary_outputs[new_name] = self.ng.primary_outputs.pop(port)
        return True

    def replace_all_in_cone(self, output_name: str,
                            old_prim: str, new_prim: str) -> list[str]:
        """
        Replace all cells of type old_prim with new_prim within the fanin
        cone of output_name.

        Returns list of changed cell names.
        """
        cone  = self.ng.extract_cone(output_name)
        yold  = PRIM_TO_YOSYS.get(old_prim, f"${old_prim}")
        ynew  = PRIM_TO_YOSYS.get(new_prim, f"${new_prim}")
        changed: list[str] = []
        for nid in cone:
            if self.ng.G.nodes[nid].get("gate_type") == yold:
                self.ng.G.nodes[nid]["gate_type"] = ynew
                changed.append(nid)
        return changed

    def replace_all_globally(self, old_prim: str,
                             new_prim: str) -> list[str]:
        """Replace all cells of type old_prim with new_prim across the entire design."""
        yold = PRIM_TO_YOSYS.get(old_prim, f"${old_prim}")
        ynew = PRIM_TO_YOSYS.get(new_prim, f"${new_prim}")
        changed: list[str] = []
        for nid, nd in self.ng.G.nodes(data=True):
            if nd.get("ntype") == "cell" and nd.get("gate_type") == yold:
                nd["gate_type"] = ynew
                changed.append(nid)
        return changed

    # ── fanout buffering ──────────────────────────────────────────────────────

    def _limit_driver_fanout(self, src_nid: str,
                             loads: list[dict[str, Optional[str]]],
                             src_wire: str,
                             max_fanout: int) -> int:
        if len(loads) <= max_fanout:
            for load in loads:
                self._add_edge(src_nid, load["dst"], src_wire, load.get("port"))
            return 0

        group_size = math.ceil(len(loads) / max_fanout)
        inserted = 0
        for i in range(0, len(loads), group_size):
            chunk = loads[i:i + group_size]
            buf_name = self._fresh_name("fo_buf")
            buf_wire = self._fresh_wire(f"fo_{src_wire}")
            self._add_cell(buf_name, "buf", buf_wire)
            self._add_edge(src_nid, buf_name, src_wire, "A")
            inserted += 1
            inserted += self._limit_driver_fanout(
                buf_name, chunk, buf_wire, max_fanout)
        return inserted

    def buffer_high_fanout(self, wire_or_cell: str,
                           max_fanout: int) -> int:
        """
        Insert a buffer tree on the output of wire_or_cell so that no
        single driver sees more than max_fanout loads.

        Returns number of buf cells inserted.
        """
        if max_fanout < 2:
            raise ValueError("max_fanout must be at least 2.")

        src_nid = self.ng.resolve(wire_or_cell)
        outgoing = list(self.ng.G.out_edges(src_nid, data=True))
        if len(outgoing) <= max_fanout:
            return 0

        src_wire = self.ng.G.nodes[src_nid]["output_wire"]
        loads: list[dict[str, Optional[str]]] = []
        for _, dst, edata in outgoing:
            loads.append({
                "dst": dst,
                "port": edata.get("port"),
                "wire": edata.get("wire", src_wire),
            })
        for load in loads:
            self._remove_edge(src_nid, load["dst"], load.get("wire") or src_wire)

        return self._limit_driver_fanout(src_nid, loads, src_wire, max_fanout)

    def buffer_all_high_fanout(self, max_fanout: int) -> int:
        """Insert buffer trees for every PI/cell driver above max_fanout."""
        if max_fanout < 2:
            raise ValueError("max_fanout must be at least 2.")

        candidates = [
            nid for nid, nd in list(self.ng.G.nodes(data=True))
            if nd.get("ntype") in {"pi", "cell"}
        ]
        inserted = 0
        for nid in candidates:
            if nid in self.ng.G and self.ng.G.out_degree(nid) > max_fanout:
                inserted += self.buffer_high_fanout(nid, max_fanout)
        return inserted

    def buffer_each_load(self, wire_or_cell: str) -> int:
        """Insert one buffer per current load of a driver."""
        src_nid = self.ng.resolve(wire_or_cell)
        outgoing = list(self.ng.G.out_edges(src_nid, data=True))
        if not outgoing:
            return 0

        src_wire = self.ng.G.nodes[src_nid]["output_wire"]
        loads = [
            {"dst": dst, "port": edata.get("port"), "wire": edata.get("wire", src_wire)}
            for _, dst, edata in outgoing
        ]
        for load in loads:
            self._remove_edge(src_nid, load["dst"], load.get("wire") or src_wire)

        inserted = 0
        for load in loads:
            buf_name = self._fresh_name("ded_buf")
            buf_wire = self._fresh_wire(f"ded_{src_wire}")
            self._add_cell(buf_name, "buf", buf_wire)
            self._add_edge(src_nid, buf_name, src_wire, "A")
            self._add_edge(buf_name, load["dst"], buf_wire, load.get("port"))
            inserted += 1
        return inserted

    # ── depth-balancing buffer insertion ──────────────────────────────────────

    def add_balance_buffers(self, from_name: str,
                            to_names: list[str]) -> dict[str, int]:
        """
        Equalize the combinational depth from from_name to each sink in
        to_names by inserting a chain of buf cells on short paths.

        Returns dict: sink_name → number of buffers inserted.
        """
        depths: dict[str, int] = {}
        for tname in to_names:
            d, _ = self.ng.get_max_depth(from_name, tname)
            depths[tname] = max(d, 0)

        target_depth = max(depths.values(), default=0)
        inserted: dict[str, int] = {}

        for tname, d in depths.items():
            gap = target_depth - d
            if gap <= 0:
                inserted[tname] = 0
                continue

            # Find the driver of the sink
            try:
                sink_nid = self.ng.resolve(tname)
            except KeyError:
                inserted[tname] = 0
                continue

            # Find the direct input edge to the sink
            in_edges = list(self.ng.G.in_edges(sink_nid, data=True))
            if not in_edges:
                inserted[tname] = 0
                continue

            prev_driver, _, edata = in_edges[0]
            prev_wire = edata.get("wire", "?")

            # Remove original edge; insert buffer chain
            self._remove_edge(prev_driver, sink_nid, prev_wire)
            cur_driver = prev_driver
            cur_wire   = prev_wire
            for _ in range(gap):
                buf_name = self._fresh_name("bal_buf")
                buf_wire = self._fresh_wire(f"bal_{tname}")
                self._add_cell(buf_name, "buf", buf_wire)
                self._add_edge(cur_driver, buf_name, cur_wire)
                cur_driver = buf_name
                cur_wire   = buf_wire

            self._add_edge(cur_driver, sink_nid, cur_wire)
            inserted[tname] = gap

        return inserted

    # ── invert-buf fusion ─────────────────────────────────────────────────────

    def fuse_not_buf_pairs(self) -> int:
        """
        Replace any 'not → buf' cascade with a single 'not'.
        (The buf is redundant and adds depth.)
        Returns number of pairs fused.
        """
        fused = 0
        changed = True
        while changed:
            changed = False
            for nid, nd in list(self.ng.G.nodes(data=True)):
                if nd.get("gate_type") != "$buf":
                    continue
                preds = list(self.ng.G.predecessors(nid))
                if len(preds) != 1:
                    continue
                pred_nd = self.ng.G.nodes.get(preds[0], {})
                if pred_nd.get("gate_type") != "$not":
                    continue
                # buf has single predecessor (not) and single output
                # Redirect all buf successors to the not cell
                not_wire = pred_nd["output_wire"]
                for succ in list(self.ng.G.successors(nid)):
                    buf_wire = nd["output_wire"]
                    self._remove_edge(nid, succ, buf_wire)
                    self._add_edge(preds[0], succ, not_wire)
                self._remove_cell(nid)
                fused += 1
                changed = True
        return fused

    def collapse_not_not_pairs(self) -> int:
        """Collapse NOT->NOT pairs by reconnecting the second NOT's loads."""
        collapsed = 0
        candidates: list[tuple[str, str]] = []
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("gate_type") != "$not" or nd.get("is_po"):
                continue
            preds = list(self.ng.G.predecessors(nid))
            if len(preds) != 1:
                continue
            pred = preds[0]
            pred_nd = self.ng.G.nodes.get(pred, {})
            if pred_nd.get("gate_type") != "$not":
                continue
            pred_preds = list(self.ng.G.predecessors(pred))
            if len(pred_preds) != 1:
                continue
            candidates.append((nid, pred_preds[0]))

        for nid, driver in candidates:
            if nid not in self.ng.G or driver not in self.ng.G:
                continue
            nd = self.ng.G.nodes.get(nid, {})
            if nd.get("gate_type") != "$not" or nd.get("is_po"):
                continue
            self._replace_cell_output_with_driver(nid, driver)
            collapsed += 1
        return collapsed

    def constant_input_gates(self, gate_type: str, const_value: int) -> list[str]:
        ytype = PRIM_TO_YOSYS.get(gate_type.lower(), f"${gate_type.lower()}")
        const_node = CONST_1 if int(const_value) else CONST_0
        result = []
        for nid, nd in self.ng.G.nodes(data=True):
            if nd.get("ntype") != "cell" or nd.get("gate_type") != ytype:
                continue
            if any(pred == const_node for pred in self.ng.G.predecessors(nid)):
                result.append(nid)
        return result

    def simplify_constant_gates(self) -> int:
        """Apply safe local constant propagation for common primitive gates."""
        simplified = 0
        changed = True
        while changed:
            changed = False
            for nid, nd in list(self.ng.G.nodes(data=True)):
                if nd.get("ntype") != "cell" or nd.get("gate_type") in DFF_TYPES:
                    continue
                preds = list(self.ng.G.predecessors(nid))
                pred_set = set(preds)
                gt = nd.get("gate_type")
                replacement: Optional[str] = None
                rewrite: Optional[tuple[str, str]] = None
                nonconst = [p for p in preds if p not in {CONST_0, CONST_1}]
                if gt == "$and" and CONST_0 in pred_set:
                    replacement = CONST_0
                elif gt == "$and" and CONST_1 in pred_set:
                    replacement = nonconst[0] if nonconst else CONST_1
                elif gt == "$or" and CONST_1 in pred_set:
                    replacement = CONST_1
                elif gt == "$or" and CONST_0 in pred_set:
                    replacement = nonconst[0] if nonconst else CONST_0
                elif gt == "$nand" and CONST_0 in pred_set:
                    replacement = CONST_1
                elif gt == "$nand" and CONST_1 in pred_set:
                    if nonconst:
                        rewrite = ("not", nonconst[0])
                    else:
                        replacement = CONST_0
                elif gt == "$nor" and CONST_1 in pred_set:
                    replacement = CONST_0
                elif gt == "$nor" and CONST_0 in pred_set:
                    if nonconst:
                        rewrite = ("not", nonconst[0])
                    else:
                        replacement = CONST_1
                elif gt == "$xor" and CONST_0 in pred_set:
                    replacement = nonconst[0] if nonconst else CONST_0
                elif gt == "$xor" and CONST_1 in pred_set:
                    if nonconst:
                        rewrite = ("not", nonconst[0])
                    else:
                        replacement = CONST_0
                elif gt == "$xnor" and CONST_0 in pred_set:
                    if nonconst:
                        rewrite = ("not", nonconst[0])
                    else:
                        replacement = CONST_1
                elif gt == "$xnor" and CONST_1 in pred_set:
                    replacement = nonconst[0] if nonconst else CONST_1
                elif gt == "$buf" and len(preds) == 1:
                    replacement = preds[0]
                elif gt == "$not" and len(preds) == 1:
                    if preds[0] == CONST_0:
                        replacement = CONST_1
                    elif preds[0] == CONST_1:
                        replacement = CONST_0
                    else:
                        pred_nd = self.ng.G.nodes.get(preds[0], {})
                        pred_preds = list(self.ng.G.predecessors(preds[0]))
                        if pred_nd.get("gate_type") == "$not" and len(pred_preds) == 1:
                            replacement = pred_preds[0]
                if rewrite is not None:
                    self._rewrite_cell_as_unary(nid, rewrite[0], rewrite[1])
                    simplified += 1
                    changed = True
                    continue
                if replacement is None:
                    continue
                self._replace_cell_output_with_driver(nid, replacement)
                simplified += 1
                changed = True
        return simplified

    def replace_xor_with_nand(self) -> int:
        """Replace each 2-input XOR with the standard 4-NAND implementation."""
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$xor":
                continue
            preds = list(self.ng.G.predecessors(nid))
            if len(preds) != 2:
                continue
            in_edges = [(p, self.ng.G.get_edge_data(p, nid, {})) for p in preds]
            for pred, edata in in_edges:
                self._remove_edge(pred, nid, edata.get("wire", self.ng.output_wire(pred)))

            a, b = preds
            a_wire = self.ng.output_wire(a)
            b_wire = self.ng.output_wire(b)
            t1 = self._fresh_name("xor_nand")
            t2 = self._fresh_name("xor_nand")
            t3 = self._fresh_name("xor_nand")
            w1 = self._fresh_wire(f"{nid}_n1")
            w2 = self._fresh_wire(f"{nid}_n2")
            w3 = self._fresh_wire(f"{nid}_n3")
            self._add_cell(t1, "nand", w1)
            self._add_cell(t2, "nand", w2)
            self._add_cell(t3, "nand", w3)
            nd["gate_type"] = "$nand"
            self._add_edge(a, t1, a_wire, "A")
            self._add_edge(b, t1, b_wire, "B")
            self._add_edge(a, t2, a_wire, "A")
            self._add_edge(t1, t2, w1, "B")
            self._add_edge(b, t3, b_wire, "A")
            self._add_edge(t1, t3, w1, "B")
            self._add_edge(t2, nid, w2, "A")
            self._add_edge(t3, nid, w3, "B")
            converted += 1
        return converted

    def replace_xnor_with_nor(self, cone_output: Optional[str] = None) -> int:
        """Replace 2-input XNOR cells with a four-NOR implementation."""
        scope = None
        if cone_output:
            scope = self.ng.extract_cone(cone_output)
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$xnor":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)

            p = self._fresh_name("xnor_nor")
            q = self._fresh_name("xnor_nor")
            r = self._fresh_name("xnor_nor")
            wp = self._fresh_wire(f"{nid}_nor_p")
            wq = self._fresh_wire(f"{nid}_nor_q")
            wr = self._fresh_wire(f"{nid}_nor_r")
            self._add_cell(p, "nor", wp)
            self._add_cell(q, "nor", wq)
            self._add_cell(r, "nor", wr)
            nd["gate_type"] = "$nor"

            self._add_edge(a, p, a_wire, "A")
            self._add_edge(b, p, b_wire, "B")
            self._add_edge(a, q, a_wire, "A")
            self._add_edge(p, q, wp, "B")
            self._add_edge(b, r, b_wire, "A")
            self._add_edge(p, r, wp, "B")
            self._add_edge(q, nid, wq, "A")
            self._add_edge(r, nid, wr, "B")
            converted += 1
        return converted

    def replace_or_with_nand_not(self, cone_output: Optional[str] = None) -> int:
        """Replace 2-input OR cells with NAND/NOT using De Morgan."""
        scope = None
        if cone_output:
            scope = self.ng.extract_cone(cone_output)
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$or":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)

            na = self._fresh_name("or_not")
            nb = self._fresh_name("or_not")
            wna = self._fresh_wire(f"{nid}_not_a")
            wnb = self._fresh_wire(f"{nid}_not_b")
            self._add_cell(na, "not", wna)
            self._add_cell(nb, "not", wnb)
            nd["gate_type"] = "$nand"

            self._add_edge(a, na, a_wire, "A")
            self._add_edge(b, nb, b_wire, "A")
            self._add_edge(na, nid, wna, "A")
            self._add_edge(nb, nid, wnb, "B")
            converted += 1
        return converted

    def replace_and_with_nand_not(self, cone_output: Optional[str] = None) -> int:
        """Replace 2-input AND cells with NAND followed by NOT."""
        scope = None
        if cone_output:
            scope = self.ng.extract_cone(cone_output)
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$and":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)

            n1 = self._fresh_name("and_nand")
            w1 = self._fresh_wire(f"{nid}_nand")
            self._add_cell(n1, "nand", w1)
            nd["gate_type"] = "$not"
            self._add_edge(a, n1, a_wire, "A")
            self._add_edge(b, n1, b_wire, "B")
            self._add_edge(n1, nid, w1, "A")
            converted += 1
        return converted

    def replace_or_with_and_not(self, cone_output: Optional[str] = None) -> int:
        """Replace 2-input OR cells with AND/NOT using De Morgan."""
        scope = None
        if cone_output:
            scope = self.ng.extract_cone(cone_output)
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$or":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)

            na = self._fresh_name("or_not")
            nb = self._fresh_name("or_not")
            mid = self._fresh_name("or_and")
            wna = self._fresh_wire(f"{nid}_not_a")
            wnb = self._fresh_wire(f"{nid}_not_b")
            wm = self._fresh_wire(f"{nid}_and")
            self._add_cell(na, "not", wna)
            self._add_cell(nb, "not", wnb)
            self._add_cell(mid, "and", wm)
            nd["gate_type"] = "$not"
            self._add_edge(a, na, a_wire, "A")
            self._add_edge(b, nb, b_wire, "A")
            self._add_edge(na, mid, wna, "A")
            self._add_edge(nb, mid, wnb, "B")
            self._add_edge(mid, nid, wm, "A")
            converted += 1
        return converted

    def replace_xor_with_and_or_not(self, cone_output: Optional[str] = None) -> int:
        """Replace 2-input XOR cells with (a & ~b) | (~a & b)."""
        scope = None
        if cone_output:
            scope = self.ng.extract_cone(cone_output)
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$xor":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)

            na = self._fresh_name("xor_not")
            nb = self._fresh_name("xor_not")
            t1 = self._fresh_name("xor_and")
            t2 = self._fresh_name("xor_and")
            wna = self._fresh_wire(f"{nid}_not_a")
            wnb = self._fresh_wire(f"{nid}_not_b")
            w1 = self._fresh_wire(f"{nid}_and_1")
            w2 = self._fresh_wire(f"{nid}_and_2")
            self._add_cell(na, "not", wna)
            self._add_cell(nb, "not", wnb)
            self._add_cell(t1, "and", w1)
            self._add_cell(t2, "and", w2)
            nd["gate_type"] = "$or"

            self._add_edge(a, na, a_wire, "A")
            self._add_edge(b, nb, b_wire, "A")
            self._add_edge(a, t1, a_wire, "A")
            self._add_edge(nb, t1, wnb, "B")
            self._add_edge(na, t2, wna, "A")
            self._add_edge(b, t2, b_wire, "B")
            self._add_edge(t1, nid, w1, "A")
            self._add_edge(t2, nid, w2, "B")
            converted += 1
        return converted
