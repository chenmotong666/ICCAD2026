"""
eda/netlist_graph.py
====================
Cell-only directed graph representation of a gate-level netlist.

Graph model
-----------
Every node is a "driver" 鈥?something that drives exactly one output wire.
There are three node types:

    "pi"    鈥?primary input port bit      node_id: "PI:a",  "PI:data[3]"
    "const" 鈥?constant 0 or 1             node_id: "CONST_0", "CONST_1"
    "cell"  鈥?gate or flip-flop instance  node_id: the instance name, e.g. "U42"

Directed edges go driver 鈫?reader (cell 鈫?cell, pi 鈫?cell).
Each edge carries a 'wire' attribute: the net name on that connection.

Node attributes
---------------
    ntype       : "cell" | "pi" | "const"
    gate_type   : Yosys internal type, e.g. "$and"  (cells only)
    output_wire : name of the single wire this node drives
    is_po       : True when this node's output wire is a primary output port

O(1) lookup caches (kept consistent after every mutation)
    wire_driver  : wire_name 鈫?node_id   (who drives this wire)
    wire_readers : wire_name 鈫?[node_ids] (who reads this wire)

Why cell-only (no wire nodes)?
-------------------------------
The contest gate set has exactly one output per primitive. That means:
    wire name  ==  cell's output
so a dedicated wire node carries zero extra information.
Removing wire nodes halves node count, simplifies every algorithm, and
makes out_degree(node) == fanout(node's output wire) a trivially true identity.

Yosys JSON bit conventions
---------------------------
    0        鈫?constant logic-0
    1        鈫?constant logic-1
    2+       鈫?real signal wire (Yosys-assigned integer)
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import networkx as nx

# 鈹€鈹€ type/name mappings 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

YOSYS_TO_PRIM: dict[str, str] = {
    "$and":  "and",  "$or":   "or",  "$nand": "nand", "$nor":  "nor",
    "$xor":  "xor",  "$xnor": "xnor","$not":  "not",  "$buf":  "buf",
    "$dff":  "dff",  "$adff": "dff", "$sdff": "dff",  "$dffe": "dff",
    "dff":   "dff",
}
PRIM_TO_YOSYS: dict[str, str] = {
    "and":"$and","or":"$or","nand":"$nand","nor":"$nor",
    "xor":"$xor","xnor":"$xnor","not":"$not","buf":"$buf","dff":"$dff",
}
DFF_TYPES: frozenset[str] = frozenset({"$dff","$adff","$sdff","$dffe","dff"})

CONST_0 = "CONST_0"
CONST_1 = "CONST_1"
CONST_X = "CONST_X"
CONST_Z = "CONST_Z"


def _bit_label(bit, bit_name: dict[int, str]) -> str:
    if bit in (0, "0"):
        return "1'b0"
    if bit in (1, "1"):
        return "1'b1"
    if bit in ("x", "X"):
        return "1'bx"
    if bit in ("z", "Z"):
        return "1'bz"
    return bit_name.get(bit, f"_w{bit}_")


def _output_bit_label(bit, bit_name: dict[int, str], fallback: str) -> str:
    if bit in ("x", "X", "z", "Z"):
        return fallback
    return _bit_label(bit, bit_name)


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲
class NetlistGraph:
    """
    Cell-only directed graph for gate-level netlist analysis and mutation.
    Constructed from a Yosys JSON dump; mutated in-place by NetlistTransformer.
    """

    def __init__(self) -> None:
        self.G: nx.DiGraph            = nx.DiGraph()
        self.module_name: str         = "top"
        self.primary_inputs:  dict[str, str] = {}   # port_name/bit_label 鈫?node_id
        self.primary_outputs: dict[str, str] = {}   # port_name            鈫?driving node_id
        self.wire_driver:  dict[str, str]        = {}   # wire_name 鈫?node_id
        self.wire_readers: dict[str, list[str]]  = {}   # wire_name 鈫?[node_ids]
        self.cell_aliases: dict[str, str] = {}
        self.port_widths: dict[str, int] = {}

    # 鈹€鈹€ construction from Yosys JSON 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    @classmethod
    def from_yosys_json(cls, json_path: str) -> "NetlistGraph":
        with open(json_path) as f:
            data = json.load(f)
        return cls._parse(data)

    @classmethod
    def _parse(cls, data: dict) -> "NetlistGraph":
        modules = data.get("modules", {})
        if not modules:
            raise ValueError("No modules found in Yosys JSON")
        if "top" in modules:
            mod_name, mod = "top", modules["top"]
        else:
            mod_name, mod = next(
                ((name, module) for name, module in modules.items()
                 if module.get("cells") or module.get("ports")),
                next(iter(modules.items())),
            )

        g = cls()
        g.module_name = mod_name

        # 鈹€鈹€ 1. Build bit_id 鈫?wire_name from netnames and ports 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        bit_name: dict[int, str] = {0: "1'b0", 1: "1'b1"}
        output_bits: list[tuple[str, str, object]] = []

        for port_name, pinfo in mod.get("ports", {}).items():
            bits = pinfo["bits"]
            g.port_widths[port_name] = max(len(bits), 1)
            for i, bit in enumerate(bits):
                label = port_name if len(bits) == 1 else f"{port_name}[{i}]"
                if pinfo["direction"] == "input":
                    bit_name[bit] = label
                elif pinfo["direction"] == "output":
                    output_bits.append((port_name, label, bit))
                    bit_name.setdefault(bit, label)

        for net_name, ninfo in mod.get("netnames", {}).items():
            bits = ninfo["bits"]
            hidden = bool(ninfo.get("hide_name"))
            for i, bit in enumerate(bits):
                label = net_name if len(bits) == 1 else f"{net_name}[{i}]"
                if not hidden and bit not in bit_name:
                    bit_name[bit] = label

        for net_name, ninfo in mod.get("netnames", {}).items():
            bits = ninfo["bits"]
            for i, bit in enumerate(bits):
                label = net_name if len(bits) == 1 else f"{net_name}[{i}]"
                bit_name.setdefault(bit, label)

        # 鈹€鈹€ 2. Constant nodes 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        g.G.add_node(CONST_0, ntype="const", output_wire="1'b0", is_po=False)
        g.G.add_node(CONST_1, ntype="const", output_wire="1'b1", is_po=False)
        g.G.add_node(CONST_X, ntype="const", output_wire="1'bx", is_po=False)
        g.G.add_node(CONST_Z, ntype="const", output_wire="1'bz", is_po=False)
        g.wire_driver["1'b0"] = CONST_0
        g.wire_driver["1'b1"] = CONST_1
        g.wire_driver["1'bx"] = CONST_X
        g.wire_driver["1'bz"] = CONST_Z

        # 鈹€鈹€ 3. Primary input nodes 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        for port_name, pinfo in mod.get("ports", {}).items():
            if pinfo["direction"] != "input":
                continue
            bits = pinfo["bits"]
            for i, bit in enumerate(bits):
                label = port_name if len(bits) == 1 else f"{port_name}[{i}]"
                nid   = f"PI:{label}"
                g.G.add_node(nid, ntype="pi", output_wire=label, is_po=False)
                g.wire_driver[label] = nid
                g.primary_inputs[label] = nid
            # Convenience alias: "data" 鈫?same as "data[0]" when unambiguous
            if len(bits) == 1:
                g.primary_inputs[port_name] = f"PI:{port_name}"
            else:
                g.primary_inputs[port_name] = f"PI:{port_name}[0]"

        # 鈹€鈹€ 4. Identify which wire names are primary outputs 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        po_wire_to_port: dict[str, str] = {}   # canonical wire_name -> output bit label
        for _port_name, label, bit in output_bits:
            po_wire_to_port[_bit_label(bit, bit_name)] = label

        # 鈹€鈹€ 5. Cell nodes 鈥?first pass: register output wires 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        # We must know all wire_driver entries before adding edges.
        cell_raw: list[tuple[str, dict]] = []
        for cell_name, cinfo in mod.get("cells", {}).items():
            ctype = cinfo["type"]
            conns = cinfo["connections"]
            out_port = "Y" if "Y" in conns else "Q"
            out_bits = conns.get(out_port, [])
            out_wire = (
                _output_bit_label(out_bits[0], bit_name, f"_unused_{cell_name}")
                if out_bits else f"_out_{cell_name}"
            )
            is_po = out_wire in po_wire_to_port
            g.G.add_node(cell_name, ntype="cell", gate_type=ctype,
                         output_wire=out_wire, is_po=is_po,
                         input_ports=[], input_wires=[])
            if not out_wire.startswith("1'b"):
                g.wire_driver[out_wire] = cell_name
            if is_po:
                g.primary_outputs[po_wire_to_port[out_wire]] = cell_name
            cell_raw.append((cell_name, cinfo))

        # 鈹€鈹€ 6. Cell nodes 鈥?second pass: add edges 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        for cell_name, cinfo in cell_raw:
            conns    = cinfo["connections"]
            out_port = "Y" if "Y" in conns else "Q"
            for port, bits in conns.items():
                if port == out_port:
                    continue
                for bit in bits:
                    in_wire    = _bit_label(bit, bit_name)
                    nd = g.G.nodes[cell_name]
                    nd.setdefault("input_ports", []).append((port, in_wire))
                    nd.setdefault("input_wires", []).append(in_wire)
                    driver_nid = g.wire_driver.get(in_wire)
                    if driver_nid is None:
                        continue
                    g.G.add_edge(driver_nid, cell_name, wire=in_wire, port=port)
                    g.wire_readers.setdefault(in_wire, []).append(cell_name)

        for _port_name, label, bit in output_bits:
            driver_wire = _bit_label(bit, bit_name)
            driver_nid = g.wire_driver.get(driver_wire)
            if driver_nid is None:
                driver_nid = CONST_X
            g.primary_outputs[label] = driver_nid
            g.wire_driver.setdefault(label, driver_nid)
            if driver_nid in g.G and g.G.nodes[driver_nid].get("ntype") == "cell":
                g.G.nodes[driver_nid]["is_po"] = True

        return g

    # 鈹€鈹€ name resolution 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def resolve(self, name: str) -> str:
        """
        Resolve any user-facing name to a graph node_id.
        Accepts: cell instance name ("U42"), wire/net name ("w1"),
                 port name ("a"), bus bit ("data[3]"), cell name directly.
        """
        name = str(name).strip().strip("\"'`")
        name = name.rstrip("?.。,;:")
        if name in self.G:
            return name
        if name in self.cell_aliases:
            return self.cell_aliases[name]
        if name in self.wire_driver:
            return self.wire_driver[name]
        pi_key = f"PI:{name}"
        if pi_key in self.G:
            return pi_key
        raise KeyError(f"Cannot resolve '{name}': not a node, wire, or port name.")

    def find_cells_by_pattern(self, pattern: str) -> list[str]:
        """Return node_ids of cells whose instance name contains pattern."""
        return [n for n, d in self.G.nodes(data=True)
                if d.get("ntype") == "cell" and pattern in n]

    def find_cells_by_type(self, prim: str) -> list[str]:
        """Return node_ids of cells matching a primitive type name (e.g. 'buf')."""
        ytype = PRIM_TO_YOSYS.get(prim, f"${prim}")
        return [n for n, d in self.G.nodes(data=True)
                if d.get("ntype") == "cell" and d.get("gate_type") == ytype]

    def node_label(self, nid: str) -> str:
        """Human-readable label for a node (used in path/depth reports)."""
        nd = self.G.nodes.get(nid, {})
        ntype = nd.get("ntype")
        if ntype == "pi":
            return nd.get("output_wire", nid)
        if ntype == "const":
            return nd.get("output_wire", nid)
        if ntype == "cell":
            prim = YOSYS_TO_PRIM.get(nd["gate_type"], nd["gate_type"].lstrip("$"))
            if nid.startswith("$"):
                short = nid.rsplit("$", 1)[-1]
            else:
                short = nid
            return f"[{prim.upper()}] {short} 鈫?{nd.get('output_wire','?')}"

    def output_wire(self, nid: str) -> str:
        return self.G.nodes[nid].get("output_wire", nid)

    # 鈹€鈹€ analysis: depth 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def get_max_depth(self, from_name: str,
                      to_name: str) -> tuple[int, list[str]]:
        """
        Longest combinational path (in gate counts) from from_name to to_name.
        DFF outputs act as sequential boundaries (pseudo-PIs).

        Algorithm: DP on topological order of a DAG that excludes DFF
        forward-edges (treating DFF as sinks when they are not the source).

        Returns (depth, path_labels); depth == -1 if no combinational path exists.
        """
        src = self.resolve(from_name)
        dst = self.resolve(to_name)

        def _is_dff(nid: str) -> bool:
            return self.G.nodes.get(nid, {}).get("gate_type", "") in DFF_TYPES

        # Build combinational DAG: skip outgoing edges from DFF nodes
        # (unless DFF is our explicit source)
        dag_edges = []
        for u, v in self.G.edges():
            if _is_dff(u) and u != src:
                continue
            dag_edges.append((u, v))
        DAG = nx.DiGraph(dag_edges)

        # Copy node attributes onto DAG nodes for ntype access
        for nid in DAG.nodes:
            DAG.nodes[nid].update(self.G.nodes.get(nid, {}))

        try:
            topo = list(nx.topological_sort(DAG))
        except nx.NetworkXUnfeasible:
            raise ValueError("Combinational cycle detected; check DFF handling.")

        dist: dict[str, int]           = {}
        pred: dict[str, Optional[str]] = {}

        for node in topo:
            if node == src:
                dist[node] = 0
                pred[node] = None
                continue
            best_d, best_p = -1, None
            for p in DAG.predecessors(node):
                if p not in dist:
                    continue
                inc = 1 if DAG.nodes[node].get("ntype") == "cell" else 0
                d   = dist[p] + inc
                if d > best_d:
                    best_d, best_p = d, p
            if best_d >= 0:
                dist[node] = best_d
                pred[node] = best_p

        if dst not in dist:
            return -1, []

        path, cur = [], dst
        while cur is not None:
            path.append(cur)
            cur = pred.get(cur)
        path.reverse()
        return dist[dst], [self.node_label(n) for n in path]

    # 鈹€鈹€ analysis: path queries 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def find_path(self, from_name: str, to_name: str,
                  avoid: Optional[str] = None,
                  must_pass: Optional[str] = None) -> Optional[list[str]]:
        """
        Find any path from from_name to to_name using BFS.
        avoid     : node/wire name to exclude from the search.
        must_pass : node/wire name the path must visit.
        Returns list of node labels, or None if no path exists.
        """
        src = self.resolve(from_name)
        dst = self.resolve(to_name)

        exclude: set[str] = set()
        if avoid:
            try:
                exclude.add(self.resolve(avoid))
            except KeyError:
                pass

        subG = self.G.subgraph([n for n in self.G if n not in exclude])

        try:
            if must_pass:
                mid  = self.resolve(must_pass)
                p1   = nx.shortest_path(subG, src, mid)
                p2   = nx.shortest_path(subG, mid, dst)
                path = p1 + p2[1:]
            else:
                path = nx.shortest_path(subG, src, dst)
            return [self.node_label(n) for n in path]
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def list_paths(self, from_name: str, to_name: str,
                   max_paths: int = 100,
                   max_seconds: float = 5.0,
                   max_expansions: int = 200_000) -> list[list[str]]:
        """Enumerate simple paths from from_name to to_name with hard safety caps."""
        src = self.resolve(from_name)
        dst = self.resolve(to_name)
        paths: list[list[str]] = []
        seen_path_keys: set[tuple[str, ...]] = set()
        try:
            first = nx.shortest_path(self.G, src, dst)
            paths.append([self.node_label(n) for n in first])
            seen_path_keys.add(tuple(first))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

        deadline = time.monotonic() + max_seconds
        expansions = 0
        stack: list[tuple[str, list[str], set[str]]] = [(src, [src], {src})]

        while stack and len(paths) < max_paths:
            if time.monotonic() >= deadline or expansions >= max_expansions:
                break
            node, path, seen = stack.pop()
            expansions += 1
            if node == dst:
                key = tuple(path)
                if key not in seen_path_keys:
                    paths.append([self.node_label(n) for n in path])
                    seen_path_keys.add(key)
                continue
            for succ in self.G.successors(node):
                if succ in seen:
                    continue
                stack.append((succ, path + [succ], seen | {succ}))
        return paths

    def immediate_successors(self, name: str) -> list[str]:
        """Return human-readable labels for direct successor cells."""
        nid = self.resolve(name)
        return [self.node_label(s) for s in self.G.successors(nid)]

    def all_paths_pass_through(self, from_name: str, to_name: str,
                                through: str) -> tuple[bool, Optional[list[str]]]:
        """
        Check whether every path from from_name to to_name passes through 'through'.
        Strategy: remove 'through' and check for a surviving path (counterexample).
        Returns (all_pass: bool, counterexample_labels | None).
        """
        src = self.resolve(from_name)
        dst = self.resolve(to_name)
        mid = self.resolve(through)

        subG = self.G.subgraph([n for n in self.G if n != mid])
        try:
            cex = nx.shortest_path(subG, src, dst)
            return False, [self.node_label(n) for n in cex]
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return True, None

    # 鈹€鈹€ analysis: cone 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def extract_cone(self, output_name: str) -> set[str]:
        """
        Backward BFS from the driver of output_name.
        Returns the set of combinational cell node_ids in the transitive fanin cone.
        Stops at PIs, constants, and DFF outputs (treating DFF as opaque sources).
        """
        start = self.resolve(output_name)
        cone: set[str]    = set()
        visited: set[str] = set()
        stack = [start]

        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            nd    = self.G.nodes.get(nid, {})
            ntype = nd.get("ntype", "")
            if ntype in ("pi", "const"):
                continue
            if ntype == "cell":
                cone.add(nid)
                if nd.get("gate_type", "") not in DFF_TYPES:
                    stack.extend(self.G.predecessors(nid))

        return cone

    def get_cone_size(self, output_name: str) -> int:
        return len(self.extract_cone(output_name))

    def transitive_fanin_cone(self, output_name: str) -> list[str]:
        """Return labels for cells in the transitive fanin cone."""
        cone = self.extract_cone(output_name)
        return [self.node_label(n) for n in sorted(cone)]

    def transitive_fanout_cone(self, input_name: str) -> list[str]:
        """Return labels for cells reachable from input_name."""
        return [self.node_label(n) for n in sorted(self.transitive_fanout_nodes(input_name))]

    def transitive_fanout_nodes(self, input_name: str) -> set[str]:
        """Return cell node ids reachable from input_name."""
        start = self.resolve(input_name)
        visited: set[str] = set()
        cone: set[str] = set()
        stack = list(self.G.successors(start))

        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            nd = self.G.nodes.get(nid, {})
            if nd.get("ntype") == "cell":
                cone.add(nid)
                if nd.get("gate_type", "") in DFF_TYPES:
                    continue
            stack.extend(self.G.successors(nid))

        return cone

    def get_fanout(self, name: str) -> int:
        """Fanout = out_degree of the node driving this name."""
        return self.G.out_degree(self.resolve(name))

    def report_outputs_cone_gt(self, threshold: int) -> list[tuple[str, int]]:
        """Return [(port_name, size)] for all POs with cone > threshold gates."""
        return [
            (name, self.get_cone_size(name))
            for name in self.primary_outputs
            if self.get_cone_size(name) > threshold
        ]

    # 鈹€鈹€ analysis: clock domain 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def same_clock_domain(self, ff1_name: str,
                          ff2_name: str) -> tuple[bool, str]:
        """
        Compare the CLK-port predecessors of two DFF cells.
        A shared predecessor node means the same clock domain.
        Returns (same: bool, explanation_string).
        """
        def clk_driver(nid: str) -> Optional[str]:
            for u, _, d in self.G.in_edges(nid, data=True):
                w = d.get("wire", "")
                if re.search(r"clk|clock|ck", w, re.I):
                    return u
            return None

        n1 = self.resolve(ff1_name)
        n2 = self.resolve(ff2_name)
        c1 = clk_driver(n1)
        c2 = clk_driver(n2)

        if c1 is None or c2 is None:
            return False, "Could not identify CLK input on one or both DFFs."
        if c1 == c2:
            w = self.output_wire(c1)
            return True, f"Both driven by clock '{w}'."
        return False, (f"{ff1_name} uses clock '{self.output_wire(c1)}', "
                       f"{ff2_name} uses clock '{self.output_wire(c2)}'.")

    # 鈹€鈹€ summary 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def summary(self) -> dict:
        cells = [(n, d) for n, d in self.G.nodes(data=True)
                 if d.get("ntype") == "cell"]
        hist: dict[str, int] = {}
        for _, d in cells:
            prim = YOSYS_TO_PRIM.get(d["gate_type"], d["gate_type"])
            hist[prim] = hist.get(prim, 0) + 1
        return {
            "module":              self.module_name,
            "primary_inputs":      sorted(
                k for k in self.primary_inputs
                if "[" not in k or k not in self.primary_inputs.get(k.split("[")[0], "")),
            "primary_outputs":     list(self.primary_outputs.keys()),
            "cell_count":          len(cells),
            "gate_type_histogram": dict(sorted(hist.items())),
        }

    def __repr__(self) -> str:
        s = self.summary()
        return (f"NetlistGraph(module={s['module']}, "
                f"cells={s['cell_count']}, "
                f"pi={len(s['primary_inputs'])}, "
                f"po={len(s['primary_outputs'])})")


