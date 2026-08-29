"""Gated, time-bounded Design Compiler attempt.

Invoked only when host_probe reports dc_shell-t is actually runnable.
Any failure restores the entry graph and returns an empty note; nothing
is written to stdout / #RESPONSE.

Runtime assets (dc_opt.tcl, dff_blackbox.v, prebuilt *.db) are resolved
from sys._MEIPASS when frozen and from the source tree otherwise.
``gen_lib`` is never spawned at runtime (PyInstaller frozen binary cannot
run it; contest Python 3.6 cannot import it).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from eda.host_probe import (
    dc_preload,
    note_reprobe_outcome,
    probe_host_tools,
    reprobe_recovery_due,
    run_in_process_group,
)
from eda.transformer import NetlistTransformer
from eda.yosys_backend import safe_temp_dir


_DC_TIMEOUT_SEC = 90.0
_DC_HEADROOM_SEC = 15.0
_DC_PREFLIGHT_TIMEOUT_SEC = 8.0
_DC_LICENSE_FAIL_RE = re.compile(
    r"cannot checkout|license (checkout )?(fail|denied|unavailable|error)"
    r"|failed to get license|unable to obtain a license|license server",
    re.IGNORECASE,
)
_DC_CELL_CAP = 8000
# R47 E1: plain `compile` on the real 14927-cell test24 shape converged in
# 63.1s (depth 25->17, no UIL/OPT) inside a 240s window — reopening the
# 8k-15k band for the UNSTYLED deep path (compile is forced there by the
# deep_band selection in _run_dc; compile_ultra at 15k took 185.6s and
# stays excluded via the 90s run timeout).
_DC_COMPILE_CELL_CAP = 15000
_DC_CELL_FLOOR = 3000
# R35 C1/C4: unstyled mid-shallow bands proven in 90s (dc_budget_sweep
# 1747/41 compile_ultra 61s; 3–8k compile_ultra 74s wrote a primitive
# netlist, no UIL/OPT).  Not a test-name gate.  Deep-small
# (depth>=100 and cells<=3000) stays closed: same sweep KILL at 2324/201.
# 15k stays closed.  R39 A1: and_not joins the style gate via the
# packaged two-stage primitives_and_not.db (the seed-harvest pipeline);
# its narrow/mid bands stay closed because those require style=="".
# nand_not stays closed (no offline evidence yet).
_DC_NARROW_CELL_LO = 1500
_DC_NARROW_CELL_HI = 3000
_DC_MID_CELL_LO = 3000
_DC_NARROW_DEPTH_LO = 30
_DC_NARROW_DEPTH_HI = 99
# R40 B2: remaining-budget boundary below which plain `compile` replaces
# `compile_ultra` (worth gate keeps DC out below 105s, so this applies to
# the (105, 165] window).  G1 sweep: equal depth, ~1/8 wall time.
_DC_COMPILE_BUDGET_SEC = 165.0
# R40 B3a: wall-time cap for the and_not materialize step inside the
# request budget (the commit CEC still needs its own reserve).
_DC_MATERIALIZE_TIMEOUT_SEC = 60.0

_STYLE_CELLS = {
    "nor_not": "NOR2,INV",
    "and_or_not": "AND2,OR2,INV",
    # R40: and_not intentionally has NO subset entry.  DC refuses to map
    # onto single-polarity libraries (OPT-102, re-proven by the R40 G2/G3
    # sweeps); the and_not recipe is full-library DC + materialize_and_not
    # (see _run_dc), the seed-harvest pipeline documented in
    # engines/run_engine_sweep.py.
}
_DC_STYLE_OK = frozenset({"", "nor_not", "and_or_not", "and_not"})
_FULL_CELLS = "AND2,OR2,NAND2,NOR2,XOR2,XNOR2,INV,BUF"
_STYLE_DB = {
    "": "primitives_full.db",
    "full": "primitives_full.db",
    "and_not": "primitives_and_not.db",
    "nand_not": "primitives_nand_not.db",
    "nor_not": "primitives_nor_not.db",
    "and_or_not": "primitives_and_or_not.db",
}

DC_LOG_FATAL_MARKERS = ("UIL-91", "UIL-93", "OPT-102")

# R42 F3/F6: observability side-channel for the license preflight.  The
# boolean return of dc_license_preflight stays unchanged (test lock); the
# detail below feeds the existing [DC TRACE] stderr row only.
_LAST_DC_PREFLIGHT: dict[str, object] = {"reason": "ok", "wall_s": 0.0}

# R40 B0: DC emits library-cell module instances (AND2, INV, ...) with
# named ports; the verification loader only understands Verilog primitive
# keywords.  This mirrors engines/convert_engine_out.py, which the frozen
# onefile binary cannot import; equivalence between the two is locked by
# tests/test_r39_opt_plan.py.
_DC_CELL_MAP = {
    "AND2": "and", "OR2": "or", "NAND2": "nand", "NOR2": "nor",
    "XOR2": "xor", "XNOR2": "xnor", "INV": "not", "BUF": "buf",
    "AND": "and", "OR": "or", "NAND": "nand", "NOR": "nor",
    "XOR": "xor", "XNOR": "xnor", "NOT": "not",
    "GTECH_AND2": "and", "GTECH_OR2": "or", "GTECH_NAND2": "nand",
    "GTECH_NOR2": "nor", "GTECH_XOR2": "xor", "GTECH_XNOR2": "xnor",
    "GTECH_NOT": "not", "GTECH_BUF": "buf",
}
_DC_INST_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+"
    r"((?:\\\S+\s*)?[A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*;\s*$"
)
_DC_PORT_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([^()]+?)\s*\)")


def _convert_synopsys_cell_netlist(text: str) -> str:
    """Rewrite Synopsys library-cell instances to contest primitives."""
    out_lines: list[str] = []
    in_dff_module = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if re.match(r"^\s*module\s+dff\b", line):
            in_dff_module = True
            continue
        if in_dff_module:
            if re.match(r"^\s*endmodule\b", stripped):
                in_dff_module = False
            continue
        m = _DC_INST_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        cell_type, inst_name, args = m.groups()
        prim = _DC_CELL_MAP.get(cell_type)
        if prim is None:
            out_lines.append(line)
            continue
        ports = {
            pm.group(1).upper(): pm.group(2)
            for pm in _DC_PORT_RE.finditer(args)
        }
        if "Y" not in ports or "A" not in ports:
            out_lines.append(line)
            continue
        if prim in ("not", "buf"):
            out_lines.append(f"  {prim} {inst_name} ({ports['Y']}, {ports['A']});")
        elif "B" in ports:
            out_lines.append(
                f"  {prim} {inst_name} ({ports['Y']}, {ports['A']}, {ports['B']});"
            )
        else:
            out_lines.append(line)
    return "\n".join(out_lines) + "\n"


def dc_timeout_sec() -> float:
    return _DC_TIMEOUT_SEC


def dc_skip_preflight() -> bool:
    """True when the 8s license preflight should be skipped.

    Default-on except under pytest (existing ``_run_dc`` mocks assume no
    extra spawn).  ``CADA_DC_SKIP_PREFLIGHT=0/1`` overrides.
    """
    raw = (os.environ.get("CADA_DC_SKIP_PREFLIGHT") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return "PYTEST_CURRENT_TEST" in os.environ


def dc_license_preflight(dc_bin: str) -> bool:
    """True iff dc_shell starts within 8s without a license-fail phrase.

    Not a full ``-x exit`` checkout (R31 W3-3).  A tiny echo/exit script
    is enough to decide whether the 90s compile window would be wasted
    on a queue, so D1 can refund ABC.  R42 F3: the temp dir follows the
    safe_temp_dir fallback chain (a read-only system TMPDIR must not
    masquerade as a license queue) and the failure class / measured
    wall-time are recorded in ``_LAST_DC_PREFLIGHT`` for the [DC TRACE]
    row; the boolean contract is unchanged.
    """

    def _record(reason: str, started: float) -> None:
        wall = round(time.monotonic() - started, 3)
        _LAST_DC_PREFLIGHT.update(reason=reason, wall_s=wall)
        # R46 G14: every preflight outcome lands on stderr with its wall
        # time, so license-queue latency is measurable post-run (G-series
        # discipline: nothing enters stdout/#RESPONSE).
        if "PYTEST_CURRENT_TEST" not in os.environ:
            print(
                f"[PROBE] kind=dc phase=preflight reason={reason} "
                f"wall_s={wall}",
                file=sys.stderr,
            )

    if dc_skip_preflight():
        _LAST_DC_PREFLIGHT.update(reason="ok", wall_s=0.0)
        return True
    if not dc_bin:
        _LAST_DC_PREFLIGHT.update(reason="no_bin", wall_s=0.0)
        return False
    t0 = time.monotonic()
    try:
        try:
            tmp_parent = safe_temp_dir()
        except Exception:
            _record("tmpdir", t0)
            return False
        with tempfile.TemporaryDirectory(
            prefix="dc_preflight_", dir=tmp_parent
        ) as tmp:
            tcl = Path(tmp) / "preflight.tcl"
            tcl.write_text("echo CADA_DC_PREFLIGHT_OK\nexit\n", encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = tmp
            env["TMPDIR"] = tmp
            env["TMP"] = tmp
            cmd = (
                f"{dc_preload()}{shlex.quote(dc_bin)} "
                f"-f {shlex.quote(str(tcl))}"
            )
            completed = run_in_process_group(
                ["bash", "-lc", cmd],
                timeout=_DC_PREFLIGHT_TIMEOUT_SEC,
                cwd=tmp,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if completed is None:
                _record("timeout", t0)
                return False
            blob = completed.stdout or ""
            if _DC_LICENSE_FAIL_RE.search(blob):
                _record("denied", t0)
                return False
            ok = (
                "CADA_DC_PREFLIGHT_OK" in blob
                or int(getattr(completed, "returncode", 1) or 1) == 0
            )
            _record("ok" if ok else "fail", t0)
            return ok
    except Exception:
        _record("tmpdir", t0)
        return False


def dc_log_is_fatal(text: str) -> bool:
    """True when a DC transcript shows a mapping/library failure."""
    blob = text or ""
    return any(marker in blob for marker in DC_LOG_FATAL_MARKERS)


def _runtime_roots() -> list[Path]:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        mei = getattr(sys, "_MEIPASS", None)
        if mei:
            roots.append(Path(mei))
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parents[1])
    return roots


def resolve_dc_assets(style: str = "") -> Optional[tuple[Path, Path, Path]]:
    """Return (dc_opt.tcl, dff_blackbox.v, primitives_*.db) or None."""
    db_name = _STYLE_DB.get((style or "").strip().lower() or "full", "primitives_full.db")
    for root in _runtime_roots():
        for prefix in (root / "engines", root):
            tcl = prefix / "dc_opt.tcl"
            blackbox = prefix / "lib" / "dff_blackbox.v"
            db = prefix / "lib" / "dc" / db_name
            if not db.is_file():
                db = prefix / "lib" / db_name
            if tcl.is_file() and blackbox.is_file() and db.is_file():
                return tcl, blackbox, db
    return None


def _dc_trace(backend, status: str, reason: str = "", **fields) -> dict:
    """Stderr-only DC telemetry.  Never writes to stdout / #RESPONSE."""
    row = {"status": status, "reason": reason}
    row.update(fields)
    try:
        setattr(backend, "_last_dc_trace", row)
    except Exception:
        pass
    parts = [f"[DC TRACE] status={status}"]
    if reason:
        parts.append(f"reason={reason}")
    for key in (
        "cells", "depth", "style", "remaining", "xor_density",
        "wall_s", "compile_cmd", "delay", "rc", "preflight",
        "before_depth", "after_depth", "before_cells", "after_cells",
        "refund_tried", "refund_improved", "near_timeout",
    ):
        if key in fields and fields[key] is not None:
            parts.append(f"{key}={fields[key]}")
    print(" ".join(parts), file=sys.stderr)
    return row


