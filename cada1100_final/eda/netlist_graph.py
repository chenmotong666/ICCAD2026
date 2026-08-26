"""
eda/netlist_graph.py
====================
Cell-only directed graph representation of a gate-level netlist.

Graph model
-----------
Every node is a "driver" -something that drives exactly one output wire.
There are three node types:

    "pi"    -primary input port bit      node_id: "PI:a",  "PI:data[3]"
    "const" -constant 0 or 1             node_id: "CONST_0", "CONST_1"
    "cell"  -gate or flip-flop instance  node_id: the instance name, e.g. "U42"

Directed edges go driver ->reader (cell ->cell, pi ->cell).
Each edge carries a 'wire' attribute: the net name on that connection.

Node attributes
---------------
    ntype       : "cell" | "pi" | "const"
    gate_type   : Yosys internal type, e.g. "$and"  (cells only)
    output_wire : name of the single wire this node drives
    is_po       : True when this node's output wire is a primary output port

O(1) lookup caches (kept consistent after every mutation)
    wire_driver  : wire_name ->node_id   (who drives this wire)
    wire_readers : wire_name ->[node_ids] (who reads this wire)

Why cell-only (no wire nodes)?
-------------------------------
The contest gate set has exactly one output per primitive. That means:
    wire name  ==  cell's output
so a dedicated wire node carries zero extra information.
Removing wire nodes halves node count and simplifies traversal.  Pin-level
fanout is tracked separately because a single reader may consume two pins and
primary outputs are loads too.

Yosys JSON bit conventions
---------------------------
    0        ->constant logic-0
    1        ->constant logic-1
    2+       ->real signal wire (Yosys-assigned integer)
"""

from __future__ import annotations

import json
import fnmatch
import re
import time
from pathlib import Path
from typing import Optional

import networkx as nx


# Canonical constants live in eda.constants; re-exported for backward compat.
from .constants import (  # noqa: F401 - re-exported
    YOSYS_TO_PRIM, PRIM_TO_YOSYS, DFF_TYPES,
    CONST_0, CONST_1, CONST_X, CONST_Z,
    GATE_PRIMITIVES, GATE_PRIMITIVES_SET,
)


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


# DFF input ports that can never be the clock.  Guards the clk_name wire-name
# fallback in same_clock_domain so a DATA or RESET net that happens to be named
# "clk2"/"ck_div" is never misattributed as the clock source.
_DFF_NON_CLOCK_PORTS: frozenset[str] = frozenset({
    "D", "DATA", "DIN", "I0",
    "Q", "RST", "RN", "RSTN", "RST_N", "RESET", "ARST", "SRST",
    "SET", "SN", "SETN", "SET_N", "EN", "CE", "GND", "VCC",
})


def _output_bit_label(bit, bit_name: dict[int, str], fallback: str) -> str:
    if bit in ("x", "X", "z", "Z"):
        return fallback
    return _bit_label(bit, bit_name)


def _cell_output_port(conns: dict) -> str:
    for port in ("Y", "Q", "q", "\\Y", "\\Q", "\\q"):
        if port in conns:
            return port
    return "Y"


