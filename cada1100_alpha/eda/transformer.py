"""
eda/transformer.py
==================
Structural mutations on a NetlistGraph in-place.

Design principles
-----------------
1. Every public method leaves the graph in a valid, consistent state
   (wire_driver / wire_readers caches stay correct).

2. Mutations only touch graph edges and node attributes -they never
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
import time
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
        self._ctr: dict[str, int] = {}   # prefix ->next counter value
        self._deadline_monotonic: Optional[float] = None
        self._budget_exhausted: bool = False

    def set_deadline(self, deadline_monotonic: Optional[float]) -> None:
        self._deadline_monotonic = deadline_monotonic
        self._budget_exhausted = False

    def _time_budget_exhausted(self, reserve: float = 0.25) -> bool:
        if self._deadline_monotonic is None:
            return False
        exhausted = time.monotonic() >= self._deadline_monotonic - reserve
        if exhausted:
            self._budget_exhausted = True
        return exhausted

    def budget_exhausted(self) -> bool:
        return self._budget_exhausted

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

    def _build_gate_index(self, gate_types: set[str]) -> dict[tuple, str]:
        """Build O(1) lookup index: (ytype, sorted_input_drivers...) → cell_name.

        Only indexes cells whose gate_type is in *gate_types* and that are not
        primary-output drivers.  Call once before a batch of lookups.
        """
        index: dict[tuple, str] = {}
        po_drivers = set(self.ng.primary_outputs.values())
        commutative = {"$and", "$or", "$nand", "$nor", "$xor", "$xnor"}
        for nid, nd in self.ng.G.nodes(data=True):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell":
                continue
            gt = nd.get("gate_type")
            if gt not in gate_types:
                continue
            if nid in po_drivers:
                continue
            existing = self._cell_input_drivers(nid)
            if not existing:
                continue
            drivers = tuple(
                sorted(d for _p, d, _w in existing)
                if gt in commutative
                else tuple(d for _p, d, _w in existing)
            )
            key = (gt, drivers)
            if key not in index:
                index[key] = nid
        return index

    def _reuse_or_create_cell(self, gate_type: str, inputs: list[str],
                               prefix: str,
                               index: Optional[dict] = None) -> tuple[str, str, bool]:
        """Return (cell_name, output_wire, is_reused) for a gate.

        If *index* is provided, use it for O(1) lookup of existing gates.
        Otherwise scan all nodes (slower).
        """
        ytype = PRIM_TO_YOSYS.get(gate_type, f"${gate_type}")
        commutative = ytype in {"$and", "$or", "$nand", "$nor", "$xor", "$xnor"}
        key_inputs = tuple(sorted(inputs)) if commutative else tuple(inputs)
        if index is not None:
            existing = index.get((ytype, key_inputs))
            if existing is not None and existing in self.ng.G:
                return existing, self.ng.output_wire(existing), True
        else:
            # Slow path: linear scan (for single-gate lookups)
            for nid, nd in self.ng.G.nodes(data=True):
                if self._time_budget_exhausted():
                    break
                if nd.get("ntype") != "cell" or nd.get("gate_type") != ytype:
                    continue
                if nid in self.ng.primary_outputs.values():
                    continue
                existing = self._cell_input_drivers(nid)
                if len(existing) != len(inputs):
                    continue
                existing_drivers = [d for _p, d, _w in existing]
                if commutative:
                    existing_drivers = sorted(existing_drivers)
                    inputs_sorted = sorted(inputs)
                else:
                    inputs_sorted = tuple(inputs)
                if tuple(existing_drivers) == tuple(inputs_sorted):
                    return nid, self.ng.output_wire(nid), True
        name = self._fresh_name(prefix)
        wire = self._fresh_wire(f"{prefix}_y")
        self._add_cell(name, gate_type, wire)
        # Update index so subsequent lookups can find this new cell
        if index is not None:
            index[(ytype, key_inputs)] = name
        return name, wire, False

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

    def _find_not_of_driver(self, driver: str) -> Optional[str]:
        """Return an existing NOT cell fed directly by driver, if one exists."""
        if driver not in self.ng.G:
            return None
        for succ in self.ng.G.successors(driver):
            nd = self.ng.G.nodes.get(succ, {})
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$not":
                continue
            inputs = self._cell_input_drivers(succ)
            if len(inputs) == 1 and inputs[0][1] == driver:
                return succ
        return None

    def _not_of_driver(self, driver: str, hint: str) -> tuple[str, str, bool]:
        """Reuse or create NOT(driver), returning (not_cell, wire, created)."""
        existing = self._find_not_of_driver(driver)
        if existing is not None:
            return existing, self.ng.output_wire(existing), False
        inv = self._fresh_name(f"{hint}_not")
        winv = self._fresh_wire(f"{hint}_not")
        self._add_cell(inv, "not", winv)
        self._add_edge(driver, inv, self.ng.output_wire(driver), "A")
        return inv, winv, True

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
                if driver in self.ng.G and self.ng.G.nodes[driver].get("ntype") == "cell":
                    self.ng.G.nodes[driver]["is_po"] = True
        self._remove_cell(cell)

    def _rewrite_cell_as_unary(self, cell: str, gate_type: str, driver: str) -> None:
        nd = self.ng.G.nodes[cell]
        self._clear_cell_inputs(cell)
        nd["gate_type"] = PRIM_TO_YOSYS.get(gate_type, f"${gate_type}")
        self._add_edge(driver, cell, self.ng.output_wire(driver), "A")


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
            po_drivers = {
                nid for nid in self.ng.primary_outputs.values()
                if nid in self.ng.G
            }
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


    def insert_gate_before_pattern(self, name_pattern: str,
                                   new_gate: str,
                                   extra_input_name: str) -> list[str]:
        """
        For every cell whose name contains name_pattern, insert a new gate
        between that cell's first input driver and the cell itself.

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


    def _limit_driver_fanout(self, src_nid: str,
                             loads: list[dict[str, Optional[str]]],
                             src_wire: str,
                             max_fanout: int) -> int:
        if len(loads) <= max_fanout:
            for load in loads:
                self._add_edge(src_nid, load["dst"], src_wire, load.get("port"))
            return 0

        # A buffer consumes one fanout slot on the parent and contributes up to
        # max_fanout downstream slots.  Use the minimum number of child buffers
        # needed at this level, and keep any spare parent slots for direct loads.
        child_count = math.ceil((len(loads) - max_fanout) / (max_fanout - 1))
        child_count = max(1, min(max_fanout, child_count))
        direct_count = max(0, max_fanout - child_count)
        direct_loads = loads[:direct_count]
        remaining_loads = loads[direct_count:]

        for load in direct_loads:
            self._add_edge(src_nid, load["dst"], src_wire, load.get("port"))

        group_size = math.ceil(len(remaining_loads) / child_count)
        inserted = 0
        for i in range(0, len(remaining_loads), group_size):
            chunk = remaining_loads[i:i + group_size]
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


    def add_balance_buffers(self, from_name: str,
                            to_names: list[str]) -> dict[str, int]:
        """
        Equalize the combinational depth from from_name to each sink in
        to_names by inserting a chain of buf cells on short paths.

        Returns dict: sink_name ->number of buffers inserted.
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


    def try_reconnect_input_pin(self, gate_name: str, pin_name: str,
                                 signal_name: str) -> bool:
        """Reconnect one input pin of a gate to a different driver signal.

        Returns True on success, False if the gate or signal cannot be resolved.
        """
        gate_nid = self.ng.resolve(gate_name)
        nd = self.ng.G.nodes.get(gate_nid, {})
        if nd.get("ntype") != "cell":
            return False
        signal_nid = self.ng.resolve(signal_name)
        signal_wire = self.ng.output_wire(signal_nid)

        # Find and remove the old connection for this pin
        old_wire = ""
        ports = list(nd.get("input_ports", []))
        for idx, (port, wire) in enumerate(ports):
            if str(port) == pin_name or str(port).lstrip("\\") == pin_name.lstrip("\\"):
                old_wire = wire
                # Remove old edge
                old_driver = self.ng.wire_driver.get(wire)
                if old_driver and self.ng.G.has_edge(old_driver, gate_nid):
                    self._remove_edge(old_driver, gate_nid, wire)
                # Update port mapping
                ports[idx] = (pin_name, signal_wire)
                break
        else:
            # Pin not found in existing ports — add as new port
            ports.append((pin_name, signal_wire))
            # Also check graph edges
            for pred, _dst, edata in list(self.ng.G.in_edges(gate_nid, data=True)):
                if str(edata.get("port", "")) == pin_name:
                    old_wire = edata.get("wire", "")
                    self._remove_edge(pred, gate_nid, old_wire)

        nd["input_ports"] = ports
        nd["input_wires"] = [w for _, w in ports]
        # Add new edge
        self._add_edge(signal_nid, gate_nid, signal_wire, pin_name)
        return True

    def fuse_not_buf_pairs(self) -> int:
        """
        Replace any 'not ->buf' cascade with a single 'not'.
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
                # Update primary_outputs if BUF was a PO driver
                for port, driver in list(self.ng.primary_outputs.items()):
                    if driver == nid:
                        self.ng.primary_outputs[port] = preds[0]
                        pred_nd = self.ng.G.nodes.get(preds[0], {})
                        if pred_nd.get("ntype") == "cell":
                            pred_nd["is_po"] = True
                self._remove_cell(nid)
                fused += 1
                changed = True
        return fused

    def collapse_not_not_pairs(self) -> int:
        """Collapse NOT->NOT pairs by reconnecting the second NOT's loads.

        Handles PO-boundary nodes: primary_outputs are updated via
        _replace_cell_output_with_driver when collapsing the second NOT.
        """
        collapsed = 0
        candidates: list[tuple[str, str]] = []
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("gate_type") != "$not":
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
            if nd.get("gate_type") != "$not":
                continue
            self._replace_cell_output_with_driver(nid, driver)
            collapsed += 1
        return collapsed

    def collapse_buf_buf_pairs(self) -> int:
        """Collapse BUF->BUF chains into a single BUF.

        Chains arise from buffer_high_fanout, buffer_each_load, or
        add_balance_buffers that interact with pre-existing buffers.
        """
        collapsed = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$buf":
                continue
            preds = list(self.ng.G.predecessors(nid))
            if len(preds) != 1:
                continue
            pred = preds[0]
            if self.ng.G.nodes.get(pred, {}).get("gate_type") != "$buf":
                continue
            if self.ng.G.out_degree(pred) != 1:
                continue
            self._replace_cell_output_with_driver(nid, pred)
            collapsed += 1
        return collapsed

    def collapse_inverted_primitives(self) -> int:
        """Fold AND/OR/XOR followed by a private NOT into NAND/NOR/XNOR.
        Also folds NAND/NOR/XNOR followed by NOT back to AND/OR/XOR."""
        mapping = {"$and": "$nand", "$or": "$nor", "$xor": "$xnor"}
        reverse_mapping = {"$nand": "$and", "$nor": "$or", "$xnor": "$xor"}
        folded = 0
        changed = True
        while changed:
            changed = False
            for not_cell, not_nd in list(self.ng.G.nodes(data=True)):
                if not_nd.get("ntype") != "cell" or not_nd.get("gate_type") != "$not":
                    continue
                preds = list(self.ng.G.predecessors(not_cell))
                if len(preds) != 1:
                    continue
                src = preds[0]
                src_nd = self.ng.G.nodes.get(src, {})
                new_type = mapping.get(src_nd.get("gate_type"))
                if not new_type or self.ng.G.out_degree(src) != 1:
                    continue
                if src_nd.get("ntype") != "cell" or src_nd.get("gate_type") in DFF_TYPES:
                    continue

                old_src_wire = src_nd.get("output_wire")
                not_wire = not_nd.get("output_wire")
                src_nd["gate_type"] = new_type
                src_nd["output_wire"] = not_wire
                src_nd["is_po"] = bool(src_nd.get("is_po") or not_nd.get("is_po"))
                if old_src_wire and self.ng.wire_driver.get(old_src_wire) == src:
                    del self.ng.wire_driver[old_src_wire]
                if not_wire:
                    self.ng.wire_driver[not_wire] = src

                for succ in list(self.ng.G.successors(not_cell)):
                    edge = self.ng.G.get_edge_data(not_cell, succ, {})
                    self._remove_edge(not_cell, succ, edge.get("wire", not_wire))
                    self._add_edge(src, succ, not_wire, edge.get("port"))
                for port, driver in list(self.ng.primary_outputs.items()):
                    if driver == not_cell:
                        self.ng.primary_outputs[port] = src
                self._remove_cell(not_cell)
                folded += 1
                changed = True

        # Reverse pass: NAND/NOR/XNOR followed by private NOT → AND/OR/XOR
        changed = True
        while changed:
            changed = False
            for not_cell, not_nd in list(self.ng.G.nodes(data=True)):
                if not_nd.get("ntype") != "cell" or not_nd.get("gate_type") != "$not":
                    continue
                preds = list(self.ng.G.predecessors(not_cell))
                if len(preds) != 1:
                    continue
                src = preds[0]
                src_nd = self.ng.G.nodes.get(src, {})
                new_type = reverse_mapping.get(src_nd.get("gate_type"))
                if not new_type or self.ng.G.out_degree(src) != 1:
                    continue
                if src_nd.get("ntype") != "cell" or src_nd.get("gate_type") in DFF_TYPES:
                    continue

                old_src_wire = src_nd.get("output_wire")
                not_wire = not_nd.get("output_wire")
                src_nd["gate_type"] = new_type
                src_nd["output_wire"] = not_wire
                src_nd["is_po"] = bool(src_nd.get("is_po") or not_nd.get("is_po"))
                if old_src_wire and self.ng.wire_driver.get(old_src_wire) == src:
                    del self.ng.wire_driver[old_src_wire]
                if not_wire:
                    self.ng.wire_driver[not_wire] = src

                for succ in list(self.ng.G.successors(not_cell)):
                    edge = self.ng.G.get_edge_data(not_cell, succ, {})
                    self._remove_edge(not_cell, succ, edge.get("wire", not_wire))
                    self._add_edge(src, succ, not_wire, edge.get("port"))
                for port, driver in list(self.ng.primary_outputs.items()):
                    if driver == not_cell:
                        self.ng.primary_outputs[port] = src
                self._remove_cell(not_cell)
                folded += 1
                changed = True
        return folded

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

    def simplify_constant_gates(self, remove_buf: bool = False) -> int:
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
                    # XOR(0, x, ...) = XOR(x, ...). But if CONST_1 also present:
                    # XOR(0, 1) = 1, XOR(0, 1, x) = NOT(x)
                    has_const1 = CONST_1 in pred_set
                    if has_const1 and not nonconst:
                        replacement = CONST_1  # XOR(0, 1) = 1
                    elif has_const1 and nonconst:
                        rewrite = ("not", nonconst[0])  # XOR(0, 1, x) = NOT(x)
                    else:
                        replacement = nonconst[0] if nonconst else CONST_0
                elif gt == "$xor" and CONST_1 in pred_set:
                    if nonconst:
                        rewrite = ("not", nonconst[0])
                    else:
                        replacement = CONST_1  # XOR(1) = 1 (was incorrectly CONST_0)
                elif gt == "$xnor" and CONST_0 in pred_set:
                    has_const1 = CONST_1 in pred_set
                    if has_const1 and not nonconst:
                        replacement = CONST_0  # XNOR(0, 1) = 0
                    elif has_const1 and nonconst:
                        replacement = nonconst[0]  # XNOR(0, 1, x) = x (was incorrectly NOT)
                    elif nonconst:
                        rewrite = ("not", nonconst[0])
                    else:
                        replacement = CONST_1
                elif gt == "$xnor" and CONST_1 in pred_set:
                    replacement = nonconst[0] if nonconst else CONST_0  # XNOR(1, x) = x, XNOR(1) = 0 (was CONST_1)
                elif gt == "$buf" and len(preds) == 1:
                    if preds[0] in {CONST_0, CONST_1}:
                        replacement = preds[0]  # Always propagate constant through BUF
                    elif remove_buf:
                        replacement = preds[0]  # BUF(SIGNAL) → SIGNAL when remove_buf
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

    def simplify_boolean_identities(self) -> int:
        """Apply local Boolean identities such as x&x=x and x^x=0."""
        simplified = 0
        changed = True
        while changed:
            changed = False
            for nid, nd in list(self.ng.G.nodes(data=True)):
                if nd.get("ntype") != "cell" or nd.get("gate_type") in DFF_TYPES:
                    continue
                gt = nd.get("gate_type")
                inputs_list = self._cell_input_drivers(nid)
                replacement: Optional[str] = None
                rewrite: Optional[tuple[str, str]] = None

                # Consensus theorem: OR(AND(a,b), AND(NOT(a),c), AND(b,c))
                # → drop AND(b,c) which is redundant.  Must check BEFORE the
                # len(inputs)!=2 guard, since this requires exactly 3 inputs.
                if gt == "$or" and len(inputs_list) == 3:
                    and_info: list[tuple[str, str, str]] = []
                    for _port, drv, _wire in inputs_list:
                        drv_nd = self.ng.G.nodes.get(drv, {})
                        if drv_nd.get("gate_type") != "$and":
                            break
                        drv_inputs = self._cell_input_drivers(drv)
                        if len(drv_inputs) != 2:
                            break
                        (_la, la_drv, _lw), (_lb, lb_drv, _bw) = drv_inputs
                        and_info.append((drv, la_drv, lb_drv))
                    else:
                        # All 3 inputs are 2-input AND — check consensus
                        for i in range(3):
                            ai_name, ai_a, ai_b = and_info[i]
                            for j in range(3):
                                if i == j: continue
                                aj_name, aj_a, aj_b = and_info[j]
                                k = 3 - i - j
                                ak_name, ak_a, ak_b = and_info[k]
                                for ai_x, ai_y in ((ai_a, ai_b), (ai_b, ai_a)):
                                    for aj_x, aj_y in ((aj_a, aj_b), (aj_b, aj_a)):
                                        if (ai_y == ak_a or ai_y == ak_b) and \
                                           (aj_y == ak_a or aj_y == ak_b) and \
                                           self._are_complements(ai_x, aj_x):
                                            self._remove_edge(ak_name, nid,
                                                            self.ng.output_wire(ak_name))
                                            keep = [and_info[x][0] for x in (i, j)]
                                            self._clear_cell_inputs(nid)
                                            self._add_edge(keep[0], nid,
                                                          self.ng.output_wire(keep[0]), "A")
                                            self._add_edge(keep[1], nid,
                                                          self.ng.output_wire(keep[1]), "B")
                                            simplified += 1
                                            changed = True
                                            replacement = "<consensus>"  # signal handled
                                            break
                                    if replacement is not None: break
                                if replacement is not None: break
                            if replacement is not None: break
                        if replacement is not None:
                            continue

                # Binary identities: only handle exactly 2-input gates
                if len(inputs_list) != 2:
                    continue
                (_pa, a, _a_wire), (_pb, b, _b_wire) = inputs_list

                if a == b:
                    if gt in {"$and", "$or"}:
                        replacement = a
                    elif gt == "$nand":
                        rewrite = ("not", a)
                    elif gt == "$nor":
                        rewrite = ("not", a)
                    elif gt == "$xor":
                        replacement = CONST_0
                    elif gt == "$xnor":
                        replacement = CONST_1
                elif self._are_complements(a, b):
                    if gt == "$and":
                        replacement = CONST_0
                    elif gt == "$or":
                        replacement = CONST_1
                    elif gt == "$nand":
                        replacement = CONST_1
                    elif gt == "$nor":
                        replacement = CONST_0
                    elif gt == "$xor":
                        replacement = CONST_1
                    elif gt == "$xnor":
                        replacement = CONST_0
                # Absorption laws: a AND (a OR b) = a,  a OR (a AND b) = a
                elif replacement is None and rewrite is None:
                    a_nd = self.ng.G.nodes.get(a, {})
                    b_nd = self.ng.G.nodes.get(b, {})
                    if gt == "$and":
                        if (b_nd.get("gate_type") == "$or"
                                and a in self.ng.G.predecessors(b)):
                            replacement = a
                        elif (a_nd.get("gate_type") == "$or"
                                and b in self.ng.G.predecessors(a)):
                            replacement = b
                    elif gt == "$or":
                        if (b_nd.get("gate_type") == "$and"
                                and a in self.ng.G.predecessors(b)):
                            replacement = a
                        elif (a_nd.get("gate_type") == "$and"
                                and b in self.ng.G.predecessors(a)):
                            replacement = b
                # De Morgan inverse: NAND(NOT(a), NOT(b)) -> NOR(a,b)
                # Reduces 3 gates to 1 by stripping input inverters
                elif replacement is None and rewrite is None:
                    a_src = self._get_not_source(a)
                    b_src = self._get_not_source(b)
                    if gt in ("$nand", "$nor") and a_src is not None and b_src is not None:
                        if (self.ng.G.out_degree(a) == 1
                                and self.ng.G.out_degree(b) == 1):
                            self._remove_cell(a)
                            self._remove_cell(b)
                            nd["gate_type"] = "$nor" if gt == "$nand" else "$nand"
                            self._add_edge(a_src, nid,
                                          self.ng.output_wire(a_src), "A")
                            self._add_edge(b_src, nid,
                                          self.ng.output_wire(b_src), "B")
                            simplified += 1
                            changed = True
                            continue

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

        # N-ary gate simplification: remove duplicate inputs, detect complements,
        # and remove constant-valued inputs from multi-input associative gates.
        changed = True
        while changed:
            changed = False
            for nid, nd in list(self.ng.G.nodes(data=True)):
                if nd.get("ntype") != "cell" or nd.get("gate_type") in DFF_TYPES:
                    continue
                gt = nd.get("gate_type")
                inputs = self._cell_input_drivers(nid)
                if len(inputs) <= 2:
                    continue  # already handled above
                replacement: Optional[str] = None

                drivers = [d for _p, d, _w in inputs]
                unique = list(dict.fromkeys(drivers))  # preserve order, dedupe

                # Complement detection: AND/NAND with a and NOT(a) → const
                if gt in {"$and", "$nand", "$or", "$nor"}:
                    for i, di in enumerate(drivers):
                        for dj in drivers[i + 1:]:
                            if self._are_complements(di, dj):
                                if gt == "$and":
                                    replacement = CONST_0
                                elif gt == "$nand":
                                    replacement = CONST_1
                                elif gt == "$or":
                                    replacement = CONST_1
                                elif gt == "$nor":
                                    replacement = CONST_0
                                break
                        if replacement is not None:
                            break
                    if replacement is not None:
                        self._replace_cell_output_with_driver(nid, replacement)
                        simplified += 1
                        changed = True
                        continue

                # XOR/XNOR complement: a XOR NOT(a) = 1, a XNOR NOT(a) = 0
                if gt in {"$xor", "$xnor"}:
                    for i, di in enumerate(drivers):
                        for dj in drivers[i + 1:]:
                            if self._are_complements(di, dj):
                                if len(drivers) == 2:
                                    replacement = CONST_1 if gt == "$xor" else CONST_0
                                else:
                                    # N-ary: replace complement pair with constant,
                                    # rebuild gate with remaining drivers
                                    const_val = CONST_1 if gt == "$xor" else CONST_0
                                    remaining = [d for k, d in enumerate(drivers)
                                                 if k != i and k != j]
                                    remaining.append(const_val)
                                    # Deduplicate and rebuild
                                    unique_drv = list(dict.fromkeys(remaining))
                                    self._clear_cell_inputs(nid)
                                    for idx, drv in enumerate(unique_drv):
                                        self._add_edge(drv, nid,
                                                      self.ng.output_wire(drv),
                                                      f"I{idx}")
                                    simplified += 1
                                    changed = True
                                break
                        if replacement is not None:
                            break
                    if replacement is not None:
                        self._replace_cell_output_with_driver(nid, replacement)
                        simplified += 1
                        changed = True
                        continue

                # Deduplication: if duplicates found, rebuild with fewer inputs
                if len(unique) < len(drivers):
                    if len(unique) == 1:
                        # Single unique input: reduce to identity or NOT
                        if gt in {"$and", "$or", "$buf"}:
                            replacement = unique[0]
                        elif gt in {"$nand", "$nor"}:
                            self._rewrite_cell_as_unary(nid, "not", unique[0])
                            simplified += 1
                            changed = True
                            continue
                        elif gt == "$xor":
                            replacement = CONST_0  # x XOR x = 0
                        elif gt == "$xnor":
                            replacement = CONST_1  # x XNOR x = 1
                        if replacement is not None:
                            self._replace_cell_output_with_driver(nid, replacement)
                            simplified += 1
                            changed = True
                            continue
                    else:
                        # Rebuild gate with deduplicated inputs
                        self._clear_cell_inputs(nid)
                        for idx, driver in enumerate(unique):
                            port = f"I{idx}"
                            self._add_edge(driver, nid,
                                          self.ng.output_wire(driver), port)
                        simplified += 1
                        changed = True
                        continue

        return simplified

    def merge_functionally_equivalent_gates(self, max_support: int = 8) -> int:
        """Merge gates that compute the same Boolean function, even if their
        internal structure differs.

        Only gates whose combinational support (number of PI/const/DFF inputs)
        is ≤ *max_support* are considered, keeping runtime bounded.
        Default 6 (2^6=64 truth-table evaluations per candidate).  The internal
        hard safety cap is also 6, so this is the practical maximum.

        Returns the number of gates merged (removed).
        """
        import itertools

        merged = 0
        # Collect candidate gates: combinational cells with small support
        candidates: list[tuple[str, frozenset[str]]] = []
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") in DFF_TYPES:
                continue
            if nd.get("is_po") and nid in self.ng.primary_outputs.values():
                continue
            support = self._gate_support_inputs(nid)
            if len(support) <= max_support:
                candidates.append((nid, support))

        if len(candidates) <= 1:
            return 0

        # Group by support fingerprint for early pruning
        by_fingerprint: dict[tuple, list[str]] = {}
        for nid, support in candidates:
            fp = (len(support), tuple(sorted(support)))
            by_fingerprint.setdefault(fp, []).append(nid)

        # Within each fingerprint group, compare truth tables
        po_drivers = set(self.ng.primary_outputs.values())
        changed = True
        while changed:
            changed = False
            for _fp, group in list(by_fingerprint.items()):
                if len(group) <= 1:
                    continue
                # Compute truth tables for surviving gates
                tt_map: dict[tuple, str] = {}  # truth_table_tuple -> canonical nid
                survivors: list[str] = []
                for nid in group:
                    if nid not in self.ng.G or nid in po_drivers:
                        continue
                    nd = self.ng.G.nodes.get(nid, {})
                    if nd.get("ntype") != "cell" or nd.get("gate_type") in DFF_TYPES:
                        continue
                    tt = self._gate_truth_table(nid)
                    if tt is None:
                        survivors.append(nid)
                        continue
                    if tt not in tt_map:
                        tt_map[tt] = nid
                        survivors.append(nid)
                    else:
                        # Merge into canonical
                        keep = tt_map[tt]
                        if keep not in self.ng.G:
                            tt_map[tt] = nid
                            survivors.append(nid)
                            continue
                        self._replace_cell_output_with_driver(nid, keep)
                        merged += 1
                        changed = True
                by_fingerprint[_fp] = survivors

        return merged

    def _gate_support_inputs(self, nid: str) -> frozenset[str]:
        """Return the frozenset of PI/const/DFF-output wires in the fanin cone of *nid*.

        Stops at DFF outputs (sequential boundaries) and PIs.
        """
        visited: set[str] = set()
        support: set[str] = set()
        stack = [nid]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            nd = self.ng.G.nodes.get(node, {})
            ntype = nd.get("ntype")
            if ntype in ("pi", "const") or (
                ntype == "cell" and nd.get("gate_type") in DFF_TYPES
            ):
                support.add(nd.get("output_wire", node))
                continue
            if ntype == "cell":
                for _port, wire in nd.get("input_ports", []):
                    pred = self.ng.wire_driver.get(wire)
                    if pred is not None:
                        stack.append(pred)
        return frozenset(support)

    def _gate_truth_table(self, nid: str) -> Optional[tuple]:
        """Compute the truth-table tuple for gate *nid*, or None if too large.

        The truth table is a tuple of output bits in lexicographic input order.
        """
        import itertools
        support = sorted(self._gate_support_inputs(nid))
        if len(support) > 8:  # hard safety cap: 2^8 = 256 evaluations
            return None
        results: list[int] = []
        for values in itertools.product((0, 1), repeat=len(support)):
            env = dict(zip(support, values))
            try:
                val = self._eval_gate_tt(nid, env, {})
                results.append(int(val))
            except (KeyError, RecursionError):
                return None
        return tuple(results)

    def _eval_gate_tt(self, nid: str, env: dict[str, int],
                       memo: dict[str, int]) -> int:
        """Evaluate gate *nid* under input assignment *env* (truth-table helper)."""
        if nid in memo:
            return memo[nid]
        nd = self.ng.G.nodes.get(nid, {})
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
                self._eval_gate_tt(self.ng.wire_driver[wire], env, memo)
                for _port, wire in nd.get("input_ports", [])
                if wire in self.ng.wire_driver
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

    def _are_complements(self, a: str, b: str) -> bool:
        return self._is_not_of(a, b) or self._is_not_of(b, a)

    def _get_not_source(self, maybe_not: str):
        """Return the driver of the NOT gate's input, or None if not a NOT."""
        nd = self.ng.G.nodes.get(maybe_not, {})
        if nd.get("ntype") != "cell" or nd.get("gate_type") != "$not":
            return None
        inputs = self._cell_input_drivers(maybe_not)
        if len(inputs) == 1:
            return inputs[0][1]
        return None

    def _is_not_of(self, maybe_not: str, source: str) -> bool:
        nd = self.ng.G.nodes.get(maybe_not, {})
        if nd.get("ntype") != "cell" or nd.get("gate_type") != "$not":
            return False
        inputs = self._cell_input_drivers(maybe_not)
        return len(inputs) == 1 and inputs[0][1] == source

    def merge_aig_equivalent_gates(self, max_support: int = 8,
                                     max_depth: int = 20) -> int:
        """Merge gates with identical AND-Inverter Graph (AIG) signatures.

        Normalises each gate into AND+NOT canonical form, computes a
        structural hash, and merges nodes with matching signatures.
        Finds equivalences that direct-predecessor matching misses (e.g.
        NOR(a,b) and AND(NOT(a),NOT(b)) have the same AIG signature).

        *max_support* caps the number of PI/Dff boundary inputs (default 8).
        *max_depth* limits recursion depth per signature (default 20).
        Returns number of gates merged.
        """
        # Build AIG signature for every cell
        sig_cache: dict[str, tuple] = {}

        def _aig_sig(nid: str, memo: dict[str, tuple],
                      visiting: set[str], depth: int) -> Optional[tuple]:
            if nid in memo:
                return memo[nid]
            if nid in visiting or depth <= 0:
                return None  # cycle guard / depth limit
            visiting.add(nid)

            nd = self.ng.G.nodes.get(nid, {})
            ntype = nd.get("ntype")

            if ntype == "pi":
                sig = ("pi", nd.get("output_wire", nid))
            elif ntype == "const":
                sig = ("const", nd.get("output_wire", nid))
            elif ntype == "cell" and nd.get("gate_type") in DFF_TYPES:
                sig = ("dff", nd.get("output_wire", nid))
            elif ntype == "cell":
                gt = nd.get("gate_type")
                inputs = self._cell_input_drivers(nid)
                child_sigs = []
                for _port, drv, _wire in inputs:
                    cs = _aig_sig(drv, memo, visiting, depth - 1)
                    if cs is None:
                        visiting.remove(nid)
                        return None
                    child_sigs.append(cs)

                if gt == "$buf":
                    sig = child_sigs[0] if child_sigs else ("const", "1'b0")
                elif gt == "$not":
                    inner = child_sigs[0] if child_sigs else ("const", "1'b0")
                    sig = ("not", inner)
                elif gt == "$and":
                    sig = ("and", tuple(sorted(child_sigs, key=repr)))
                elif gt == "$or":
                    # OR → NOT(AND(NOT(a), NOT(b)))
                    not_children = tuple(sorted((("not", c) for c in child_sigs), key=repr))
                    sig = ("not", ("and", not_children))
                elif gt == "$nand":
                    sig = ("not", ("and", tuple(sorted(child_sigs, key=repr))))
                elif gt == "$nor":
                    # NOR → AND(NOT(a), NOT(b))
                    sig = ("and", tuple(sorted((("not", c) for c in child_sigs), key=repr)))
                elif gt == "$xor":
                    # XOR → OR(AND(a, NOT(b)), AND(NOT(a), b)) → AIG form
                    if len(child_sigs) == 2:
                        a, b = child_sigs
                        na, nb = ("not", a), ("not", b)
                        t1 = ("not", ("and", tuple(sorted((a, nb), key=repr))))
                        t2 = ("not", ("and", tuple(sorted((na, b), key=repr))))
                        sig = ("not", ("and", tuple(sorted((t1, t2), key=repr))))
                    else:
                        sig = ("xor_n", tuple(sorted(child_sigs, key=repr)))
                elif gt == "$xnor":
                    if len(child_sigs) == 2:
                        a, b = child_sigs
                        na, nb = ("not", a), ("not", b)
                        t1 = ("not", ("and", tuple(sorted((a, nb), key=repr))))
                        t2 = ("not", ("and", tuple(sorted((na, b), key=repr))))
                        inner = ("not", ("and", tuple(sorted((t1, t2), key=repr))))
                        sig = ("not", inner)
                    else:
                        sig = ("xnor_n", tuple(sorted(child_sigs, key=repr)))
                else:
                    sig = (gt, tuple(sorted(child_sigs, key=repr)))
            else:
                sig = ("unknown", nid)

            visiting.remove(nid)
            memo[nid] = sig
            return sig

        # Collect signatures for all combinational cells with bounded support
        seen: dict[tuple, str] = {}
        merged = 0
        po_drivers = set(self.ng.primary_outputs.values())

        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell":
                continue
            if nd.get("gate_type") in DFF_TYPES:
                continue
            if nid in po_drivers:
                continue
            # Skip cells with too many support inputs (deep cones)
            support = self._gate_support_inputs(nid)
            if len(support) > max_support:
                continue

            sig = _aig_sig(nid, sig_cache, set(), max_depth)
            if sig is None:
                continue

            if sig in seen:
                keep = seen[sig]
                if keep in self.ng.G and keep not in po_drivers:
                    self._replace_cell_output_with_driver(nid, keep)
                    merged += 1
            else:
                seen[sig] = nid

        return merged

    def balance_associative_trees(self, max_leaves: int = 256) -> int:
        """Rebuild private AND/OR/XOR trees into balanced binary trees."""
        balanced = 0
        associative = {"$and", "$or", "$xor"}
        for root, nd in list(self.ng.G.nodes(data=True)):
            if root not in self.ng.G:
                continue
            nd = self.ng.G.nodes.get(root, {})
            gate_type = nd.get("gate_type")
            if nd.get("ntype") != "cell" or gate_type not in associative:
                continue
            # Only process maximal trees; a same-type parent will handle us.
            if any(
                self.ng.G.nodes.get(succ, {}).get("gate_type") == gate_type
                and self.ng.G.out_degree(root) == 1
                for succ in self.ng.G.successors(root)
            ):
                continue
            leaves, internal = self._collect_associative_tree(root, gate_type)
            if len(leaves) <= 2 or len(leaves) > max_leaves:
                continue
            old_depth = self._assoc_tree_depth(root, gate_type)
            ideal_depth = math.ceil(math.log2(len(leaves)))
            if old_depth <= ideal_depth:
                continue
            self._rebuild_associative_tree(root, gate_type, leaves, internal)
            balanced += 1
        return balanced

    def _collect_associative_tree(
        self,
        root: str,
        gate_type: str,
    ) -> tuple[list[str], set[str]]:
        leaves: list[str] = []
        internal: set[str] = set()

        def visit(nid: str) -> None:
            nd = self.ng.G.nodes.get(nid, {})
            if (
                nid != root
                and nd.get("ntype") == "cell"
                and nd.get("gate_type") == gate_type
                and self.ng.G.out_degree(nid) == 1
                and not nd.get("is_po")
            ):
                internal.add(nid)
                for _port, pred, _wire in self._cell_input_drivers(nid):
                    visit(pred)
                return
            leaves.append(nid)

        internal.add(root)
        for _port, pred, _wire in self._cell_input_drivers(root):
            visit(pred)
        return leaves, internal

    def _assoc_tree_depth(self, root: str, gate_type: str) -> int:
        """Compute tree depth consistent with _collect_associative_tree.
        Uses same out_degree==1 filter for non-root nodes."""
        def depth(nid: str) -> int:
            nd = self.ng.G.nodes.get(nid, {})
            # Must match _collect_associative_tree: out_degree!=1 stops traversal
            if nid != root and (
                nd.get("ntype") != "cell"
                or nd.get("gate_type") != gate_type
                or self.ng.G.out_degree(nid) != 1
                or nd.get("is_po")
            ):
                return 0
            child_depths = [
                depth(pred)
                for _port, pred, _wire in self._cell_input_drivers(nid)
            ]
            return 1 + (max(child_depths) if child_depths else 0)

        return depth(root)

    def _rebuild_associative_tree(
        self,
        root: str,
        gate_type: str,
        leaves: list[str],
        internal: set[str],
    ) -> None:
        root_nd = self.ng.G.nodes[root]
        root_wire = root_nd.get("output_wire", root)
        root_is_po = bool(root_nd.get("is_po"))

        self._clear_cell_inputs(root)
        for cell in sorted(internal - {root}):
            if cell in self.ng.G:
                self._remove_cell(cell)

        level = list(leaves)
        while len(level) > 2:
            next_level: list[str] = []
            for i in range(0, len(level), 2):
                if i + 1 >= len(level):
                    next_level.append(level[i])
                    continue
                left, right = level[i], level[i + 1]
                cell = self._fresh_name("bal_assoc")
                wire = self._fresh_wire(f"{cell}_y")
                self._add_cell(cell, YOSYS_TO_PRIM.get(gate_type, gate_type.lstrip("$")), wire)
                self._add_edge(left, cell, self.ng.output_wire(left), "A")
                self._add_edge(right, cell, self.ng.output_wire(right), "B")
                next_level.append(cell)
            level = next_level

        root_nd["gate_type"] = gate_type
        root_nd["output_wire"] = root_wire
        root_nd["is_po"] = root_is_po
        self.ng.wire_driver[root_wire] = root
        if len(level) == 1:
            self._replace_cell_output_with_driver(root, level[0])
            return
        self._add_edge(level[0], root, self.ng.output_wire(level[0]), "A")
        self._add_edge(level[1], root, self.ng.output_wire(level[1]), "B")

    def replace_xor_with_nand(self, cone_output: Optional[str] = None) -> int:
        """Replace each 2-input XOR with the standard 4-NAND implementation."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        nand_index = self._build_gate_index({"$nand"})  # P2: O(1) reuse lookup
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$xor":
                continue
            if scope is not None and nid not in scope:
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
            # P2: reuse existing NAND gates if already present
            t1, w1, _ = self._reuse_or_create_cell("nand", [a, b], "xor_nand", nand_index)
            t2, w2, _ = self._reuse_or_create_cell("nand", [a, t1], "xor_nand", nand_index)
            t3, w3, _ = self._reuse_or_create_cell("nand", [b, t1], "xor_nand", nand_index)
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

    def replace_buf_with_not_not(self, cone_output: Optional[str] = None) -> int:
        """Replace BUF(x) with NOT(NOT(x)) for primitive-style constrained cones.

        Reuses existing NOT(x) gates via _not_of_driver to avoid creating
        duplicate inverters when many BUFs share the same source.
        """
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$buf":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 1:
                continue
            (_pa, a, a_wire) = inputs[0]
            self._clear_cell_inputs(nid)
            # Reuse existing NOT(a) if available
            na, wna, _ = self._not_of_driver(a, "buf")
            nd["gate_type"] = "$not"
            self._add_edge(na, nid, wna, "A")
            converted += 1
        return converted

    def replace_xnor_with_nor(self, cone_output: Optional[str] = None) -> int:
        """Replace 2-input XNOR cells with a four-NOR implementation."""
        scope = None
        if cone_output:
            scope = self.ng.extract_cone(cone_output)
        nor_index = self._build_gate_index({"$nor"})  # P2: O(1) reuse lookup
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$xnor":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)

            # P2: reuse existing NOR gates
            p, wp, _ = self._reuse_or_create_cell("nor", [a, b], "xnor_nor", nor_index)
            q, wq, _ = self._reuse_or_create_cell("nor", [a, p], "xnor_nor", nor_index)
            r, wr, _ = self._reuse_or_create_cell("nor", [b, p], "xnor_nor", nor_index)
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
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$or":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)

            na, wna, _ = self._not_of_driver(a, "or")
            nb, wnb, _ = self._not_of_driver(b, "or")
            nd["gate_type"] = "$nand"

            self._add_edge(na, nid, wna, "A")
            self._add_edge(nb, nid, wnb, "B")
            converted += 1
        return converted

    def replace_and_with_nand_not(self, cone_output: Optional[str] = None) -> int:
        """Replace 2-input AND cells with NAND followed by NOT.

        Reuses existing NAND(a,b) gates via _reuse_or_create_cell to avoid
        creating duplicate NAND cells across many AND gates.
        """
        scope = None
        if cone_output:
            scope = self.ng.extract_cone(cone_output)
        nand_index = self._build_gate_index({"$nand"})
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$and":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)

            # Reuse existing NAND(a,b) if available
            n1, w1, _ = self._reuse_or_create_cell("nand", [a, b], "and_nand", nand_index)
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
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$or":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)

            mid = self._fresh_name("or_and")
            wm = self._fresh_wire(f"{nid}_and")
            na, wna, _ = self._not_of_driver(a, "or")
            nb, wnb, _ = self._not_of_driver(b, "or")
            self._add_cell(mid, "and", wm)
            nd["gate_type"] = "$not"
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
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$xor":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)

            t1 = self._fresh_name("xor_and")
            t2 = self._fresh_name("xor_and")
            w1 = self._fresh_wire(f"{nid}_and_1")
            w2 = self._fresh_wire(f"{nid}_and_2")
            na, wna, _ = self._not_of_driver(a, "xor")
            nb, wnb, _ = self._not_of_driver(b, "xor")
            self._add_cell(t1, "and", w1)
            self._add_cell(t2, "and", w2)
            nd["gate_type"] = "$or"

            self._add_edge(a, t1, a_wire, "A")
            self._add_edge(nb, t1, wnb, "B")
            self._add_edge(na, t2, wna, "A")
            self._add_edge(b, t2, b_wire, "B")
            self._add_edge(t1, nid, w1, "A")
            self._add_edge(t2, nid, w2, "B")
            converted += 1
        return converted

    def replace_nand_with_and_not(self, cone_output: Optional[str] = None) -> int:
        """Replace NAND(a,b) with NOT(AND(a,b))."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$nand":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)
            mid = self._fresh_name("nand_and")
            wm = self._fresh_wire(f"{nid}_and")
            self._add_cell(mid, "and", wm)
            nd["gate_type"] = "$not"
            self._add_edge(a, mid, a_wire, "A")
            self._add_edge(b, mid, b_wire, "B")
            self._add_edge(mid, nid, wm, "A")
            converted += 1
        return converted

    def replace_nor_with_and_not(self, cone_output: Optional[str] = None) -> int:
        """Replace NOR(a,b) with AND(NOT(a), NOT(b))."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$nor":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)
            na, wna, _ = self._not_of_driver(a, "nor")
            nb, wnb, _ = self._not_of_driver(b, "nor")
            nd["gate_type"] = "$and"
            self._add_edge(na, nid, wna, "A")
            self._add_edge(nb, nid, wnb, "B")
            converted += 1
        return converted

    def replace_xnor_with_and_or_not(self, cone_output: Optional[str] = None) -> int:
        """Replace XNOR with AND/OR/NOT logic."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$xnor":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)
            t1 = self._fresh_name("xnor_and")
            t2 = self._fresh_name("xnor_and")
            w1 = self._fresh_wire(f"{nid}_and_1")
            w2 = self._fresh_wire(f"{nid}_and_2")
            na, wna, _ = self._not_of_driver(a, "xnor")
            nb, wnb, _ = self._not_of_driver(b, "xnor")
            self._add_cell(t1, "and", w1)
            self._add_cell(t2, "and", w2)
            nd["gate_type"] = "$or"
            self._add_edge(a, t1, a_wire, "A")
            self._add_edge(b, t1, b_wire, "B")
            self._add_edge(na, t2, wna, "A")
            self._add_edge(nb, t2, wnb, "B")
            self._add_edge(t1, nid, w1, "A")
            self._add_edge(t2, nid, w2, "B")
            converted += 1
        return converted

    def replace_nor_with_nand_not(self, cone_output: Optional[str] = None) -> int:
        """Replace NOR(a,b) with NOT(NAND(NOT(a), NOT(b)))."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$nor":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)
            mid = self._fresh_name("nor_nand")
            wm = self._fresh_wire(f"{nid}_nand")
            na, wna, _ = self._not_of_driver(a, "nor")
            nb, wnb, _ = self._not_of_driver(b, "nor")
            self._add_cell(mid, "nand", wm)
            nd["gate_type"] = "$not"
            self._add_edge(na, mid, wna, "A")
            self._add_edge(nb, mid, wnb, "B")
            self._add_edge(mid, nid, wm, "A")
            converted += 1
        return converted

    def replace_xnor_with_nand(self, cone_output: Optional[str] = None) -> int:
        """Replace XNOR with a NAND-only implementation."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        nand_index = self._build_gate_index({"$nand"})  # P2: O(1) reuse lookup
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$xnor":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)
            # P2: reuse existing NAND gates
            t1, w1, _ = self._reuse_or_create_cell("nand", [a, b], "xnor_nand", nand_index)
            t2, w2, _ = self._reuse_or_create_cell("nand", [a, t1], "xnor_nand", nand_index)
            t3, w3, _ = self._reuse_or_create_cell("nand", [b, t1], "xnor_nand", nand_index)
            tx, wx, _ = self._reuse_or_create_cell("nand", [t2, t3], "xnor_nand", nand_index)
            nd["gate_type"] = "$nand"
            self._add_edge(a, t1, a_wire, "A")
            self._add_edge(b, t1, b_wire, "B")
            self._add_edge(a, t2, a_wire, "A")
            self._add_edge(t1, t2, w1, "B")
            self._add_edge(b, t3, b_wire, "A")
            self._add_edge(t1, t3, w1, "B")
            self._add_edge(t2, tx, w2, "A")
            self._add_edge(t3, tx, w3, "B")
            self._add_edge(tx, nid, wx, "A")
            self._add_edge(tx, nid, wx, "B")
            converted += 1
        return converted

    def replace_and_with_nor_not(self, cone_output: Optional[str] = None) -> int:
        """Replace AND(a,b) with NOR(NOT(a), NOT(b))."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$and":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)
            na, wna, _ = self._not_of_driver(a, "and")
            nb, wnb, _ = self._not_of_driver(b, "and")
            nd["gate_type"] = "$nor"
            self._add_edge(na, nid, wna, "A")
            self._add_edge(nb, nid, wnb, "B")
            converted += 1
        return converted

    def replace_or_with_nor_not(self, cone_output: Optional[str] = None) -> int:
        """Replace OR(a,b) with NOT(NOR(a,b))."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$or":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)
            mid = self._fresh_name("or_nor")
            wm = self._fresh_wire(f"{nid}_nor")
            self._add_cell(mid, "nor", wm)
            nd["gate_type"] = "$not"
            self._add_edge(a, mid, a_wire, "A")
            self._add_edge(b, mid, b_wire, "B")
            self._add_edge(mid, nid, wm, "A")
            converted += 1
        return converted

    def replace_nand_with_nor_not(self, cone_output: Optional[str] = None) -> int:
        """Replace NAND with NOR/NOT logic."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$nand":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)
            mid = self._fresh_name("nand_nor")
            wm = self._fresh_wire(f"{nid}_and")
            na, wna, _ = self._not_of_driver(a, "nand")
            nb, wnb, _ = self._not_of_driver(b, "nand")
            self._add_cell(mid, "nor", wm)
            nd["gate_type"] = "$nor"
            self._add_edge(na, mid, wna, "A")
            self._add_edge(nb, mid, wnb, "B")
            self._add_edge(mid, nid, wm, "A")
            self._add_edge(mid, nid, wm, "B")
            converted += 1
        return converted

    def replace_xor_with_nor(self, cone_output: Optional[str] = None) -> int:
        """Replace XOR with a NOR-only implementation."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        nor_index = self._build_gate_index({"$nor"})  # P2: O(1) reuse lookup
        converted = 0
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if self._time_budget_exhausted():
                break
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$xor":
                continue
            if scope is not None and nid not in scope:
                continue
            inputs = self._cell_input_drivers(nid)
            if len(inputs) != 2:
                continue
            (_pa, a, a_wire), (_pb, b, b_wire) = inputs
            self._clear_cell_inputs(nid)
            # P2: reuse existing NOR gates
            p, wp, _ = self._reuse_or_create_cell("nor", [a, b], "xor_nor", nor_index)
            q, wq, _ = self._reuse_or_create_cell("nor", [a, p], "xor_nor", nor_index)
            r, wr, _ = self._reuse_or_create_cell("nor", [b, p], "xor_nor", nor_index)
            xnor_cell, wx, _ = self._reuse_or_create_cell("nor", [q, r], "xor_nor", nor_index)
            nd["gate_type"] = "$nor"
            self._add_edge(a, p, a_wire, "A")
            self._add_edge(b, p, b_wire, "B")
            self._add_edge(a, q, a_wire, "A")
            self._add_edge(p, q, wp, "B")
            self._add_edge(b, r, b_wire, "A")
            self._add_edge(p, r, wp, "B")
            self._add_edge(q, xnor_cell, wq, "A")
            self._add_edge(r, xnor_cell, wr, "B")
            self._add_edge(xnor_cell, nid, wx, "A")
            self._add_edge(xnor_cell, nid, wx, "B")
            converted += 1
        return converted
