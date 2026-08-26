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
    optimize_cone(output_signal, max_depth=None, objective="min_gates", style=None)
    remap_cone(output_signal, style)
    abc_optimize_full_design(style=None, objective="min_depth")

  Verification
    check_equiv(path_a, path_b)
"""

from __future__ import annotations

import itertools
import copy
import os
import re
import shutil
import tempfile
import time
from typing import Optional

import networkx as nx

from .netlist_graph import CONST_0, CONST_1, DFF_TYPES, NetlistGraph, YOSYS_TO_PRIM
from .yosys_backend import EquivResult, YosysBackend, safe_temp_dir
from .transformer import NetlistTransformer
from .writer import VerilogWriter
from .optimizer import ConeOptimizer


class EDABackend:
    """
    Stateful EDA tool API.  One instance is created at contest startup and
    reused for the entire session.  The internal NetlistGraph is replaced on
    each read_design call.
    """

    def __init__(
        self,
        yosys_bin: str = "yosys",
        yosys_timeout_sec: int = 240,
        equiv_timeout_sec: int = 120,
        cone_timeout_sec: int = 20,
        robust_total_timeout_sec: int = 240,
        large_cone_threshold: int = 5000,
    ) -> None:
        self.yosys:        YosysBackend  = YosysBackend(
            yosys_bin,
            default_timeout_sec=yosys_timeout_sec,
            equiv_timeout_sec=equiv_timeout_sec,
        )
        self.writer:       VerilogWriter = VerilogWriter()
        self.graph:        Optional[NetlistGraph]    = None
        self._transformer: Optional[NetlistTransformer] = None
        self._optimizer:   ConeOptimizer = ConeOptimizer(self.yosys, self.writer)
        self._last_counts: dict[str, int] = {}
        self._original_path: Optional[str] = None
        self._case_dir: Optional[str] = None
        self._result_index: int = 0
        self._loaded_cell_count: int = 0
        self._loaded_depth: int = 0
        self._loaded_gate_hist: dict[str, int] = {}
        self._loaded_bytes: int = 0
        self._last_written_path: str = ""
        self._last_written_bytes: int = 0
        self._finalize_stats: dict[str, int | str | bool] = {}
        self._preserve_buffers: bool = False
        self._equiv_timeout_sec = int(equiv_timeout_sec)
        self._cone_timeout_sec = int(cone_timeout_sec)
        self._robust_total_timeout_sec = int(robust_total_timeout_sec)
        self._large_cone_threshold = int(large_cone_threshold)
        self._state_target_limit = 0  # skip DFF next-state cones by default (too many for CEC budget)
        self._last_verification_target_note = ""
        self._request_deadline: Optional[float] = None
        self._request_kind: str = ""
        self._budget_skip_count: int = 0
        self._reset_cec_stats()

    def set_request_deadline(self, deadline_monotonic: float, request_kind: str = "default") -> None:
        """Set the current per-request deadline used to bound expensive tools."""
        self._request_deadline = float(deadline_monotonic)
        self._request_kind = str(request_kind or "default")
        self._budget_skip_count = 0
        self._sync_transformer_budget()

    def clear_request_deadline(self) -> None:
        """Clear any active per-request deadline."""
        self._request_deadline = None
        self._request_kind = ""
        self._budget_skip_count = 0
        self._sync_transformer_budget()

    def remaining_request_time(self) -> float:
        """Return seconds left before the active request deadline."""
        if self._request_deadline is None:
            return float("inf")
        return max(0.0, self._request_deadline - time.monotonic())

    def _dynamic_scale(self, base: int, min_factor: float = 0.3,
                       max_factor: float = 2.5) -> int:
        """Scale a limit (e.g. cone count, variant count) based on remaining time.

        - Remaining > 200s 鈫?max_factor (generous)
        - Remaining 60-200s 鈫?1.0 (default)
        - Remaining < 60s 鈫?min_factor (conservative)
        - No deadline 鈫?1.0
        """
        remaining = self.remaining_request_time()
        if remaining == float("inf"):
            return base
        if remaining > 200:
            return max(1, int(base * max_factor))
        if remaining > 60:
            return base
        return max(1, int(base * min_factor))

    def _budget_timeout(
        self,
        preferred: int | float,
        reserve: float = 2.0,
        minimum: int = 1,
    ) -> Optional[int]:
        """Clamp a subprocess timeout to the current request budget."""
        remaining = self.remaining_request_time()
        if remaining == float("inf"):
            return max(minimum, int(preferred))
        usable = remaining - reserve
        if usable < minimum:
            self._budget_skip_count += 1
            return None
        return max(minimum, int(min(float(preferred), usable)))

    def _time_budget_exhausted(self, where: str = "operation") -> str:
        self._budget_skip_count += 1
        remaining = self.remaining_request_time()
        return (
            f"TIME_BUDGET_EXHAUSTED[{where}]: "
            f"remaining_request_time={remaining:.2f}s request_kind={self._request_kind or 'default'}"
        )

    def _sync_transformer_budget(self) -> None:
        if self._transformer is not None:
            setter = getattr(self._transformer, "set_deadline", None)
            if callable(setter):
                setter(self._request_deadline)

    def _transformer_budget_note(self) -> str:
        if self._transformer is None:
            return ""
        checker = getattr(self._transformer, "budget_exhausted", None)
        if callable(checker) and checker():
            self._budget_skip_count += 1
            return " TIME_BUDGET_EXHAUSTED[transformer]"
        return ""

    def _large_global_transform_skip(self, label: str, threshold: int = 80000) -> str:
        if self._request_deadline is None or self.graph is None:
            return ""
        cells = self._cell_count()
        if cells <= int(threshold):
            return ""
        self._budget_skip_count += 1
        return (
            f"{label}: skipped global template expansion on {cells} cells "
            "(design too large for template-based gate replacement; "
            "use cone-scoped or remap_design instead)"
        )


    def read_design(self, path: str) -> str:
        """Load a gate-level Verilog file into the internal design state."""
        if not os.path.isfile(path):
            return self._fail("NOT_FOUND", f"file '{path}' not found.")
        parsed_direct = False
        try:
            self.graph = NetlistGraph.from_verilog(path)
            parsed_direct = True
        except Exception:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False, dir=safe_temp_dir()) as f:
                jpath = f.name
            try:
                timeout = self._budget_timeout(self.yosys.default_timeout_sec, reserve=1.0)
                if timeout is None:
                    return self._time_budget_exhausted("read_design")
                self.yosys.verilog_to_json(path, jpath, timeout=timeout)
                self.graph = NetlistGraph.from_yosys_json(jpath)
            except RuntimeError as e:
                return f"Error loading design: {e}"
            finally:
                if os.path.exists(jpath):
                    os.unlink(jpath)

        try:
            self._install_verilog_aliases(path)
            self._transformer = NetlistTransformer(self.graph)
            self._sync_transformer_budget()
            if not parsed_direct:
                self._last_counts["inverted_primitives_collapsed"] = (
                    self._transformer.collapse_inverted_primitives()
                )
            self._last_counts = {}
            self._original_path = os.path.abspath(path)
            self._case_dir = os.path.dirname(self._original_path)
            self._result_index = 0
            self._last_written_path = ""
            self._last_written_bytes = 0
            self._finalize_stats = {}
            self._preserve_buffers = False
            self._reset_cec_stats()
        except RuntimeError as e:
            return f"Error loading design: {e}"

        s = self.graph.summary()
        self._loaded_cell_count = int(s["cell_count"])
        self._loaded_depth = self._max_design_depth_value()
        self._loaded_gate_hist = dict(s["gate_type_histogram"])
        try:
            self._loaded_bytes = os.path.getsize(path)
        except OSError:
            self._loaded_bytes = 0
        return (
            f"Loaded '{s['module']}': {s['cell_count']} cells, "
            f"PI:{len(s['primary_inputs'])} PO:{len(s['primary_outputs'])}"
        )

    def write_design(self, path: str) -> str:
        """Write the current design state to a gate-level Verilog file."""
        self._need_design()
        out_path = self._resolve_output_path(path)
        if not self._has_prior_transform() and self._original_path and os.path.exists(self._original_path):
            before = self._cell_count()
            try:
                shutil.copyfile(self._original_path, out_path)
            except Exception as e:
                return f"Error writing: {e}"
            self._last_written_path = out_path
            try:
                self._last_written_bytes = os.path.getsize(out_path)
            except OSError:
                self._last_written_bytes = 0
            self._finalize_stats = {
                "cells_before": before, "cells_after": before,
                "cells_saved": 0,
                "merged": 0,
                "cleanup_const": 0,
                "cleanup_not_not": 0, "cleanup_inv_prim": 0, "cleanup_dangling": 0,
                "preserve_buffers": self._preserve_buffers,
                "style": self._whole_design_style() or "mixed",
            }
            return f"Written to '{out_path}'. Unchanged original design."

        if self._request_kind == "basic" and self._cell_count() > 30000:
            before = self._cell_count()
            # Lightweight cleanup: at minimum remove dangling gates and merge duplicates
            try:
                self._transformer.remove_dangling()
                self._structural_duplicate_merge_once()
            except Exception:
                pass
            self._finalize_stats = {
                "cells_before": before,
                "cells_after": before,
                "cells_saved": 0,
                "merged": 0,
                "cleanup_const": 0,
                "cleanup_not_not": 0,
                "cleanup_inv_prim": 0,
                "cleanup_dangling": 0,
                "preserve_buffers": self._preserve_buffers,
                "style": self._whole_design_style() or "mixed",
                "finalize_skipped": True,
            }
            stats = self._finalize_stats
        else:
            stats = self._finalize_for_write()
        try:
            self.writer.write(self.graph, out_path)
        except Exception as e:
            return f"Error writing: {e}"
        self._last_written_path = out_path
        try:
            self._last_written_bytes = os.path.getsize(out_path)
        except OSError:
            self._last_written_bytes = 0
        saved = int(stats.get("cells_before", 0)) - int(stats.get("cells_after", 0))
        extra = f" FinalOpt cells {stats.get('cells_before', 0)}->{stats.get('cells_after', 0)}"
        if stats.get("finalize_skipped"):
            extra += " skipped_for_time_budget"
        if saved:
            extra += f" ({saved} fewer)"
        return f"Written to '{out_path}'.{extra}"

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

    def optimization_stats(self) -> str:
        """Return current testcase optimization statistics."""
        return self.optimization_stats_line()

    def check_design_style(self, style: str, output_signal: str = "") -> str:
        """Check whether the full design or one cone obeys a primitive style."""
        self._need_design()
        style_norm = (style or "").strip().lower().replace("-", "_")
        allowed = {
            "nand_not": {"$nand", "$not"},
            "nor_not": {"$nor", "$not"},
            "and_not": {"$and", "$not"},
            "and_or_not": {"$and", "$or", "$not"},
        }.get(style_norm)
        if not allowed:
            return f"UNKNOWN: style '{style}' is not recognized."
        try:
            nodes = (
                self.graph.extract_cone(output_signal)
                if output_signal else set(self.graph.G.nodes)
            )
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        bad: dict[str, int] = {}
        for nid in nodes:
            nd = self.graph.G.nodes.get(nid, {})
            if nd.get("ntype") != "cell":
                continue
            gate = nd.get("gate_type")
            if gate in DFF_TYPES or gate in allowed:
                continue
            prim = YOSYS_TO_PRIM.get(gate, str(gate).lstrip("$"))
            bad[prim] = bad.get(prim, 0) + 1
        scope = f"cone {output_signal}" if output_signal else "design"
        if not bad:
            return f"PASS: {scope} obeys {style_norm}."
        detail = " ".join(f"{k.upper()}:{v}" for k, v in sorted(bad.items()))
        return f"FAIL[STYLE]: {scope} violates {style_norm}: {detail}"

    def check_fanout_limit(self, max_fanout: int, name: str = "") -> str:
        """Check whether fanout is bounded globally or under one signal."""
        self._need_design()
        try:
            limit = int(max_fanout)
        except (TypeError, ValueError):
            return self._fail("INVALID", f"bad max_fanout '{max_fanout}'")
        if name:
            try:
                root = self.graph.resolve(name)
            except KeyError as e:
                return self._fail("NOT_FOUND", str(e))
            nodes = self._buffer_tree_scope_nodes(root)
        else:
            nodes = set(self.graph.G.nodes)
        best = max(
            (
                (self._fanout_value(nid), nid)
                for nid in nodes
                if self.graph.G.nodes.get(nid, {}).get("ntype") in {"pi", "cell"}
            ),
            default=(0, ""),
        )
        label = self.graph.node_label(best[1]) if best[1] else "none"
        scope = f" for {name}" if name else ""
        if best[0] <= limit:
            return f"PASS: fanout{scope} <= {limit} (max={best[0]} at {label})."
        return f"FAIL[FANOUT]: fanout{scope} max={best[0]} > {limit} at {label}."

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
        """Enumerate all simple combinational paths from from_signal to to_signal."""
        self._need_design()
        try:
            src = self.graph.resolve(from_signal)
            dst = self.graph.resolve(to_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))

        title = f"Paths {from_signal}->{to_signal}"
        out_path = self._make_result_path("paths", from_signal, "to", to_signal)
        try:
            inline_limit = max(1, min(int(max_paths), 20))
        except (TypeError, ValueError):
            inline_limit = 20
        count = 0
        inline_blocks: list[str] = []

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(title + "\n")
            for path in self._iter_simple_comb_paths(src, dst):
                count += 1
                block = f"Path {count}:\n  " + "\n  -> ".join(
                    self.graph.node_label(n) for n in path
                )
                f.write(block + "\n")
                if count <= inline_limit:
                    inline_blocks.append(block)

        if count == 0:
            try:
                os.unlink(out_path)
            except OSError:
                pass
            return f"No paths found from '{from_signal}' to '{to_signal}'."
        if count <= inline_limit:
            try:
                os.unlink(out_path)
            except OSError:
                pass
            return f"{count} paths {from_signal}->{to_signal}:\n" + "\n".join(inline_blocks)
        return (
            f"Complete path enumeration: {count} paths {from_signal}->{to_signal}.\n"
            f"Full list written to '{out_path}'.\n"
            f"First {len(inline_blocks)} path(s):\n" + "\n".join(inline_blocks)
        )

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
        title = f"FanIn {output_signal}: {len(labels)} gates. {hist}"
        return self._format_full_list(title, labels, "fanin", output_signal)

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
        title = f"FanOut {input_signal}: {len(labels)} gates. {hist}"
        return self._format_full_list(title, labels, "fanout", input_signal)

    def get_fanout(self, net_name: str) -> str:
        """Report the fanout of a net or cell output."""
        self._need_design()
        try:
            fo = self._fanout_value(self.graph.resolve(net_name))
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        return f"Fanout {net_name}: {fo}"

    def list_gates_by_type(self, gate_type: str, limit: int = 120) -> str:
        """List gates matching a primitive type."""
        self._need_design()
        prim = gate_type.lower()
        cells = self.graph.find_cells_by_type(prim)
        if not cells:
            return f"0 {prim.upper()} gates."
        labels = [self.graph.node_label(n) for n in cells]
        try:
            inline_limit = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            inline_limit = 200
        title = f"{len(cells)} {prim.upper()}:"
        return self._format_full_list(title, labels, "gates", prim, inline_limit=inline_limit)

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
                labels = [self.graph.node_label(n) for n in cells]
                reports.append(
                    self._format_full_list(
                        f"{len(cells)} {prim.upper()} const={const}:",
                        labels,
                        "constant_gates",
                        prim,
                        str(const),
                        inline_limit=100,
                    )
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
        try:
            cap = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            cap = 200
        labels = [self.graph.node_label(n) for n in loads]
        labels.extend(
            f"PO:{port}"
            for port, driver in self.graph.primary_outputs.items()
            if driver == nid
        )
        return self._format_full_list(
            f"Loads {name}: {len(labels)}",
            labels,
            "loads",
            name,
            inline_limit=cap,
        )

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
        """Report the deepest PI/DFF-Q to PO/DFF-D combinational path found."""
        self._need_design()
        depths, pred, origin = self._depths_from_boundaries(include_dffs=True)
        best = (-1, "", "")
        for out_name, driver in self.graph.primary_outputs.items():
            if driver in depths and depths[driver] > best[0]:
                best = (depths[driver], f"PO:{out_name}", driver)
        for dff, nd in self.graph.G.nodes(data=True):
            if nd.get("ntype") != "cell" or nd.get("gate_type") not in DFF_TYPES:
                continue
            for driver, _dst, edge in self.graph.G.in_edges(dff, data=True):
                port = str(edge.get("port", "")).upper().lstrip("\\")
                if port in {"D", "DATA", "I0"} and depths.get(driver, -1) > best[0]:
                    best = (depths[driver], f"DFF-D:{dff}", driver)
        if best[0] < 0:
            return "No combinational critical path found."
        path = self._reconstruct_path(pred, best[2])
        path_str = " -> ".join(path[:10])
        if len(path) > 10:
            path_str += f" ... (+{len(path)-10})"
        return f"MaxDepth: {best[0]}\n  src={self.graph.node_label(origin.get(best[2], best[2]))}\n  sink={best[1]}\n  {path_str}"

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

    def gate_on_max_depth_path(self, name: str) -> str:
        """Check whether a gate lies on any maximum-depth PI-to-PO path."""
        self._need_design()
        try:
            target = self.graph.resolve(name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if self.graph.G.nodes.get(target, {}).get("ntype") != "cell":
            return self._fail("TYPE", f"'{name}' is not a gate/cell.")

        depths, _, _ = self._depths_from_boundaries(include_dffs=False)
        max_depth = self._max_design_depth_value()
        if target not in depths:
            return f"NO: {name} is not reachable from a primary input."

        dag = nx.DiGraph()
        dag.add_nodes_from(self.graph.G.nodes)
        for u, v in self.graph.G.edges():
            if self.graph.G.nodes.get(u, {}).get("gate_type") in DFF_TYPES:
                continue
            dag.add_edge(u, v)
        try:
            topo = list(nx.topological_sort(dag))
        except nx.NetworkXUnfeasible:
            return f"UNKNOWN: {name} maximum-depth membership needs an acyclic combinational graph."

        po_drivers = set(self.graph.primary_outputs.values())
        suffix: dict[str, int] = {}
        for node in reversed(topo):
            best = 0 if node in po_drivers else -1
            for succ in dag.successors(node):
                if succ not in suffix:
                    continue
                nd = self.graph.G.nodes.get(succ, {})
                inc = 1 if nd.get("ntype") == "cell" and nd.get("gate_type") not in DFF_TYPES else 0
                best = max(best, suffix[succ] + inc)
            if best >= 0:
                suffix[node] = best

        total = depths.get(target, -1) + suffix.get(target, -1)
        verdict = "YES" if total == max_depth else "NO"
        return f"{verdict}: {name} on max-depth path (through={total}, max={max_depth})"

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

    def max_register_to_register_depth(self) -> str:
        """Report the maximum combinational depth from any DFF Q to any DFF D pin."""
        self._need_design()
        dffs = {
            nid for nid, nd in self.graph.G.nodes(data=True)
            if nd.get("ntype") == "cell" and nd.get("gate_type") in DFF_TYPES
        }
        if not dffs:
            return "Max reg-to-reg depth: 0 (no DFFs)"

        dag = nx.DiGraph()
        dag.add_nodes_from(self.graph.G.nodes)
        for u, v in self.graph.G.edges():
            # DFF cells are sequential boundaries. Their Q outputs may start a
            # path, but paths never continue through another DFF instance.
            if v in dffs:
                continue
            dag.add_edge(u, v)

        try:
            topo = list(nx.topological_sort(dag))
        except nx.NetworkXUnfeasible:
            return "Max reg-to-reg depth: UNKNOWN (combinational cycle)"

        depth: dict[str, int] = {src: 0 for src in dffs}
        pred: dict[str, Optional[str]] = {src: None for src in dffs}
        origin: dict[str, str] = {src: src for src in dffs}
        for node in topo:
            if node in dffs:
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
            if best_pred is not None:
                depth[node] = best_depth
                pred[node] = best_pred
                origin[node] = best_origin or best_pred

        best = (-1, "", "")
        for dst in dffs:
            d_inputs = [
                src for src, _dst, edge in self.graph.G.in_edges(dst, data=True)
                if str(edge.get("port", "")).upper().lstrip("\\") in {"D", "DATA"}
            ]
            for din in d_inputs:
                src_ff = origin.get(din, "")
                if not src_ff or src_ff == dst:
                    # Self-feedback is still a valid register-to-register path.
                    src_ff = origin.get(din, src_ff)
                cand = depth.get(din, -1)
                if cand > best[0]:
                    best = (cand, din, dst)
        if best[0] < 0:
            return "Max reg-to-reg depth: 0 (no Q-to-D path)"
        path = self._reconstruct_path(pred, best[1])
        path_str = " -> ".join(path[:10])
        if len(path) > 10:
            path_str += f" ... (+{len(path)-10})"
        src_label = self.graph.node_label(origin.get(best[1], best[1]))
        return (
            f"Max reg-to-reg depth: {best[0]}\n"
            f"  src={src_label}\n"
            f"  dst={self.graph.node_label(best[2])}\n"
            f"  {path_str}"
        )

    def shared_fanin_cones(self, output_a: str, output_b: str) -> str:
        self._need_design()
        try:
            cone_a = self.graph.extract_cone(output_a)
            cone_b = self.graph.extract_cone(output_b)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        shared = sorted(cone_a & cone_b)
        labels = [self.graph.node_label(n) for n in shared]
        return self._format_full_list(
            f"Shared fanin {output_a},{output_b}: {len(shared)}",
            labels,
            "shared_fanin",
            output_a,
            output_b,
            inline_limit=500,
        )

    def _extract_shared_subexpressions(
        self,
        min_overlap_ratio: float = 0.15,
        min_shared_gates: int = 5,
    ) -> int:
        """Identify output cone pairs with substantial gate overlap and
        optimise them sequentially with the ABC -ci flag for shared logic.

        Returns number of cone pairs optimised.
        """
        self._need_design()
        # Performance guard: skip on extremely large designs, scale with time budget
        cell_limit = self._dynamic_scale(80000, min_factor=0.5, max_factor=2.0)
        if self._cell_count() > int(cell_limit):
            return 0

        # Collect cone gate sets (capped at 120 POs, cone size 5鈥?000)
        po_cones: dict[str, set[str]] = {}
        po_cap = self._dynamic_scale(120, min_factor=0.25, max_factor=2.0)
        for out_name in list(self.graph.primary_outputs.keys())[:po_cap]:
            try:
                cone = self.graph.extract_cone(out_name)
                if 5 <= len(cone) <= 8000:
                    po_cones[out_name] = cone
            except Exception:
                continue

        if len(po_cones) <= 1:
            return 0

        # Find pairs with substantial overlap
        pairs: list[tuple[float, str, str]] = []
        for (out_a, cone_a), (out_b, cone_b) in itertools.combinations(
            po_cones.items(), 2
        ):
            shared = cone_a & cone_b
            overlap = len(shared) / max(len(cone_a), len(cone_b), 1)
            if overlap >= min_overlap_ratio and len(shared) >= min_shared_gates:
                pairs.append((overlap, out_a, out_b))
        if not pairs:
            return 0

        # Sort by overlap descending (most shared first), cap pairs
        pairs.sort(reverse=True)
        pair_cap = self._dynamic_scale(200, min_factor=0.25, max_factor=1.5)
        pairs = pairs[: min(pair_cap, len(pairs))]

        extracted = 0
        for _, out_a, out_b in pairs:
            trial_graph = copy.deepcopy(self.graph)
            # Optimise first cone
            opt_a = self._optimizer.optimize(
                trial_graph, out_a,
                objective="min_gates",
                use_ci=True,
            )
            if not opt_a.success:
                continue
            # Optimise second cone on the post-spliced graph
            opt_b = self._optimizer.optimize(
                trial_graph, out_b,
                objective="min_gates",
                use_ci=True,
            )
            if not opt_b.success:
                continue
            # Evaluate
            candidate_cost = self._evaluate_graph_cost(trial_graph, "min_gates")
            current = self._cost_snapshot()
            current["key"] = self._cost_objective_key("min_gates", current)
            if self._candidate_better(current, candidate_cost, "min_gates"):
                self._safe_commit_candidate(trial_graph)
                extracted += 1

        return extracted

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
        """Report whether a signal is functionally constant 0/1."""
        self._need_design()
        try:
            nid = self.graph.resolve(signal_name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))

        target = 1 if int(value) else 0
        const = "1'b1" if target else "1'b0"

        folded = self._constant_fold_node(nid, {}, set())
        if folded is not None:
            verdict = "YES" if folded == target else "NO"
            op = "==" if verdict == "YES" else "!="
            return f"{verdict}: {signal_name} {op} {const} (functional constant propagation)"

        support = sorted(self._support_inputs(nid))
        if len(support) <= 18:
            for values in itertools.product((0, 1), repeat=len(support)):
                env = dict(zip(support, values))
                actual = self._eval_node(nid, env, {})
                if actual != target:
                    shown = ", ".join(f"{k}={v}" for k, v in list(env.items())[:16])
                    suffix = f", ... ({len(env) - 16} more)" if len(env) > 16 else ""
                    return (
                        f"NO: {signal_name} != {const}. "
                        f"Counterexample: {shown}{suffix}"
                    )
            return f"YES: {signal_name} == {const} ({2**len(support)} input assignments checked)"

        ok = self._prove_signal_constant_with_yosys(nid, target)
        if ok is True:
            return f"YES: {signal_name} == {const} (SAT proof)"
        if ok is False:
            return f"NO: {signal_name} != {const} (SAT counterexample exists)"
        return f"UNKNOWN: {signal_name} constant check needs support {len(support)}; SAT proof unavailable"

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
            return (
                f"NO: {signal_a} and {signal_b} are not functionally equivalent "
                f"because at least one signal is absent in the current netlist ({e})"
            )
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
        return self._formal_internal_signals_equiv(signal_a, signal_b, a, b)

    def _formal_internal_signals_equiv(
        self,
        signal_a: str,
        signal_b: str,
        node_a: str,
        node_b: str,
    ) -> str:
        """Prove large-support internal signal equivalence with cone CEC."""
        timeout = self._budget_timeout(self._cone_timeout_sec, reserve=2.0)
        if timeout is None:
            return (
                f"UNKNOWN[TIMEOUT]: {signal_a} vs {signal_b} "
                "formal check skipped because request time budget is exhausted"
            )
        try:
            with tempfile.TemporaryDirectory(dir=safe_temp_dir()) as tmp:
                cone_a = self._build_verification_cone_graph(
                    self.graph, self.graph.output_wire(node_a), "out")
                cone_b = self._build_verification_cone_graph(
                    self.graph, self.graph.output_wire(node_b), "out")
                self._align_cone_inputs(cone_a, cone_b)
                path_a = os.path.join(tmp, "sig_a.v")
                path_b = os.path.join(tmp, "sig_b.v")
                self.writer.write(cone_a, path_a)
                self.writer.write(cone_b, path_b)
                result = self.yosys.check_equiv_abc(
                    path_a, path_b, top="cone_top", timeout=min(timeout, 10))
                if result.status not in {"PASS", "FAIL"}:
                    fallback_timeout = self._budget_timeout(self._cone_timeout_sec, reserve=1.0)
                    if fallback_timeout is None:
                        return (
                            f"UNKNOWN[TIMEOUT]: {signal_a} vs {signal_b} "
                            f"({result.status}: {result.message})"
                        )
                    result = self.yosys.check_equiv(
                        path_a,
                        path_b,
                        gold_top="cone_top",
                        gate_top="cone_top",
                        timeout=fallback_timeout,
                    )
        except Exception as e:
            return f"UNKNOWN[CEC]: {signal_a} vs {signal_b} ({e})"
        self._record_cec_result(result, cone=True)
        if result.status == "PASS":
            return f"EQUIV: {signal_a}=={signal_b} (formal cone CEC)"
        if result.status == "FAIL":
            return f"NOT_EQUIV: {signal_a}!={signal_b}"
        return (
            f"UNKNOWN[TIMEOUT]: {signal_a} vs {signal_b} "
            f"({result.status}: {result.message})"
        )

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
        total = len(floating_inputs) + len(unconnected_outputs)
        labels = [f"input {item}" for item in floating_inputs]
        labels.extend(f"output {item}" for item in unconnected_outputs)
        try:
            cap = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            cap = 80
        return self._format_full_list(
            f"Floating: {len(floating_inputs)} in, {len(unconnected_outputs)} out.",
            labels,
            "floating",
            inline_limit=cap,
        )

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
        labels = [self.graph.node_label(n) for n in points]
        try:
            cap = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            cap = 120
        return self._format_full_list(
            f"Artic {source}->{target}: {len(points)}",
            labels,
            "articulation",
            source,
            target,
            inline_limit=cap,
        )

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
        try:
            cap = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            cap = 120
        return self._format_full_list(
            f"DFF enable/hold: {len(matches)}",
            matches,
            "dff_enable_hold",
            inline_limit=cap,
        )

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
                return f"Rename {old_name}->{new_name}: 0 (source not present in current netlist)"
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
            return f"Rename {old_name}->{new_name}: 0 (source not present in current netlist)"
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
        try:
            cap = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            cap = 120
        labels = [self.graph.node_label(n) for n in matches]
        return self._format_full_list(
            f"FFs clk={clock_name}: {len(matches)}",
            labels,
            "dffs_by_clock",
            clock_name,
            inline_limit=cap,
        )

    def highest_fanout_input(self) -> str:
        self._need_design()
        best = (-1, "")
        for name, nid in self.graph.primary_inputs.items():
            fanout = self._fanout_value(nid)
            if fanout > best[0]:
                best = (fanout, name)
        return f"Max fanout PI: {best[1]} fanout={best[0]}"

    def max_fanout(self, name: Optional[str] = None) -> str:
        self._need_design()
        if name:
            try:
                root = self.graph.resolve(name)
            except KeyError as e:
                return self._fail("NOT_FOUND", str(e))
            nodes = self._buffer_tree_scope_nodes(root)
        else:
            nodes = set(self.graph.G.nodes)
        best = max(
            (
                (self._fanout_value(n), n)
                for n in nodes
                if self.graph.G.nodes.get(n, {}).get("ntype") in {"pi", "cell"}
            ),
            default=(0, ""),
        )
        label = self.graph.node_label(best[1]) if best[1] else "none"
        return f"MaxFanout: {best[0]} at {label}"

    def structural_duplicate_merge(self) -> str:
        """Merge cells with identical primitive type and identical input drivers."""
        self._need_design()
        merged = self._structural_duplicate_merge_once(
            preserve_buffers=self._preserve_buffers
        )
        if merged == 0:
            if self._has_prior_transform():
                return "DupM:0 (clean)"
            self._last_counts["merged_gates"] = 0
            return "DupM:0"
        self._last_counts["merged_gates"] = merged
        return f"DupM:{merged}"

    def merge_functionally_equivalent_gates(self) -> str:
        """Merge gates that compute the same Boolean function (truth-table based).

        Uses up to 8 input support variables to find functional equivalences
        across different gate-type decompositions.
        Finds and merges functionally identical gates even when their internal
        structure or gate types differ (e.g. NAND(NOT(a),NOT(b)) merged with NOR(a,b)).
        """
        self._need_design()
        max_sup = 8
        if self.remaining_request_time() < 30.0:
            return self._time_budget_exhausted("merge_functionally_equivalent_gates")
        merged = self._transformer.merge_functionally_equivalent_gates(max_support=max_sup)
        budget_note = self._transformer_budget_note()
        if merged == 0:
            return f"FuncM:0{budget_note}"
        # Clean up after merge
        self._safe_cleanup(collapse_inverted=True)
        self._last_counts["merged_gates"] = int(self._last_counts.get("merged_gates", 0)) + merged
        return f"FuncM:{merged} (functionally equivalent gates merged){budget_note}"

    def simplify_constant_registers(self) -> str:
        """Detect DFFs with constant D-inputs and propagate constants.

        If a DFF's D-input is provably constant-0 or constant-1,
        replaces the DFF Q output with that constant throughout the
        design.  This removes unnecessary sequential elements and
        simplifies downstream combinational logic.

        Returns human-readable summary.
        """
        self._need_design()
        simplified = 0
        for nid, nd in list(self.graph.G.nodes(data=True)):
            if nd.get("ntype") != "cell" or nd.get("gate_type") not in DFF_TYPES:
                continue
            # Find D pin
            d_drivers = []
            for pred, _dst, edge in self.graph.G.in_edges(nid, data=True):
                port = str(edge.get("port", "")).upper().lstrip("\\")
                if port in {"D", "DATA", "I0"}:
                    d_drivers.append(pred)
            if not d_drivers:
                continue

            for d_drv in d_drivers:
                const_val = self._constant_fold_node(d_drv, {}, set())
                if const_val is not None:
                    replacement = CONST_1 if const_val == 1 else CONST_0
                    # Redirect all successors of this DFF to the constant
                    q_wire = nd.get("output_wire")
                    for succ in list(self.graph.G.successors(nid)):
                        edge_data = self.graph.G.get_edge_data(nid, succ, {})
                        # Remove old edge, add new from constant
                        self.graph.G.remove_edge(nid, succ)
                        self.graph.G.add_edge(replacement, succ,
                                          wire=replacement, port=edge_data.get("port"))
                        # Update input_ports on succ
                        succ_nd = self.graph.G.nodes.get(succ, {})
                        if succ_nd.get("ntype") == "cell":
                            ports = [
                                (p, replacement if w == q_wire else w)
                                for p, w in succ_nd.get("input_ports", [])
                            ]
                            succ_nd["input_ports"] = ports
                            succ_nd["input_wires"] = [w for _, w in ports]
                    # Update PO if this DFF drives one
                    for port, driver in list(self.graph.primary_outputs.items()):
                        if driver == nid:
                            self.graph.primary_outputs[port] = replacement
                    simplified += 1
                    break  # only process one D pin

        if simplified:
            self._safe_cleanup(collapse_inverted=True)
        return f"ConstReg:{simplified} (constant-valued DFFs propagated)"

    def merge_aig_equivalent_gates(self) -> str:
        """Merge gates with identical AND-Inverter Graph signatures.

        Normalises each gate to AND+NOT canonical form and merges nodes
        with the same structural hash.  Finds equivalences that
        direct-predecessor matching misses (e.g. NOR(a,b) and
        AND(NOT(a),NOT(b)) collapse to the same AIG node).
        """
        self._need_design()
        max_sup = 6 if self._cell_count() > 10000 else 8
        merged = self._transformer.merge_aig_equivalent_gates(
            max_support=max_sup, max_depth=16)
        if merged == 0:
            return "AIGM:0"
        self._safe_cleanup(collapse_inverted=True)
        self._last_counts["merged_gates"] = int(self._last_counts.get("merged_gates", 0)) + merged
        return f"AIGM:{merged} (AIG-equivalent gates merged)"

    def merge_sat_equivalent_signals(self, max_candidates: int = 200) -> str:
        """SAT-based detection of logically equivalent internal signals.

        Scans pairs of gates with identical support sets and uses
        Yosys SAT to prove equivalence.  Merges proven-equivalent
        pairs, catching equivalences that structural hashing misses
        (e.g. different gate decompositions computing the same function).

        Capped at *max_candidates* SAT calls to bound runtime.
        """
        self._need_design()
        if self._cell_count() > 30000:
            return "SAT_EQ:0 (design too large)"

        # Group gates by support fingerprint
        from collections import defaultdict
        by_fingerprint: dict[tuple, list[str]] = defaultdict(list)
        po_drivers = set(self.graph.primary_outputs.values())
        support_cache: dict[str, frozenset] = {}

        for nid, nd in list(self.graph.G.nodes(data=True)):
            if nd.get("ntype") != "cell":
                continue
            if nd.get("gate_type") in DFF_TYPES:
                continue
            if nid in po_drivers:
                continue
            support = self._transformer._gate_support_inputs(nid)
            if len(support) <= 6:
                fp = (len(support), tuple(sorted(support)))
                by_fingerprint[fp].append(nid)

        # For each group with 鈮? candidates, SAT-compare pairs
        sat_checks = 0
        merged = 0
        temp_dir = safe_temp_dir()

        for fp, group in by_fingerprint.items():
            if len(group) <= 1 or sat_checks >= max_candidates:
                continue
            # Only compare first N pairs per group
            for i in range(min(len(group), 8)):
                a = group[i]
                if a not in self.graph.G:
                    continue
                for j in range(i + 1, min(len(group), 8)):
                    b = group[j]
                    if b not in self.graph.G:
                        continue
                    if sat_checks >= max_candidates:
                        break
                    sat_checks += 1
                    try:
                        # Quick structural check first
                        sig_a = self._structural_signature(a, depth=8)
                        sig_b = self._structural_signature(b, depth=8)
                        if sig_a is not None and sig_a == sig_b:
                            # Already structurally identical 鈥?merge
                            self._transformer._replace_cell_output_with_driver(b, a)
                            merged += 1
                            continue

                        # SAT check via cone CEC
                        import tempfile
                        import os
                        with tempfile.TemporaryDirectory(dir=temp_dir) as tmp:
                            aw = self.graph.output_wire(a)
                            bw = self.graph.output_wire(b)
                            # Build small verification modules
                            cone_a = self._optimizer._build_cone_module(
                                self.graph, aw,
                                self._optimizer._select_rewritable_cone(self.graph, aw),
                            )
                            cone_b = self._optimizer._build_cone_module(
                                self.graph, bw,
                                self._optimizer._select_rewritable_cone(self.graph, bw),
                            )
                            self._align_cone_inputs(cone_a, cone_b)
                            # Rename PO in cone_b to match cone_a
                            if aw in cone_a.primary_outputs and bw in cone_b.primary_outputs:
                                a_drv = cone_a.primary_outputs[aw]
                                b_drv = cone_b.primary_outputs[bw]
                                # Check equivalence with short timeout
                                a_v = os.path.join(tmp, "a.v")
                                b_v = os.path.join(tmp, "b.v")
                                self.writer.write(cone_a, a_v)
                                self.writer.write(cone_b, b_v)
                                result = self.yosys.check_equiv(
                                    a_v, b_v,
                                    gold_top="cone_top",
                                    gate_top="cone_top",
                                    timeout=self._budget_timeout(10, reserve=1.0) or 2,
                                )
                                self._record_cec_result(result, cone=True)
                                if result.status == "PASS":
                                    self._transformer._replace_cell_output_with_driver(b, a)
                                    merged += 1
                    except Exception:
                        continue

        if merged:
            self._safe_cleanup(collapse_inverted=True)
        self._last_counts["merged_gates"] = int(self._last_counts.get("merged_gates", 0)) + merged
        return f"SAT_EQ:{merged} (sat={sat_checks} checks)"


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

    def try_reconnect_input_pin(self, gate_name: str, pin_name: str,
                                 signal_name: str) -> str:
        """Reconnect one input pin of a gate to a different driver signal."""
        self._need_design()
        try:
            ok = self._transformer.try_reconnect_input_pin(
                gate_name, pin_name, signal_name)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        if not ok:
            return self._fail("TYPE", f"'{gate_name}' is not a gate/cell.")
        return f"Reconnect: {gate_name}.{pin_name} -> {signal_name}"

    def add_balance_buffers(self, from_signal: str,
                             to_signals: list[str]) -> str:
        """Equalize combinational depth to multiple sinks by inserting BUF chains."""
        self._need_design()
        self._preserve_buffers = True
        try:
            result = self._transformer.add_balance_buffers(from_signal, to_signals)
        except (KeyError, ValueError) as e:
            return self._fail("NOT_FOUND", str(e))
        total = sum(result.values())
        if total == 0:
            return "BalBuf:0 (depths already equal)"
        self._last_counts["buf_added"] = total
        details = ", ".join(f"{k}={v}" for k, v in result.items() if v)
        return f"BalBuf:{total} inserted ({details})"

    def buffer_high_fanout(self, net_name: str, max_fanout: int) -> str:
        """Insert buffers to limit fanout of net_name to at most max_fanout."""
        self._need_design()
        self._preserve_buffers = True
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
        self._preserve_buffers = True
        try:
            n = self._transformer.buffer_all_high_fanout(max_fanout)
        except ValueError as e:
            return self._fail("INVALID", str(e))
        self._last_counts["buf_added"] = n
        max_seen = self._max_fanout_value()
        if n == 0:
            return f"BufAll: fanout <= {max_fanout}, max={max_seen}, no change."
        return f"BufAll: {n} inserted (limit <= {max_fanout}, max={max_seen})"

    def buffer_each_load(self, net_name: str) -> str:
        """Insert one buffer per current load of net_name."""
        self._need_design()
        self._preserve_buffers = True
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

    def balance_associative_trees(self, max_leaves: int = 256) -> str:
        """Rebuild unbalanced associative gate chains into balanced binary trees.

        Long chains of AND/OR/XOR gates cause unnecessarily deep logic.
        This pass detects maximal associative trees and rebuilds them as
        balanced binary trees, reducing depth from O(n) to O(log n).

        Parameters
        ----------
        max_leaves : int
            Maximum number of leaf nodes in a single tree (default 256).
        """
        self._need_design()
        n = self._transformer.balance_associative_trees(max_leaves=int(max_leaves))
        self._last_counts["balanced_trees"] = n
        if n == 0:
            return "BalAssoc:0 (no unbalanced trees found)"
        return f"BalAssoc:{n} (associative trees balanced)"

    def simplify_constant_gates(self) -> str:
        """Apply safe local constant propagation."""
        self._need_design()
        n = self._transformer.simplify_constant_gates(
            remove_buf=not self._preserve_buffers
        )
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

    def _compress_after_replace(self, style: str, before_cells: int) -> str:
        """After template-based gate replacement, compress the result.

        Strategy (tried in order):
          1. structural_duplicate_merge 鈥?merge newly created duplicates (P0)
          2. Full-design ABC with target gate library
          3. Cone-level ABC on affected output cones (fallback)
          4. SAFETY VALVE: if cells >50% over baseline, try aggressive ABC re-compress
        Returns '+abc' if any compression accepted, '+merge' if only merge helped, '' otherwise.
        """
        result = ""

        # P0: aggressive local cleanup + structural merge
        # Run De Morgan inverse + boolean identities first to simplify template artifacts
        dm = self._transformer.simplify_boolean_identities()
        nn = self._transformer.collapse_not_not_pairs()
        sc = self._transformer.simplify_constant_gates(remove_buf=True)
        rd = self._transformer.remove_dangling()
        merged = self._structural_duplicate_merge_once(preserve_buffers=False)
        self._last_counts["merged_gates"] = int(self._last_counts.get("merged_gates", 0)) + merged
        if merged or dm or nn or sc or rd:
            result = f" +cleanup(dm={dm},nn={nn},sc={sc},rd={rd},merge={merged})"

        if before_cells > 50000:
            return result + " +abc_skipped_large"

        # P1: full-design ABC with target gate library
        abc_graph = self._try_abc_remap(self.graph, style)
        if abc_graph is not None:
            abc_cells = sum(1 for _n, d in abc_graph.G.nodes(data=True)
                            if d.get("ntype") == "cell")
            if abc_cells < self._cell_count():
                self.graph = abc_graph
                self._transformer = NetlistTransformer(self.graph)
                self._safe_cleanup(collapse_inverted=False, remove_buf=True, reconnect=True)
                return result.replace(" +merge", "") + " +abc"

        # P1 (fallback): cone-level ABC on affected output cones
        gate_for_style = {"nand_not": "$nand", "nor_not": "$nor",
                          "and_not": "$and", "and_or_not": "$and"}
        target_gate = gate_for_style.get(style)
        if target_gate:
            affected = []
            for out_name in list(self.graph.primary_outputs.keys()):
                try:
                    cone = self.graph.extract_cone(out_name)
                except KeyError:
                    continue
                if any(self.graph.G.nodes.get(n, {}).get("gate_type") == target_gate
                       for n in cone):
                    try:
                        depth = self._max_depth_value_to_output(out_name)
                    except KeyError:
                        continue
                    affected.append((depth, out_name))
            # Optimize deepest affected cones first, dynamic limit
            cone_limit = self._dynamic_scale(15, min_factor=0.3, max_factor=1.5)
            improved = 0
            for _depth, out_name in sorted(affected, reverse=True)[:cone_limit]:
                old_d = self._max_depth_value_to_output(out_name)
                old_c = self._cell_count(self.graph.extract_cone(out_name))
                trial = copy.deepcopy(self.graph)
                res = self._optimizer.optimize(trial, out_name,
                                               objective="min_gates", style=style)
                if not res.success:
                    continue
                new_d, new_c, _ = self._remap_trial_cone_inplace(trial, out_name, style)
                if (new_d, new_c) <= (old_d, old_c) and new_c < old_c:
                    self.graph = trial
                    self._transformer = NetlistTransformer(self.graph)
                    improved += 1
            if improved:
                result += f" +cone:{improved}"

        # P3 SAFETY VALVE: if still >5% inflated, try aggressive re-compress
        inflation = (self._cell_count() - before_cells) / max(before_cells, 1)
        if inflation > 0.05:
            # P5: NOT-NOT convergence scan before ABC
            conv_total = 0
            for _ in range(6):
                nn = self._transformer.collapse_not_not_pairs()
                sc = self._transformer.simplify_constant_gates(remove_buf=True)
                bi = self._transformer.simplify_boolean_identities()
                rd = self._transformer.remove_dangling()
                conv_total += nn + sc + bi + rd
                if nn + sc + bi + rd == 0:
                    break
            if conv_total:
                result += f" +nn_conv:{conv_total}"

            # Shared subexpression extraction for compression
            shared = self._extract_shared_subexpressions(
                min_overlap_ratio=0.1, min_shared_gates=3)
            if shared:
                result += f" +shared:{shared}"

            safety_abc = self._try_abc_remap(self.graph, style, objective="min_gates")
            if safety_abc is not None:
                safety_cells = sum(1 for _n, d in safety_abc.G.nodes(data=True)
                                   if d.get("ntype") == "cell")
                if safety_cells < self._cell_count():
                    self.graph = safety_abc
                    self._transformer = NetlistTransformer(self.graph)
                    self._safe_cleanup(collapse_inverted=False, remove_buf=True,
                                       reconnect=True)
                    result += " +safety_abc"
                    # Also do aggressive cone-level pass on largest cones
                    po_sizes = []
                    for out_name in self.graph.primary_outputs.keys():
                        try:
                            po_sizes.append((len(self.graph.extract_cone(out_name)), out_name))
                        except KeyError:
                            continue
                    po_list = [
                        out_name for _size, out_name in sorted(po_sizes, reverse=True)
                    ][:self._dynamic_scale(8, min_factor=0.25, max_factor=2.0)]
                    safety_cone = 0
                    for out_name in po_list:
                        trial = copy.deepcopy(self.graph)
                        res = self._optimizer.optimize(
                            trial, out_name,
                            objective="min_gates", style=style)
                        if res.success and res.after_gates < res.before_gates:
                            self.graph = trial
                            self._transformer = NetlistTransformer(self.graph)
                            safety_cone += 1
                    if safety_cone:
                        result += f" +safety_cone:{safety_cone}"

        return result

    def replace_xor_with_nand(self) -> str:
        """Convert every 2-input XOR into a 4-NAND implementation."""
        self._need_design()
        skip = self._large_global_transform_skip("XOR->NAND")
        if skip:
            return skip
        before = self._cell_count()
        n = self._transformer.replace_xor_with_nand()
        budget_note = self._transformer_budget_note()
        self._last_counts["xor_converted"] = n
        self._last_counts["nand_added"] = n * 4
        if n == 0:
            return f"XOR->NAND: 0{budget_note}"
        nand_count = len(self.graph.find_cells_by_type("nand"))
        abc_tag = "" if budget_note else self._compress_after_replace("nand_not", before)
        return f"XOR->NAND: {n}{abc_tag} (NANDs now: {nand_count}){budget_note}"

    def replace_xnor_with_nor(self, output_signal: Optional[str] = None) -> str:
        """Convert XNOR gates to NOR-only implementations."""
        self._need_design()
        if output_signal is None:
            skip = self._large_global_transform_skip("XNOR->NOR")
            if skip:
                return skip
        try:
            before = self._cell_count()
            n = self._transformer.replace_xnor_with_nor(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        budget_note = self._transformer_budget_note()
        self._last_counts["xnor_converted"] = n
        self._last_counts["nor_added"] = n * 4
        if n == 0:
            cleanup = self._transformer.remove_dangling()
            self._last_counts["dangling_removed"] = cleanup
            if cleanup:
                return f"XNOR->NOR: 0 (dangling={cleanup}){budget_note}"
            return f"XNOR->NOR: 0{budget_note}"
        nor_count = len(self.graph.find_cells_by_type("nor"))
        abc_tag = ""
        if output_signal is None and not budget_note:
            abc_tag = self._compress_after_replace("nor_not", before)
        return f"XNOR->NOR: {n}{abc_tag} (NORs now: {nor_count}){budget_note}"

    def replace_or_with_nand_not(self, output_signal: Optional[str] = None) -> str:
        """Convert OR gates to NAND/NOT implementations."""
        self._need_design()
        if output_signal is None:
            skip = self._large_global_transform_skip("OR->NAND")
            if skip:
                return skip
        try:
            before = self._cell_count()
            n = self._transformer.replace_or_with_nand_not(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        budget_note = self._transformer_budget_note()
        self._last_counts["or_converted"] = n
        self._last_counts["nand_added"] = n
        if n == 0:
            if budget_note:
                return f"OR->NAND: 0{budget_note}"
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
        return f"OR->NAND: {n} (NANDs: {nand_count}){budget_note}"

    def replace_xor_with_nor(self, output_signal: Optional[str] = None) -> str:
        """Replace XOR gates with NOR-only implementations."""
        self._need_design()
        if output_signal is None:
            skip = self._large_global_transform_skip("XOR->NOR")
            if skip:
                return skip
        try:
            before = self._cell_count()
            n = self._transformer.replace_xor_with_nor(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        budget_note = self._transformer_budget_note()
        self._last_counts["xor_converted"] = n
        self._last_counts["nor_added"] = n * 4
        if n == 0:
            return f"XOR->NOR: 0{budget_note}"
        nor_count = len(self.graph.find_cells_by_type("nor"))
        abc_tag = ""
        if output_signal is None and not budget_note:
            abc_tag = self._compress_after_replace("nor_not", before)
        return f"XOR->NOR: {n}{abc_tag} (NORs now: {nor_count}){budget_note}"

    def replace_xnor_with_nand(self, output_signal: Optional[str] = None) -> str:
        """Replace XNOR gates with NAND-only implementations."""
        self._need_design()
        if output_signal is None:
            skip = self._large_global_transform_skip("XNOR->NAND")
            if skip:
                return skip
        try:
            before = self._cell_count()
            n = self._transformer.replace_xnor_with_nand(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        budget_note = self._transformer_budget_note()
        self._last_counts["xnor_converted"] = n
        self._last_counts["nand_added"] = n * 5
        if n == 0:
            return f"XNOR->NAND: 0{budget_note}"
        nand_count = len(self.graph.find_cells_by_type("nand"))
        abc_tag = ""
        if output_signal is None and not budget_note:
            abc_tag = self._compress_after_replace("nand_not", before)
        return f"XNOR->NAND: {n}{abc_tag} (NANDs now: {nand_count}){budget_note}"

    def replace_xor_with_and_or_not(self, output_signal: Optional[str] = None) -> str:
        """Replace XOR gates with AND/OR/NOT implementations."""
        self._need_design()
        if output_signal is None:
            skip = self._large_global_transform_skip("XOR->AND/OR/NOT")
            if skip:
                return skip
        try:
            before = self._cell_count()
            n = self._transformer.replace_xor_with_and_or_not(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        budget_note = self._transformer_budget_note()
        self._last_counts["xor_converted"] = n
        if n == 0:
            return f"XOR->AND/OR/NOT: 0{budget_note}"
        and_count = len(self.graph.find_cells_by_type("and"))
        or_count = len(self.graph.find_cells_by_type("or"))
        abc_tag = ""
        if output_signal is None and not budget_note:
            abc_tag = self._compress_after_replace("and_or_not", before)
        return f"XOR->AND/OR/NOT: {n}{abc_tag} (ANDs:{and_count} ORs:{or_count}){budget_note}"

    def replace_xnor_with_and_or_not(self, output_signal: Optional[str] = None) -> str:
        """Replace XNOR gates with AND/OR/NOT implementations."""
        self._need_design()
        if output_signal is None:
            skip = self._large_global_transform_skip("XNOR->AND/OR/NOT")
            if skip:
                return skip
        try:
            before = self._cell_count()
            n = self._transformer.replace_xnor_with_and_or_not(output_signal)
        except KeyError as e:
            return self._fail("NOT_FOUND", str(e))
        budget_note = self._transformer_budget_note()
        self._last_counts["xnor_converted"] = n
        if n == 0:
            return f"XNOR->AND/OR/NOT: 0{budget_note}"
        and_count = len(self.graph.find_cells_by_type("and"))
        or_count = len(self.graph.find_cells_by_type("or"))
        abc_tag = ""
        if output_signal is None and not budget_note:
            abc_tag = self._compress_after_replace("and_or_not", before)
        return f"XNOR->AND/OR/NOT: {n}{abc_tag} (ANDs:{and_count} ORs:{or_count}){budget_note}"

    def full_cleanup_optimize(self) -> str:
        """Run all cleanup+optimization passes iteratively until convergence.

        Passes: constant prop 鈫?Boolean identities 鈫?NOT-NOT collapse 鈫?
                structural merge 鈫?functional merge 鈫?remove dangling 鈫?
                depth optimization.
        Repeats until no pass produces further improvement.
        """
        self._need_design()
        before_depth = self._max_design_depth_value()
        before_cells = self._cell_count()
        total = {"const": 0, "bool": 0, "not_not": 0, "dup": 0, "func": 0, "dangling": 0}
        improved = 0

        for iteration in range(10):
            delta = 0
            # Local cleanups
            c = self._safe_cleanup(collapse_inverted=True, remove_buf=True, reconnect=True)
            delta += sum(int(v) for v in c.values())
            for k in ("const", "bool", "not_not"):
                total[k] += int(c.get(k, 0))
            # Structural merge
            m = self._structural_duplicate_merge_once(preserve_buffers=False)
            delta += m
            total["dup"] += m
            # Tree balancing (inside loop: may expose new merge/cleanup opportunities)
            bal = self._transformer.balance_associative_trees()
            delta += bal
            total["balanced"] = total.get("balanced", 0) + bal
            # AIG merge + functional merge (every iteration)
            am = self._transformer.merge_aig_equivalent_gates(max_support=6, max_depth=16)
            delta += am
            total["aig"] = total.get("aig", 0) + am
            max_sup = 6 if self._cell_count() > 20000 else 8
            fm = self._transformer.merge_functionally_equivalent_gates(max_support=max_sup)
            delta += fm
            total["func"] += fm
            # Dangling removal
            d = self._transformer.remove_dangling()
            delta += d
            total["dangling"] += d
            if delta == 0:
                break
            improved += 1

        # Depth optimization pass
        depth_result = self.optimize_design_depth()

        after_depth = self._max_design_depth_value()
        after_cells = self._cell_count()
        return (
            f"FullOpt: {improved} iter(s). const={total['const']} bool={total['bool']} "
            f"not_not={total['not_not']} dup={total['dup']} func={total['func']} "
            f"dangling={total['dangling']}. Depth {before_depth}->{after_depth} "
            f"cells {before_cells}->{after_cells}"
        )

    def optimize_design_depth(self) -> str:
        """Apply verified local depth/gate cleanup passes across the design."""
        self._need_design()
        before_snapshot = self._cost_snapshot()
        before_snapshot["key"] = self._cost_objective_key("min_depth", before_snapshot)
        before_depth = int(before_snapshot["depth"])
        before_cells = int(before_snapshot["cells"])
        current_style = self._whole_design_style()
        if current_style in {"and_not", "nand_not", "nor_not", "and_or_not"} and before_cells > 20000:
            return (
                f"DesignDepth: unchanged; skipped large style-preserving design "
                f"(style={current_style}, cells={before_cells}, depth={before_depth})"
            )
        cleanup_counts = self._safe_cleanup(collapse_inverted=True)
        dup_msg = self.structural_duplicate_merge()
        merged = int(self._last_counts.get("merged_gates", 0))
        balanced = self._transformer.balance_associative_trees()
        if balanced:
            cleanup_counts = self._safe_cleanup(collapse_inverted=True)
        tried = 0
        improved = 0
        # Slack-aware cone optimization:
        # Critical cones (slack=0) 鈫?min_depth; non-critical (slack>0) 鈫?min_gates
        slack = self._slack_map()
        candidates = self._critical_depth_targets(
            limit=self._dynamic_scale(36),
            max_cone_size=self._dynamic_scale(5000, min_factor=0.5, max_factor=1.5),
        )
        # Sort by (slack asc, depth desc): critical cones optimised first
        candidates.sort(key=lambda x: (
            slack.get(
                self.graph.wire_driver.get(x[2])
                or self.graph.resolve(x[2]),
                9999,
            ),
            -x[0],
        ))
        current_best = self._cost_snapshot()
        current_best["key"] = self._cost_objective_key("min_depth", current_best)
        for _depth, label, target_signal in candidates:
            if self.remaining_request_time() < 30.0:
                break
            old_cone_depth = self._max_depth_value_to_output(target_signal)
            # Choose objective based on slack, escalate for deep critical cones
            driver = self.graph.wire_driver.get(target_signal) or self.graph.resolve(target_signal)
            cone_slack = slack.get(driver, 9999)
            if cone_slack > 0:
                cone_objectives = ["min_gates"]
            else:
                cone_objectives = ["min_depth", "depth_lut"]
                if _depth > 100 and self.remaining_request_time() > 90.0:
                    cone_objectives.append("depth_aggressive")
            best_trial: Optional[NetlistGraph] = None
            best_cost: Optional[dict] = None
            for cone_obj in cone_objectives:
                if self.remaining_request_time() < 30.0:
                    break
                trial_graph = copy.deepcopy(self.graph)
                result = self._optimizer.optimize(
                    trial_graph, target_signal,
                    objective=cone_obj,
                    use_ci=True,
                )
                tried += 1
                if not result.success:
                    continue
                saved_graph = self.graph
                saved_tx = self._transformer
                self.graph = trial_graph
                self._transformer = NetlistTransformer(self.graph)
                try:
                    self._safe_cleanup(collapse_inverted=True)
                    new_cone_depth = self._max_depth_value_to_output(target_signal)
                finally:
                    self.graph = saved_graph
                    self._transformer = saved_tx
                candidate_cost = self._evaluate_graph_cost(trial_graph, "min_depth")
                cone_ok = new_cone_depth <= old_cone_depth
                if not cone_ok:
                    continue
                if best_cost is None or self._candidate_better(best_cost, candidate_cost, "min_depth"):
                    best_trial = trial_graph
                    best_cost = candidate_cost
            if (
                best_trial is not None
                and best_cost is not None
                and self._candidate_better(current_best, best_cost, "min_depth")
            ):
                self._safe_commit_candidate(best_trial)
                current_best = best_cost
                improved += 1
                # Recompute slack after graph change
                slack = self._slack_map()

        # Full-design ABC pass for depth reduction
        current_style = self._whole_design_style()
        abc_result = abc_optimize_full_design(
            self, style=current_style if current_style else None,
            objective="min_depth")
        if "rejected" not in abc_result.lower() and "failed" not in abc_result.lower():
            improved += 1
            # Re-iterate: ABC may have exposed new optimization opportunities
            slack = self._slack_map()
            candidates2 = self._critical_depth_targets(
                limit=self._dynamic_scale(18),
                max_cone_size=self._dynamic_scale(3000, min_factor=0.3, max_factor=1.0),
            )
            candidates2.sort(key=lambda x: (slack.get(
                self.graph.wire_driver.get(x[2]) or self.graph.resolve(x[2]), 9999), -x[0]))
            for _depth, _label, target_signal in candidates2[:8]:
                if self.remaining_request_time() < 30.0:
                    break
                objectives = ["min_depth", "depth_lut"]
                if _depth > 100 and self.remaining_request_time() > 90.0:
                    objectives.append("depth_aggressive")
                best_trial = None
                best_cost = None
                for obj in objectives:
                    if self.remaining_request_time() < 30.0:
                        break
                    trial = copy.deepcopy(self.graph)
                    result = self._optimizer.optimize(
                        trial, target_signal, objective=obj, use_ci=True)
                    if not result.success:
                        continue
                    saved_g, saved_t = self.graph, self._transformer
                    self.graph = trial
                    self._transformer = NetlistTransformer(self.graph)
                    try:
                        self._safe_cleanup(collapse_inverted=True)
                        after_cost = self._cost_snapshot()
                        after_cost["key"] = self._cost_objective_key("min_depth", after_cost)
                    finally:
                        self.graph, self._transformer = saved_g, saved_t
                    if best_cost is None or self._candidate_better(best_cost, after_cost, "min_depth"):
                        best_trial = trial
                        best_cost = after_cost
                if (
                    best_trial is not None
                    and best_cost is not None
                    and self._candidate_better(current_best, best_cost, "min_depth")
                ):
                    self.graph = best_trial
                    self._transformer = NetlistTransformer(self.graph)
                    current_best = best_cost
                    improved += 1

        # Shared subexpression extraction on overlapping cones
        shared_extracted = self._extract_shared_subexpressions()

        after_depth = self._max_design_depth_value()
        after_cells = self._cell_count()
        return (
            f"DesignDepth: cleanup={sum(cleanup_counts.values())} merge={merged} "
            f"balanced={balanced} "
            f"cones {improved}/{tried} shared={shared_extracted}. "
            f"Depth {before_depth}->{after_depth} "
            f"cells {before_cells}->{after_cells}"
        )

# Style-to-ABC-gate-set mapping (consistent with ConeOptimizer._STYLE_ABC_GATE_SET)
# NOTE: ABC -g flag does NOT accept "NOT" as a gate type; inverters are handled internally.
_STYLE_ABC_GATE_SET: dict[str, str] = {
    "nand_not":   "NAND",
    "nor_not":    "NOR",
    "and_not":    "AND",
    "and_or_not": "AND,OR",
}


def _abc_gate_set_for_style(style: Optional[str]) -> str:
    if style:
        gs = _STYLE_ABC_GATE_SET.get(style.strip().lower().replace("-", "_"))
        if gs:
            return gs
    return "AND,OR,NAND,NOR,XOR,XNOR"


def _cost_objective_key(self, objective: str, snapshot: dict) -> tuple:
    """Return a comparable cost key for a snapshot.

    Hard constraints are checked separately.  Smaller tuple is better.
    """
    objective = (objective or "min_depth").strip().lower()
    if objective in {"min_gates", "gate_count", "area"}:
        return (
            int(snapshot.get("cells", 0)),
            int(snapshot.get("depth", 0)),
            int(snapshot.get("max_fanout", 0)),
        )
    if objective in {"min_fanout", "fanout"}:
        return (
            int(snapshot.get("max_fanout", 0)),
            int(snapshot.get("cells", 0)),
            int(snapshot.get("depth", 0)),
        )
    return (
        int(snapshot.get("depth", 0)),
        int(snapshot.get("cells", 0)),
        int(snapshot.get("max_fanout", 0)),
    )


def _cost_snapshot(self) -> dict:
    """Collect the design-level metrics used by candidate optimization."""
    self._need_design()
    return {
        "cells": self._cell_count(),
        "depth": self._max_design_depth_value(),
        "max_fanout": self._max_fanout_value(),
        "style": self._whole_design_style() or "mixed",
    }


def _evaluate_graph_cost(
    self,
    graph: NetlistGraph,
    objective: str = "min_depth",
    style: Optional[str] = None,
    fanout_limit: Optional[int] = None,
) -> dict:
    """Evaluate a candidate graph without committing it."""
    saved_graph = self.graph
    saved_tx = self._transformer
    try:
        self.graph = graph
        self._transformer = NetlistTransformer(self.graph)
        snapshot = self._cost_snapshot()
        style_norm = (style or "").strip().lower().replace("-", "_")
        snapshot["style_ok"] = not style_norm or self._whole_design_style() == style_norm
        snapshot["fanout_ok"] = fanout_limit is None or snapshot["max_fanout"] <= int(fanout_limit)
        snapshot["key"] = self._cost_objective_key(objective, snapshot)
        return snapshot
    finally:
        self.graph = saved_graph
        self._transformer = saved_tx


def _candidate_better(
    self,
    before: dict,
    after: dict,
    objective: str = "min_depth",
    require_improvement: bool = True,
) -> bool:
    """Return True if a candidate improves the selected objective and obeys constraints."""
    if not after.get("style_ok", True) or not after.get("fanout_ok", True):
        return False
    # Depth guard: when minimizing gates, reject any depth regression.
    if objective in ("min_gates", "gate_count", "area"):
        before_d = int(before.get("depth", 0) or 0)
        after_d = int(after.get("depth", 0) or 0)
        if before_d > 0 and after_d > before_d:
            return False
    before_key = before.get("key") or self._cost_objective_key(objective, before)
    after_key = after.get("key") or self._cost_objective_key(objective, after)
    if require_improvement:
        return after_key < before_key
    return after_key <= before_key


def _commit_candidate_graph(self, graph: NetlistGraph) -> None:
    self.graph = graph
    self._transformer = NetlistTransformer(self.graph)


def _graph_has_combinational_cycle(self, graph: Optional[NetlistGraph] = None) -> bool:
    """Return True if the combinational subgraph contains a cycle.

    Excludes edges into DFFs (sequential boundaries) but checks the
    purely combinational portion for feedback loops.
    """
    g = graph if graph is not None else self.graph
    if g is None:
        return False
    dag = nx.DiGraph()
    dag.add_nodes_from(g.G.nodes)
    for u, v in g.G.edges():
        v_nd = g.G.nodes.get(v, {})
        if v_nd.get("gate_type") in DFF_TYPES:
            continue  # sequential boundary
        dag.add_edge(u, v)
    try:
        nx.topological_sort(dag)
        return False
    except nx.NetworkXUnfeasible:
        return True


def _safe_commit_candidate(self, trial_graph: NetlistGraph) -> bool:
    """Commit a candidate graph, but check for combinational cycles first.

    If a cycle is detected, attempts to repair via remove_dangling + cleanup.
    Returns True if commit succeeded, False if cycle could not be resolved.
    """
    if not self._graph_has_combinational_cycle(trial_graph):
        self._commit_candidate_graph(trial_graph)
        return True
    # Try to repair: dangling removal + cleanup may break cycles
    saved_graph = self.graph
    saved_tx = self._transformer
    self.graph = trial_graph
    self._transformer = NetlistTransformer(self.graph)
    try:
        self._transformer.remove_dangling()
        self._transformer.simplify_constant_gates(remove_buf=True)
        if not self._graph_has_combinational_cycle(trial_graph):
            self._commit_candidate_graph(trial_graph)
            return True
    finally:
        if self.graph is trial_graph and self._graph_has_combinational_cycle():
            self.graph = saved_graph
            self._transformer = saved_tx
        elif self.graph is not trial_graph:
            pass  # already committed clean version
    return False
def _critical_depth_targets(
    self,
    limit: int = 30,
    max_cone_size: int = 5000,
) -> list[tuple[int, str, str]]:
    """Return deepest PO and DFF-D boundary cones as optimization targets.

    The target signal is a driven wire that can be passed to ConeOptimizer.
    """
    self._need_design()
    depths, _, _ = self._depths_from_boundaries(include_dffs=True)
    rows: list[tuple[int, str, str]] = []
    seen_targets: set[str] = set()

    def add_target(depth: int, label: str, driver: str) -> None:
        if depth <= 0 or driver not in self.graph.G:
            return
        signal = self.graph.output_wire(driver)
        if signal in seen_targets:
            return
        try:
            cone_size = len(self.graph.extract_cone(signal))
        except Exception:
            return
        if not cone_size or cone_size > max_cone_size:
            return
        seen_targets.add(signal)
        rows.append((int(depth), label, signal))

    for out_name, driver in self.graph.primary_outputs.items():
        add_target(int(depths.get(driver, -1)), f"PO:{out_name}", driver)

    for dff, nd in self.graph.G.nodes(data=True):
        if nd.get("ntype") != "cell" or nd.get("gate_type") not in DFF_TYPES:
            continue
        for driver, _dst, edge in self.graph.G.in_edges(dff, data=True):
            port = str(edge.get("port", "")).upper().lstrip("\\")
            if port in {"D", "DATA", "I0"}:
                add_target(int(depths.get(driver, -1)), f"DFF-D:{dff}", driver)

    rows.sort(reverse=True)
    return rows[: max(1, int(limit))]


def optimize_cone(self, output_signal: str,
                  max_depth: Optional[int] = None,
                  objective: str = "min_gates",
                  style: Optional[str] = None) -> str:
    """ABC-optimize a single output cone with optional style and depth constraints."""
    self._need_design()
    style_norm = (style or "").strip().lower().replace("-", "_") or None
    try:
        out_name = output_signal
        old_cone_depth = self._max_depth_value_to_output(out_name)
        old_cells = self._cell_count(self.graph.extract_cone(out_name))
    except KeyError as e:
        return self._fail("NOT_FOUND", str(e))
    before_cost = self._cost_snapshot()
    before_cost["key"] = self._cost_objective_key(objective, before_cost)
    old_global_depth = int(before_cost["depth"])

    if (
        objective in ("min_depth", "depth", "depth_lut", "depth_aggressive")
        and style_norm
        and old_cells > 20000
    ):
        style_ok = self._cone_style_ok(output_signal, style_norm)
        return (
            f"Cone {output_signal}: unchanged; huge style-preserving depth cone skipped "
            f"(cone_gates={old_cells}, depth={old_cone_depth}, style_ok={style_ok})"
        )

    # Binary search for minimum depth when objective is min_depth
    if objective in ("min_depth", "depth", "depth_lut", "depth_aggressive") and max_depth is None and old_cone_depth >= 2:
        return globals()["_optimize_cone_binary_depth"](
            self, output_signal, old_cone_depth, style_norm,
            before_cost, old_global_depth, old_cells)

    trial_graph = copy.deepcopy(self.graph)
    result = self._optimizer.optimize(
        trial_graph, output_signal,
        max_depth=max_depth,
        objective=objective,
        style=style_norm,
    )
    if not result.success:
        # If ABC with style-specific gates failed, fall back to default gate set
        # and try post-ABC remap
        if style_norm:
            trial_graph2 = copy.deepcopy(self.graph)
            result2 = self._optimizer.optimize(
                trial_graph2, output_signal,
                max_depth=max_depth,
                objective=objective,
                style=None,  # default gate set
            )
            if result2.success:
                remap_depth, remap_cells, style_ok = self._remap_trial_cone_inplace(
                    trial_graph2, output_signal, style_norm)
                if style_ok:
                    # Compute global depth on trial_graph2
                    saved_g = self.graph
                    saved_t = self._transformer
                    self.graph = trial_graph2
                    self._transformer = NetlistTransformer(self.graph)
                    try:
                        self._safe_cleanup(collapse_inverted=True)
                        trial_global_depth = self._max_design_depth_value()
                        trial_cost = self._cost_snapshot()
                        trial_cost["key"] = self._cost_objective_key(objective, trial_cost)
                    finally:
                        self.graph = saved_g
                        self._transformer = saved_t
                    if (remap_depth <= old_cone_depth and
                        self._candidate_better(before_cost, trial_cost, objective)):
                        self.graph = trial_graph2
                        self._transformer = NetlistTransformer(self.graph)
                        self._safe_cleanup(collapse_inverted=True)
                        new_global = self._max_design_depth_value()
                        new_cells_total = self._cell_count()
                        return (
                            f"Cone {output_signal}: ABC+remap {old_cells}->{remap_cells} gates, "
                            f"depth {old_cone_depth}->{remap_depth}. "
                            f"Global depth {old_global_depth}->{new_global}, "
                            f"cells {old_cells}->{new_cells_total}"
                        )
                return (
                    f"Cone {output_signal}: unchanged; "
                    f"candidate gates {result2.before_gates}->{result2.after_gates}, "
                    f"style_ok={style_ok}"
                )
        return f"Cone {output_signal}: unchanged; {result.reason}"

    # ABC succeeded with the chosen gate set
    saved_graph = self.graph
    saved_tx = self._transformer
    self.graph = trial_graph
    self._transformer = NetlistTransformer(self.graph)
    try:
        self._safe_cleanup(collapse_inverted=True)
        new_cone_depth = self._max_depth_value_to_output(output_signal)
        new_global_depth = self._max_design_depth_value()
        new_cells_total = self._cell_count()
        new_cone_cells = self._cell_count(self.graph.extract_cone(output_signal))
        trial_cost = self._cost_snapshot()
        trial_cost["key"] = self._cost_objective_key(objective, trial_cost)
    finally:
        self.graph = saved_graph
        self._transformer = saved_tx

    if new_cone_depth <= old_cone_depth and self._candidate_better(before_cost, trial_cost, objective):
        self.graph = trial_graph
        self._transformer = NetlistTransformer(self.graph)
        return (
            f"Cone {output_signal}: optimized {old_cells}->{new_cone_cells} gates, "
            f"depth {old_cone_depth}->{new_cone_depth}. "
            f"Global depth {old_global_depth}->{new_global_depth}, "
            f"cells {new_cells_total}"
        )
    # P4: auto-retry with opposite objective before giving up
    retry_obj = "min_depth" if objective == "min_gates" else "min_gates"
    trial_graph2 = copy.deepcopy(saved_graph)
    result2 = self._optimizer.optimize(
        trial_graph2, output_signal,
        max_depth=max_depth,
        objective=retry_obj,
        style=style_norm,
    )
    if result2.success:
        saved_g = self.graph
        saved_t = self._transformer
        self.graph = trial_graph2
        self._transformer = NetlistTransformer(self.graph)
        try:
            self._safe_cleanup(collapse_inverted=True)
            retry_depth = self._max_depth_value_to_output(output_signal)
            retry_global = self._max_design_depth_value()
            retry_cost = self._cost_snapshot()
            retry_cost["key"] = self._cost_objective_key(objective, retry_cost)
        finally:
            self.graph = saved_g
            self._transformer = saved_t
        if (retry_depth <= old_cone_depth
                and self._candidate_better(before_cost, retry_cost, objective)):
            self.graph = trial_graph2
            self._transformer = NetlistTransformer(self.graph)
            return (
                f"Cone {output_signal}: retry[{retry_obj}] optimized "
                f"{old_cells}->{self._cell_count(self.graph.extract_cone(output_signal))} gates, "
                f"depth {old_cone_depth}->{retry_depth}. "
                f"Global depth {old_global_depth}->{retry_global}"
            )

    return (
        f"Cone {output_signal}: rejected; "
        f"candidate depth {old_cone_depth}->{new_cone_depth} ({old_cells}->{new_cone_cells} gates) "
        f"does not improve global score ({old_global_depth},{old_cells}) vs ({new_global_depth},{new_cells_total})"
    )



def _optimize_cone_binary_depth(
    self,
    output_signal: str,
    old_cone_depth: int,
    style_norm,
    before_cost: dict,
    old_global_depth: int,
    old_cells: int,
) -> str:
    """Binary-search a smaller legal cone depth under the request deadline."""
    lo, hi = max(1, old_cone_depth // 2), old_cone_depth
    best_graph = None
    best_depth = old_cone_depth
    best_result = None

    cone_size = self._cell_count(self.graph.extract_cone(output_signal))
    max_iter = 1 if cone_size > 20000 else (2 if cone_size > 5000 else (4 if cone_size > 500 else 6))
    depth_obj = "depth_aggressive" if old_cone_depth > 100 else "min_depth"

    old_timeout = getattr(self._optimizer, "cone_timeout_sec", None)
    try:
        if cone_size > 50000:
            remaining = self.remaining_request_time()
            if remaining != float("inf") and remaining < 30.0:
                return (
                    f"Cone {output_signal}: skipped huge-cone depth retry; "
                    f"remaining_request_time={remaining:.1f}s cone_size={cone_size}"
                )
            if old_timeout is not None and remaining != float("inf"):
                self._optimizer.cone_timeout_sec = max(
                    2, min(int(old_timeout), int(remaining - 30))
                )
            trial_graph = copy.deepcopy(self.graph)
            result = self._optimizer.optimize(
                trial_graph,
                output_signal,
                max_depth=None,
                objective=depth_obj,
                style=style_norm,
            )
            if result.success:
                best_graph = trial_graph
                best_result = result
            else:
                return (
                    f"Cone {output_signal}: huge-cone single depth retry failed; "
                    f"{result.reason}"
                )
        for _iteration in range(max_iter):
            remaining = self.remaining_request_time()
            if best_graph is not None or lo >= hi or remaining < 30.0:
                break
            estimated_single = max(8.0, min(90.0, cone_size / 1000.0))
            if remaining != float("inf") and estimated_single > remaining / 2.0:
                break
            mid = (lo + hi) // 2
            if old_timeout is not None and remaining != float("inf"):
                self._optimizer.cone_timeout_sec = max(
                    2, min(int(old_timeout), int(remaining - 30))
                )
            trial_graph = copy.deepcopy(self.graph)
            result = self._optimizer.optimize(
                trial_graph,
                output_signal,
                max_depth=mid,
                objective=depth_obj,
                style=style_norm,
            )
            if result.success:
                best_graph = trial_graph
                best_depth = mid
                best_result = result
                hi = mid
            else:
                lo = mid + 1
    finally:
        if old_timeout is not None:
            self._optimizer.cone_timeout_sec = old_timeout

    if best_graph is None or best_result is None:
        return (
            f"Cone {output_signal}: binary search failed; "
            f"cannot prove depth < {old_cone_depth}"
        )

    saved_g = self.graph
    saved_t = self._transformer
    self.graph = best_graph
    self._transformer = NetlistTransformer(self.graph)
    try:
        self._safe_cleanup(collapse_inverted=True)
        new_cone_depth = self._max_depth_value_to_output(output_signal)
        new_global_depth = self._max_design_depth_value()
        new_cone_cells = self._cell_count(self.graph.extract_cone(output_signal))
        new_total_cells = self._cell_count()
        trial_cost = self._cost_snapshot()
        trial_cost["key"] = self._cost_objective_key("min_depth", trial_cost)
        style_ok = not style_norm or self._cone_style_ok(output_signal, style_norm)
    finally:
        self.graph = saved_g
        self._transformer = saved_t

    if (
        style_ok
        and new_cone_depth <= old_cone_depth
        and new_cone_cells <= max(int(old_cells * 1.1), old_cells + 16)
        and self._candidate_better(before_cost, trial_cost, "min_depth")
    ):
        self.graph = best_graph
        self._transformer = NetlistTransformer(self.graph)
        return (
            f"Cone {output_signal}: binary depth {old_cone_depth}->{new_cone_depth} "
            f"({old_cells}->{new_cone_cells} cone gates). "
            f"Global depth {old_global_depth}->{new_global_depth}, cells {new_total_cells}"
        )
    return (
        f"Cone {output_signal}: binary search rejected; "
        f"depth {old_cone_depth}->{new_cone_depth}, gates {old_cells}->{new_cone_cells}, "
        f"style_ok={style_ok}"
    )


def _apply_remap_cone_inplace(self, output_signal: str, style: str) -> None:
    """Remap the fanin cone of output_signal to the target primitive style in-place.

    Calls each transformer method exactly once with cone_output to restrict scope.
    """
    style = style.strip().lower().replace("-", "_")
    cone_cells = {
        nid for nid in self.graph.extract_cone(output_signal)
        if self.graph.G.nodes.get(nid, {}).get("gate_type") not in DFF_TYPES
    }
    if not cone_cells:
        return

    tx = self._transformer
    co = output_signal  # cone_output parameter restricts scope

    if style == "nand_not":
        tx.replace_xnor_with_nand(co)
        tx.replace_xor_with_nand(co)
        tx.replace_nor_with_nand_not(co)
        tx.replace_or_with_nand_not(co)
        tx.replace_and_with_nand_not(co)
        tx.replace_buf_with_not_not(co)
    elif style == "nor_not":
        tx.replace_xor_with_nor(co)
        tx.replace_xnor_with_nor(co)
        tx.replace_nand_with_nor_not(co)
        tx.replace_and_with_nor_not(co)
        tx.replace_or_with_nor_not(co)
        tx.replace_buf_with_not_not(co)
    elif style == "and_not":
        tx.replace_xnor_with_and_or_not(co)
        tx.replace_xor_with_and_or_not(co)
        tx.replace_nand_with_and_not(co)
        tx.replace_nor_with_and_not(co)
        tx.replace_or_with_and_not(co)
        tx.replace_buf_with_not_not(co)
    elif style == "and_or_not":
        tx.replace_xnor_with_and_or_not(co)
        tx.replace_xor_with_and_or_not(co)
        tx.replace_nand_with_and_not(co)
        tx.replace_nor_with_and_not(co)
        tx.replace_buf_with_not_not(co)


def remap_cone(self, output_signal: str, style: str) -> str:
    """Remap a single output cone to the target primitive style."""
    self._need_design()
    style = style.strip().lower().replace("-", "_")
    if style not in _STYLE_ABC_GATE_SET:
        return f"RemapCone {output_signal}: unknown style '{style}'."

    try:
        old_cone_cells = len(self.graph.extract_cone(output_signal))
        old_cone_depth = self._max_depth_value_to_output(output_signal)
    except KeyError as e:
        return self._fail("NOT_FOUND", str(e))

    trial_graph = copy.deepcopy(self.graph)
    saved_graph = self.graph
    saved_tx = self._transformer
    best_depth, best_cells, best_style_ok = self._remap_trial_cone_inplace(
        trial_graph, output_signal, style)

    if not best_style_ok:
        return f"RemapCone {output_signal}: could not satisfy style '{style}'."

    # Also try ABC-then-remap for potentially better results
    abc_graph = copy.deepcopy(self.graph)
    abc_result = self._optimizer.optimize(
        abc_graph, output_signal,
        objective="min_gates",
        style=style,  # use target gate set directly
    )
    if abc_result.success:
        abc_depth, abc_cells, abc_style_ok = self._remap_trial_cone_inplace(
            abc_graph, output_signal, style)
        if abc_style_ok and abc_cells < best_cells:
            best_depth, best_cells = abc_depth, abc_cells
            self.graph = abc_graph
            self._transformer = NetlistTransformer(self.graph)
            self._safe_cleanup(collapse_inverted=True)
            return (
                f"RemapCone {output_signal}: ABC+remap {old_cone_cells}->{self._cell_count(self.graph.extract_cone(output_signal))} gates, "
                f"depth {old_cone_depth}->{best_depth}. style={style}"
            )

    # Fall back to template remap result
    self.graph = trial_graph
    self._transformer = NetlistTransformer(self.graph)
    self._safe_cleanup(collapse_inverted=True)
    new_cone_cells = self._cell_count(self.graph.extract_cone(output_signal))
    new_cone_depth = self._max_depth_value_to_output(output_signal)
    return (
        f"RemapCone {output_signal}: {old_cone_cells}->{new_cone_cells} gates, "
        f"depth {old_cone_depth}->{new_cone_depth}. style={style}"
    )


def abc_optimize_full_design(self, style: Optional[str] = None,
                              objective: str = "min_depth") -> str:
    """Run ABC optimization on the entire design with optional style gate set."""
    self._need_design()
    gate_set = _abc_gate_set_for_style(style)
    objective = (objective or "min_depth").strip().lower()
    style_norm = (style or "").strip().lower().replace("-", "_") or None
    before = self._cost_snapshot()
    before["key"] = self._cost_objective_key(objective, before)
    before_depth = int(before["depth"])
    before_cells = int(before["cells"])

    with tempfile.TemporaryDirectory(dir=safe_temp_dir()) as tmp:
        vin = os.path.join(tmp, "full_in.v")
        self.writer.write(self.graph, vin)

        style_norm = (style or "").strip().lower().replace("-", "_")
        is_and_not = (style_norm == "and_not")
        if before_cells > 50000:
            variants = ("remap", "area") if objective in {"min_gates", "gate_count", "area"} else ("depth", "remap")
        elif before_cells > 20000:
            variants = ("remap", "area", "aggressive") if objective in {"min_gates", "gate_count", "area"} else ("depth", "remap", "aggressive")
        elif objective in {"min_gates", "gate_count", "area"}:
            variants = ("aig_native", "remap", "area") if is_and_not else ("remap", "area", "aggressive")
        elif objective in {"min_depth", "depth"}:
            variants = ("depth", "remap", "aggressive") if is_and_not else ("depth", "aggressive", "iterative", "remap")
        else:
            variants = ("remap", "aig_native", "area") if is_and_not else ("remap", "area", "aggressive", "default")

        best: Optional[dict] = None
        errors: list[str] = []
        top_name = self.graph.module_name or "top"
        for idx, variant in enumerate(variants):
            abc_timeout = self._budget_timeout(
                min(self.yosys.default_timeout_sec, 180),
                reserve=6.0,
            )
            if abc_timeout is None:
                errors.append(f"{variant}: time budget exhausted before ABC")
                break
            vout = os.path.join(tmp, f"full_out_{idx}_{variant}.v")
            vjson = os.path.join(tmp, f"full_out_{idx}_{variant}.json")
            try:
                self.yosys.abc_optimize_with_gates(
                    vin,
                    vout,
                    gate_set,
                    top=top_name,
                    objective=objective,
                    variant=variant,
                    timeout=abc_timeout,
                )
                equiv_timeout = self._budget_timeout(
                    min(self._equiv_timeout_sec, 120),
                    reserve=4.0,
                )
                if equiv_timeout is None:
                    errors.append(f"{variant}: time budget exhausted before equivalence")
                    break
                result = self.yosys.check_equiv(
                    vin,
                    vout,
                    gold_top=top_name,
                    gate_top=top_name,
                    timeout=equiv_timeout,
                )
                self._record_cec_result(result)
            except RuntimeError as e:
                errors.append(f"{variant}: {e}")
                continue

            if result.status == "FAIL":
                errors.append(f"{variant}: equivalence failed")
                continue
            if result.status != "PASS":
                errors.append(f"{variant}: equiv {result.status}")
                continue

            try:
                parse_timeout = self._budget_timeout(self.yosys.default_timeout_sec, reserve=2.0)
                if parse_timeout is None:
                    errors.append(f"{variant}: time budget exhausted before parse")
                    break
                self.yosys.verilog_to_json(vout, vjson, top=top_name, timeout=parse_timeout)
                candidate_graph = NetlistGraph.from_yosys_json(vjson)
            except Exception as e:
                errors.append(f"{variant}: parse {e}")
                continue

            candidate_cost = self._evaluate_graph_cost(
                candidate_graph,
                objective=objective,
                style=style_norm,
            )
            if not self._candidate_better(before, candidate_cost, objective):
                errors.append(
                    f"{variant}: no improvement depth={candidate_cost['depth']} cells={candidate_cost['cells']}"
                )
                continue
            row = {
                "graph": candidate_graph,
                "variant": variant,
                "cost": candidate_cost,
            }
            if best is None or candidate_cost["key"] < best["cost"]["key"]:
                best = row

        if best is None:
            detail = "; ".join(errors[:4]) if errors else "no candidate"
            return (
                f"ABC full-design: rejected. "
                f"baseline depth={before_depth} cells={before_cells}. {detail}"
            )

        self._commit_candidate_graph(best["graph"])
        self._safe_cleanup(collapse_inverted=(style_norm is None), remove_buf=(style_norm is None))
        final = self._cost_snapshot()
        return (
            f"ABC full-design[{best['variant']}]: cells {before_cells}->{final['cells']}, "
            f"depth {before_depth}->{final['depth']}"
        )


def remap_design(self, style: str) -> str:
        """Perform deterministic whole-design technology remapping.

        Tries ABC-first (direct synthesis with target gate library) before
        falling back to template-based gate replacement.
        """
        self._need_design()
        style = style.strip().lower().replace("-", "_")
        if style not in {"nand_not", "and_or_not", "and_not", "nor_not"}:
            return f"Applied no remap because target style '{style}' is unknown."

        before_graph = self.graph
        before_transformer = self._transformer
        before_counts = dict(self._last_counts)
        before_cells = self._cell_count()
        before_depth = self._max_design_depth_value()
        best: Optional[dict] = None

        # -- ABC-first path: try direct ABC synthesis with target gate library --
        abc_first_graph = self._try_abc_remap(before_graph, style)
        if abc_first_graph is not None:
            abc_first_graph_style = ""
            saved = self.graph
            self.graph = abc_first_graph
            self._transformer = NetlistTransformer(self.graph)
            try:
                for _ in range(4):
                    delta = self._safe_cleanup(collapse_inverted=False, remove_buf=False, reconnect=True)
                    merged = self._structural_duplicate_merge_once(preserve_buffers=False)
                    if sum(int(v) for v in delta.values()) + merged == 0:
                        break
                abc_first_style = self._whole_design_style()
            finally:
                self.graph = saved
                self._transformer = before_transformer
            if abc_first_style == style:
                abc_cells = sum(1 for _n, d in abc_first_graph.G.nodes(data=True)
                                if d.get("ntype") == "cell")
                abc_depth = -1
                saved = self.graph
                self.graph = abc_first_graph
                self._transformer = NetlistTransformer(self.graph)
                try:
                    abc_depth = self._max_design_depth_value()
                finally:
                    self.graph = saved
                    self._transformer = before_transformer
                best = {
                    "graph": abc_first_graph,
                    "detail": f"abc-first",
                    "after_cells": abc_cells,
                    "after_depth": abc_depth,
                    "hist": self._style_histogram_text(style) if self.graph is abc_first_graph else "",
                    "cleanup_total": 0,
                    "merged_total": 0,
                    "pre_cleanup": False,
                }

        # -- Template-based path (with pre_cleanup variants) --
        for pre_cleanup in (False, True):
            trial_graph = copy.deepcopy(before_graph)
            self.graph = trial_graph
            self._transformer = NetlistTransformer(self.graph)
            try:
                prefix = ""
                if pre_cleanup:
                    pre = self._safe_cleanup(
                        collapse_inverted=False,
                        remove_buf=False,
                        reconnect=True,
                    )
                    pre_merged = self._structural_duplicate_merge_once(
                        preserve_buffers=self._preserve_buffers
                    )
                    prefix = f"preclean={sum(pre.values())}+merge{pre_merged}; "
                detail = prefix + self._apply_remap_design_inplace(style)
                cleanup_total = 0
                merged_total = 0
                for _ in range(6):
                    delta = self._safe_cleanup(
                        collapse_inverted=False,
                        remove_buf=False,
                        reconnect=True,
                    )
                    merged = self._structural_duplicate_merge_once(
                        preserve_buffers=self._preserve_buffers
                    )
                    cleanup_total += sum(int(v) for v in delta.values())
                    merged_total += merged
                    if sum(int(v) for v in delta.values()) + merged == 0:
                        break
                style_ok = self._whole_design_style() == style
                if style_ok:
                    abc_graph = self._try_abc_remap(trial_graph, style)
                    if abc_graph is not None:
                        abc_cells = sum(1 for _n, d in abc_graph.G.nodes(data=True)
                                        if d.get("ntype") == "cell")
                        if abc_cells < self._cell_count():
                            trial_graph = abc_graph
                            self.graph = trial_graph
                            self._transformer = NetlistTransformer(self.graph)
                            detail = detail + " +abc"
                after_cells = self._cell_count()
                after_depth = self._max_design_depth_value()
                hist = self._style_histogram_text(style)
            finally:
                self.graph = before_graph
                self._transformer = before_transformer

            if not style_ok:
                continue
            candidate = {
                "graph": trial_graph,
                "detail": detail,
                "after_cells": after_cells,
                "after_depth": after_depth,
                "hist": hist,
                "cleanup_total": cleanup_total,
                "merged_total": merged_total,
                "pre_cleanup": pre_cleanup,
            }
            if best is None or (
                after_cells,
                after_depth,
                int(pre_cleanup),
            ) < (
                int(best["after_cells"]),
                int(best["after_depth"]),
                int(bool(best["pre_cleanup"])),
            ):
                best = candidate

        if best is None:
            self._last_counts = before_counts
            return f"Remap {style}: rejected; candidate did not satisfy target style."

        # Quality guard: reject if result is significantly worse than original
        after_cells = int(best["after_cells"])
        after_depth = int(best["after_depth"])
        cells_inflation = (after_cells - before_cells) / max(before_cells, 1)
        depth_inflation = (after_depth - before_depth) / max(before_depth, 1)
        # Stricter depth guard: depth is the primary cost metric,
        # any significant depth increase is unacceptable
        if False and (cells_inflation > 0.3 or depth_inflation > 0.2):
            # Full-design remap rejected 鈥?try progressive cone-level remap
            self.graph = before_graph
            self._transformer = before_transformer
            remapped, saved = self._progressive_cone_remap(style)
            if remapped > 0:
                after_cells_cone = self._cell_count()
                after_depth_cone = self._max_design_depth_value()
                return (
                    f"Remap {style}: full-design rejected (cells {before_cells}->{after_cells} "
                    f"{cells_inflation:+.0%}); "
                    f"progressive cone remap: {remapped} cones, {saved} gates saved. "
                    f"Final: {before_cells}->{after_cells_cone} cells, "
                    f"depth {before_depth}->{after_depth_cone}"
                )
            self._last_counts = before_counts
            return (
                f"Remap {style}: REJECTED. Best candidate cells {before_cells}->{after_cells} "
                f"({cells_inflation:+.0%}) depth {before_depth}->{after_depth} "
                f"({depth_inflation:+.0%}); regression too large, keeping original design."
            )

        self.graph = best["graph"]
        self._transformer = NetlistTransformer(self.graph)
        self._last_counts = before_counts
        after_cells = int(best["after_cells"])
        after_depth = int(best["after_depth"])
        self._last_counts["remap_cells_delta"] = max(0, after_cells - before_cells)
        self._last_counts["remap_applied"] = 1
        delta = after_cells - before_cells
        warning = ""
        if cells_inflation > 0.2 or depth_inflation > 0.15:
            warning = (
                f" hard-style cost warning cells={cells_inflation:+.0%} "
                f"depth={depth_inflation:+.0%};"
            )
        return (
            f"Remap {style}: {best['detail']}. Cells {before_cells}->{after_cells} "
            f"({delta:+d}); depth {before_depth}->{after_depth};{warning} {best['hist']}"
        )

def _apply_remap_design_inplace(self, style: str) -> str:

        if style == "nand_not":
            xnor_n = self._transformer.replace_xnor_with_nand()
            xor_n = self._transformer.replace_xor_with_nand()
            nor_n = self._transformer.replace_nor_with_nand_not()
            or_n = self._transformer.replace_or_with_nand_not()
            and_n = self._transformer.replace_and_with_nand_not()
            buf_n = self._transformer.replace_buf_with_not_not()
            return f"XNOR={xnor_n} XOR={xor_n} NOR={nor_n} OR={or_n} AND={and_n} BUF={buf_n}"
        if style == "and_or_not":
            xnor_n = self._transformer.replace_xnor_with_and_or_not()
            xor_n = self._transformer.replace_xor_with_and_or_not()
            nand_n = self._transformer.replace_nand_with_and_not()
            nor_n = self._transformer.replace_nor_with_and_not()
            buf_n = self._transformer.replace_buf_with_not_not()
            return f"XNOR={xnor_n} XOR={xor_n} NAND={nand_n} NOR={nor_n} BUF={buf_n}"
        if style == "and_not":
            xnor_n = self._transformer.replace_xnor_with_and_or_not()
            xor_n = self._transformer.replace_xor_with_and_or_not()
            nand_n = self._transformer.replace_nand_with_and_not()
            nor_n = self._transformer.replace_nor_with_and_not()
            or_n = self._transformer.replace_or_with_and_not()
            buf_n = self._transformer.replace_buf_with_not_not()
            return f"XNOR={xnor_n} XOR={xor_n} NAND={nand_n} NOR={nor_n} OR={or_n} BUF={buf_n}"
        if style == "nor_not":
            xor_n = self._transformer.replace_xor_with_nor()
            xnor_n = self._transformer.replace_xnor_with_nor()
            nand_n = self._transformer.replace_nand_with_nor_not()
            and_n = self._transformer.replace_and_with_nor_not()
            or_n = self._transformer.replace_or_with_nor_not()
            buf_n = self._transformer.replace_buf_with_not_not()
            return f"XOR={xor_n} XNOR={xnor_n} NAND={nand_n} AND={and_n} OR={or_n} BUF={buf_n}"
        return "unknown"

def check_equiv(self, path_a: str, path_b: str) -> str:
        """Check functional equivalence between two Verilog files."""
        timeout = self._budget_timeout(self._equiv_timeout_sec, reserve=2.0)
        if timeout is None:
            return self._time_budget_exhausted("check_equiv")
        result = self.yosys.check_equiv(
            path_a,
            path_b,
            gold_top="top",
            gate_top="top",
            timeout=timeout,
        )
        self._record_cec_result(result)
        return self._format_equiv_result(
            result,
            pass_text=f"EQUIV: {path_a} == {path_b}",
            fail_text=f"NOT_EQUIV: {path_a} != {path_b}",
            timeout_text=f"UNKNOWN[TIMEOUT]: full CEC did not finish within {self._equiv_timeout_sec}s",
        )

def check_original_equiv(self) -> str:

        """Check the current design against the originally loaded Verilog."""
        self._safe_cleanup(collapse_inverted=False, remove_buf=False, reconnect=True)
        result = self._check_original_equiv_result()
        self._record_cec_result(result)
        if result.status not in {"PASS", "FAIL"}:
            return self._check_original_equiv_by_output_cones(result)
        return self._format_equiv_result(
            result,
            pass_text="EQUIV: current == original",
            fail_text="NOT_EQUIV: current != original",
            timeout_text=(
                "UNKNOWN[TIMEOUT]: full CEC current vs original did not "
                f"finish within {self._equiv_timeout_sec}s"
            ),
        )

def check_original_equiv_robust(self) -> str:

        """Full CEC with per-output cone fallback for inconclusive runs."""
        self._safe_cleanup(collapse_inverted=False, remove_buf=False, reconnect=True)
        result = self._check_original_equiv_result()
        self._record_cec_result(result)
        if result.status in {"PASS", "FAIL"}:
            return self._format_equiv_result(
                result,
                pass_text="EQUIV: current == original",
                fail_text="NOT_EQUIV: current != original",
                timeout_text=(
                    "UNKNOWN[TIMEOUT]: full CEC current vs original did not "
                    f"finish within {self._equiv_timeout_sec}s"
                ),
            )
        return self._check_original_equiv_by_output_cones(result)

def _check_original_equiv_result(self) -> EquivResult:

        self._need_design()
        if not self._original_path:
            return EquivResult("ERROR", "no original design path recorded", "yosys-equiv", 0.0)
        fd, temp_v = tempfile.mkstemp(suffix="_current_equiv.v", dir=safe_temp_dir())
        os.close(fd)
        try:
            self.writer.write(self.graph, temp_v)
            top = self.graph.module_name or "top"
            timeout = self._budget_timeout(self._equiv_timeout_sec, reserve=2.0)
            if timeout is None:
                return EquivResult("TIMEOUT", "request time budget exhausted", "budget", 0.0)
            return self.yosys.check_equiv(
                self._original_path,
                temp_v,
                gold_top=top,
                gate_top=top,
                timeout=timeout,
            )
        except Exception as e:
            return EquivResult("ERROR", str(e), "yosys-equiv", 0.0)
        finally:
            if os.path.exists(temp_v):
                os.unlink(temp_v)

def _format_equiv_result(

        self,
        result: EquivResult,
        pass_text: str,
        fail_text: str,
        timeout_text: str,
    ) -> str:
        if result.status == "PASS":
            return pass_text
        if result.status == "FAIL":
            detail = f"\n{result.message}" if result.message else ""
            return f"{fail_text}{detail}".rstrip()
        if result.status == "TIMEOUT":
            return timeout_text
        if result.status == "UNKNOWN":
            detail = f": {result.message}" if result.message else ""
            return f"UNKNOWN[CEC]{detail}"
        detail = f": {result.message}" if result.message else ""
        return f"ERROR[CEC]{detail}"

def _record_cec_result(
    self,
    result: EquivResult,
    cone: bool = False,
    aggregate: bool = True,
) -> None:

        prefix = "cone_cec" if cone else "cec"
        if aggregate:
            status_suffix = {
                "PASS": f"{prefix}_pass",
                "FAIL": f"{prefix}_fail",
                "TIMEOUT": f"{prefix}_timeout",
                "UNKNOWN": f"{prefix}_unknown",
                "ERROR": f"{prefix}_error",
            }.get(result.status, f"{prefix}_unknown")
            self._cec_stats[status_suffix] = self._cec_stats.get(status_suffix, 0) + 1
        if cone:
            engine = (result.engine or "").lower()
            engine_prefix = ""
            if "abc" in engine:
                engine_prefix = "cone_cec_abc"
            elif "yosys" in engine:
                engine_prefix = "cone_cec_yosys"
            if engine_prefix:
                engine_key = {
                    "PASS": f"{engine_prefix}_pass",
                    "FAIL": f"{engine_prefix}_fail",
                    "TIMEOUT": f"{engine_prefix}_timeout",
                    "UNKNOWN": f"{engine_prefix}_unknown",
                    "ERROR": f"{engine_prefix}_error",
                }.get(result.status, f"{engine_prefix}_unknown")
                self._cec_stats[engine_key] = self._cec_stats.get(engine_key, 0) + 1

def _check_original_equiv_by_output_cones(self, full_result: EquivResult) -> str:

        self._need_design()
        if not self._original_path:
            return "ERROR[CEC]: no original design path recorded"
        try:
            original_graph = self._load_graph_for_verification(self._original_path)
        except Exception as e:
            return f"ERROR[CEC]: failed to load original graph for cone fallback: {e}"

        gate_graph = self.graph
        fd, current_v = tempfile.mkstemp(suffix="_current_cone_norm.v", dir=safe_temp_dir())
        os.close(fd)
        try:
            self.writer.write(self.graph, current_v)
            gate_graph = NetlistGraph.from_verilog(current_v)
        except Exception:
            gate_graph = self.graph
        finally:
            if os.path.exists(current_v):
                os.unlink(current_v)

        targets = self._verification_targets(original_graph, gate_graph)
        if not targets:
            return "UNKNOWN[CEC]: no observable outputs available for cone fallback"

        local_deadline = time.monotonic() + max(1, self._robust_total_timeout_sec)
        request_deadline = self._request_deadline
        deadline = min(local_deadline, request_deadline) if request_deadline is not None else local_deadline
        pass_outputs: list[str] = []
        fail_outputs: list[str] = []
        timeout_outputs: list[str] = []
        unknown_outputs: list[str] = []
        error_outputs: list[str] = []
        first_fail_detail = ""

        with tempfile.TemporaryDirectory(dir=safe_temp_dir()) as tmp:
            for output_name, gold_signal, gate_signal in targets:
                if time.monotonic() >= deadline:
                    done = set(pass_outputs + fail_outputs + timeout_outputs + unknown_outputs + error_outputs)
                    timeout_outputs.extend(name for name, _gold, _gate in targets if name not in done)
                    break
                try:
                    if output_name.startswith("__dff_d_") and self._target_structurally_identical(
                        original_graph,
                        gate_graph,
                        gold_signal,
                        gate_signal,
                    ):
                        result = EquivResult("PASS", "structural cone match", "structural-cec-skip", 0.0)
                        self._record_cec_result(result, cone=True)
                        pass_outputs.append(output_name)
                        continue
                    gold_cone = self._build_verification_cone_graph(original_graph, gold_signal, output_name)
                    gate_cone = self._build_verification_cone_graph(gate_graph, gate_signal, output_name)
                    self._align_cone_inputs(gold_cone, gate_cone)
                    safe = re.sub(r"[^A-Za-z0-9_$]+", "_", output_name).strip("_") or "out"
                    gold_v = os.path.join(tmp, f"{safe}_gold.v")
                    gate_v = os.path.join(tmp, f"{safe}_gate.v")
                    self.writer.write(gold_cone, gold_v)
                    self.writer.write(gate_cone, gate_v)
                    # Tiered cone timeout based on cone cell count. Try ABC
                    # CEC first because AIG-native cec is often faster on
                    # large combinational cones; fall back to Yosys SAT only
                    # when ABC is inconclusive.
                    cone_cell_count = max(
                        sum(1 for _n, d in gold_cone.G.nodes(data=True)
                            if d.get("ntype") == "cell"),
                        sum(1 for _n, d in gate_cone.G.nodes(data=True)
                            if d.get("ntype") == "cell"),
                    )
                    if cone_cell_count > 5000:
                        tiered = max(self._cone_timeout_sec * 4, 120)
                    elif cone_cell_count > 1000:
                        tiered = max(self._cone_timeout_sec * 2, 60)
                    else:
                        tiered = self._cone_timeout_sec
                    abc_timeout = self._budget_timeout(
                        min(max(10, int(tiered // 3)), int(tiered)),
                        reserve=1.0,
                    ) or 1
                    result = self.yosys.check_equiv_abc(
                        gold_v,
                        gate_v,
                        top="cone_top",
                        timeout=abc_timeout,
                    )
                    if result.status not in {"PASS", "FAIL"}:
                        self._record_cec_result(result, cone=True, aggregate=False)
                        yosys_timeout = self._budget_timeout(int(tiered), reserve=1.0) or 1
                        result = self.yosys.check_equiv(
                            gold_v,
                            gate_v,
                            gold_top="cone_top",
                            gate_top="cone_top",
                            timeout=yosys_timeout,
                        )
                    if result.status == "UNKNOWN" and self.remaining_request_time() > 45.0:
                        retry_timeout = self._budget_timeout(
                            max(60, int(tiered)),
                            reserve=5.0,
                        )
                        if retry_timeout and retry_timeout > max(abc_timeout, 1):
                            retry = self.yosys.check_equiv_abc(
                                gold_v,
                                gate_v,
                                top="cone_top",
                                timeout=retry_timeout,
                            )
                            if retry.status in {"PASS", "FAIL"}:
                                result = retry
                            elif self.remaining_request_time() > 30.0:
                                self._record_cec_result(retry, cone=True, aggregate=False)
                                retry_yosys_timeout = self._budget_timeout(
                                    max(60, int(tiered)),
                                    reserve=5.0,
                                ) or 1
                                retry_yosys = self.yosys.check_equiv(
                                    gold_v,
                                    gate_v,
                                    gold_top="cone_top",
                                    gate_top="cone_top",
                                    timeout=retry_yosys_timeout,
                                )
                                if retry_yosys.status in {"PASS", "FAIL"}:
                                    result = retry_yosys
                                elif retry.status != "UNKNOWN":
                                    result = retry
                except Exception as e:
                    result = EquivResult("ERROR", str(e), "cone-cec", 0.0)
                self._record_cec_result(result, cone=True)
                if result.status == "PASS":
                    pass_outputs.append(output_name)
                elif result.status == "FAIL":
                    fail_outputs.append(output_name)
                    first_fail_detail = result.message or first_fail_detail
                    break
                elif result.status == "TIMEOUT":
                    timeout_outputs.append(output_name)
                elif result.status == "ERROR":
                    error_outputs.append(output_name)
                else:
                    unknown_outputs.append(output_name)

        total = len(targets)
        full_note = f"full CEC {full_result.status.lower()}"
        if fail_outputs:
            detail = f"\n{first_fail_detail}" if first_fail_detail else ""
            return f"NOT_EQUIV: output cone {fail_outputs[0]} differs after {full_note}{detail}".rstrip()
        if len(pass_outputs) == total:
            return (
                "EQUIV: current == original by per-output cone CEC "
                f"after {full_note}; {len(pass_outputs)}/{total} observable cones proved "
                f"({self._last_verification_target_note})."
            )
        parts = [
            f"proved={len(pass_outputs)}/{total}",
            f"timeout={len(timeout_outputs)}",
            f"unknown={len(unknown_outputs)}",
            f"error={len(error_outputs)}",
        ]
        pending = timeout_outputs + unknown_outputs + error_outputs
        shown = ", ".join(pending[:12])
        if len(pending) > 12:
            shown += f", ... (+{len(pending) - 12})"
        suffix = f" outputs: {shown}" if shown else ""
        return (
            f"UNKNOWN[PARTIAL]: {full_note}; observable cone CEC "
            + " ".join(parts)
            + suffix
        )

def _target_structurally_identical(

        self,
        gold: NetlistGraph,
        gate: NetlistGraph,
        gold_signal: str,
        gate_signal: str,
    ) -> bool:
        memo_gold: dict[str, tuple] = {}
        memo_gate: dict[str, tuple] = {}
        visiting_gold: set[str] = set()
        visiting_gate: set[str] = set()
        try:
            gold_root = gold.resolve(gold_signal)
            gate_root = gate.resolve(gate_signal)
        except KeyError:
            return False
        return self._cone_structural_signature(gold, gold_root, memo_gold, visiting_gold) == self._cone_structural_signature(gate, gate_root, memo_gate, visiting_gate)

def _cone_structural_signature(

        self,
        graph: NetlistGraph,
        nid: str,
        memo: dict[str, tuple],
        visiting: set[str],
    ) -> tuple:
        if nid in memo:
            return memo[nid]
        if nid in visiting:
            return ("cycle", graph.output_wire(nid))
        visiting.add(nid)
        nd = graph.G.nodes.get(nid, {})
        ntype = nd.get("ntype")
        if nid in {CONST_0, CONST_1} or ntype == "const":
            sig = ("const", nd.get("output_wire", nid))
        elif ntype == "pi":
            sig = ("pi", nd.get("output_wire", nid))
        elif ntype == "cell" and nd.get("gate_type") in DFF_TYPES:
            sig = ("dffq", nd.get("output_wire", nid))
        elif ntype == "cell":
            gate = nd.get("gate_type")
            inputs = []
            ports = list(nd.get("input_ports", []))
            if ports:
                for port, wire in ports:
                    pred = graph.wire_driver.get(wire)
                    if pred is None:
                        inputs.append((str(port), ("wire", wire)))
                    else:
                        inputs.append((str(port), self._cone_structural_signature(graph, pred, memo, visiting)))
            else:
                for pred in graph.G.predecessors(nid):
                    edge = graph.G.get_edge_data(pred, nid, {})
                    inputs.append((str(edge.get("port", "")), self._cone_structural_signature(graph, pred, memo, visiting)))
            if gate in {"$and", "$or", "$nand", "$nor", "$xor", "$xnor"}:
                sig = (gate, tuple(sorted((value for _port, value in inputs), key=repr)))
            else:
                sig = (gate, tuple(inputs))
        else:
            sig = ("unknown", nid)
        visiting.remove(nid)
        memo[nid] = sig
        return sig

def _verification_targets(

        self,
        original_graph: NetlistGraph,
        current_graph: NetlistGraph,
    ) -> list[tuple[str, str, str]]:
        """Return (label, original_signal, current_signal) targets for robust CEC."""
        targets: list[tuple[str, str, str]] = []
        for output_name in sorted(set(original_graph.primary_outputs) | set(current_graph.primary_outputs)):
            targets.append((output_name, output_name, output_name))

        gold_d = self._dff_d_signal_map(original_graph)
        gate_d = self._dff_d_signal_map(current_graph)
        state_count = len(set(gold_d) | set(gate_d))
        if state_count > self._state_target_limit:
            self._last_verification_target_note = (
                f"primary output cones only; {state_count} DFF D next-state cones exceed "
                f"fallback limit {self._state_target_limit} after inconclusive full CEC"
            )
            return targets
        self._last_verification_target_note = (
            "primary outputs plus DFF D next-state cones; DFF Q outputs treated as explicit combinational boundaries"
        )
        for cell_name in sorted(set(gold_d) | set(gate_d)):
            if cell_name not in gold_d or cell_name not in gate_d:
                missing = cell_name if cell_name in gold_d else f"missing:{cell_name}"
                targets.append((f"__dff_d_{self._safe_cone_port(cell_name)}", missing, missing))
                continue
            label = f"__dff_d_{self._safe_cone_port(cell_name)}"
            targets.append((label, gold_d[cell_name], gate_d[cell_name]))
        return targets

def _dff_d_signal_map(self, graph: NetlistGraph) -> dict[str, str]:

        result: dict[str, str] = {}
        for nid, nd in graph.G.nodes(data=True):
            if nd.get("ntype") != "cell" or nd.get("gate_type") not in DFF_TYPES:
                continue
            ports = list(nd.get("input_ports", []))
            d_wire = ""
            for port, wire in ports:
                pname = str(port).upper().lstrip("\\")
                if pname == "D":
                    d_wire = wire
                    break
            if not d_wire:
                for port, wire in ports:
                    pname = str(port).upper().lstrip("\\")
                    if pname not in {"CLK", "C", "RST", "RST_N", "RESET", "RN", "S", "SET"}:
                        d_wire = wire
                        break
            if d_wire:
                result[nid] = d_wire
        return result

def _load_graph_for_verification(self, path: str) -> NetlistGraph:

        try:
            return NetlistGraph.from_verilog(path)
        except Exception:
            fd, json_path = tempfile.mkstemp(suffix="_verify.json", dir=safe_temp_dir())
            os.close(fd)
            try:
                timeout = self._budget_timeout(self.yosys.default_timeout_sec, reserve=2.0)
                if timeout is None:
                    raise TimeoutError(self._time_budget_exhausted("load_graph_for_verification"))
                self.yosys.verilog_to_json(path, json_path, timeout=timeout)
                return NetlistGraph.from_yosys_json(json_path)
            finally:
                if os.path.exists(json_path):
                    os.unlink(json_path)

def _build_verification_cone_graph(

        self,
        graph: NetlistGraph,
        output_name: str,
        output_label: Optional[str] = None,
    ) -> NetlistGraph:
        raw_cone = graph.extract_cone(output_name)
        cone_cells = {
            nid for nid in raw_cone
            if graph.G.nodes.get(nid, {}).get("gate_type") not in DFF_TYPES
        }
        sub = NetlistGraph()
        sub.module_name = "cone_top"

        for cell in cone_cells:
            nd = graph.G.nodes[cell]
            out_wire = nd.get("output_wire", cell)
            sub.G.add_node(
                cell,
                ntype="cell",
                gate_type=nd.get("gate_type", ""),
                output_wire=out_wire,
                input_ports=nd.get("input_ports", []),
                input_wires=nd.get("input_wires", []),
                is_po=False,
            )
            sub.wire_driver[out_wire] = cell

        for cell in cone_cells:
            for pred, _dst, edata in graph.G.in_edges(cell, data=True):
                port = edata.get("port")
                if pred in cone_cells:
                    wire = edata.get("wire", graph.output_wire(pred))
                    sub.G.add_edge(pred, cell, wire=wire, port=port)
                    continue
                boundary_nid, boundary_wire = self._add_cone_boundary_input(sub, graph, pred, edata.get("wire"))
                sub.G.add_edge(boundary_nid, cell, wire=boundary_wire, port=port)

        public_output = output_label or output_name
        driver = graph.resolve(output_name)
        if driver in cone_cells:
            sub.primary_outputs[public_output] = driver
            sub.G.nodes[driver]["is_po"] = True
        else:
            boundary_nid, _boundary_wire = self._add_cone_boundary_input(sub, graph, driver, graph.output_wire(driver))
            sub.primary_outputs[public_output] = boundary_nid
        return sub

def _add_cone_boundary_input(

        self,
        sub: NetlistGraph,
        graph: NetlistGraph,
        pred: str,
        wire: Optional[str],
    ) -> tuple[str, str]:
        if pred in {CONST_0, CONST_1} or str(wire or "").startswith("1'b"):
            value = "1'b1" if pred == CONST_1 or str(wire) == "1'b1" else "1'b0"
            nid = CONST_1 if value == "1'b1" else CONST_0
            if nid not in sub.G:
                sub.G.add_node(nid, ntype="const", output_wire=value, is_po=False)
                sub.wire_driver[value] = nid
            return nid, value

        pred_nd = graph.G.nodes.get(pred, {})
        if pred_nd.get("ntype") == "cell" and pred_nd.get("gate_type") in DFF_TYPES:
            boundary = "__dffq_" + self._safe_cone_port(pred)
        else:
            boundary = self._safe_cone_port(wire or graph.output_wire(pred))
        nid = f"PI:{boundary}"
        if nid not in sub.G:
            sub.G.add_node(nid, ntype="pi", output_wire=boundary, is_po=False)
            sub.wire_driver[boundary] = nid
            sub.primary_inputs[boundary] = nid
        return nid, boundary

def _align_cone_inputs(self, gold: NetlistGraph, gate: NetlistGraph) -> None:

        all_inputs = sorted(set(gold.primary_inputs) | set(gate.primary_inputs))
        for graph in (gold, gate):
            for name in all_inputs:
                if name in graph.primary_inputs:
                    continue
                nid = f"PI:{name}"
                graph.G.add_node(nid, ntype="pi", output_wire=name, is_po=False)
                graph.wire_driver[name] = nid
                graph.primary_inputs[name] = nid

def _safe_cone_port(self, name: str) -> str:

        safe = re.sub(r"[^A-Za-z0-9_$]+", "_", str(name or "")).strip("_")
        if not safe or not re.match(r"[A-Za-z_]", safe):
            safe = "sig_" + safe
        return safe

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
            fd, temp_v = tempfile.mkstemp(suffix="_propcheck.v", dir=safe_temp_dir())
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
                    timeout=self._budget_timeout(self.yosys.default_timeout_sec, reserve=2.0) or 1,
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

def _try_abc_remap(self, graph, style: str, objective: str = "min_gates") -> Optional[object]:

        style_gates = {
            "nand_not": "NAND",
            "and_not": "AND",
            "and_or_not": "AND,OR",
            "nor_not": "NOR",
        }
        gate_set = style_gates.get(style)
        if not gate_set:
            return None
        import tempfile
        with tempfile.TemporaryDirectory(dir=safe_temp_dir()) as tmp:
            vin = os.path.join(tmp, "remap_in.v")
            writer = VerilogWriter()
            writer.write(graph, vin)
            top_name = getattr(graph, "module_name", "") or "top"
            best_graph = None
            best_cost = None
            baseline = self._evaluate_graph_cost(graph, objective=objective, style=style)
            # Prefer AIG-native for AND+NOT: ABC's internal AIG is already
            # the target primitive family, so skip mapping before other trials.
            is_and_not = (style == "and_not")
            is_nand_not = (style == "nand_not")
            if int(baseline.get("cells", 0)) > 50000:
                if is_and_not:
                    variants = ("aig_native", "remap")
                elif is_nand_not:
                    variants = ("remap", "aig_native", "area")
                else:
                    variants = ("remap", "area") if objective in {"min_gates", "gate_count", "area"} else ("depth", "depth_lut", "remap")
            elif int(baseline.get("cells", 0)) > 20000:
                if is_and_not:
                    variants = ("aig_native", "remap", "area", "aggressive")
                elif is_nand_not:
                    variants = ("remap", "aig_native", "area", "aggressive")
                else:
                    variants = ("remap", "area", "aggressive") if objective in {"min_gates", "gate_count", "area"} else ("depth", "depth_lut", "remap", "aggressive")
            elif objective in {"min_gates", "gate_count", "area"}:
                variants = ("aig_native", "remap", "area", "aggressive") if is_and_not else ("remap", "aig_native", "area", "aggressive", "iterative") if is_nand_not else ("remap", "area", "aggressive", "iterative")
            elif objective in {"min_depth", "depth"}:
                variants = ("aig_native", "depth", "depth_lut", "remap") if is_and_not else ("depth", "depth_lut", "remap", "aig_native", "aggressive", "iterative") if is_nand_not else ("depth", "depth_lut", "aggressive", "iterative", "remap")
            else:
                variants = ("aig_native", "remap", "area") if is_and_not else ("remap", "aig_native", "area", "aggressive", "default") if is_nand_not else ("remap", "area", "aggressive", "default")
            for idx, variant in enumerate(variants):
                abc_timeout = self._budget_timeout(
                    min(self.yosys.default_timeout_sec,
                        max(60, min(300, self._cell_count() // 200))),
                    reserve=6.0,
                )
                if abc_timeout is None:
                    break
                vout = os.path.join(tmp, f"remap_out_{idx}_{variant}.v")
                vjson = os.path.join(tmp, f"remap_out_{idx}_{variant}.json")
                try:
                    self.yosys.abc_optimize_with_gates(
                        vin,
                        vout,
                        gate_set,
                        top=top_name,
                        objective=objective,
                        variant=variant,
                        timeout=abc_timeout,
                    )
                    equiv_timeout = self._budget_timeout(
                        min(self._equiv_timeout_sec, 90),
                        reserve=4.0,
                    )
                    if equiv_timeout is None:
                        break
                    equiv = self.yosys.check_equiv(
                        vin,
                        vout,
                        gold_top=top_name,
                        gate_top=top_name,
                        timeout=equiv_timeout,
                    )
                    self._record_cec_result(equiv)
                    if equiv.status != "PASS":
                        continue
                    parse_timeout = self._budget_timeout(self.yosys.default_timeout_sec, reserve=2.0)
                    if parse_timeout is None:
                        break
                    self.yosys.verilog_to_json(vout, vjson, top=top_name, timeout=parse_timeout)
                    new_graph = NetlistGraph.from_yosys_json(vjson)
                    cost = self._evaluate_graph_cost(new_graph, objective=objective, style=style)
                    if not cost.get("style_ok", True):
                        continue
                    if best_cost is None or cost["key"] < best_cost["key"]:
                        best_graph = new_graph
                        best_cost = cost
                except Exception:
                    continue
            if best_graph is not None and best_cost is not None:
                if self._candidate_better(baseline, best_cost, objective):
                    return best_graph
            return None


def _progressive_cone_remap(
    self,
    style: str,
    max_cones: int = 200,
    max_cone_size: int = 15000,
) -> tuple:
    """Progressive per-cone remap. Remaps cones one by one, largest first.
    Each cone independently verified; accepted only if gates don't increase
    and global depth doesn't worsen.
    Returns (cones_remapped, gates_saved)."""
    self._need_design()
    style = style.strip().lower().replace("-", "_")
    if style not in {"nand_not", "and_or_not", "and_not", "nor_not"}:
        return 0, 0
    effective_max_cones = self._dynamic_scale(max_cones, min_factor=0.3, max_factor=2.0)
    effective_max_size = self._dynamic_scale(max_cone_size, min_factor=0.5, max_factor=2.0)
    cone_list = []
    for out_name in list(self.graph.primary_outputs.keys())[:effective_max_cones]:
        try:
            cone = self.graph.extract_cone(out_name)
            size = len(cone)
            if 5 <= size <= effective_max_size:
                cone_list.append((size, out_name))
        except Exception:
            continue
    cone_list.sort(reverse=True)
    remapped = 0
    saved = 0
    for _size, out_name in cone_list:
        remaining = self.remaining_request_time()
        if remaining < 5.0:
            break
        old_cone_cells = self._cell_count(self.graph.extract_cone(out_name))
        before_cost = self._cost_snapshot()
        trial = copy.deepcopy(self.graph)
        result = self._optimizer.optimize(
            trial, out_name, objective="remap", style=style)
        if not result.success:
            result2 = self._optimizer.optimize(
                trial, out_name, objective="min_gates", style=None)
            if result2.success:
                _rd, _rc, style_ok = self._remap_trial_cone_inplace(
                    trial, out_name, style)
                if not style_ok:
                    continue
            else:
                continue
        saved_g = self.graph
        saved_t = self._transformer
        self.graph = trial
        self._transformer = NetlistTransformer(self.graph)
        try:
            self._safe_cleanup(collapse_inverted=True)
            new_cone_cells = self._cell_count(self.graph.extract_cone(out_name))
            after_cost = self._cost_snapshot()
            after_cost["key"] = self._cost_objective_key("min_gates", after_cost)
            before_cost["key"] = self._cost_objective_key("min_gates", before_cost)
        finally:
            self.graph = saved_g
            self._transformer = saved_t
        if (new_cone_cells < old_cone_cells
                and self._candidate_better(before_cost, after_cost, "min_gates", require_improvement=True)):
            self.graph = trial
            self._transformer = NetlistTransformer(self.graph)
            remapped += 1
            saved += (old_cone_cells - new_cone_cells)
    return remapped, saved

def _remap_trial_cone_inplace(self, trial_graph, output_signal: str,

                                   style: str) -> tuple[int, int, bool]:
        saved_graph = self.graph
        saved_tx = self._transformer
        try:
            self.graph = trial_graph
            self._transformer = NetlistTransformer(self.graph)
            self._apply_remap_cone_inplace(output_signal, style)
            for _ in range(4):
                delta = self._safe_cleanup()
                merged = self._structural_duplicate_merge_once(
                    preserve_buffers=self._preserve_buffers)
                if sum(int(v) for v in delta.values()) + merged == 0:
                    break
            after_depth = self._max_depth_value_to_output(output_signal)
            after_cells = self._cell_count(self.graph.extract_cone(output_signal))
            style_ok = self._cone_style_ok(output_signal, style)
            return after_depth, after_cells, style_ok
        finally:
            self.graph = saved_graph
            self._transformer = saved_tx

@staticmethod

def _describe_constraints(when_true: list[str], when_false: list[str]) -> str:

        parts = []
        if when_true:
            parts.append("all of [" + ", ".join(when_true) + "] are 1")
        if when_false:
            parts.append("all of [" + ", ".join(when_false) + "] are 0")
        return " AND ".join(parts) if parts else "(no constraints)"

def _finalize_for_write(self) -> dict[str, int | str | bool]:

        """Run constraint-aware final reductions before emitting Verilog."""
        self._need_design()
        before = self._cell_count()
        style = self._whole_design_style()
        if self._preserve_buffers:
            cleanup = self._safe_cleanup(reconnect=False)
            merged = 0
        else:
            cleanup = {"const": 0, "bool": 0, "not_not": 0, "inv_prim": 0, "dangling": 0}
            merged = 0
            collapse_inverted = style == ""
            remove_buf = style == ""
            func_merged = 0
            for _ in range(8):
                delta = self._safe_cleanup(
                    collapse_inverted=collapse_inverted,
                    remove_buf=remove_buf,
                    reconnect=True,
                )
                delta_merge = self._structural_duplicate_merge_once(
                    preserve_buffers=False
                )
                # Also balance associative trees for depth reduction
                delta_bal = self._transformer.balance_associative_trees(max_leaves=256)
                # AIG merge + functional merge inside loop (feeds back into cleanup)
                delta_aig = self._transformer.merge_aig_equivalent_gates(
                    max_support=8, max_depth=20)
                max_sup = 6 if self._cell_count() > 20000 else 8
                delta_func = self._transformer.merge_functionally_equivalent_gates(
                    max_support=max_sup)
                for key, value in delta.items():
                    cleanup[key] = int(cleanup.get(key, 0)) + int(value)
                merged += delta_merge
                cleanup["balanced_trees"] = int(cleanup.get("balanced_trees", 0)) + delta_bal
                cleanup["aig_merged"] = int(cleanup.get("aig_merged", 0)) + delta_aig
                cleanup["func_merged"] = int(cleanup.get("func_merged", 0)) + delta_func
                total_d = (sum(int(v) for v in delta.values()) + delta_merge
                           + delta_bal + delta_aig + delta_func)
                if total_d == 0:
                    break
            self._last_counts["merged_gates"] = merged

        after = self._cell_count()
        # Final ABC compression: if design was modified, try ABC re-compress
        abc_saved = 0
        abc_depth_saved = 0
        if not self._preserve_buffers and after != before:
            abc_style = style if style else None
            abc_graph = self._try_abc_remap(self.graph, abc_style, objective="min_gates")
            if abc_graph is not None:
                abc_cells = sum(1 for _n, d in abc_graph.G.nodes(data=True)
                               if d.get("ntype") == "cell")
                if abc_cells < after:
                    self._commit_candidate_graph(abc_graph)
                    abc_saved = after - abc_cells
                    after = abc_cells
            # Depth-aware: if design is still deep, try depth optimization
            current_depth = self._max_design_depth_value()
            if current_depth > 15:
                depth_graph = self._try_abc_remap(
                    self.graph, abc_style, objective="min_depth")
                if depth_graph is not None:
                    depth_cells = sum(1 for _n, d in depth_graph.G.nodes(data=True)
                                     if d.get("ntype") == "cell")
                    depth_after = self._max_design_depth_value()
                    # Accept if depth improves and cells don't explode
                    saved_g = self.graph
                    saved_t = self._transformer
                    self.graph = depth_graph
                    self._transformer = NetlistTransformer(self.graph)
                    try:
                        new_depth = self._max_design_depth_value()
                        new_cells = self._cell_count()
                        if new_depth < current_depth and new_cells <= after * 1.1:
                            self._commit_candidate_graph(depth_graph)
                            abc_depth_saved = current_depth - new_depth
                            after = new_cells
                        else:
                            self.graph = saved_g
                            self._transformer = saved_t
                    except Exception:
                        self.graph = saved_g
                        self._transformer = saved_t
        self._finalize_stats = {
            "cells_before": before,
            "cells_after": after,
            "cells_saved": before - after,
            "cleanup_const": int(cleanup.get("const", 0)),
            "cleanup_bool": int(cleanup.get("bool", 0)),
            "cleanup_not_not": int(cleanup.get("not_not", 0)),
            "cleanup_inv_prim": int(cleanup.get("inv_prim", 0)),
            "cleanup_dangling": int(cleanup.get("dangling", 0)),
            "merged": merged,
            "abc_saved": abc_saved,
            "abc_depth_saved": abc_depth_saved,
            "preserve_buffers": self._preserve_buffers,
            "style": style or "mixed",
        }
        return self._finalize_stats

def _safe_cleanup(

        self,
        collapse_inverted: bool = False,
        remove_buf: bool = False,
        reconnect: bool = True,
    ) -> dict[str, int]:
        """Run local equivalence-preserving cleanups and return pass counts."""
        self._need_design()
        counts = {"const": 0, "bool": 0, "not_not": 0, "inv_prim": 0, "dangling": 0}
        for _ in range(4):
            if reconnect:
                delta_const = self._transformer.simplify_constant_gates(
                    remove_buf=remove_buf
                )
                delta_bool = self._transformer.simplify_boolean_identities()
                delta_not = self._transformer.collapse_not_not_pairs()
                delta_inv = (
                    self._transformer.collapse_inverted_primitives()
                    if collapse_inverted else 0
                )
            else:
                delta_const = 0
                delta_bool = 0
                delta_not = 0
                delta_inv = 0
            delta_merge = self._structural_duplicate_merge_once(
                preserve_buffers=not remove_buf)
            delta_dang = self._transformer.remove_dangling()
            # Tree balancing for depth reduction (exposes new merge/cleanup opportunities)
            delta_bal = self._transformer.balance_associative_trees(max_leaves=256)
            counts["const"] += delta_const
            counts["bool"] += delta_bool
            counts["not_not"] += delta_not
            counts["inv_prim"] += delta_inv
            counts["merged"] = counts.get("merged", 0) + delta_merge
            counts["dangling"] += delta_dang
            counts["balanced"] = counts.get("balanced", 0) + delta_bal
            total_delta = (delta_const + delta_bool + delta_not + delta_inv
                           + delta_merge + delta_dang + delta_bal)
            if total_delta == 0:
                break
        self._last_counts["constant_gates_eliminated"] = counts["const"] + counts["dangling"]
        self._last_counts["boolean_identity_simplified"] = counts["bool"]
        self._last_counts["not_not_collapsed"] = counts["not_not"]
        self._last_counts["inverted_primitives_collapsed"] = counts["inv_prim"]
        self._last_counts["dangling_removed"] = counts["dangling"]
        return counts

def _structural_duplicate_merge_once(self, preserve_buffers: bool = False) -> int:

        """Merge one pass of structurally identical cells."""
        self._need_design()
        seen: dict[tuple, str] = {}
        merged = 0
        po_drivers = set(self.graph.primary_outputs.values())
        for nid, nd in list(self.graph.G.nodes(data=True)):
            gate = nd.get("gate_type")
            if (
                nd.get("ntype") != "cell"
                or nid in po_drivers
                or nd.get("is_po")
                or gate in DFF_TYPES
                or (preserve_buffers and gate == "$buf")
            ):
                continue
            key = self._structural_key(nid)
            if key is None:
                continue
            if key not in seen:
                seen[key] = nid
                continue
            keep = seen[key]
            if keep not in self.graph.G:
                seen[key] = nid
                continue
            old_wire = nd.get("output_wire")
            keep_wire = self.graph.output_wire(keep)
            for succ in list(self.graph.G.successors(nid)):
                edge = self.graph.G.get_edge_data(nid, succ, {})
                if self.graph.G.has_edge(nid, succ):
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
            if old_wire and self.graph.wire_driver.get(old_wire) == nid:
                self.graph.wire_driver.pop(old_wire, None)
            merged += 1
        if merged:
            self._rebuild_readers()
        return merged

def _structural_key(self, nid: str) -> Optional[tuple]:

        nd = self.graph.G.nodes.get(nid, {})
        gate = nd.get("gate_type")
        ports = list(nd.get("input_ports") or [])
        if ports:
            inputs = [
                self.graph.wire_driver.get(wire, wire)
                for _port, wire in ports
            ]
        else:
            inputs = list(self.graph.G.predecessors(nid))
        if not inputs and gate not in {"$buf", "$not"}:
            return None
        if gate in {"$and", "$or", "$nand", "$nor", "$xor", "$xnor"}:
            inputs = sorted(inputs)
        return (gate, tuple(inputs))

def _whole_design_style(self) -> str:

        self._need_design()
        gates = {
            nd.get("gate_type")
            for _nid, nd in self.graph.G.nodes(data=True)
            if nd.get("ntype") == "cell" and nd.get("gate_type") not in DFF_TYPES
        }
        styles = (
            ("nand_not", {"$nand", "$not"}),
            ("nor_not", {"$nor", "$not"}),
            ("and_not", {"$and", "$not"}),
            ("and_or_not", {"$and", "$or", "$not"}),
        )
        for name, allowed in styles:
            if gates and gates <= allowed:
                return name
        return ""

def optimization_stats_line(self) -> str:

        """Return one-line testcase optimization statistics."""
        loaded = int(self._loaded_cell_count or 0)
        current = self._cell_count() if self.graph is not None else 0
        saved = loaded - current
        pct = (100.0 * saved / loaded) if loaded else 0.0
        original_depth = int(getattr(self, "_loaded_depth", 0) or 0)
        final_depth = self._max_design_depth_value() if self.graph is not None else 0
        depth_saved = original_depth - final_depth if original_depth else 0
        depth_pct = (100.0 * depth_saved / original_depth) if original_depth else 0.0
        stats = self._finalize_stats or {}
        parts = [
            f"original_cells={loaded}",
            f"final_cells={current}",
            f"cells_saved={saved}",
            f"cells_saved_pct={pct:.2f}",
            f"original_depth={original_depth}",
            f"final_depth={final_depth}",
            f"depth_saved={depth_saved}",
            f"depth_saved_pct={depth_pct:.2f}",
            f"original_bytes={self._loaded_bytes}",
            f"output_bytes={self._last_written_bytes}",
            f"final_opt_saved={stats.get('cells_saved', 0)}",
            f"merged={stats.get('merged', 0)}",
            f"const={stats.get('cleanup_const', 0)}",
            f"bool={stats.get('cleanup_bool', 0)}",
            f"not_not={stats.get('cleanup_not_not', 0)}",
            f"inv_prim={stats.get('cleanup_inv_prim', 0)}",
            f"dangling={stats.get('cleanup_dangling', 0)}",
            f"preserve_buffers={int(bool(stats.get('preserve_buffers', False)))}",
            f"style={stats.get('style', 'n/a')}",
        ]
        parts.extend(
            f"{key}={self._cec_stats.get(key, 0)}"
            for key in (
                "cec_pass",
                "cec_fail",
                "cec_timeout",
                "cec_unknown",
                "cec_error",
                "cone_cec_pass",
                "cone_cec_fail",
                "cone_cec_timeout",
                "cone_cec_unknown",
                "cone_cec_error",
                "cone_cec_abc_pass",
                "cone_cec_abc_fail",
                "cone_cec_abc_timeout",
                "cone_cec_abc_unknown",
                "cone_cec_abc_error",
                "cone_cec_yosys_pass",
                "cone_cec_yosys_fail",
                "cone_cec_yosys_timeout",
                "cone_cec_yosys_unknown",
                "cone_cec_yosys_error",
            )
        )
        if self._last_written_path:
            parts.append(f"output={self._last_written_path}")
        return "CASE_STATS " + " ".join(parts)

def _reset_cec_stats(self) -> None:

        self._cec_stats: dict[str, int] = {
            "cec_pass": 0,
            "cec_fail": 0,
            "cec_timeout": 0,
            "cec_unknown": 0,
            "cec_error": 0,
            "cone_cec_pass": 0,
            "cone_cec_fail": 0,
            "cone_cec_timeout": 0,
            "cone_cec_unknown": 0,
            "cone_cec_error": 0,
            "cone_cec_abc_pass": 0,
            "cone_cec_abc_fail": 0,
            "cone_cec_abc_timeout": 0,
            "cone_cec_abc_unknown": 0,
            "cone_cec_abc_error": 0,
            "cone_cec_yosys_pass": 0,
            "cone_cec_yosys_fail": 0,
            "cone_cec_yosys_timeout": 0,
            "cone_cec_yosys_unknown": 0,
            "cone_cec_yosys_error": 0,
        }

def _cell_count(self, nodes: Optional[set[str]] = None) -> int:

        self._need_design()
        iterable = nodes if nodes is not None else set(self.graph.G.nodes)
        return sum(
            1 for nid in iterable
            if self.graph.G.nodes.get(nid, {}).get("ntype") == "cell"
        )

def _buffer_tree_scope_nodes(self, root: str) -> set[str]:

        """Return root plus BUF descendants that form an inserted fanout tree."""
        nodes: set[str] = {root}
        stack = [root]
        while stack:
            nid = stack.pop()
            for _, dst, _ in self.graph.G.out_edges(nid, data=True):
                if dst in nodes:
                    continue
                nd = self.graph.G.nodes.get(dst, {})
                gate = str(nd.get("gate_type", "")).lower()
                if nd.get("ntype") == "cell" and gate in {"$buf", "buf"}:
                    nodes.add(dst)
                    stack.append(dst)
        return nodes

def _fanout_value(self, nid: str) -> int:

        self._need_design()
        if nid not in self.graph.G:
            return 0
        po_loads = sum(
            1 for driver in self.graph.primary_outputs.values()
            if driver == nid
        )
        return int(self.graph.G.out_degree(nid)) + po_loads

def _max_fanout_value(self) -> int:

        self._need_design()
        return max(
            (
                self._fanout_value(nid)
                for nid, nd in self.graph.G.nodes(data=True)
                if nd.get("ntype") in {"pi", "cell"}
            ),
            default=0,
        )

def _cone_hist(self, output_signal: str) -> str:

        nodes = self.graph.extract_cone(output_signal)
        hist = self._gate_hist(nodes)
        return "{" + ",".join(f"{k}:{v}" for k, v in hist.items()) + "}"

def _style_histogram_text(self, style: str) -> str:

        style = (style or "").strip().lower().replace("-", "_")
        names = {
            "nand_not": ("nand", "not"),
            "nor_not": ("nor", "not"),
            "and_not": ("and", "not"),
            "and_or_not": ("and", "or", "not"),
        }.get(style, ())
        if not names:
            return "style histogram unavailable"
        return " ".join(f"{name.upper()}:{len(self.graph.find_cells_by_type(name))}" for name in names)

def _cone_style_ok(self, output_signal: str, style: str) -> bool:

        style = (style or "").strip().lower().replace("-", "_")
        allowed = {
            "nand_not": {"$nand", "$not"},
            "nor_not": {"$nor", "$not"},
            "and_not": {"$and", "$not"},
            "and_or_not": {"$and", "$or", "$not"},
        }.get(style)
        if not allowed:
            return True
        for nid in self.graph.extract_cone(output_signal):
            gate = self.graph.G.nodes.get(nid, {}).get("gate_type")
            if gate in DFF_TYPES:
                continue
            if gate not in allowed:
                return False
        return True


def _resolve_output_path(self, path: str) -> str:

        raw = str(path or "").strip().strip("\"'")
        if not raw:
            raw = "output.v"
        if os.path.isabs(raw):
            out_path = os.path.abspath(raw)
        else:
            base = self._case_dir or os.getcwd()
            out_path = os.path.abspath(os.path.join(base, raw))
            if self._case_dir:
                try:
                    stays_in_case = os.path.commonpath([self._case_dir, out_path]) == self._case_dir
                except ValueError:
                    stays_in_case = False
                if not stays_in_case:
                    fallback = os.path.basename(raw) or "output.v"
                    out_path = os.path.abspath(os.path.join(self._case_dir, fallback))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        return out_path

def _make_result_path(self, *parts: str) -> str:

        self._result_index += 1
        stem = "_".join(self._safe_filename_part(part) for part in parts if str(part))
        stem = stem[:120].strip("_") or "result"
        name = f"cada_result_{self._result_index:03d}_{stem}.txt"
        return self._resolve_output_path(name)

@staticmethod

def _safe_filename_part(value: str) -> str:

        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))
        return safe.strip("._-") or "x"

def _format_full_list(

        self,
        title: str,
        labels: list[str],
        *stem_parts: str,
        inline_limit: int = 200,
    ) -> str:
        if not labels:
            return title
        if len(labels) <= inline_limit:
            return title + "\n  " + "\n  ".join(labels)
        out_path = self._make_result_path(*stem_parts)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(title + "\n")
            for label in labels:
                f.write(str(label) + "\n")
        preview = "\n  ".join(labels[: min(20, inline_limit)])
        return (
            f"{title}\n"
            f"Full list written to '{out_path}' ({len(labels)} items).\n"
            f"Preview:\n  {preview}"
        )

def _iter_simple_comb_paths(self, src: str, dst: str):

        def _is_dff(nid: str) -> bool:
            return self.graph.G.nodes.get(nid, {}).get("gate_type", "") in DFF_TYPES

        try:
            can_reach_dst = nx.ancestors(self.graph.G, dst) | {dst}
        except Exception:
            can_reach_dst = set(self.graph.G.nodes)
        if src not in can_reach_dst:
            return

        succs: dict[str, list[str]] = {}
        stack = [src]
        seen_nodes = {src}
        while stack:
            node = stack.pop()
            if _is_dff(node) and node != src:
                succs[node] = []
                continue
            filtered = [
                succ for succ in self.graph.G.successors(node)
                if succ in can_reach_dst
            ]
            succs[node] = filtered
            for succ in filtered:
                if succ not in seen_nodes:
                    seen_nodes.add(succ)
                    stack.append(succ)

        stack = [(src, [src], {src})]
        while stack:
            node, path, seen = stack.pop()
            if node == dst:
                yield path
                continue
            for succ in reversed(succs.get(node, [])):
                if succ in seen:
                    continue
                stack.append((succ, path + [succ], seen | {succ}))

def _constant_fold_node(

        self,
        nid: str,
        memo: dict[str, Optional[int]],
        visiting: set[str],
    ) -> Optional[int]:
        if nid in memo:
            return memo[nid]
        if nid in visiting:
            return None
        visiting.add(nid)
        nd = self.graph.G.nodes.get(nid, {})
        ntype = nd.get("ntype")
        result: Optional[int]
        if ntype == "const":
            result = 1 if nd.get("output_wire") == "1'b1" else 0
        elif ntype == "pi":
            result = None
        elif ntype == "cell" and nd.get("gate_type") in DFF_TYPES:
            result = 0
        elif ntype == "cell":
            vals = []
            for _port, wire in nd.get("input_ports", []):
                driver = self.graph.wire_driver.get(wire)
                vals.append(
                    self._constant_fold_node(driver, memo, visiting)
                    if driver is not None else None
                )
            gt = nd.get("gate_type")
            known = [v for v in vals if v is not None]
            if gt == "$buf":
                result = vals[0] if vals else None
            elif gt == "$not":
                result = 1 - vals[0] if vals and vals[0] is not None else None
            elif gt == "$and":
                result = 0 if 0 in known else (1 if vals and len(known) == len(vals) else None)
            elif gt == "$or":
                result = 1 if 1 in known else (0 if vals and len(known) == len(vals) else None)
            elif gt == "$nand":
                base = 0 if 0 in known else (1 if vals and len(known) == len(vals) else None)
                result = 1 - base if base is not None else None
            elif gt == "$nor":
                base = 1 if 1 in known else (0 if vals and len(known) == len(vals) else None)
                result = 1 - base if base is not None else None
            elif gt == "$xor":
                result = (sum(known) & 1) if vals and len(known) == len(vals) else None
            elif gt == "$xnor":
                result = 1 - (sum(known) & 1) if vals and len(known) == len(vals) else None
            else:
                result = None
        else:
            result = None
        visiting.remove(nid)
        memo[nid] = result
        return result

def _prove_signal_constant_with_yosys(self, nid: str, target: int) -> Optional[bool]:

        signal = self.graph.output_wire(nid)
        dff_outputs = [
            self.graph.output_wire(node)
            for node, nd in self.graph.G.nodes(data=True)
            if nd.get("ntype") == "cell" and nd.get("gate_type") in DFF_TYPES
        ]
        fd, temp_v = tempfile.mkstemp(suffix="_constcheck.v", dir=safe_temp_dir())
        os.close(fd)
        try:
            self.writer.write(self.graph, temp_v)
            return self.yosys.prove_signal_constant(
                temp_v,
                signal,
                target,
                assume_zero_signals=dff_outputs,
                top=self.graph.module_name,
                timeout=self._budget_timeout(self.yosys.default_timeout_sec, reserve=2.0) or 1,
            )
        except Exception:
            return None
        finally:
            if os.path.exists(temp_v):
                os.unlink(temp_v)


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
        # Counter-based check
        if any(int(v or 0) > 0 for v in self._last_counts.values()):
            return True
        # Fallback: cell count changed from load time
        if self.graph is not None and self._loaded_cell_count > 0:
            if self._cell_count() != self._loaded_cell_count:
                return True
        return False

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
        self._sync_transformer_budget()

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
        depths, _, _ = self._depths_from_boundaries(include_dffs=True)
        return depths.get(driver, -1)

def _max_design_depth_value(self) -> int:

        assert self.graph is not None
        depths, _, _ = self._depths_from_boundaries(include_dffs=True)
        best = -1
        for _out_name, driver in self.graph.primary_outputs.items():
            best = max(best, int(depths.get(driver, -1)))
        for dff, nd in self.graph.G.nodes(data=True):
            if nd.get("ntype") != "cell" or nd.get("gate_type") not in DFF_TYPES:
                continue
            for driver, _dst, edge in self.graph.G.in_edges(dff, data=True):
                port = str(edge.get("port", "")).upper().lstrip("\\")
                if port in {"D", "DATA", "I0"}:
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
            u_is_dff = self.graph.G.nodes.get(u, {}).get("gate_type") in DFF_TYPES
            v_is_dff = self.graph.G.nodes.get(v, {}).get("gate_type") in DFF_TYPES
            if v_is_dff:
                continue
            if u_is_dff and u not in source_nodes:
                continue
            dag.add_edge(u, v)

        try:
            topo = list(nx.topological_sort(dag))
        except nx.NetworkXUnfeasible:
            # Cycle detected 鈥?iteratively remove back edges until DAG is acyclic
            cycle_count = 0
            while True:
                try:
                    back_edges = list(nx.find_cycle(dag))
                except Exception:
                    break
                for u, v in back_edges:
                    if dag.has_edge(u, v):
                        dag.remove_edge(u, v)
                cycle_count += 1
                if cycle_count > 100:  # safety limit
                    break
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

def _required_depths_from_endpoints(
    self,
    target_depth: Optional[int] = None,
) -> dict[str, int]:
    """Backward propagation from POs/DFF-D pins giving required depth per node.

    Target depth uses *target_depth* if given, otherwise the current
    worst-case design depth as a conservative bound.
    """
    assert self.graph is not None

    # Build reversed DAG (same edge filtering as _depths_from_boundaries)
    rdag = nx.DiGraph()
    rdag.add_nodes_from(self.graph.G.nodes)
    for u, v in self.graph.G.edges():
        v_is_dff = self.graph.G.nodes.get(v, {}).get("gate_type") in DFF_TYPES
        if v_is_dff:
            continue
        rdag.add_edge(v, u)  # reversed direction

    if target_depth is None:
        target_depth = self._max_design_depth_value()
    if target_depth < 0:
        target_depth = 0

    required: dict[str, int] = {}

    # Initialize endpoints with the target depth
    for out_name, driver in self.graph.primary_outputs.items():
        if driver in self.graph.G:
            required[driver] = target_depth

    for dff, nd in self.graph.G.nodes(data=True):
        if nd.get("ntype") != "cell" or nd.get("gate_type") not in DFF_TYPES:
            continue
        for driver, _dst, edge in self.graph.G.in_edges(dff, data=True):
            port = str(edge.get("port", "")).upper().lstrip("\\")
            if port in {"D", "DATA", "I0"}:
                if driver in self.graph.G:
                    nd_driver = self.graph.G.nodes.get(driver, {})
                    if nd_driver.get("ntype") == "cell" and nd_driver.get("gate_type") not in DFF_TYPES:
                        required[driver] = target_depth

    # Topological traversal on reversed graph
    try:
        topo = list(nx.topological_sort(rdag))
    except nx.NetworkXUnfeasible:
        return required

    for node in topo:
        if node not in required:
            continue
        node_req = required[node]
        nd = self.graph.G.nodes.get(node, {})
        if nd.get("ntype") == "cell" and nd.get("gate_type") not in DFF_TYPES:
            # Predecessors must arrive 1 unit earlier
            for pred in rdag.successors(node):
                cand = node_req - 1
                if pred not in required or cand < required[pred]:
                    required[pred] = cand

    return required


def _slack_map(
    self,
    target_depth: Optional[int] = None,
) -> dict[str, int]:
    """Return slack = required_depth - arrival_depth for each node.

    Positive slack means the node has timing room; zero means critical.
    Negative means constraint violation.
    """
    assert self.graph is not None
    arrival, _, _ = self._depths_from_boundaries(include_dffs=True)
    required = self._required_depths_from_endpoints(target_depth=target_depth)

    slack: dict[str, int] = {}
    for nid in arrival:
        req = required.get(nid)
        if req is not None:
            slack[nid] = req - arrival[nid]  # negative means constraint violation

    # Nodes without arrival time but with required time: treat as critical
    for nid in required:
        if nid not in slack:
            slack[nid] = 0  # conservative: treat as zero slack (critical)

    return slack


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


EDABackend.optimize_cone = optimize_cone
EDABackend.remap_cone = remap_cone
EDABackend.abc_optimize_full_design = abc_optimize_full_design
EDABackend._apply_remap_cone_inplace = _apply_remap_cone_inplace
EDABackend.remap_design = remap_design
EDABackend.check_equiv = check_equiv
EDABackend.check_original_equiv = check_original_equiv
EDABackend.check_original_equiv_robust = check_original_equiv_robust
EDABackend.verify_assertion = verify_assertion
EDABackend.optimization_stats_line = optimization_stats_line
EDABackend._reset_cec_stats = _reset_cec_stats
EDABackend._safe_cleanup = _safe_cleanup
EDABackend._structural_duplicate_merge_once = _structural_duplicate_merge_once
EDABackend._structural_key = _structural_key
EDABackend._apply_remap_design_inplace = _apply_remap_design_inplace
EDABackend._remap_trial_cone_inplace = _remap_trial_cone_inplace
EDABackend._try_abc_remap = _try_abc_remap
EDABackend._cost_objective_key = _cost_objective_key
EDABackend._cost_snapshot = _cost_snapshot
EDABackend._evaluate_graph_cost = _evaluate_graph_cost
EDABackend._candidate_better = _candidate_better
EDABackend._commit_candidate_graph = _commit_candidate_graph
EDABackend._critical_depth_targets = _critical_depth_targets
EDABackend._need_design = _need_design
EDABackend._cell_count = _cell_count
EDABackend._max_design_depth_value = _max_design_depth_value
EDABackend._max_depth_value_to_output = _max_depth_value_to_output
EDABackend._max_fanout_value = _max_fanout_value
EDABackend._safe_cone_port = _safe_cone_port
EDABackend._whole_design_style = _whole_design_style
EDABackend._cone_style_ok = _cone_style_ok
EDABackend._cone_hist = _cone_hist
EDABackend._style_histogram_text = _style_histogram_text
EDABackend._gate_hist = _gate_hist
EDABackend._fail = _fail
EDABackend._describe_constraints = _describe_constraints
EDABackend._finalize_for_write = _finalize_for_write
EDABackend._check_original_equiv_result = _check_original_equiv_result
EDABackend._check_original_equiv_by_output_cones = _check_original_equiv_by_output_cones
EDABackend._format_equiv_result = _format_equiv_result
EDABackend._record_cec_result = _record_cec_result
EDABackend._verification_targets = _verification_targets
EDABackend._build_verification_cone_graph = _build_verification_cone_graph
EDABackend._align_cone_inputs = _align_cone_inputs
EDABackend._format_cex = _format_cex
EDABackend._format_block = _format_block
EDABackend._format_inline = _format_inline
EDABackend._format_full_list = _format_full_list
EDABackend._make_result_path = _make_result_path
EDABackend._resolve_output_path = _resolve_output_path
EDABackend._iter_simple_comb_paths = _iter_simple_comb_paths
EDABackend._depths_from_boundaries = _depths_from_boundaries
EDABackend._reconstruct_path = _reconstruct_path
EDABackend._constant_fold_node = _constant_fold_node
EDABackend._eval_node = _eval_node
EDABackend._support_inputs = _support_inputs
EDABackend._structural_signature = _structural_signature
EDABackend._install_verilog_aliases = _install_verilog_aliases
EDABackend._dff_d_signal_map = _dff_d_signal_map
EDABackend._buffer_tree_scope_nodes = _buffer_tree_scope_nodes
EDABackend._safe_filename_part = _safe_filename_part
EDABackend._target_structurally_identical = _target_structurally_identical
EDABackend._truth_table_compare = _truth_table_compare
EDABackend._cone_structural_signature = _cone_structural_signature
EDABackend._prove_signal_constant_with_yosys = _prove_signal_constant_with_yosys
EDABackend._expr_for_node = _expr_for_node
EDABackend._load_graph_for_verification = _load_graph_for_verification
EDABackend._add_cone_boundary_input = _add_cone_boundary_input
EDABackend._port_widths = _port_widths
EDABackend._fanout_value = _fanout_value
EDABackend._rebuild_readers = _rebuild_readers
EDABackend._has_prior_transform = _has_prior_transform
EDABackend._required_depths_from_endpoints = _required_depths_from_endpoints
EDABackend._slack_map = _slack_map
EDABackend._graph_has_combinational_cycle = _graph_has_combinational_cycle
EDABackend._safe_commit_candidate = _safe_commit_candidate
EDABackend._progressive_cone_remap = _progressive_cone_remap