def _strip_verilog_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def _decl_range(width_s: str) -> Optional[tuple[int, int]]:
    if not width_s:
        return None
    m = re.search(r"\[(\d+)\s*:\s*(\d+)\]", width_s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _expanded_labels(
    name: str,
    width: int,
    declared_range: Optional[tuple[int, int]] = None,
) -> list[str]:
    if width <= 1:
        return [name]
    if declared_range is None:
        indices = range(width)
    else:
        left, right = declared_range
        step = 1 if right >= left else -1
        indices = range(left, right + step, step)
    return [f"{name}[{i}]" for i in indices]


def _json_bit_indices(info: dict, width: int) -> list[int]:
    """Return source-level indices for Yosys JSON bits (stored LSB first)."""
    offset = int(info.get("offset", 0) or 0)
    upto = bool(info.get("upto", 0))
    if upto:
        return [offset + width - 1 - i for i in range(width)]
    return [offset + i for i in range(width)]


def _json_decl_range(info: dict, width: int) -> Optional[tuple[int, int]]:
    if width <= 1:
        return None
    offset = int(info.get("offset", 0) or 0)
    if bool(info.get("upto", 0)):
        return offset, offset + width - 1
    return offset + width - 1, offset


def _split_verilog_list(text: str) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
        if ch == "," and depth == 0:
            part = "".join(cur).strip()
            if part:
                parts.append(part)
            cur = []
            continue
        cur.append(ch)
    part = "".join(cur).strip()
    if part:
        parts.append(part)
    return parts


def _parse_named_port_map(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for port, signal in re.findall(r"\.\s*([A-Za-z_\\][A-Za-z0-9_$\\]*)\s*\((.*?)\)", text, re.S):
        result[port.strip()] = signal.strip()
    return result


class NetlistGraph:
    """
    Cell-only directed graph for gate-level netlist analysis and mutation.
    Constructed from a Yosys JSON dump; mutated in-place by NetlistTransformer.
    """

    def __init__(self) -> None:
        self.G: nx.DiGraph            = nx.DiGraph()
        self.module_name: str         = "top"
        self.primary_inputs:  dict[str, str] = {}   # port_name/bit_label ->node_id
        self.primary_outputs: dict[str, str] = {}   # port_name            ->driving node_id
        self.wire_driver:  dict[str, str]        = {}   # wire_name ->node_id
        self.wire_readers: dict[str, list[str]]  = {}   # wire_name ->[node_ids]
        self.cell_aliases: dict[str, str] = {}
        # Names of wires removed by an equivalence-preserving rewrite are
        # retained here.  This lets later requests in the same testcase refer
        # to a signal that was folded or merged by an earlier request.
        self.signal_aliases: dict[str, str] = {}
        self.port_widths: dict[str, int] = {}
        self.signal_ranges: dict[str, tuple[int, int]] = {}
        # R20: combinational-view cache keyed by graph fingerprint.  Callers
        # receive a copy so they may mutate it (cut / all_paths_through).
        self._comb_cache: dict = {}
        self._comb_cache_fp: Optional[tuple] = None
        self._mut_epoch: int = 0

    def mark_mutated(self) -> None:
        """Invalidate combinational-view cache after an in-place rewrite."""
        self._mut_epoch = int(getattr(self, "_mut_epoch", 0)) + 1
        self._comb_cache.clear()
        self._comb_cache_fp = None


    @classmethod
    def from_yosys_json(cls, json_path: str) -> "NetlistGraph":
        with open(json_path) as f:
            data = json.load(f)
        return cls._parse(data)

    @classmethod
    def from_verilog(cls, verilog_path: str) -> "NetlistGraph":
        """Parse the contest's flat primitive-gate Verilog directly.

        Yosys canonicalizes ``nand/nor/xnor`` into positive gates followed by
        inverters.  That is fine for logic, but it changes gate counts and can
        break prompts that require a specific primitive set.  The contest input
        syntax is deliberately small, so preserving the source primitives here
        is both safer and smaller.  The caller can still fall back to Yosys for
        anything outside this subset.
        """
        text = Path(verilog_path).read_text(encoding="utf-8")
        text = _strip_verilog_comments(text)
        # R18: this parser understands only primitive-gate instantiations.
        # `assign` statements and declaration initializers (wire w = ...) would
        # be silently dropped, leaving wires undriven (CONST_X) with no error.
        # Refuse loudly so the caller falls back to the Yosys JSON path, which
        # resolves assigns and initializers correctly.
        if re.search(r"\bassign\b", text) or re.search(
            r"^\s*(?:input|output|wire|reg)\b[^;]*=[^=]", text, re.M
        ):
            raise ValueError(
                "assign statements / declaration initializers are not "
                "supported by the direct parser; use the Yosys JSON path"
            )
        # R37 D1: parameterized instances ("and #(1) g0 (...)"), generate
        # blocks, and `include content match neither the strict nor the
        # loose instance pattern, so they would be dropped silently.  Fail
        # loudly and let the Yosys JSON path handle them instead.
        if re.search(r"\b(?:and|or|nand|nor|xor|xnor|not|buf|dff)\s*#\s*\(", text):
            raise ValueError(
                "parameterized primitive instances are not supported by "
                "the direct parser; use the Yosys JSON path"
            )
        if re.search(r"\bgenerate\b", text):
            raise ValueError(
                "generate blocks are not supported by the direct parser; "
                "use the Yosys JSON path"
            )
        if "`include" in text:
            raise ValueError(
                "`include directives are not supported by the direct "
                "parser; use the Yosys JSON path"
            )
        if len(re.findall(r"\bmodule\s+", text)) > 1:
            raise ValueError(
                "multiple module declarations are not supported by the "
                "direct parser; use the Yosys JSON path"
            )
        m = re.search(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*;", text, re.S)
        if not m:
            raise ValueError("No top module declaration found")

        g = cls()
        g.module_name = m.group(1)

        decls: dict[str, dict[str, int]] = {"input": {}, "output": {}, "wire": {}}
        for kind, width_s, names_s in re.findall(
            r"\b(input|output|wire)\b\s*(?:wire\s+|reg\s+)?"
            r"(\[[^\]]+\])?\s*([^;]+);",
            text,
            re.S,
        ):
            declared_range = _decl_range(width_s or "")
            if (width_s or "").strip() and declared_range is None:
                raise ValueError(
                    f"parameterized width '{width_s.strip()}' is not "
                    "supported by the direct parser; use the Yosys JSON path"
                )
            width = (
                abs(declared_range[0] - declared_range[1]) + 1
                if declared_range is not None else 1
            )
            for raw_name in _split_verilog_list(names_s):
                name = raw_name.strip()
                if not name:
                    continue
                # Drop declaration modifiers that can appear after input/output.
                name = re.sub(r"^(?:wire|reg)\s+", "", name).strip()
                name = name.split("=")[0].strip()
                if not name:
                    continue
                decls[kind][name] = width
                g.port_widths.setdefault(name, width)
                if declared_range is not None:
                    prev_range = g.signal_ranges.get(name)
                    if prev_range is not None and prev_range != declared_range:
                        raise ValueError(
                            f"inconsistent range declarations for '{name}': "
                            f"{prev_range} vs {declared_range}; use the Yosys JSON path"
                        )
                    g.signal_ranges[name] = declared_range

        g.G.add_node(CONST_0, ntype="const", output_wire="1'b0", is_po=False)
        g.G.add_node(CONST_1, ntype="const", output_wire="1'b1", is_po=False)
        g.G.add_node(CONST_X, ntype="const", output_wire="1'bx", is_po=False)
        g.G.add_node(CONST_Z, ntype="const", output_wire="1'bz", is_po=False)
        g.wire_driver["1'b0"] = CONST_0
        g.wire_driver["1'b1"] = CONST_1
        g.wire_driver["1'bx"] = CONST_X
        g.wire_driver["1'bz"] = CONST_Z

        for port_name, width in decls["input"].items():
            labels = _expanded_labels(
                port_name, width, g.signal_ranges.get(port_name)
            )
            for label in labels:
                nid = f"PI:{label}"
                g.G.add_node(
                    nid, ntype="pi", output_wire=label, is_po=False,
                    origin_id=nid, origin_wire=label,
                )
                g.wire_driver[label] = nid
                g.primary_inputs[label] = nid
            if width == 1:
                g.primary_inputs[port_name] = f"PI:{port_name}"
            else:
                g.primary_inputs[port_name] = f"PI:{labels[-1]}"

        primitive_re = re.compile(
            r"\b(and|or|nand|nor|xor|xnor|not|buf|dff)\s+"
            r"((?:\\[^\s]+\s)|(?:[A-Za-z0-9_$][A-Za-z0-9_$]*))\s*\((.*?)\)\s*;",
            re.S,
        )
        cell_raw: list[tuple[str, str, list[tuple[str, str]], str]] = []
        for prim, inst, args_s in primitive_re.findall(text):
            prim = prim.lower()
            inst = inst.strip()
            if inst.startswith("\\"):
                inst = inst[1:]
            if prim == "dff":
                port_map = _parse_named_port_map(args_s)
                out_wire = (
                    port_map.get("Q")
                    or port_map.get("q")
                    or port_map.get("\\Q")
                    or port_map.get("\\q")
                )
                if not out_wire:
                    parts = _split_verilog_list(args_s)
                    if len(parts) == 4:
                        # Contest Q&A accepts positional clk,rst_n,d,q as well.
                        port_map = {"clk": parts[0], "rst_n": parts[1], "d": parts[2], "q": parts[3]}
                        out_wire = parts[3]
                    elif len(parts) >= 5:
                        # R15 (F-07): a 5+-pin positional DFF follows the
                        # contest RN,SN,CK,D,Q model; the output is the LAST
                        # pin.  The old len>=4 rule picked parts[3], which is
                        # the D pin on a 5-pin instance.
                        port_map = {
                            "rn": parts[0], "sn": parts[1], "ck": parts[2],
                            "d": parts[3], "q": parts[-1],
                        }
                        out_wire = parts[-1]
                if not out_wire:
                    raise ValueError(f"DFF '{inst}' has no output port")
                inputs = [
                    (port, wire)
                    for port, wire in port_map.items()
                    if port not in {"Q", "q", "\\Q", "\\q"}
                ]
            else:
                if "{" in args_s:
                    raise ValueError(
                        f"concatenation in primitive '{inst}' is not "
                        "supported by the direct parser; use the Yosys JSON path"
                    )
                parts = _split_verilog_list(args_s)
                if any(part.lstrip().startswith(".") for part in parts):
                    raise ValueError(
                        f"named ports on combinational primitive '{inst}' "
                        "are not supported by the direct parser; "
                        "use the Yosys JSON path"
                    )
                expected = 2 if prim in {"not", "buf"} else 3
                if len(parts) < expected:
                    raise ValueError(f"Primitive '{inst}' has too few pins")
                out_wire = parts[0]
                inputs = [(chr(ord("A") + i), wire) for i, wire in enumerate(parts[1:expected])]

            g.G.add_node(
                inst,
                ntype="cell",
                gate_type=PRIM_TO_YOSYS.get(prim, f"${prim}"),
                output_wire=out_wire,
                input_ports=list(inputs),
                input_wires=[wire for _port, wire in inputs],
                is_po=False,
                origin_id=inst,
                origin_wire=out_wire,
            )
            if not out_wire.startswith("1'b"):
                prev = g.wire_driver.get(out_wire)
                if prev is not None and prev != inst:
                    prev_nt = g.G.nodes.get(prev, {}).get("ntype")
                    if prev_nt == "cell":
                        raise ValueError(
                            f"multiple drivers for wire '{out_wire}' "
                            f"({prev} and {inst}); use the Yosys JSON path"
                        )
                g.wire_driver[out_wire] = inst
            g.cell_aliases[inst] = inst
            cell_raw.append((inst, prim, inputs, out_wire))

        # R15 (F-07): consistency guard.  The strict pattern above requires
        # a named instance; any candidate instantiation the loose pattern
        # counts but the strict one cannot parse (unnamed `and (y,a,b);`,
        # exotic instance syntax, ...) must fail loudly so the caller falls
        # back to the Yosys JSON path instead of silently dropping cells.
        # The loose pattern needs the primitive keyword to be followed by
        # an identifier or directly by '(' so words like "buffer" never
        # misfire.
        loose_re = re.compile(
            r"\b(and|or|nand|nor|xor|xnor|not|buf|dff)"
            r"(?:\s+(?:\\[^\s]+\s+|[A-Za-z0-9_$]+)\s*|\s*)\("
        )
        loose_count = len(loose_re.findall(text))
        if loose_count != len(cell_raw):
            raise ValueError(
                "instance syntax not fully supported by the direct parser "
                f"({len(cell_raw)} of {loose_count} instantiations parsed); "
                "use the Yosys JSON path"
            )
        if not cell_raw and any(k in text for k in (" and ", " or ", " dff ")):
            raise ValueError("No primitive instances parsed")

        for inst, _prim, inputs, _out_wire in cell_raw:
            for port, in_wire in inputs:
                if re.search(r"\[\d+\s*:\s*\d+\]", in_wire or "") and in_wire not in g.wire_driver:
                    raise ValueError(
                        f"part-select '{in_wire}' is not supported by the "
                        "direct parser; use the Yosys JSON path"
                    )
                driver_nid = g.wire_driver.get(in_wire)
                if driver_nid is None:
                    continue
                g.G.add_edge(driver_nid, inst, wire=in_wire, port=port)
                g.wire_readers.setdefault(in_wire, []).append(inst)

        for port_name, width in decls["output"].items():
            for label in _expanded_labels(
                port_name, width, g.signal_ranges.get(port_name)
            ):
                driver_nid = g.wire_driver.get(label, CONST_X)
                g.primary_outputs[label] = driver_nid
                g.wire_driver.setdefault(label, driver_nid)
                if driver_nid in g.G and g.G.nodes[driver_nid].get("ntype") == "cell":
                    g.G.nodes[driver_nid]["is_po"] = True

        return g

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

        bit_name: dict[int, str] = {0: "1'b0", 1: "1'b1"}
        output_bits: list[tuple[str, str, object]] = []

        for port_name, pinfo in mod.get("ports", {}).items():
            bits = pinfo["bits"]
            g.port_widths[port_name] = max(len(bits), 1)
            declared_range = _json_decl_range(pinfo, len(bits))
            if declared_range is not None:
                g.signal_ranges[port_name] = declared_range
            indices = _json_bit_indices(pinfo, len(bits))
            for i, bit in enumerate(bits):
                label = port_name if len(bits) == 1 else f"{port_name}[{indices[i]}]"
                if pinfo["direction"] == "input":
                    bit_name[bit] = label
                elif pinfo["direction"] == "output":
                    output_bits.append((port_name, label, bit))
                    bit_name.setdefault(bit, label)

        for net_name, ninfo in mod.get("netnames", {}).items():
            bits = ninfo["bits"]
            declared_range = _json_decl_range(ninfo, len(bits))
            if declared_range is not None:
                g.signal_ranges[net_name] = declared_range
            indices = _json_bit_indices(ninfo, len(bits))
            hidden = bool(ninfo.get("hide_name"))
            for i, bit in enumerate(bits):
                label = net_name if len(bits) == 1 else f"{net_name}[{indices[i]}]"
                if not hidden and bit not in bit_name:
                    bit_name[bit] = label

        for net_name, ninfo in mod.get("netnames", {}).items():
            bits = ninfo["bits"]
            indices = _json_bit_indices(ninfo, len(bits))
            for i, bit in enumerate(bits):
                label = net_name if len(bits) == 1 else f"{net_name}[{indices[i]}]"
                bit_name.setdefault(bit, label)

        g.G.add_node(CONST_0, ntype="const", output_wire="1'b0", is_po=False)
        g.G.add_node(CONST_1, ntype="const", output_wire="1'b1", is_po=False)
        g.G.add_node(CONST_X, ntype="const", output_wire="1'bx", is_po=False)
        g.G.add_node(CONST_Z, ntype="const", output_wire="1'bz", is_po=False)
        g.wire_driver["1'b0"] = CONST_0
        g.wire_driver["1'b1"] = CONST_1
        g.wire_driver["1'bx"] = CONST_X
        g.wire_driver["1'bz"] = CONST_Z

        for port_name, pinfo in mod.get("ports", {}).items():
            if pinfo["direction"] != "input":
                continue
            bits = pinfo["bits"]
            indices = _json_bit_indices(pinfo, len(bits))
            for i, bit in enumerate(bits):
                label = port_name if len(bits) == 1 else f"{port_name}[{indices[i]}]"
                nid   = f"PI:{label}"
                g.G.add_node(
                    nid, ntype="pi", output_wire=label, is_po=False,
                    origin_id=nid, origin_wire=label,
                )
                g.wire_driver[label] = nid
                g.primary_inputs[label] = nid
            # Convenience alias: "data" ->same as "data[0]" when unambiguous
            if len(bits) == 1:
                g.primary_inputs[port_name] = f"PI:{port_name}"
            else:
                first_index = _json_bit_indices(pinfo, len(bits))[0]
                g.primary_inputs[port_name] = f"PI:{port_name}[{first_index}]"

        po_wire_to_port: dict[str, str] = {}   # canonical wire_name -> output bit label
        for _port_name, label, bit in output_bits:
            po_wire_to_port[_bit_label(bit, bit_name)] = label

        # We must know all wire_driver entries before adding edges.
        cell_raw: list[tuple[str, dict]] = []
        for cell_name, cinfo in mod.get("cells", {}).items():
            ctype = cinfo["type"]
            conns = cinfo["connections"]
            out_port = _cell_output_port(conns)
            out_bits = conns.get(out_port, [])
            out_wire = (
                _output_bit_label(out_bits[0], bit_name, f"_unused_{cell_name}")
                if out_bits else f"_out_{cell_name}"
            )
            is_po = out_wire in po_wire_to_port
            g.G.add_node(
                cell_name, ntype="cell", gate_type=ctype,
                output_wire=out_wire, is_po=is_po,
                input_ports=[], input_wires=[],
                origin_id=cell_name, origin_wire=out_wire,
            )
            if not out_wire.startswith("1'b"):
                g.wire_driver[out_wire] = cell_name
            if is_po:
                g.primary_outputs[po_wire_to_port[out_wire]] = cell_name
            cell_raw.append((cell_name, cinfo))

        for cell_name, cinfo in cell_raw:
            conns    = cinfo["connections"]
            out_port = _cell_output_port(conns)
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


    def resolve(self, name: str) -> str:
        """
        Resolve any user-facing name to a graph node_id.
        Accepts: cell instance name ("U42"), wire/net name ("w1"),
                 port name ("a"), bus bit ("data[3]"), cell name directly.
        """
        name = str(name).strip().strip("\"'`")
        name = name.rstrip("?.,;:")
        if name in self.G:
            return name
        if name in self.cell_aliases:
            return self.cell_aliases[name]
        if name in self.wire_driver:
            return self.wire_driver[name]
        seen: set[str] = set()
        alias = name
        while alias in self.signal_aliases and alias not in seen:
            seen.add(alias)
            alias = self.signal_aliases[alias]
            if alias in self.wire_driver:
                return self.wire_driver[alias]
            if alias in self.G:
                return alias
        pi_key = f"PI:{name}"
        if pi_key in self.G:
            return pi_key
        # Port-name aliases: single-bit ports resolve via wire_driver already,
        # but a bare multi-bit port name ("data" for `input [7:0] data`) is
        # stored only as a primary_inputs alias, never as a node or wire.  A
        # primary_outputs key covers any output not reached through
        # wire_driver (e.g. a PO that is an undriven wire).
        if name in self.primary_inputs:
            return self.primary_inputs[name]
        if name in self.primary_outputs:
            return self.primary_outputs[name]
        # R43: the direct parser stores escaped net names verbatim while
        # user queries use the bare spelling (R37 D2 stripped instance-name
        # aliases only).  Exact-match retry over the stored spelling.
        actual = self._escaped_name_target(name)
        if actual is not None and actual != name:
            return self.resolve(actual)
        raise KeyError(f"Cannot resolve '{name}': not a node, wire, or port name.")

    def _escaped_name_target(self, name: str):
        """Map a bare query to a stored escaped spelling ("\\esc.name").

        Built lazily from existing wire/node/port keys, so it never invents
        a match; later transforms only add generated plain names.
        """
        index = getattr(self, "_escaped_name_index", None)
        if index is None:
            index = {}
            keys = (
                list(self.wire_driver)
                + list(self.G.nodes)
                + list(self.primary_inputs)
                + list(self.primary_outputs)
            )
            for key in keys:
                if isinstance(key, str) and key.startswith("\\"):
                    index.setdefault(key.lstrip("\\").strip(), key)
            self._escaped_name_index = index
        return index.get(name)

    def find_cells_by_pattern(self, pattern: str) -> list[str]:
        """Return cell ids matching a glob, or a literal substring."""
        has_glob = any(mark in pattern for mark in "*?[")
        return [
            n for n, d in self.G.nodes(data=True)
            if d.get("ntype") == "cell"
            and (fnmatch.fnmatchcase(n, pattern) if has_glob else pattern in n)
        ]

    def find_cells_by_type(self, prim: str) -> list[str]:
        """Return node_ids of cells matching a primitive type name (e.g. 'buf')."""
        ytype = PRIM_TO_YOSYS.get(prim, f"${prim}")
        if ytype in DFF_TYPES:
            # The DFF family ($dff/$adff/$sdff/$dffe) all answer to the
            # 'dff' primitive; exact-matching '$dff' would miss async-reset
            # / sync-enable flops on the Yosys-JSON path.
            return [n for n, d in self.G.nodes(data=True)
                    if d.get("ntype") == "cell" and d.get("gate_type") in DFF_TYPES]
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
            return f"[{prim.upper()}] {short} -> {nd.get('output_wire','?')}"
        # Unknown/other node types (e.g. 'po'): fall back to the raw id so
        # callers building report strings never receive None.
        return nd.get("output_wire", nid)

    def output_wire(self, nid: str) -> str:
        return self.G.nodes[nid].get("output_wire", nid)


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

        DAG = self._combinational_graph(src, copy=False)

        try:
            topo = list(nx.topological_sort(DAG))
        except nx.NetworkXUnfeasible:
            raise ValueError("Combinational cycle detected.")

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

        subG = self._combinational_graph(src, exclude, copy=False)

        try:
            if must_pass:
                mid  = self.resolve(must_pass)
                p1   = nx.shortest_path(subG, src, mid)
                p2   = nx.shortest_path(subG, mid, dst)
                path = p1 + p2[1:]
                if len(set(path)) != len(path):
                    # Cycles can make the two legs share interior nodes;
                    # recompute the second leg avoiding the first (R8).
                    pruned = subG.copy()
                    pruned.remove_nodes_from(set(p1) - {mid})
                    p2   = nx.shortest_path(pruned, mid, dst)
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
        graph = self._combinational_graph(src, copy=False)
        try:
            first = nx.shortest_path(graph, src, dst)
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
            for succ in graph.successors(node):
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

        subG = self._combinational_graph(src, {mid}, copy=False)
        try:
            cex = nx.shortest_path(subG, src, dst)
            return False, [self.node_label(n) for n in cex]
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return True, None


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

    def _comb_fingerprint(self) -> tuple:
        # Mutation epoch plus structural sizes: O(1) instead of O(E) XOR.
        # Transformer / backend rewrites must call mark_mutated().
        return (
            id(self.G),
            self.G.number_of_nodes(),
            self.G.number_of_edges(),
            len(self.wire_driver),
            len(self.primary_outputs),
            int(getattr(self, "_mut_epoch", 0)),
        )

    def _combinational_graph(
        self,
        source: Optional[str] = None,
        exclude: Optional[set[str]] = None,
        copy: bool = True,
    ) -> nx.DiGraph:
        """Copy the data graph while treating every DFF Q as a boundary PI.

        Edges into a DFF represent D/clock/reset/set pins and must never allow
        a PI-to-PO path to pass through a sequential element.  Dedicated
        register-to-register routines inspect D pins explicitly.
        """
        fp = self._comb_fingerprint()
        if self._comb_cache_fp != fp:
            self._comb_cache.clear()
            self._comb_cache_fp = fp
        # Non-DFF sources share the default comb view (DFF out-edges stay cut).
        cache_source = source
        if cache_source is not None:
            nd = self.G.nodes.get(cache_source, {})
            if nd.get("gate_type", "") not in DFF_TYPES:
                cache_source = None
        blocked = exclude or set()
        key = (cache_source, frozenset(blocked) if blocked else None)
        cached = self._comb_cache.get(key)
        if cached is not None:
            return cached.copy() if copy else cached
        graph = self.G.subgraph(n for n in self.G if n not in blocked).copy()
        for nid, nd in list(graph.nodes(data=True)):
            if nd.get("gate_type", "") in DFF_TYPES:
                graph.remove_edges_from(list(graph.in_edges(nid)))
                if nid != cache_source:
                    graph.remove_edges_from(list(graph.out_edges(nid)))
        self._comb_cache[key] = graph
        return graph.copy() if copy else graph

    def fanout_counts(self) -> dict[str, int]:
        """Count sink pins and primary-output connections per driver."""
        counts: dict[str, int] = {
            nid: 0 for nid, nd in self.G.nodes(data=True)
            if nd.get("ntype") in {"pi", "cell", "const"}
        }
        for dst, nd in self.G.nodes(data=True):
            if nd.get("ntype") != "cell":
                continue
            ports = list(nd.get("input_ports") or [])
            if ports:
                for _port, wire in ports:
                    driver = self.wire_driver.get(wire)
                    if driver is not None:
                        counts[driver] = counts.get(driver, 0) + 1
                continue
            for driver, _dst, _edge in self.G.in_edges(dst, data=True):
                counts[driver] = counts.get(driver, 0) + 1
        for driver in self.primary_outputs.values():
            counts[driver] = counts.get(driver, 0) + 1
        return counts

    def get_fanout(self, name: str) -> int:
        """Fanout is the number of sink pins plus primary-output loads."""
        return self.fanout_counts().get(self.resolve(name), 0)

    def report_outputs_cone_gt(self, threshold: int) -> list[tuple[str, int]]:
        """Return [(port_name, size)] for all POs with cone > threshold gates."""
        rows: list[tuple[str, int]] = []
        for name in self.primary_outputs:
            size = self.get_cone_size(name)
            if size > threshold:
                rows.append((name, size))
        return rows


    def same_clock_domain(self, ff1_name: str,
                          ff2_name: str) -> tuple[Optional[bool], str]:
        """
        Compare the CLK-port predecessors of two DFF cells.
        A shared predecessor node means the same clock domain.
        Returns (same, explanation_string) where same is True/False when
        determined, or None (UNKNOWN) if a CLK input cannot be identified.
        """
        # Anchored fallback: matches clk/clock/ck names (optionally escaped,
        # numbered or suffixed) but not data wires containing "ck" (e.g. ack).
        clk_name_re = re.compile(r"^(\\)?(clk|clock|ck)(\d+|_.*)?$", re.I)

        def clk_driver(nid: str) -> Optional[str]:
            nd = self.G.nodes.get(nid, {})
            for port, wire in nd.get("input_ports", []):
                normalized = str(port).upper().lstrip("\\")
                if normalized in {"C", "CK", "CLK", "CLOCK"}:
                    return self.wire_driver.get(wire)
            for u, _, d in self.G.in_edges(nid, data=True):
                port = str(d.get("port", "")).upper().lstrip("\\")
                if port in {"C", "CK", "CLK", "CLOCK"}:
                    return u
                # The wire-name fallback must never fire on a data/control
                # pin: a D-pin net named "clk2" (or a reset "ck_div") would
                # otherwise be misattributed as the clock source.
                if port in _DFF_NON_CLOCK_PORTS:
                    continue
                w = d.get("wire", "")
                if clk_name_re.match(w):
                    return u
            return None

        def root_clock_source(nid: Optional[str]) -> Optional[str]:
            """Trace back through clock buffers to the non-buffer source."""
            seen: set[str] = set()
            cur = nid
            while cur is not None and cur not in seen:
                seen.add(cur)
                nd = self.G.nodes.get(cur, {})
                if nd.get("ntype") != "cell" or nd.get("gate_type") != "$buf":
                    return cur
                preds = [p for p, _d in self.G.in_edges(cur)]
                if len(preds) != 1:
                    return cur
                cur = preds[0]
            return cur

        n1 = self.resolve(ff1_name)
        n2 = self.resolve(ff2_name)
        c1 = clk_driver(n1)
        c2 = clk_driver(n2)

        if c1 is None or c2 is None:
            return None, "Could not identify CLK input on one or both DFFs."
        c1 = root_clock_source(c1)
        c2 = root_clock_source(c2)
        if c1 == c2:
            w = self.output_wire(c1)
            return True, f"Both driven by clock '{w}'."
        return False, (f"{ff1_name} uses clock '{self.output_wire(c1)}', "
                       f"{ff2_name} uses clock '{self.output_wire(c2)}'.")


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


