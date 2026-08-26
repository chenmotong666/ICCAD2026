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
import heapq
import re
from collections import deque
import time
from typing import Optional

import networkx as nx

from .netlist_graph import (
    NetlistGraph, CONST_0, CONST_1,
    YOSYS_TO_PRIM, PRIM_TO_YOSYS, DFF_TYPES,
)
from .constants import DFF_DATA_PORTS

# T-H-06: identity repeaters inserted under a persistent style constraint.
# Tagged so merge/cleanup cannot fold a fanout tree back into one cell.
FANOUT_IDENTITY_ORIGIN_PREFIX = "synthetic:fo_id:"


def is_fanout_identity_node(nd: Optional[dict]) -> bool:
    return str((nd or {}).get("origin_id") or "").startswith(
        FANOUT_IDENTITY_ORIGIN_PREFIX
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
        # Design-scope style for buffer insertion ("" = insert $buf).
        self._buffer_style: str = ""
        # Standing "must not contain BUF" exclusion: use NOT-NOT instead.
        self._buffer_forbid_buf: bool = False

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
                  output_wire: str, is_po: bool = False,
                  origin_id: Optional[str] = None) -> None:
        """Register a new cell node in the graph."""
        ytype = PRIM_TO_YOSYS.get(gate_type, f"${gate_type}")
        self.ng.G.add_node(name, ntype="cell", gate_type=ytype,
                           output_wire=output_wire, is_po=is_po,
                           input_ports=[], input_wires=[],
                           origin_id=origin_id or f"synthetic:{name}",
                           origin_wire=output_wire)
        self.ng.wire_driver[output_wire] = name
        self.ng.mark_mutated()

    def _insert_identity_repeater(
        self,
        parent: str,
        parent_wire: str,
        prefix: str,
        wire_hint: str = "",
    ) -> tuple[str, str]:
        """Insert one fanout-tree node: $buf, or a style-legal identity.

        AND(x,x) is identity but counts as two sink pins, so a k-ary tree
        built that way cannot meet the same pin-count fanout bound as $buf.
        Every contest style allows $not, so the identity repeater is a
        NOT-NOT pair (one pin per level, depth +2).

        The unstyled $buf path keeps historical wire hints (``fo_{src}`` /
        ``ded_{src}``) so public buffer cases stay byte-identical.
        """
        style = (self._buffer_style or "").strip().lower().replace("-", "_")
        if style not in {"nand_not", "nor_not", "and_not", "and_or_not"} and not self._buffer_forbid_buf:
            name = self._fresh_name(prefix)
            wire = self._fresh_wire(wire_hint or prefix)
            self._add_cell(name, "buf", wire)
            self._add_edge(parent, name, parent_wire, "A")
            return name, wire
        n1 = self._fresh_name(f"{prefix}_n1")
        w1 = self._fresh_wire(wire_hint or f"{prefix}_n1")
        n2 = self._fresh_name(f"{prefix}_n2")
        w2 = self._fresh_wire(wire_hint or f"{prefix}_n2")
        self._add_cell(
            n1, "not", w1, origin_id=f"{FANOUT_IDENTITY_ORIGIN_PREFIX}{n1}"
        )
        self._add_edge(parent, n1, parent_wire, "A")
        self._add_cell(
            n2, "not", w2, origin_id=f"{FANOUT_IDENTITY_ORIGIN_PREFIX}{n2}"
        )
        self._add_edge(n1, n2, w1, "A")
        return n2, w2

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
        self.ng.mark_mutated()
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
            self.ng.mark_mutated()
        readers = self.ng.wire_readers.get(wire, [])
        if dst in readers:
            readers.remove(dst)
        while any(
            existing_wire == wire
            for _port, existing_wire in self.ng.G.nodes.get(dst, {}).get("input_ports", [])
        ):
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
        nd = self.ng.G.nodes.get(nid, {})
        out_wire = nd.get("output_wire")
        for _port, in_wire in list(nd.get("input_ports") or []):
            readers = self.ng.wire_readers.get(in_wire, [])
            while nid in readers:
                readers.remove(nid)
        if out_wire:
            for succ in list(self.ng.G.successors(nid)):
                while any(
                    wire == out_wire
                    for _port, wire in self.ng.G.nodes.get(succ, {}).get("input_ports", [])
                ):
                    self._forget_input(succ, out_wire)
            self.ng.wire_readers.pop(out_wire, None)
        if nid in self.ng.G:
            self.ng.G.remove_node(nid)
            self.ng.mark_mutated()
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
        old_wire = self.ng.G.nodes.get(cell, {}).get("output_wire")
        new_wire = self.ng.G.nodes[driver].get("output_wire")
        loads = self._local_driver_loads(cell)
        self._detach_driver_loads(cell, loads)
        for load in loads:
            self._connect_load(driver, new_wire, load)
        self._remove_cell(cell)
        if old_wire and new_wire and old_wire != new_wire:
            self.ng.signal_aliases[old_wire] = new_wire
        driver_nd = self.ng.G.nodes.get(driver, {})
        if driver_nd.get("ntype") == "cell":
            driver_nd["is_po"] = driver in set(self.ng.primary_outputs.values())

    def _local_driver_loads(self, nid: str) -> list[dict[str, object]]:
        """Return loads for one driver without rebuilding a whole-design index."""
        loads: list[dict[str, object]] = []
        for dst in list(self.ng.G.successors(nid)):
            nd = self.ng.G.nodes.get(dst, {})
            ports = list(nd.get("input_ports") or [])
            if ports:
                for port, wire in ports:
                    if self.ng.wire_driver.get(wire) == nid:
                        loads.append({"dst": dst, "port": str(port), "wire": wire})
            else:
                edge = self.ng.G.get_edge_data(nid, dst, {})
                loads.append({
                    "dst": dst,
                    "port": edge.get("port"),
                    "wire": edge.get("wire", self.ng.output_wire(nid)),
                })
        for po_name, driver in self.ng.primary_outputs.items():
            if driver == nid:
                loads.append({"po": po_name})
        return loads

    def materialize_constant_inputs(
        self,
        proofs: dict[str, dict[str, int]],
    ) -> int:
        """Replace proven-constant input drivers on selected cells by CONST nodes."""
        replaced = 0
        for index, (cell, driver_values) in enumerate(proofs.items()):
            if index % 256 == 0 and self._time_budget_exhausted(reserve=1.0):
                break
            if cell not in self.ng.G:
                continue
            inputs = self._cell_input_drivers(cell)
            rebuilt: list[tuple[str, str]] = []
            changed = False
            for port, driver, _wire in inputs:
                if driver in driver_values:
                    const_driver = CONST_1 if int(driver_values[driver]) else CONST_0
                    rebuilt.append((port, const_driver))
                    replaced += 1
                    changed = True
                else:
                    rebuilt.append((port, driver))
            if not changed:
                continue
            self._clear_cell_inputs(cell)
            for port, driver in rebuilt:
                self._add_edge(driver, cell, self.ng.output_wire(driver), port)
        return replaced

    def _load_count(self, nid: str) -> int:
        """Count sink pins and primary-output loads for one driver."""
        count = 0
        for dst in self.ng.G.successors(nid):
            nd = self.ng.G.nodes.get(dst, {})
            ports = list(nd.get("input_ports") or [])
            if ports:
                count += sum(
                    1 for _port, wire in ports
                    if self.ng.wire_driver.get(wire) == nid
                )
            else:
                count += 1
        count += sum(1 for driver in self.ng.primary_outputs.values() if driver == nid)
        return count

    def _rewrite_cell_as_unary(self, cell: str, gate_type: str, driver: str) -> None:
        nd = self.ng.G.nodes[cell]
        self._clear_cell_inputs(cell)
        nd["gate_type"] = PRIM_TO_YOSYS.get(gate_type, f"${gate_type}")
        self._add_edge(driver, cell, self.ng.output_wire(driver), "A")


    def fold_po_alias_pairs(self) -> int:
        """R14: fold writer-materialized PO alias pairs back into direct wiring.

        ``VerilogWriter.prepare_serialization_graph`` materializes a PO whose
        label differs from its driver's wire as an inverter pair
        (``__po_alias_inv0_*`` -> ``__po_alias_inv1_*``, NOT or NAND(x,x) /
        NOR(x,x)).  Re-loading such a file turns those pairs into real cells,
        which desynchronizes in-memory counts from the pre-write state and can
        trip style checks (NAND pairs under an and_not constraint).  Folding
        them restores the pre-write logical graph.  The fold is an identity by
        construction, so it is semantics-preserving.  Returns pairs folded.
        """
        folded = 0
        for nid in list(self.ng.G.nodes):
            nd = self.ng.G.nodes.get(nid, {})
            if nd.get("ntype") != "cell" or not str(nid).startswith("__po_alias_inv1_"):
                continue
            gt = nd.get("gate_type")
            if gt not in ("$not", "$nand", "$nor"):
                continue
            out_wire = nd.get("output_wire")
            if out_wire is None or self.ng.primary_outputs.get(str(out_wire)) != nid:
                continue
            ports = list(nd.get("input_ports") or [])
            if not ports:
                continue
            mid = str(ports[0][1])
            if gt != "$not" and (len(ports) != 2 or str(ports[1][1]) != mid):
                continue
            inv0 = self.ng.wire_driver.get(mid)
            if inv0 is None or not str(inv0).startswith("__po_alias_inv0_"):
                continue
            inv0_nd = self.ng.G.nodes.get(inv0, {})
            if inv0_nd.get("gate_type") != gt:
                continue
            inv0_ports = list(inv0_nd.get("input_ports") or [])
            if not inv0_ports:
                continue
            src = str(inv0_ports[0][1])
            if gt != "$not" and (len(inv0_ports) != 2 or str(inv0_ports[1][1]) != src):
                continue
            src_driver = self.ng.wire_driver.get(src)
            if src_driver is None:
                continue
            self.ng.primary_outputs[str(out_wire)] = src_driver
            self.ng.wire_driver[str(out_wire)] = src_driver
            if (
                src_driver in self.ng.G
                and self.ng.G.nodes[src_driver].get("ntype") == "cell"
            ):
                self.ng.G.nodes[src_driver]["is_po"] = True
            for victim in (nid, inv0):
                if victim in self.ng.G:
                    self.ng.G.remove_node(victim)
            self.ng.wire_driver.pop(mid, None)
            self.ng.wire_readers.pop(mid, None)
            folded += 1
        return folded

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
        """Replace matching BUF cells in place with a two-input primitive.

        The contest example calls this operation "insert before", but its
        required observable result is to replace each selected buffer while
        preserving the buffer output net.  Keeping the BUF after a newly
        inserted gate adds an unintended cell and does not match that contract.
        """
        primitive = new_gate.strip().lower().lstrip("$")
        if primitive not in {"and", "or", "nand", "nor", "xor", "xnor"}:
            raise ValueError(
                f"replacement gate must be a supported two-input primitive, got {new_gate!r}"
            )
        extra_nid  = self.ng.resolve(extra_input_name)
        extra_wire = self.ng.G.nodes[extra_nid]["output_wire"]
        targets = [
            nid for nid in self.ng.find_cells_by_pattern(name_pattern)
            if self.ng.G.nodes.get(nid, {}).get("gate_type") == "$buf"
        ]
        changed: list[str] = []

        for target in targets:
            inputs = self._cell_input_drivers(target)
            if len(inputs) != 1:
                continue
            _old_port, old_driver, old_wire = inputs[0]
            self._clear_cell_inputs(target)
            self.ng.G.nodes[target]["gate_type"] = PRIM_TO_YOSYS[primitive]
            self._add_edge(old_driver, target, old_wire, "A")
            self._add_edge(extra_nid, target, extra_wire, "B")
            changed.append(target)

        return changed

    def replace_matching_buffers(self, name_pattern: str,
                                 new_gate: str,
                                 extra_input_name: str) -> list[str]:
        """Explicit name for the compatibility operation above."""
        return self.insert_gate_before_pattern(
            name_pattern, new_gate, extra_input_name
        )


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
        new_type = PRIM_TO_YOSYS.get(new_prim, f"${new_prim}")
        if nd.get("gate_type") != new_type:
            nd["gate_type"] = new_type
            # R37 F1: gate_type drives the DFF edge cuts in the cached
            # combinational view; a type change must bump the epoch.
            self.ng.mark_mutated()
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
        self.ng.signal_aliases[old_name] = new_name
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


    def _driver_loads_map(self) -> dict[str, list[dict[str, object]]]:
        """Return every sink pin and primary-output connection by driver."""
        loads: dict[str, list[dict[str, object]]] = {}
        for dst, nd in self.ng.G.nodes(data=True):
            if nd.get("ntype") != "cell":
                continue
            ports = list(nd.get("input_ports") or [])
            if ports:
                for port, wire in ports:
                    driver = self.ng.wire_driver.get(wire)
                    if driver is not None:
                        loads.setdefault(driver, []).append({
                            "dst": dst, "port": str(port), "wire": wire,
                        })
                continue
            for driver, _dst, edge in self.ng.G.in_edges(dst, data=True):
                loads.setdefault(driver, []).append({
                    "dst": dst,
                    "port": edge.get("port"),
                    "wire": edge.get("wire", self.ng.output_wire(driver)),
                })
        for po_name, driver in self.ng.primary_outputs.items():
            loads.setdefault(driver, []).append({"po": po_name})
        for driver in loads:
            loads[driver].sort(key=lambda row: (
                0 if "po" in row else 1,
                str(row.get("po", "")),
                str(row.get("dst", "")),
                str(row.get("port", "")),
                str(row.get("wire", "")),
            ))
        return loads

    def _detach_driver_loads(
        self,
        src_nid: str,
        loads: list[dict[str, object]],
    ) -> None:
        """Detach graph/cache edges while retaining authoritative port maps."""
        removed_dsts: set[str] = set()
        for load in loads:
            dst = load.get("dst")
            if not isinstance(dst, str) or dst in removed_dsts:
                continue
            removed_dsts.add(dst)
            if self.ng.G.has_edge(src_nid, dst):
                self.ng.G.remove_edge(src_nid, dst)
        for load in loads:
            dst = load.get("dst")
            wire = load.get("wire")
            if not isinstance(dst, str) or not isinstance(wire, str):
                continue
            readers = self.ng.wire_readers.get(wire, [])
            while dst in readers:
                readers.remove(dst)

    def _connect_load(
        self,
        src_nid: str,
        src_wire: str,
        load: dict[str, object],
    ) -> None:
        po_name = load.get("po")
        if isinstance(po_name, str):
            self.ng.primary_outputs[po_name] = src_nid
            return
        dst = load.get("dst")
        if not isinstance(dst, str):
            return
        port = load.get("port")
        self._add_edge(
            src_nid,
            dst,
            src_wire,
            str(port) if port is not None else None,
        )

    def _refresh_primary_output_flags(self) -> None:
        po_drivers = set(self.ng.primary_outputs.values())
        for nid, nd in self.ng.G.nodes(data=True):
            if nd.get("ntype") == "cell":
                nd["is_po"] = nid in po_drivers

    def _limit_driver_fanout(self, src_nid: str,
                             loads: list[dict[str, object]],
                             src_wire: str,
                             max_fanout: int) -> int:
        if len(loads) <= max_fanout:
            for load in loads:
                self._connect_load(src_nid, src_wire, load)
            return 0

        # The exact lower bound follows from capacity accounting.  The root
        # supplies k slots; every added buffer consumes one upstream slot and
        # contributes k new slots, for a net gain of k-1 terminal loads.
        buffer_count = max(
            0, math.ceil((len(loads) - max_fanout) / (max_fanout - 1))
        )

        # Keep a PO whose name is also the source wire on the root.  Moving it
        # would leave the original cell textually driving the output port.  It
        # still consumes one of the root's k capacity slots and therefore does
        # not change the lower bound above.
        direct_po = [load for load in loads if load.get("po") == src_wire]
        remaining = [load for load in loads if load not in direct_po]

        drivers: list[tuple[str, str]] = [(src_nid, src_wire)]
        used: list[int] = [0]
        for load in direct_po:
            self._connect_load(src_nid, src_wire, load)
            used[0] += 1

        # Breadth-first construction gives minimum buffer count and minimum
        # possible tree height among those solutions.
        parent_index = 0
        for _ in range(buffer_count):
            while parent_index < len(drivers) and used[parent_index] >= max_fanout:
                parent_index += 1
            if parent_index >= len(drivers):
                raise RuntimeError("fanout tree capacity accounting failed")
            parent, parent_wire = drivers[parent_index]
            buf_name, buf_wire = self._insert_identity_repeater(
                parent, parent_wire, "fo_buf", f"fo_{src_wire}"
            )
            used[parent_index] += 1
            drivers.append((buf_name, buf_wire))
            used.append(0)

        load_driver_index = 0
        for load in remaining:
            while (
                load_driver_index < len(drivers)
                and used[load_driver_index] >= max_fanout
            ):
                load_driver_index += 1
            if load_driver_index >= len(drivers):
                raise RuntimeError("fanout tree has insufficient terminal capacity")
            driver, driver_wire = drivers[load_driver_index]
            self._connect_load(driver, driver_wire, load)
            used[load_driver_index] += 1
        return buffer_count

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
        loads = self._driver_loads_map().get(src_nid, [])
        if len(loads) <= max_fanout:
            return 0

        src_wire = self.ng.G.nodes[src_nid]["output_wire"]
        self._detach_driver_loads(src_nid, loads)
        inserted = self._limit_driver_fanout(src_nid, loads, src_wire, max_fanout)
        self._refresh_primary_output_flags()
        return inserted

    def buffer_all_high_fanout(self, max_fanout: int,
                               include_primary_inputs: bool = True) -> int:
        """Insert buffer trees for every PI/cell driver above max_fanout."""
        if max_fanout < 2:
            raise ValueError("max_fanout must be at least 2.")

        loads_by_driver = self._driver_loads_map()
        candidates = sorted(
            nid for nid, loads in loads_by_driver.items()
            if len(loads) > max_fanout
            and self.ng.G.nodes.get(nid, {}).get("ntype")
            in ({"pi", "cell"} if include_primary_inputs else {"cell"})
        )
        inserted = 0
        for nid in candidates:
            if nid not in self.ng.G:
                continue
            loads = loads_by_driver.get(nid, [])
            src_wire = self.ng.output_wire(nid)
            self._detach_driver_loads(nid, loads)
            inserted += self._limit_driver_fanout(
                nid, loads, src_wire, max_fanout
            )
        self._refresh_primary_output_flags()
        return inserted

    def buffer_each_load(self, wire_or_cell: str) -> int:
        """Insert one buffer per current load of a driver."""
        src_nid = self.ng.resolve(wire_or_cell)
        loads = self._driver_loads_map().get(src_nid, [])
        if not loads:
            return 0

        src_wire = self.ng.G.nodes[src_nid]["output_wire"]
        self._detach_driver_loads(src_nid, loads)

        inserted = 0
        for load in loads:
            buf_name, buf_wire = self._insert_identity_repeater(
                src_nid, src_wire, "ded_buf", f"ded_{src_wire}"
            )
            self._connect_load(buf_name, buf_wire, load)
            inserted += 1
        self._refresh_primary_output_flags()
        return inserted


    def add_balance_buffers(self, from_name: str,
                            to_names: list[str]) -> dict[str, int]:
        """
        Equalize the combinational depth from from_name to each sink in
        to_names by inserting a chain of identity repeaters on short paths
        ($buf, or NOT-NOT pairs when a style is active or BUF is forbidden).

        Returns dict: sink_name -> number of repeater levels inserted.
        """
        depths: dict[str, int] = {}
        chosen_edge: dict[str, tuple] = {}
        src_nid = None
        try:
            src_nid = self.ng.resolve(from_name)
        except KeyError:
            src_nid = None
        # R43: pick the sink in-edge that actually lies on the measured
        # from_name->sink path.  The historical in_edges[0] could hang the
        # balance chain off an unrelated input pin of a multi-input gate,
        # or off a DFF clock/reset pin, silently balancing the wrong wire.
        fwd = self._reachable_set(src_nid) if src_nid is not None else set()
        for tname in to_names:
            try:
                sink_nid = self.ng.resolve(tname)
            except KeyError:
                depths[tname] = 0
                continue
            d, _ = self.ng.get_max_depth(from_name, tname)
            if (
                d < 0
                and self.ng.G.nodes.get(sink_nid, {}).get("gate_type") in DFF_TYPES
            ):
                # R43 (Q&A A30/A21.2): a register endpoint measures at its
                # D input; balance through the D-data port only, never
                # CK/RN/SN.
                d_edge = None
                for u, _v, ed in self.ng.G.in_edges(sink_nid, data=True):
                    port = str(ed.get("port", "")).upper().lstrip("\\")
                    if port in DFF_DATA_PORTS:
                        d_edge = (sink_nid, u, ed)
                        break
                if d_edge is None:
                    depths[tname] = 0
                    continue
                driver = d_edge[1]
                if driver != src_nid and driver not in fwd:
                    depths[tname] = 0
                    continue
                try:
                    end_wire = self.ng.output_wire(driver)
                    d2, _ = self.ng.get_max_depth(from_name, end_wire)
                except Exception:
                    d2 = -1
                depths[tname] = max(d2, 0)
                chosen_edge[tname] = d_edge
                continue
            if d < 0 or src_nid is None:
                depths[tname] = 0
                continue
            picked = self._select_balance_in_edge(src_nid, fwd, sink_nid)
            if picked is None:
                depths[tname] = 0
                continue
            depths[tname] = d
            chosen_edge[tname] = picked

        target_depth = max(depths.values(), default=0)
        inserted: dict[str, int] = {}

        for tname in to_names:
            picked = chosen_edge.get(tname)
            if picked is None:
                inserted[tname] = 0
                continue
            gap = target_depth - depths[tname]
            if gap <= 0:
                inserted[tname] = 0
                continue
            sink_nid, prev_driver, edata = picked
            prev_wire = edata.get("wire", "?")
            prev_port = edata.get("port")

            # Remove original edge; insert identity-repeater chain
            self._remove_edge(prev_driver, sink_nid, prev_wire)
            cur_driver = prev_driver
            cur_wire   = prev_wire
            for _ in range(gap):
                cur_driver, cur_wire = self._insert_identity_repeater(
                    cur_driver, cur_wire, "bal_buf",
                    wire_hint=f"bal_{tname}",
                )

            self._add_edge(cur_driver, sink_nid, cur_wire, prev_port)
            inserted[tname] = gap

        return inserted

    def _reachable_set(self, start) -> set:
        """Bounded forward reachability for balance-buffer edge selection."""
        seen = {start}
        stack = [start]
        expansions = 0
        while stack and expansions < 50000:
            expansions += 1
            node = stack.pop()
            for nxt in self.ng.G.successors(node):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    def _select_balance_in_edge(self, src_nid, fwd: set, sink_nid: str):
        """Return (sink_nid, driver, edgedata) on a from_name->sink path.

        Only combinational sinks route here; register sinks are handled by
        the caller through their D-data port (never CK/RN/SN).
        """
        if sink_nid not in fwd:
            return None
        for u, _v, ed in self.ng.G.in_edges(sink_nid, data=True):
            if u == src_nid or u in fwd:
                return (sink_nid, u, ed)
        return None


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
            if is_fanout_identity_node(nd) or is_fanout_identity_node(pred_nd):
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
            if self._load_count(pred) != 1:
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
                if is_fanout_identity_node(not_nd) or is_fanout_identity_node(src_nd):
                    continue
                new_type = mapping.get(src_nd.get("gate_type"))
                if not new_type or self._load_count(src) != 1:
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
                if is_fanout_identity_node(not_nd) or is_fanout_identity_node(src_nd):
                    continue
                new_type = reverse_mapping.get(src_nd.get("gate_type"))
                if not new_type or self._load_count(src) != 1:
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

    def _simplify_constant_gates_legacy(self, remove_buf: bool = False) -> int:
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
                            if not (
                                is_fanout_identity_node(nd)
                                or is_fanout_identity_node(pred_nd)
                            ):
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

    def simplify_constant_gates(
        self,
        remove_buf: bool = False,
        target_cells: Optional[set[str]] = None,
        propagate: bool = True,
    ) -> int:
        """Propagate constants with a fanout work queue in near-linear time."""
        simplified = 0
        if target_cells is None:
            initial = [
                nid for nid, nd in self.ng.G.nodes(data=True)
                if nd.get("ntype") == "cell" and nd.get("gate_type") not in DFF_TYPES
            ]
        else:
            initial = [nid for nid in target_cells if nid in self.ng.G]
        queue = deque(initial)
        queued = set(initial)
        processed = 0

        def enqueue(nodes) -> None:
            for node in nodes:
                if node in self.ng.G and node not in queued:
                    queued.add(node)
                    queue.append(node)

        while queue:
            if processed % 256 == 0 and self._time_budget_exhausted(reserve=1.0):
                break
            processed += 1
            nid = queue.popleft()
            queued.discard(nid)
            if nid not in self.ng.G:
                continue
            nd = self.ng.G.nodes[nid]
            if nd.get("ntype") != "cell" or nd.get("gate_type") in DFF_TYPES:
                continue
            inputs = self._cell_input_drivers(nid)
            drivers = [driver for _port, driver, _wire in inputs]
            gt = nd.get("gate_type")
            replacement: Optional[str] = None
            rewrite: Optional[tuple[str, str]] = None
            rebuild: Optional[list[str]] = None
            nonconst = [d for d in drivers if d not in {CONST_0, CONST_1}]
            zeros = sum(d == CONST_0 for d in drivers)
            ones = sum(d == CONST_1 for d in drivers)

            if gt in {"$and", "$nand"} and zeros:
                replacement = CONST_0 if gt == "$and" else CONST_1
            elif gt in {"$and", "$nand"} and ones:
                if not nonconst:
                    replacement = CONST_1 if gt == "$and" else CONST_0
                elif len(nonconst) == 1:
                    replacement = nonconst[0] if gt == "$and" else None
                    rewrite = None if gt == "$and" else ("not", nonconst[0])
                else:
                    rebuild = nonconst
            elif gt in {"$or", "$nor"} and ones:
                replacement = CONST_1 if gt == "$or" else CONST_0
            elif gt in {"$or", "$nor"} and zeros:
                if not nonconst:
                    replacement = CONST_0 if gt == "$or" else CONST_1
                elif len(nonconst) == 1:
                    replacement = nonconst[0] if gt == "$or" else None
                    rewrite = None if gt == "$or" else ("not", nonconst[0])
                else:
                    rebuild = nonconst
            elif gt in {"$xor", "$xnor"} and (zeros or ones):
                invert = (ones & 1) ^ int(gt == "$xnor")
                if not nonconst:
                    replacement = CONST_1 if invert else CONST_0
                elif len(nonconst) == 1:
                    replacement = None if invert else nonconst[0]
                    rewrite = ("not", nonconst[0]) if invert else None
                else:
                    nd["gate_type"] = "$xnor" if invert else "$xor"
                    rebuild = nonconst
            elif gt == "$buf" and len(drivers) == 1:
                if drivers[0] in {CONST_0, CONST_1} or remove_buf:
                    replacement = drivers[0]
            elif gt == "$not" and len(drivers) == 1:
                if drivers[0] == CONST_0:
                    replacement = CONST_1
                elif drivers[0] == CONST_1:
                    replacement = CONST_0
                else:
                    pred_nd = self.ng.G.nodes.get(drivers[0], {})
                    pred_inputs = self._cell_input_drivers(drivers[0])
                    if pred_nd.get("gate_type") == "$not" and len(pred_inputs) == 1:
                        if not (
                            is_fanout_identity_node(nd)
                            or is_fanout_identity_node(pred_nd)
                        ):
                            replacement = pred_inputs[0][1]

            affected = list(self.ng.G.successors(nid))
            if rebuild is not None and rebuild != drivers:
                self._clear_cell_inputs(nid)
                for index, driver in enumerate(rebuild):
                    self._add_edge(driver, nid, self.ng.output_wire(driver), f"I{index}")
                simplified += 1
                if propagate:
                    enqueue([nid, *affected])
            elif rewrite is not None:
                self._rewrite_cell_as_unary(nid, rewrite[0], rewrite[1])
                simplified += 1
                if propagate:
                    enqueue([nid, *affected])
            elif replacement is not None:
                self._replace_cell_output_with_driver(nid, replacement)
                simplified += 1
                if propagate:
                    enqueue(affected)
        return simplified

    def simplify_boolean_identities(self, aig_only: bool = False) -> int:
        """Apply local Boolean identities such as x&x=x and x^x=0.

        ``aig_only`` (R11 F6): restrict every rewrite to the AND/NOT-closed
        subset -- only $and cells are touched, and the emitted replacements
        (identity rewires, AND(a,NOT a)=0, duplicate-pin removal) stay inside
        the strict AIG primitive basis.  Used by the strict-style cleanup
        path where the unrestricted form would emit NAND/NOR/OR/XOR gates
        that violate the requested gate basis.
        """
        simplified = 0
        changed = True
        while changed:
            if self._time_budget_exhausted(reserve=1.0):
                break
            changed = False
            for index, (nid, nd) in enumerate(list(self.ng.G.nodes(data=True))):
                if index % 256 == 0 and self._time_budget_exhausted(reserve=1.0):
                    break
                if nd.get("ntype") != "cell" or nd.get("gate_type") in DFF_TYPES:
                    continue
                if is_fanout_identity_node(nd):
                    continue
                gt = nd.get("gate_type")
                if aig_only and gt != "$and":
                    continue
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
                        if (self._load_count(a) == 1
                                and self._load_count(b) == 1):
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
            if self._time_budget_exhausted(reserve=1.0):
                break
            changed = False
            for index, (nid, nd) in enumerate(list(self.ng.G.nodes(data=True))):
                if index % 256 == 0 and self._time_budget_exhausted(reserve=1.0):
                    break
                if nd.get("ntype") != "cell" or nd.get("gate_type") in DFF_TYPES:
                    continue
                if is_fanout_identity_node(nd):
                    continue
                gt = nd.get("gate_type")
                if aig_only and gt != "$and":
                    continue
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
        Default 8 (2^8=256 truth-table evaluations per candidate).  The internal
        hard safety cap is also 8, so this is the practical maximum.

        Returns the number of gates merged (removed).
        """
        import itertools

        merged = 0
        # Global node-visit budget for support scans: a very deep netlist
        # could otherwise spend O(n * cone) work here before the support
        # filter prunes anything.  Sized so any public-case design finishes
        # its full scan (test36 needs ~4.1M visits for 12.4k cells) while a
        # 100k+ deep-cone design degrades to a partial pass (sound: fewer
        # merges, never a wrong one).
        support_budget = {
            "left": max(5_000_000, 1000 * self.ng.G.number_of_nodes())
        }
        # Collect candidate gates: combinational cells with small support
        candidates: list[tuple[str, frozenset[str]]] = []
        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") in DFF_TYPES:
                continue
            if nd.get("is_po") and nid in self.ng.primary_outputs.values():
                continue
            support = self._gate_support_inputs(nid, budget=support_budget)
            if support is None:
                break
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

    def _gate_support_inputs(
        self, nid: str, budget: Optional[dict] = None
    ) -> Optional[frozenset[str]]:
        """Return the frozenset of PI/const/DFF-output wires in the fanin cone of *nid*.

        Stops at DFF outputs (sequential boundaries) and PIs.
        ``budget`` is an optional mutable holder ``{"left": N}``; the scan
        consumes one unit per visited node and returns None (instead of a
        support set) when exhausted.
        """
        visited: set[str] = set()
        support: set[str] = set()
        stack = [nid]
        while stack:
            if budget is not None:
                if budget["left"] <= 0:
                    return None
                budget["left"] -= 1
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
        # Per-signature and global node caps: XOR/XNOR expansion adds several
        # signature nodes per cone level, so a huge reconvergent cone can
        # otherwise build a very large nested tuple.  Hitting a cap skips the
        # gate (sound: that merge simply does not happen).
        max_sig_nodes = 200_000
        max_total_nodes = 2_000_000
        sig_budget = {"nodes": 0}

        def _aig_sig(nid: str, memo: dict[str, tuple],
                      visiting: set[str], depth: int,
                      local: list[int]) -> Optional[tuple]:
            if nid in memo:
                return memo[nid]
            if nid in visiting or depth <= 0:
                return None  # cycle guard / depth limit
            visiting.add(nid)
            local[0] += 1
            sig_budget["nodes"] += 1
            if local[0] > max_sig_nodes or sig_budget["nodes"] > max_total_nodes:
                visiting.remove(nid)
                return None

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
                    cs = _aig_sig(drv, memo, visiting, depth - 1, local)
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

            sig = _aig_sig(nid, sig_cache, set(), max_depth, [0])
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

    def balance_associative_trees(
        self, max_leaves: int = 256, style: Optional[str] = None
    ) -> int:
        """Rebuild private AND/OR/XOR trees into balanced binary trees.

        Also balances NAND/NOR chains (P2-2 extension).  ``style`` is the
        gate-style constraint of the design; it gates the NAND/NOR pass.
        """
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
                and self._load_count(root) == 1
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
        # P2-2: also balance NAND/NOR chains
        balanced += self.balance_nand_nor_trees(max_leaves, style=style)
        return balanced

    def balance_associative_trees_with_duplication(
        self, max_leaves: int = 256
    ) -> int:
        """Balance maximal associative DAG cones even when nodes are shared.

        Shared internal nodes are left in place for their other consumers;
        only the selected root is rewired to a newly balanced tree over the
        same leaves.  A later dangling pass removes private remnants.
        """
        balanced = 0
        associative = {"$and", "$or", "$xor"}
        for root, root_data in list(self.ng.G.nodes(data=True)):
            if root not in self.ng.G:
                continue
            gate_type = self.ng.G.nodes[root].get("gate_type")
            if root_data.get("ntype") != "cell" or gate_type not in associative:
                continue
            if any(
                self.ng.G.nodes.get(succ, {}).get("gate_type") == gate_type
                for succ in self.ng.G.successors(root)
            ):
                continue
            leaves: list[str] = []
            visiting: set[str] = set()
            # R11: explicit-stack traversal (identical leaf order and
            # cycle/size semantics to the former recursion) so deep cones
            # cannot overflow the Python stack.
            chain_ok = True
            stack: list[tuple[str, bool]] = [(root, False)]
            while stack:
                node, expanded = stack.pop()
                if expanded:
                    visiting.remove(node)
                    continue
                if node in visiting:
                    chain_ok = False
                    break
                node_data = self.ng.G.nodes.get(node, {})
                if (
                    node != root
                    and (
                        node_data.get("ntype") != "cell"
                        or node_data.get("gate_type") != gate_type
                        or node_data.get("is_po")
                    )
                ):
                    leaves.append(node)
                    if len(leaves) > max_leaves:
                        chain_ok = False
                        break
                    continue
                visiting.add(node)
                inputs = self._cell_input_drivers(node)
                if len(inputs) != 2:
                    visiting.remove(node)
                    leaves.append(node)
                    if len(leaves) > max_leaves:
                        chain_ok = False
                        break
                    continue
                stack.append((node, True))
                for _port, pred, _wire in reversed(inputs):
                    stack.append((pred, False))

            if not chain_ok or len(leaves) <= 2 or len(leaves) > max_leaves:
                continue
            arrival_memo: dict[str, int] = {}
            arrival_visiting: set[str] = set()

            def arrival_depth(node: str) -> int:
                """Combinational arrival time, with every DFF-Q as a source.

                R11: iterative post-order DP (identical values to the former
                recursion) so deep cones cannot overflow the stack.
                """
                if node in arrival_memo:
                    return arrival_memo[node]
                work: list[tuple[str, bool]] = [(node, False)]
                while work:
                    cur, processed = work.pop()
                    if processed:
                        arrival_visiting.discard(cur)
                        child_vals = [
                            arrival_memo[pred]
                            for _port, pred, _wire in self._cell_input_drivers(cur)
                            if pred in arrival_memo
                        ]
                        arrival_memo[cur] = 1 + (
                            max(child_vals) if child_vals else 0
                        )
                        continue
                    if cur in arrival_memo or cur in arrival_visiting:
                        continue
                    cur_nd = self.ng.G.nodes.get(cur, {})
                    if (
                        cur_nd.get("ntype") != "cell"
                        or cur_nd.get("gate_type") in DFF_TYPES
                    ):
                        arrival_memo[cur] = 0
                        continue
                    arrival_visiting.add(cur)
                    work.append((cur, True))
                    for _port, pred, _wire in reversed(self._cell_input_drivers(cur)):
                        if pred not in arrival_memo:
                            work.append((pred, False))
                return arrival_memo.get(node, 0)

            # A level-by-level tree is only optimal when all leaves arrive at
            # the same time.  Logic cones often contain a mixture of early PI
            # leaves and late sub-cones, so use the Huffman scheduling rule:
            # combine the two earliest signals first.  This minimizes the
            # completion time of a binary associative tree.
            old_arrival = arrival_depth(root)
            trial_heap = [arrival_depth(leaf) for leaf in leaves]
            heapq.heapify(trial_heap)
            while len(trial_heap) > 1:
                left_depth = heapq.heappop(trial_heap)
                right_depth = heapq.heappop(trial_heap)
                heapq.heappush(trial_heap, max(left_depth, right_depth) + 1)
            if not trial_heap or trial_heap[0] >= old_arrival:
                continue

            root_nd = self.ng.G.nodes[root]
            root_wire = root_nd.get("output_wire", root)
            root_is_po = bool(root_nd.get("is_po"))
            self._clear_cell_inputs(root)
            work_heap: list[tuple[int, int, str]] = []
            serial = 0
            for leaf in leaves:
                heapq.heappush(work_heap, (arrival_depth(leaf), serial, leaf))
                serial += 1
            while len(work_heap) > 2:
                left_depth, _left_serial, left = heapq.heappop(work_heap)
                right_depth, _right_serial, right = heapq.heappop(work_heap)
                cell = self._fresh_name("bal_dup")
                wire = self._fresh_wire(f"{cell}_y")
                self._add_cell(
                    cell,
                    YOSYS_TO_PRIM.get(gate_type, gate_type.lstrip("$")),
                    wire,
                )
                self._add_edge(left, cell, self.ng.output_wire(left), "A")
                self._add_edge(right, cell, self.ng.output_wire(right), "B")
                heapq.heappush(
                    work_heap,
                    (max(left_depth, right_depth) + 1, serial, cell),
                )
                serial += 1
            _left_depth, _left_serial, left = heapq.heappop(work_heap)
            _right_depth, _right_serial, right = heapq.heappop(work_heap)
            root_nd["gate_type"] = gate_type
            root_nd["output_wire"] = root_wire
            root_nd["is_po"] = root_is_po
            self.ng.wire_driver[root_wire] = root
            self._add_edge(left, root, self.ng.output_wire(left), "A")
            self._add_edge(right, root, self.ng.output_wire(right), "B")
            balanced += 1
        return balanced

    def _assoc_tree_depth_unrestricted(self, root: str, gate_type: str) -> int:
        visiting: set[str] = set()

        def depth(node: str) -> int:
            node_data = self.ng.G.nodes.get(node, {})
            if (
                node != root
                and (
                    node_data.get("ntype") != "cell"
                    or node_data.get("gate_type") != gate_type
                    or node_data.get("is_po")
                )
            ):
                return 0
            if node in visiting:
                return 0
            visiting.add(node)
            value = 1 + max(
                (depth(pred) for _port, pred, _wire in self._cell_input_drivers(node)),
                default=0,
            )
            visiting.remove(node)
            return value

        return depth(root)

    def _collect_associative_tree(
        self,
        root: str,
        gate_type: str,
    ) -> tuple[list[str], set[str]]:
        leaves: list[str] = []
        internal: set[str] = set()
        internal.add(root)
        # R11: explicit-stack DFS (identical pre-order leaf sequence to the
        # former recursion) so >1000-deep chains cannot overflow the stack.
        stack: list[str] = [
            pred
            for _port, pred, _wire in reversed(self._cell_input_drivers(root))
        ]
        while stack:
            nid = stack.pop()
            nd = self.ng.G.nodes.get(nid, {})
            if (
                nid != root
                and nd.get("ntype") == "cell"
                and nd.get("gate_type") == gate_type
                and self._load_count(nid) == 1
                and not nd.get("is_po")
            ):
                internal.add(nid)
                for _port, pred, _wire in reversed(self._cell_input_drivers(nid)):
                    stack.append(pred)
                continue
            leaves.append(nid)
        return leaves, internal

    def _assoc_tree_depth(self, root: str, gate_type: str) -> int:
        """Compute tree depth consistent with _collect_associative_tree.
        Uses same out_degree==1 filter for non-root nodes.  R11: iterative
        post-order DP (identical values to the former recursion) so deep
        chains cannot overflow the Python stack."""
        memo: dict[str, int] = {}
        in_progress: set[str] = set()
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            nid, processed = stack.pop()
            if processed:
                in_progress.discard(nid)
                child_depths = [
                    memo[pred]
                    for _port, pred, _wire in self._cell_input_drivers(nid)
                    if pred in memo
                ]
                memo[nid] = 1 + (max(child_depths) if child_depths else 0)
                continue
            if nid in memo or nid in in_progress:
                continue
            nd = self.ng.G.nodes.get(nid, {})
            # Must match _collect_associative_tree: out_degree!=1 stops traversal
            if nid != root and (
                nd.get("ntype") != "cell"
                or nd.get("gate_type") != gate_type
                or self._load_count(nid) != 1
                or nd.get("is_po")
            ):
                memo[nid] = 0
                continue
            in_progress.add(nid)
            stack.append((nid, True))
            for _port, pred, _wire in reversed(self._cell_input_drivers(nid)):
                if pred not in memo:
                    stack.append((pred, False))
        return memo.get(root, 0)

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

    # ------------------------------------------------------------------
    # NAND / NOR chain balancing
    # ------------------------------------------------------------------

    def balance_nand_nor_trees(
        self, max_leaves: int = 256, style: Optional[str] = None
    ) -> int:
        """Balance NAND and NOR chains by expanding to AND-OR-NOT and rebuilding.

        A NAND chain  out = NAND(l0, NAND(l1, NAND(l2, ...)))
        expands to the SOP form:
            out = NOT(l0) + l1·NOT(l2) + l1·l3·NOT(l4) + ...
        which is then implemented with a balanced AND-OR-NOT tree.
        NOR chains are handled dually.
        """
        # Style gate: the rebuilt tree is AND-OR-NOT, which violates
        # nand_not / nor_not style constraints; skip balancing there.
        if style in ("nand_not", "nor_not"):
            return 0
        balanced = 0
        for gate_type in ("$nand", "$nor"):
            for root, nd in list(self.ng.G.nodes(data=True)):
                if root not in self.ng.G:
                    continue
                nd = self.ng.G.nodes.get(root, {})
                if nd.get("ntype") != "cell" or nd.get("gate_type") != gate_type:
                    continue
                # Only process maximal trees; a same-type parent handles it.
                if any(
                    self.ng.G.nodes.get(succ, {}).get("gate_type") == gate_type
                    and self._load_count(root) == 1
                    for succ in self.ng.G.successors(root)
                ):
                    continue
                # Collect ordered chain leaves
                leaves = self._collect_chain_leaves(root, gate_type)
                if leaves is None or len(leaves) <= 2 or len(leaves) > max_leaves:
                    continue
                old_depth = self._chain_depth(root, gate_type)
                ideal_depth = math.ceil(math.log2(len(leaves))) + 2
                if old_depth <= ideal_depth:
                    continue
                if gate_type == "$nand":
                    self._rebuild_nand_chain_balanced(root, leaves)
                else:
                    self._rebuild_nor_chain_balanced(root, leaves)
                balanced += 1
        return balanced

    def _collect_chain_leaves(
        self, root: str, gate_type: str
    ) -> Optional[list[str]]:
        """Collect leaves of a *chain* of same-type gates in order.

        A chain is a tree where every internal node has exactly one
        successor that is also an internal node (the other is a leaf).
        Returns ``None`` when the tree is not a simple chain.

        R11: iterative (the former tail recursion overflowed the Python
        stack on >1000-deep chains); traversal order is unchanged.
        """
        leaves: list[str] = []
        visited: set[str] = set()
        cur = root
        while True:
            if cur in visited:
                return None
            visited.add(cur)
            inputs = self._cell_input_drivers(cur)
            if len(inputs) != 2:
                return None
            internal_inputs = []
            leaf_inputs = []
            for _port, pred, _wire in inputs:
                pred_nd = self.ng.G.nodes.get(pred, {})
                if (
                    pred != root
                    and pred_nd.get("ntype") == "cell"
                    and pred_nd.get("gate_type") == gate_type
                    and self._load_count(pred) == 1
                    and not pred_nd.get("is_po")
                ):
                    internal_inputs.append(pred)
                else:
                    leaf_inputs.append(pred)
            if len(internal_inputs) > 1:
                return None          # branching → not a chain
            if len(internal_inputs) == 0:
                # Both inputs are leaves
                for _port, pred, _wire in inputs:
                    leaves.append(pred)
                return leaves
            # One internal, one leaf – side-leaf first, then continue down
            leaves.append(leaf_inputs[0])
            cur = internal_inputs[0]

    def _chain_depth(self, root: str, gate_type: str) -> int:
        """Depth of a chain (number of internal gates)."""
        depth = 0
        cur = root
        visited: set[str] = set()
        while cur in self.ng.G and cur not in visited:
            nd = self.ng.G.nodes.get(cur, {})
            if nd.get("ntype") != "cell" or nd.get("gate_type") != gate_type:
                break
            visited.add(cur)
            depth += 1
            inputs = self._cell_input_drivers(cur)
            found_next = False
            for _port, pred, _wire in inputs:
                pred_nd = self.ng.G.nodes.get(pred, {})
                if (
                    pred != root
                    and pred_nd.get("ntype") == "cell"
                    and pred_nd.get("gate_type") == gate_type
                    and self._load_count(pred) == 1
                    and not pred_nd.get("is_po")
                ):
                    cur = pred
                    found_next = True
                    break
            if not found_next:
                break
        return depth

    def _make_not(self, src: str) -> str:
        """Create a NOT gate driven by *src* and return the NOT cell name."""
        cell = self._fresh_name("bal_inv")
        wire = self._fresh_wire(f"{cell}_y")
        self._add_cell(cell, "not", wire)
        self._add_edge(src, cell, self.ng.output_wire(src), "A")
        return cell

    def _make_gate(
        self, gate_prim: str, left: str, right: str, prefix: str
    ) -> str:
        """Create a 2-input gate and return the cell name."""
        cell = self._fresh_name(prefix)
        wire = self._fresh_wire(f"{cell}_y")
        self._add_cell(cell, gate_prim, wire)
        self._add_edge(left, cell, self.ng.output_wire(left), "A")
        self._add_edge(right, cell, self.ng.output_wire(right), "B")
        return cell

    def _build_balanced_and(self, signals: list[str]) -> str:
        """Build a balanced AND tree over *signals*; return root node."""
        if len(signals) == 1:
            return signals[0]
        level = list(signals)
        while len(level) > 1:
            nxt: list[str] = []
            for i in range(0, len(level), 2):
                if i + 1 >= len(level):
                    nxt.append(level[i])
                else:
                    nxt.append(self._make_gate("and", level[i], level[i + 1], "bal_and"))
            level = nxt
        return level[0]

    def _build_balanced_or(self, signals: list[str]) -> str:
        """Build a balanced OR tree over *signals*; return root node."""
        if len(signals) == 1:
            return signals[0]
        level = list(signals)
        while len(level) > 1:
            nxt: list[str] = []
            for i in range(0, len(level), 2):
                if i + 1 >= len(level):
                    nxt.append(level[i])
                else:
                    nxt.append(self._make_gate("or", level[i], level[i + 1], "bal_or"))
            level = nxt
        return level[0]

    def _rebuild_nand_chain_balanced(
        self, root: str, leaves: list[str]
    ) -> None:
        """Rebuild a NAND chain as a balanced AND-OR-NOT tree.

        Chain function (k = len(leaves)-1 NAND gates, leaves [l0..lk]):
            out = NOT(l0) + l1*NOT(l2) + l1*l3*NOT(l4) + ... + last_term
        where last_term uses AND for k even, NOT for k odd.
        """
        k = len(leaves) - 1  # number of NAND gates

        # Collect and remove old internal cells
        old_internals = self._collect_chain_internals(root, "$nand")
        self._clear_cell_inputs(root)
        for cell in old_internals:
            if cell in self.ng.G and cell != root:
                self._remove_cell(cell)

        # NOT cache to avoid duplicate inverters
        not_cache: dict[str, str] = {}
        def get_not(leaf: str) -> str:
            if leaf not in not_cache:
                not_cache[leaf] = self._make_not(leaf)
            return not_cache[leaf]

        # --- Generate SOP terms ---
        terms: list[str] = []
        # Term 0: NOT(l0)
        terms.append(get_not(leaves[0]))

        # Regular terms: for i = 1 .. num_regular
        #   prefix_prod = l1 * l3 * ... * l_{2i-1}
        #   term_i = prefix_prod * NOT(l_{2i})
        num_regular = (k - 1) // 2
        prefix_prod: Optional[str] = None
        for i in range(1, num_regular + 1):
            odd_idx = 2 * i - 1
            leaf_odd = leaves[odd_idx]
            if prefix_prod is None:
                prefix_prod = leaf_odd
            else:
                prefix_prod = self._make_gate(
                    "and", prefix_prod, leaf_odd, "bal_pp"
                )
            even_idx = 2 * i
            term = self._make_gate(
                "and", prefix_prod, get_not(leaves[even_idx]), "bal_tm"
            )
            terms.append(term)

        # Last term
        if k >= 2:
            if k % 2 == 0:
                # k even: need prefix up to l_{k-1}, then AND with l_k
                last_odd_idx = k - 1
                leaf_odd = leaves[last_odd_idx]
                if prefix_prod is None:
                    prefix_prod = leaf_odd
                else:
                    prefix_prod = self._make_gate(
                        "and", prefix_prod, leaf_odd, "bal_pp"
                    )
                term = self._make_gate(
                    "and", prefix_prod, leaves[k], "bal_tm"
                )
                terms.append(term)
            else:
                # k odd: prefix already includes up to l_{k-2}
                # just AND with NOT(l_k)
                if prefix_prod is None:
                    # k=1: last term is just NOT(l_1)
                    term = get_not(leaves[k])
                else:
                    term = self._make_gate(
                        "and", prefix_prod, get_not(leaves[k]), "bal_tm"
                    )
                terms.append(term)

        # Build balanced OR tree over all terms
        or_root = self._build_balanced_or(terms)

        # Replace root cell output with the OR tree output
        self._replace_cell_output_with_driver(root, or_root)

    def _rebuild_nor_chain_balanced(
        self, root: str, leaves: list[str]
    ) -> None:
        """Rebuild a NOR chain as a balanced OR-AND-NOT tree (dual of NAND).

        NOR chain expands to a POS (product-of-sums):
            out = NOT(l0) * (l1+NOT(l2)) * (l1+l3+NOT(l4)) * ... * last_factor
        Dual of NAND: swap AND<->OR.
        """
        k = len(leaves) - 1

        old_internals = self._collect_chain_internals(root, "$nor")
        self._clear_cell_inputs(root)
        for cell in old_internals:
            if cell in self.ng.G and cell != root:
                self._remove_cell(cell)

        not_cache: dict[str, str] = {}
        def get_not(leaf: str) -> str:
            if leaf not in not_cache:
                not_cache[leaf] = self._make_not(leaf)
            return not_cache[leaf]

        # --- Generate POS factors (dual of NAND SOP) ---
        factors: list[str] = []
        # Factor 0: NOT(l0)
        factors.append(get_not(leaves[0]))

        num_regular = (k - 1) // 2
        prefix_sum: Optional[str] = None
        for i in range(1, num_regular + 1):
            odd_idx = 2 * i - 1
            leaf_odd = leaves[odd_idx]
            if prefix_sum is None:
                prefix_sum = leaf_odd
            else:
                prefix_sum = self._make_gate(
                    "or", prefix_sum, leaf_odd, "bal_ps"
                )
            even_idx = 2 * i
            factor = self._make_gate(
                "or", prefix_sum, get_not(leaves[even_idx]), "bal_tm"
            )
            factors.append(factor)

        # Last factor
        if k >= 2:
            if k % 2 == 0:
                last_odd_idx = k - 1
                leaf_odd = leaves[last_odd_idx]
                if prefix_sum is None:
                    prefix_sum = leaf_odd
                else:
                    prefix_sum = self._make_gate(
                        "or", prefix_sum, leaf_odd, "bal_ps"
                    )
                factor = self._make_gate(
                    "or", prefix_sum, leaves[k], "bal_tm"
                )
                factors.append(factor)
            else:
                if prefix_sum is None:
                    factor = get_not(leaves[k])
                else:
                    factor = self._make_gate(
                        "or", prefix_sum, get_not(leaves[k]), "bal_tm"
                    )
                factors.append(factor)

        # Build balanced AND tree over all POS factors
        and_root = self._build_balanced_and(factors)

        # Replace root cell output with the AND tree output
        self._replace_cell_output_with_driver(root, and_root)

    def _collect_chain_internals(self, root: str, gate_type: str) -> set[str]:
        """Collect all internal nodes of a chain."""
        internals: set[str] = set()
        cur = root
        visited: set[str] = set()
        while cur in self.ng.G and cur not in visited:
            nd = self.ng.G.nodes.get(cur, {})
            if nd.get("ntype") != "cell" or nd.get("gate_type") != gate_type:
                break
            visited.add(cur)
            internals.add(cur)
            inputs = self._cell_input_drivers(cur)
            found_next = False
            for _port, pred, _wire in inputs:
                pred_nd = self.ng.G.nodes.get(pred, {})
                if (
                    pred != root
                    and pred_nd.get("ntype") == "cell"
                    and pred_nd.get("gate_type") == gate_type
                    and self._load_count(pred) == 1
                    and not pred_nd.get("is_po")
                ):
                    cur = pred
                    found_next = True
                    break
            if not found_next:
                break
        return internals

    def replace_xor_with_nand(self, cone_output: Optional[str] = None) -> int:
        """Replace each 2-input XOR with the standard 4-NAND implementation."""
        scope = self.ng.extract_cone(cone_output) if cone_output else None
        # Reuse helpers created by this pass, but do not absorb arbitrary
        # pre-existing NAND cells into the fixed XOR template.  Keeping every
        # helper under the deterministic ``xor_nand`` namespace makes the
        # equivalence template recognizable after a serializer round-trip and
        # avoids accidental Merkle matches in unrelated NAND logic.
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
            # Keep each template self-contained.  Cross-template helper reuse
            # can create a valid but structurally different reconvergent AIG,
            # defeating the deterministic boundary Merkle proof.
            nand_index: dict = {}
            # Reuse only within this one fixed four-NAND template.
            t1, w1, reused1 = self._reuse_or_create_cell("nand", [a, b], "xor_nand", nand_index)
            t2, w2, reused2 = self._reuse_or_create_cell("nand", [a, t1], "xor_nand", nand_index)
            t3, w3, reused3 = self._reuse_or_create_cell("nand", [b, t1], "xor_nand", nand_index)
            nd["gate_type"] = "$nand"
            if not reused1:
                self._add_edge(a, t1, a_wire, "A")
                self._add_edge(b, t1, b_wire, "B")
            if not reused2:
                self._add_edge(a, t2, a_wire, "A")
                self._add_edge(t1, t2, w1, "B")
            if not reused3:
                self._add_edge(b, t3, b_wire, "A")
                self._add_edge(t1, t3, w1, "B")
            self._add_edge(t2, nid, w2, "A")
            self._add_edge(t3, nid, w3, "B")
            converted += 1
        return converted

    def lower_truth_lut_shr_to_and_not(self) -> int:
        """Lower Yosys ``constant >> {b,a}`` 2-input LUTs to AND/NOT.

        ABC's Verilog backend emits mapped truth-table gates as shift
        expressions.  On re-import Yosys represents them as ``$shr`` cells;
        they are not general shifters.  This pass recognizes a four-bit
        constant A port plus two one-bit B selectors and materializes the
        corresponding Boolean function using contest primitives.
        """
        lowered = 0

        def make_and(left: str, right: str, prefix: str) -> tuple[str, str]:
            cell = self._fresh_name(prefix)
            wire = self._fresh_wire(f"{prefix}_y")
            self._add_cell(cell, "and", wire)
            self._add_edge(left, cell, self.ng.output_wire(left), "A")
            self._add_edge(right, cell, self.ng.output_wire(right), "B")
            return cell, wire

        for nid, nd in list(self.ng.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") != "$shr":
                continue
            ports = list(nd.get("input_ports") or [])
            a_bits = [wire for port, wire in ports if str(port).upper().lstrip("\\") == "A"]
            b_wires = [wire for port, wire in ports if str(port).upper().lstrip("\\") == "B"]
            if (len(a_bits), len(b_wires)) not in {(2, 1), (4, 2)}:
                continue
            if any(str(bit) not in {"1'b0", "1'b1"} for bit in a_bits):
                continue
            drivers = [self.ng.wire_driver.get(wire) for wire in b_wires]
            if any(driver is None for driver in drivers):
                continue
            x = str(drivers[0])
            y = str(drivers[1]) if len(drivers) == 2 else ""
            table = sum(
                (1 if str(bit) == "1'b1" else 0) << index
                for index, bit in enumerate(a_bits)
            )
            self._clear_cell_inputs(nid)

            def inv(driver: str) -> tuple[str, str]:
                return self._not_of_driver(driver, "lut_inv")[:2]

            def set_and(left: str, right: str) -> None:
                nd["gate_type"] = "$and"
                self._add_edge(left, nid, self.ng.output_wire(left), "A")
                self._add_edge(right, nid, self.ng.output_wire(right), "B")

            def set_not(driver: str) -> None:
                nd["gate_type"] = "$not"
                self._add_edge(driver, nid, self.ng.output_wire(driver), "A")

            def set_identity(driver: str) -> None:
                # Keep the LUT node as the output driver.  Removing it and
                # reconnecting its loads is unsafe for a downstream vector
                # port such as $shr.B, where two bits deliberately share the
                # same port name.  A double inversion is a primitive, exact
                # identity and preserves every existing load verbatim.
                inverted, _ = inv(driver)
                set_not(inverted)

            def set_constant(value: int) -> None:
                if value:
                    set_not(CONST_0)
                else:
                    set_and(CONST_0, CONST_0)

            if len(drivers) == 1:
                if table == 0:
                    set_constant(0)
                elif table == 1:
                    set_not(x)
                elif table == 2:
                    set_identity(x)
                elif table == 3:
                    set_constant(1)
                else:
                    continue
            elif table == 0:
                set_constant(0)
            elif table == 15:
                set_constant(1)
            elif table == 1:  # ~x & ~y
                nx, _ = inv(x); ny, _ = inv(y); set_and(nx, ny)
            elif table == 2:  # x & ~y
                ny, _ = inv(y); set_and(x, ny)
            elif table == 3:  # ~y
                set_not(y)
            elif table == 4:  # ~x & y
                nx, _ = inv(x); set_and(nx, y)
            elif table == 5:  # ~x
                set_not(x)
            elif table == 7:  # ~(x & y)
                mid, _ = make_and(x, y, "lut_and"); set_not(mid)
            elif table == 8:  # x & y
                set_and(x, y)
            elif table == 10:  # x
                set_identity(x)
            elif table == 11:  # x | ~y == ~(~x & y)
                nx, _ = inv(x)
                mid, _ = make_and(nx, y, "lut_and")
                set_not(mid)
            elif table == 12:  # y
                set_identity(y)
            elif table == 13:  # ~x | y == ~(x & ~y)
                ny, _ = inv(y)
                mid, _ = make_and(x, ny, "lut_and")
                set_not(mid)
            elif table == 14:  # x | y == ~(~x & ~y)
                nx, _ = inv(x); ny, _ = inv(y)
                mid, _ = make_and(nx, ny, "lut_and")
                set_not(mid)
            elif table in {6, 9}:  # XOR / XNOR
                both, _ = make_and(x, y, "lut_xy")
                nboth, _ = inv(both)
                left, _ = make_and(x, nboth, "lut_x")
                right, _ = make_and(y, nboth, "lut_y")
                nleft, _ = inv(left); nright, _ = inv(right)
                if table == 9:
                    set_and(nleft, nright)
                else:
                    xnor, _ = make_and(nleft, nright, "lut_xnor")
                    set_not(xnor)
            else:
                continue
            lowered += 1
        return lowered

    def remap_dual_phase_and_not(self) -> int:
        """Rebuild all combinational logic as a polarity-aware AND/NOT AIG.

        Per-gate template replacement always materializes the positive phase
        of every intermediate signal.  A following NOR/NAND/OR stage then
        immediately inverts it again, which can add one level per original
        gate.  This routine constructs the phase actually requested by each
        consumer.  In particular, the complement of OR/NAND is an AND node
        directly, so De Morgan bubbles travel across multiple levels without
        becoming physical NOT gates.

        Primary inputs and DFF-Q nodes are combinational sources.  Only PO and
        DFF-D data boundaries are rewired; clocks and asynchronous controls
        remain connected exactly as they were.
        """
        supported = {
            "$and", "$or", "$not", "$buf", "$nand", "$nor",
            "$xor", "$xnor",
        }
        original_nodes = set(self.ng.G.nodes)
        original_cells = {
            nid
            for nid, nd in self.ng.G.nodes(data=True)
            if nd.get("ntype") == "cell" and nd.get("gate_type") not in DFF_TYPES
        }
        if any(
            self.ng.G.nodes[nid].get("gate_type") not in supported
            for nid in original_cells
        ):
            return 0

        # Snapshot the original fanin relation before adding the new AIG.
        fanins: dict[str, list[str]] = {}
        for nid in original_cells:
            fanins[nid] = [
                driver for _port, driver, _wire in self._cell_input_drivers(nid)
            ]
        for nid in original_cells:
            gate = self.ng.G.nodes[nid].get("gate_type")
            expected = 1 if gate in {"$not", "$buf"} else 2
            if len(fanins[nid]) != expected:
                return 0

        phase_cache: dict[tuple[str, bool], str] = {}
        not_cache: dict[str, str] = {}
        and_cache: dict[tuple[str, str], str] = {}

        def make_not(driver: str) -> str:
            if driver == CONST_0:
                return CONST_1
            if driver == CONST_1:
                return CONST_0
            if driver in not_cache:
                return not_cache[driver]
            cell = self._fresh_name("phase_not")
            wire = self._fresh_wire(f"{cell}_y")
            self._add_cell(cell, "not", wire)
            self._add_edge(driver, cell, self.ng.output_wire(driver), "A")
            not_cache[driver] = cell
            return cell

        def make_and(left: str, right: str) -> str:
            if left == CONST_0 or right == CONST_0:
                return CONST_0
            if left == CONST_1:
                return right
            if right == CONST_1:
                return left
            if left == right:
                return left
            key = tuple(sorted((left, right)))
            if key in and_cache:
                return and_cache[key]
            cell = self._fresh_name("phase_and")
            wire = self._fresh_wire(f"{cell}_y")
            self._add_cell(cell, "and", wire)
            self._add_edge(left, cell, self.ng.output_wire(left), "A")
            self._add_edge(right, cell, self.ng.output_wire(right), "B")
            and_cache[key] = cell
            return cell

        building: set[tuple[str, bool]] = set()

        def build(node: str, inverted: bool = False) -> str:
            key = (node, bool(inverted))
            if key in phase_cache:
                return phase_cache[key]
            if key in building:
                raise ValueError("combinational cycle while rebuilding AIG")
            nd = self.ng.G.nodes.get(node, {})
            gate = nd.get("gate_type")
            if nd.get("ntype") != "cell" or gate in DFF_TYPES:
                result = make_not(node) if inverted else node
                phase_cache[key] = result
                return result

            building.add(key)
            inputs = fanins[node]
            if gate == "$buf":
                result = build(inputs[0], inverted)
            elif gate == "$not":
                result = build(inputs[0], not inverted)
            elif gate == "$and":
                negative = make_and(build(inputs[0]), build(inputs[1]))
                result = make_not(negative) if inverted else negative
            elif gate == "$or":
                negative = make_and(build(inputs[0], True), build(inputs[1], True))
                result = negative if inverted else make_not(negative)
            elif gate == "$nand":
                negative = make_and(build(inputs[0]), build(inputs[1]))
                result = negative if inverted else make_not(negative)
            elif gate == "$nor":
                positive = make_and(build(inputs[0], True), build(inputs[1], True))
                result = make_not(positive) if inverted else positive
            elif gate in {"$xor", "$xnor"}:
                # XNOR = ~(a & ~b) & ~(~a & b).  Its complement is
                # materialized with one final NOT only when XOR is requested.
                a, b = inputs
                different_ab = make_and(build(a), build(b, True))
                different_ba = make_and(build(a, True), build(b))
                xnor = make_and(make_not(different_ab), make_not(different_ba))
                want_xor = (gate == "$xor") ^ bool(inverted)
                result = make_not(xnor) if want_xor else xnor
            else:  # guarded by the supported check above
                raise ValueError(f"unsupported gate {gate}")
            building.remove(key)
            phase_cache[key] = result
            return result

        po_updates: dict[str, str] = {}
        d_updates: list[tuple[str, str, str, str]] = []
        try:
            for po_name, old_driver in list(self.ng.primary_outputs.items()):
                po_updates[po_name] = build(old_driver)
            for dff, nd in list(self.ng.G.nodes(data=True)):
                if nd.get("ntype") != "cell" or nd.get("gate_type") not in DFF_TYPES:
                    continue
                for port, wire in list(nd.get("input_ports") or []):
                    if str(port).upper().lstrip("\\") != "D":
                        continue
                    old_driver = self.ng.wire_driver.get(wire)
                    if old_driver is None:
                        continue
                    new_driver = build(old_driver)
                    d_updates.append((dff, str(port), wire, new_driver))
        except (KeyError, ValueError):
            # The caller performs this pass on a transaction copy, so a zero
            # result means the candidate must simply be discarded.
            return 0

        for po_name, new_driver in po_updates.items():
            self.ng.primary_outputs[po_name] = new_driver

        for dff, port, old_wire, new_driver in d_updates:
            new_wire = self.ng.output_wire(new_driver)
            old_driver = self.ng.wire_driver.get(old_wire)
            self._add_edge(new_driver, dff, new_wire, port)
            remaining_ports = list(self.ng.G.nodes[dff].get("input_ports") or [])
            old_still_used = any(
                wire == old_wire and str(existing_port) != port
                for existing_port, wire in remaining_ports
            )
            if (
                old_driver is not None
                and old_driver != new_driver
                and not old_still_used
            ):
                if self.ng.G.has_edge(old_driver, dff):
                    self.ng.G.remove_edge(old_driver, dff)
                readers = self.ng.wire_readers.get(old_wire, [])
                while dff in readers:
                    readers.remove(dff)

        self._refresh_primary_output_flags()
        self.remove_dangling()
        self._refresh_primary_output_flags()
        remaining_original = sum(
            1 for nid in original_cells if nid in self.ng.G
        )
        # All original combinational cells should be dead once every PO/DFF-D
        # boundary has been redirected.  A nonzero remainder indicates an
        # unexpected boundary encoding and invalidates the candidate.
        if remaining_original:
            return 0
        return sum(
            1
            for nid in self.ng.G
            if nid not in original_nodes
            and self.ng.G.nodes[nid].get("ntype") == "cell"
        )

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
            # R43: a fanout-tree BUF converted to NOT-NOT must keep its
            # identity marking, or any later cleanup folds the pair back
            # and breaks a registered fanout bound.
            protect_identity = str(nd.get("origin_id", "")).startswith(
                ("synthetic:fo_", "synthetic:ded_", "synthetic:bal_")
            )
            self._clear_cell_inputs(nid)
            # Reuse existing NOT(a) if available
            na, wna, _ = self._not_of_driver(a, "buf")
            nd["gate_type"] = "$not"
            self._add_edge(na, nid, wna, "A")
            if protect_identity:
                nd["origin_id"] = f"{FANOUT_IDENTITY_ORIGIN_PREFIX}{nid}"
                partner = self.ng.G.nodes.get(na)
                if partner is not None and not is_fanout_identity_node(partner):
                    partner["origin_id"] = f"{FANOUT_IDENTITY_ORIGIN_PREFIX}{na}"
            converted += 1
        return converted

    def replace_xnor_with_nor(self, cone_output: Optional[str] = None) -> int:
        """Replace 2-input XNOR cells with a four-NOR implementation."""
        scope = None
        if cone_output:
            scope = self.ng.extract_cone(cone_output)
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

            # Keep each fixed template self-contained for deterministic
            # structural proof, while still permitting reuse inside the one
            # four-NOR diamond.
            nor_index: dict = {}
            p, wp, reused_p = self._reuse_or_create_cell("nor", [a, b], "xnor_nor", nor_index)
            q, wq, reused_q = self._reuse_or_create_cell("nor", [a, p], "xnor_nor", nor_index)
            r, wr, reused_r = self._reuse_or_create_cell("nor", [b, p], "xnor_nor", nor_index)
            nd["gate_type"] = "$nor"
            if not reused_p:
                self._add_edge(a, p, a_wire, "A")
                self._add_edge(b, p, b_wire, "B")
            if not reused_q:
                self._add_edge(a, q, a_wire, "A")
                self._add_edge(p, q, wp, "B")
            if not reused_r:
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
            n1, w1, reused = self._reuse_or_create_cell("nand", [a, b], "and_nand", nand_index)
            nd["gate_type"] = "$not"
            if not reused:
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
            t1, w1, reused1 = self._reuse_or_create_cell("nand", [a, b], "xnor_nand", nand_index)
            t2, w2, reused2 = self._reuse_or_create_cell("nand", [a, t1], "xnor_nand", nand_index)
            t3, w3, reused3 = self._reuse_or_create_cell("nand", [b, t1], "xnor_nand", nand_index)
            tx, wx, reused_x = self._reuse_or_create_cell("nand", [t2, t3], "xnor_nand", nand_index)
            nd["gate_type"] = "$nand"
            if not reused1:
                self._add_edge(a, t1, a_wire, "A")
                self._add_edge(b, t1, b_wire, "B")
            if not reused2:
                self._add_edge(a, t2, a_wire, "A")
                self._add_edge(t1, t2, w1, "B")
            if not reused3:
                self._add_edge(b, t3, b_wire, "A")
                self._add_edge(t1, t3, w1, "B")
            if not reused_x:
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
            p, wp, reused_p = self._reuse_or_create_cell("nor", [a, b], "xor_nor", nor_index)
            q, wq, reused_q = self._reuse_or_create_cell("nor", [a, p], "xor_nor", nor_index)
            r, wr, reused_r = self._reuse_or_create_cell("nor", [b, p], "xor_nor", nor_index)
            xnor_cell, wx, reused_x = self._reuse_or_create_cell("nor", [q, r], "xor_nor", nor_index)
            nd["gate_type"] = "$nor"
            if not reused_p:
                self._add_edge(a, p, a_wire, "A")
                self._add_edge(b, p, b_wire, "B")
            if not reused_q:
                self._add_edge(a, q, a_wire, "A")
                self._add_edge(p, q, wp, "B")
            if not reused_r:
                self._add_edge(b, r, b_wire, "A")
                self._add_edge(p, r, wp, "B")
            if not reused_x:
                self._add_edge(q, xnor_cell, wq, "A")
                self._add_edge(r, xnor_cell, wr, "B")
            self._add_edge(xnor_cell, nid, wx, "A")
            self._add_edge(xnor_cell, nid, wx, "B")
            converted += 1
        return converted