def dc_worth_decision(backend) -> dict:
    """Feature decision for online DC.  Public 12 cost cases seed-shortcut.

    Positive triggers (after style/cap/remaining):
    - depth>=100 and cells>3000
    - xor>=0.15 and cells>3000 and remaining>120
    - unstyled 1500<cells<=3000 and 30<=depth<=99 (C1)
    - unstyled 3000<cells<=8000 and 30<=depth<100 (C4; 90s sweep-proven)
    - unstyled 1500<cells<=3000 and depth>=100 (R40 B3c, compile-only:
      the R40 decisive sweep ran plain compile on the exact 2324/201
      shape that compile_ultra KILLed — 41.8s, depth 201->65, no UIL/OPT)

    Never opens: cells>8000 (15k compile_ultra KILL; the R40 sweep also
    showed plain compile gives no depth win on the 15k test24 shape);
    nand_not (R40 G3 re-proved OPT-102 on the subset db and there is no
    online NAND materialize).  and_not reaches the deep/xor bands
    (R39 A1) via the full-library + materialize_and_not recipe (R40 B3a);
    narrow/mid stay unstyled-only.
    """
    info = {
        "worth": False,
        "reason": "exception",
        "cells": 0,
        "depth": 0,
        "style": "",
        "remaining": 0.0,
        "xor_density": 0.0,
    }
    try:
        remaining = backend.remaining_request_time()
        info["remaining"] = remaining
        if remaining <= _DC_TIMEOUT_SEC + _DC_HEADROOM_SEC:
            info["reason"] = "remaining"
            return info
        if getattr(backend, "graph", None) is None:
            info["reason"] = "no_graph"
            return info
        style = (getattr(backend, "_required_style", None) or "").strip().lower()
        info["style"] = style
        if style not in _DC_STYLE_OK:
            info["reason"] = "style"
            return info
        cells = int(backend._cell_count())
        depth = int(backend._max_design_depth_value())
        xor_density = 0.0
        try:
            feat = backend._design_feature_vector()
            xor_density = float(feat.get("xor_density") or 0.0)
            cells = int(feat.get("cells") or cells)
            depth = int(feat.get("depth") or depth)
        except Exception:
            pass
        info["cells"] = cells
        info["depth"] = depth
        info["xor_density"] = xor_density
        # R47 E1: the unstyled deep band extends to 15k cells on plain
        # compile (63.1s measured on the real 14927-cell test24 shape with
        # depth 25->17).  Everything else above the 8k cap stays closed.
        deep_large = (
            style == ""
            and depth >= 100
            and cells <= _DC_COMPILE_CELL_CAP
        )
        if cells > _DC_CELL_CAP and not deep_large:
            info["reason"] = "cells_cap"
            return info
        narrow = (
            style == ""
            and _DC_NARROW_CELL_LO < cells <= _DC_NARROW_CELL_HI
            and _DC_NARROW_DEPTH_LO <= depth <= _DC_NARROW_DEPTH_HI
        )
        mid = (
            style == ""
            and _DC_MID_CELL_LO < cells <= _DC_CELL_CAP
            and _DC_NARROW_DEPTH_LO <= depth < 100
        )
        deep = depth >= 100 and cells > _DC_CELL_FLOOR
        # R40 B3c: deep×small unstyled band.  compile_ultra KILLs here
        # (2324/201 audit-rehearsal KILL), but the R40 decisive sweep ran
        # plain `compile` on that exact shape: 41.8s, depth 201->65, no
        # UIL/OPT.  _run_dc forces compile for this band.
        deep_small = (
            style == ""
            and depth >= 100
            and _DC_NARROW_CELL_LO < cells <= _DC_NARROW_CELL_HI
        )
        xor_ok = (
            xor_density >= 0.15
            and cells > _DC_CELL_FLOOR
            and remaining > 120.0
        )
        if deep or xor_ok or narrow or mid or deep_small:
            info["worth"] = True
            info["reason"] = "ok"
            return info
        if (
            xor_density >= 0.15
            and cells > _DC_CELL_FLOOR
            and remaining <= 120.0
        ):
            info["reason"] = "xor"
            return info
        info["reason"] = "cells_floor"
        return info
    except Exception:
        return info


def dc_attempt_worth(backend) -> bool:
    """Feature gate shared by the outer caller and try_limited_dc."""
    return bool(dc_worth_decision(backend).get("worth"))


def _dc_eng_obj(backend) -> str:
    """Wave 6: compile_ultra objective follows CostObjective.metric."""
    co = getattr(backend, "_cost_objective", None)
    if co is not None and getattr(co, "metric", "") == "gate_count":
        return "min_gates"
    return "min_depth"


def _dc_protect_env(backend) -> dict[str, str]:
    """Pass renamed identifiers so dc_opt.tcl can set_dont_touch them."""
    cells: list[str] = []
    nets: list[str] = []
    for row in getattr(backend, "_rename_constraints", []) or []:
        name = str(getattr(row, "name", "") or "").strip()
        if not name:
            continue
        if getattr(row, "kind", "") == "gate":
            cells.append(name)
        else:
            nets.append(name)
    extra: dict[str, str] = {}
    if cells:
        extra["ENG_PROTECT_CELLS"] = ",".join(dict.fromkeys(cells))
    if nets:
        extra["ENG_PROTECT_NETS"] = ",".join(dict.fromkeys(nets))
    return extra


def try_limited_dc(backend) -> str:
    """Run ≤90s compile_ultra if DC is probed-available.  Never raises."""
    try:
        probe = probe_host_tools()
        remaining = backend.remaining_request_time()
        if not probe.dc_shell or not probe.dc_bin:
            # R42 F4: the once-per-process attempt flag stays (test lock),
            # but a recorded failure ages out of the backoff window so a
            # license that recovers mid-session is not missed forever.
            can_reprobe = (
                (
                    not getattr(backend, "_dc_reprobe_attempted", False)
                    or reprobe_recovery_due("dc")
                )
                and (
                    remaining == float("inf")
                    or remaining > _DC_TIMEOUT_SEC + _DC_HEADROOM_SEC
                )
            )
            if can_reprobe:
                try:
                    setattr(backend, "_dc_reprobe_attempted", True)
                except Exception:
                    pass
                probe = probe_host_tools(force=True, startup_lec=False)
                note_reprobe_outcome("dc", bool(probe.dc_shell and probe.dc_bin))
                if probe.dc_shell and probe.dc_bin:
                    try:
                        backend._host_probe = probe
                    except Exception:
                        pass
        if not probe.dc_shell or not probe.dc_bin:
            decision = dc_worth_decision(backend)
            _dc_trace(
                backend, "skipped", "no_probe",
                cells=decision.get("cells"), depth=decision.get("depth"),
                style=decision.get("style"), remaining=decision.get("remaining"),
                xor_density=decision.get("xor_density"), wall_s=0.0,
            )
            return ""
        decision = dc_worth_decision(backend)
        if not decision.get("worth"):
            _dc_trace(
                backend, "skipped", str(decision.get("reason") or "cells_floor"),
                cells=decision.get("cells"), depth=decision.get("depth"),
                style=decision.get("style"), remaining=decision.get("remaining"),
                xor_density=decision.get("xor_density"), wall_s=0.0,
            )
            return ""
        return _run_dc(backend, probe.dc_bin)
    except Exception:
        _dc_trace(backend, "rejected", "exception", wall_s=0.0)
        return ""


def try_commit_dc_candidate(
    backend,
    loaded,
    entry,
    entry_digest: str,
    before_cost: dict,
    objective: str,
    style: str,
) -> str:
    """Accept a DC-loaded graph only when it is a strict cost improvement.

    Equal depth with no cell improvement, or Pareto-guard failure,
    restores ``entry``.  Same-depth fewer cells is accepted via
    ``_candidate_better``.  Style / rename / CEC failures also restore.
    Returns a note or "".
    """
    saved = backend.graph
    saved_tx = backend._transformer
    try:
        apply_rename = getattr(backend, "_apply_rename_restore", None)
        if callable(apply_rename):
            apply_rename(loaded)
        backend.graph = loaded
        backend._transformer = NetlistTransformer(loaded)
        if style:
            chk = backend.check_design_style(style)
            if not str(chk).startswith("PASS"):
                backend.graph = saved
                backend._transformer = saved_tx
                _dc_trace(backend, "rejected", "style")
                return ""
        after_cost = backend._evaluate_graph_cost(
            loaded, objective, style=style or None
        )
        after_depth = int(after_cost.get("depth") or 0)
        after_cells = int(after_cost.get("cells") or 0)
        before_depth = int(before_cost.get("depth") or 0)
        before_cells = int(before_cost.get("cells") or 0)
        if not backend._candidate_better(before_cost, after_cost, objective):
            backend.graph = saved
            backend._transformer = saved_tx
            _dc_trace(
                backend, "rejected", "not_better",
                before_depth=before_depth, after_depth=after_depth,
                before_cells=before_cells, after_cells=after_cells,
            )
            return ""
        equiv = backend._check_graphs_boundary_equiv(entry, loaded)
        if equiv.status != "PASS":
            cone = backend._check_original_equiv_by_output_cones(
                equiv, original_graph=entry, gate_graph=loaded,
            )
            if not cone.startswith("EQUIV:") or "PARTIAL" in cone:
                backend.graph = saved
                backend._transformer = saved_tx
                _dc_trace(
                    backend, "rejected", "cec",
                    before_depth=before_depth, after_depth=after_depth,
                    before_cells=before_cells, after_cells=after_cells,
                )
                return ""
        backend.mark_verified_transition(entry_digest, backend._graph_digest())
        _dc_trace(
            backend, "accepted", "ok",
            before_depth=before_depth, after_depth=after_depth,
            before_cells=before_cells, after_cells=after_cells,
        )
        return (
            f"DC compile_ultra: cells {before_cells}->{after_cells}, "
            f"depth {before_depth}->{after_depth}"
        )
    except Exception:
        backend.graph = saved
        backend._transformer = saved_tx
        _dc_trace(backend, "rejected", "exception")
        return ""


def _run_dc(backend, dc_bin: str) -> str:
    style = (getattr(backend, "_required_style", None) or "").strip().lower()
    # R40 B3a: DC refuses to map onto single-polarity libraries (OPT-102,
    # re-proven by the R40 G2/G3 sweeps on primitives_and_not.db /
    # primitives_nand_not.db).  and_not therefore runs the seed-harvest
    # recipe: full-library compile, then materialize_and_not lowers the
    # result to strict AND/NOT (the existing online pass also used by the
    # and_not ABC flow; engines/run_engine_sweep.py documents the same
    # recipe).  Other styles keep their subset libraries.
    dc_style = "" if style == "and_not" else style
    assets = resolve_dc_assets(dc_style)
    if assets is None:
        _dc_trace(backend, "skipped", "no_assets", wall_s=0.0, style=style)
        return ""
    tcl, blackbox, db_path = assets
    cells = _STYLE_CELLS.get(dc_style, "")
    entry = backend.graph
    entry_digest = backend._graph_digest()
    objective = _dc_eng_obj(backend)
    before_cost = backend._evaluate_graph_cost(
        entry, objective, style=style or None
    )
    depth = int(before_cost.get("depth") or 0)
    n_cells = int(before_cost.get("cells") or 0)
    try:
        factor = float(os.environ.get("CADA_DC_DELAY_FACTOR", "0.5"))
    except ValueError:
        factor = 0.5
    delay = max(0.2, factor * max(depth, 1))
    # cells > cap never reaches here (worth already rejected).  Keep the
    # cap branch as a dead branch and report compile_cmd on stderr.
    # R40 B2: with a tight remaining budget use plain compile -- G1 sweep
    # (mid 5k/35) measured compile at the same depth as compile_ultra for
    # ~1/8 of the wall time; with ample budget keep compile_ultra.
    # R40 B3c: the deep×small band ALWAYS uses compile (compile_ultra
    # KILLs it; compile converged on the exact 2324/201 shape).
    # R40 B3c + R44 P1-4: the whole deep band (depth>=100, unstyled) always
    # uses plain compile.  B3c covered deep×small (compile_ultra KILL at
    # 2324/201); the r43_G2G4 sweep on the evaluation machine generalized
    # it: 5k/120 converges under compile_ultra only in 58.4s worst case vs
    # compile's 7.3s with equal-quality output — keep ~50s for commit CEC.
    deep_band = style == "" and depth >= 100
    if deep_band:
        compile_cmd = "compile"
    elif n_cells > _DC_CELL_CAP:
        compile_cmd = "compile"
    else:
        remaining = backend.remaining_request_time()
        compile_cmd = (
            "compile"
            if remaining != float("inf") and remaining <= _DC_COMPILE_BUDGET_SEC
            else "compile_ultra"
        )
    if not dc_license_preflight(dc_bin):
        # R42 F3/F6: keep the locked reason string, enrich it with the
        # measured preflight detail (queue timeout vs explicit license
        # denial vs unwritable temp root) on the same stderr channel.
        _pf = dict(_LAST_DC_PREFLIGHT)
        _dc_trace(
            backend, "skipped", "license_queue",
            wall_s=float(_pf.get("wall_s") or 0.0),
            preflight=str(_pf.get("reason") or "unknown"),
            compile_cmd=compile_cmd, delay=round(delay, 3),
            cells=n_cells, depth=depth, style=style,
        )
        return ""
    with tempfile.TemporaryDirectory(prefix="dc_online_", dir=safe_temp_dir()) as tmp:
        work = Path(tmp)
        vin = work / "preopt.v"
        vout = work / "dc_out.v"
        lib_dir = work / "lib"
        lib_dir.mkdir()
        try:
            backend.writer.write(entry, str(vin))
        except Exception:
            _dc_trace(
                backend, "rejected", "exception", wall_s=0.0,
                compile_cmd=compile_cmd, delay=round(delay, 3),
            )
            return ""
        staged_db = lib_dir / db_path.name
        try:
            staged_db.write_bytes(db_path.read_bytes())
        except OSError:
            _dc_trace(
                backend, "rejected", "exception", wall_s=0.0,
                compile_cmd=compile_cmd, delay=delay,
            )
            return ""
        env = dict(os.environ)
        env.update({
            "ENG_INPUT": str(vin),
            "ENG_OUTPUT": str(vout),
            "ENG_BLACKBOX": str(blackbox),
            "ENG_LIB_DIR": str(lib_dir),
            "ENG_LIB_NAME": staged_db.name,
            "ENG_OBJ": objective,
            "ENG_ALLOWED": cells,
            "ENG_MAX_DELAY": f"{delay:.3f}",
            "ENG_CURRENT_DEPTH": str(max(depth, 1)),
            "ENG_DELAY_FACTOR": str(factor),
            "ENG_COMPILE_CMD": compile_cmd,
            "HOME": str(work / "home"),
            "TMPDIR": str(work / "tmp"),
            "TMP": str(work / "tmp"),
        })
        env.update(_dc_protect_env(backend))
        (work / "home").mkdir(exist_ok=True)
        (work / "tmp").mkdir(exist_ok=True)
        log = work / "dc.log"
        t0 = time.monotonic()
        completed = None
        try:
            cmd = (
                f"{dc_preload()}{shlex.quote(dc_bin)} -f {shlex.quote(str(tcl))} "
                f"-output_log_file {shlex.quote(str(log))}"
            )
            completed = run_in_process_group(
                ["bash", "-lc", cmd],
                timeout=_DC_TIMEOUT_SEC,
                cwd=str(work),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            wall_s = time.monotonic() - t0
            _dc_trace(
                backend, "rejected", "exception", wall_s=round(wall_s, 3),
                compile_cmd=compile_cmd, delay=round(delay, 3),
            )
            return ""
        wall_s = time.monotonic() - t0
        rc = None if completed is None else int(getattr(completed, "returncode", -1))
        # R43: distinguish "machine slow" from "shape hard" post-race — a
        # run consuming >75% of its hard timeout is flagged for recovery.
        near_timeout = int(bool(wall_s > 0.75 * float(_DC_TIMEOUT_SEC)))
        _dc_trace(
            backend, "ran", "ok",
            wall_s=round(wall_s, 3), compile_cmd=compile_cmd,
            delay=round(delay, 3), rc=rc, cells=n_cells, depth=depth,
            style=style, near_timeout=near_timeout,
        )
        try:
            log_text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        except OSError:
            log_text = ""
        if dc_log_is_fatal(log_text):
            _dc_trace(
                backend, "rejected", "fatal_uil_opt",
                wall_s=round(wall_s, 3), compile_cmd=compile_cmd,
                delay=round(delay, 3), rc=rc,
            )
            return ""
        if completed is None:
            _dc_trace(
                backend, "rejected", "timeout",
                wall_s=round(wall_s, 3), compile_cmd=compile_cmd,
                delay=round(delay, 3), rc=rc,
            )
            return ""
        if completed.returncode != 0 or not vout.is_file():
            # R42 F6: dc_opt.tcl exits 3 when the prebuilt .db is missing
            # and 4 when the library fails to load/link (e.g. a DC version
            # that does not read this .db); keep no_vout for every other
            # shape so post-contest traces can separate library faults.
            if rc == 3:
                fail_reason = "db_missing"
            elif rc == 4:
                fail_reason = "db_load_fail"
            else:
                fail_reason = "no_vout"
            _dc_trace(
                backend, "rejected", fail_reason,
                wall_s=round(wall_s, 3), compile_cmd=compile_cmd,
                delay=round(delay, 3), rc=rc,
            )
            return ""
        try:
            # R40 B0: DC writes Synopsys library cells (AND2/INV/...); the
            # verification loader only understands primitive keywords, so
            # convert before loading (see _convert_synopsys_cell_netlist).
            prim_path = work / "dc_out_prim.v"
            prim_path.write_text(
                _convert_synopsys_cell_netlist(
                    vout.read_text(encoding="utf-8", errors="replace")
                ),
                encoding="utf-8",
            )
            if style == "and_not":
                # R40 B3a: lower the full-library result to strict AND/NOT.
                mat_path = work / "dc_out_and_not.v"
                mat_timeout = backend._budget_timeout(
                    _DC_MATERIALIZE_TIMEOUT_SEC, reserve=45.0
                )
                if mat_timeout is None:
                    _dc_trace(
                        backend, "rejected", "materialize_budget",
                        wall_s=round(wall_s, 3), compile_cmd=compile_cmd,
                        delay=round(delay, 3), rc=rc,
                    )
                    return ""
                backend.yosys.materialize_and_not(
                    str(prim_path), str(mat_path),
                    top=str(getattr(backend.graph, "module_name", "") or "top"),
                    timeout=mat_timeout,
                )
                load_path = mat_path
            else:
                load_path = prim_path
            loaded = backend._load_graph_for_verification(str(load_path))
        except Exception:
            _dc_trace(
                backend, "rejected", "exception",
                wall_s=round(wall_s, 3), compile_cmd=compile_cmd,
                delay=round(delay, 3), rc=rc,
            )
            return ""
        note = try_commit_dc_candidate(
            backend, loaded, entry, entry_digest, before_cost, objective, style
        )
        extra = getattr(backend, "_last_dc_trace", None) or {}
        extra["wall_s"] = round(wall_s, 3)
        extra["compile_cmd"] = compile_cmd
        extra["delay"] = round(delay, 3)
        extra["rc"] = rc
        try:
            setattr(backend, "_last_dc_trace", extra)
        except Exception:
            pass
        return note
