"""
eda/yosys_backend.py
====================
Thin subprocess wrapper around the Yosys binary.

All Yosys interaction is funnelled through a single run() method so that:
  - errors are reported uniformly
  - the binary path is configurable
  - the class can be swapped for a libyosys binding later
    without touching any caller code

Designed for the contest gate set:
  and, or, nand, nor, xor, xnor, not, buf  (2-in/1-out or 1-in/1-out)
  dff  (with async active-low reset per contest spec)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import os
import re
import shlex
import shutil
import time
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from eda.host_probe import resolve_external_abc, run_in_process_group


def safe_temp_dir() -> str:
    """Return a writable ASCII temp directory for Yosys/ABC/DC/LEC artifacts.

    Preference: ``CADA_YOSYS_TMPDIR``, then ``$TMPDIR`` / ``tempfile.gettempdir()``
    ``cada_yosys_<pid>``, then (frozen) a directory next to ``sys.executable``.
    Never silently fall back to CWD — a failed mkdir must propagate.
    """
    env = (os.environ.get("CADA_YOSYS_TMPDIR") or "").strip()
    if env:
        base = Path(env)
        base.mkdir(parents=True, exist_ok=True)
        return str(base)
    tmp_root = (os.environ.get("TMPDIR") or "").strip() or tempfile.gettempdir()
    base = Path(tmp_root) / f"cada_yosys_{os.getpid()}"
    try:
        base.mkdir(parents=True, exist_ok=True)
        return str(base)
    except OSError:
        pass
    if getattr(sys, "frozen", False):
        frozen = Path(sys.executable).resolve().parent / ".yosys_tmp"
        frozen.mkdir(parents=True, exist_ok=True)
        return str(frozen)
    raise OSError("safe_temp_dir: no writable temp root")


@dataclass
class EquivResult:
    status: str
    message: str = ""
    engine: str = ""
    elapsed_sec: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"


class YosysTimeoutError(RuntimeError):
    """Raised when a Yosys subprocess exceeds its configured timeout."""


_YOSYS_UNAVAILABLE = "Yosys is unavailable on this host"


class UnavailableYosysBackend:
    """Fail-closed stub when the Yosys binary cannot start.

    Direct Verilog parse / pure-graph analysis still work.  Spawn methods
    raise ``RuntimeError``.  Equiv entry points return UNKNOWN so callers
    emit Cannot determine / INCOMPLETE[cec] instead of FAIL[RUNTIME].
    There is no ``__getattr__`` disguise: missing names stay missing.
    """

    available = False

    def __init__(self) -> None:
        self.yosys_bin = ""
        self.default_timeout_sec = 1
        self.equiv_timeout_sec = 1
        self.use_external_abc = False
        self.available = False
        self._env: dict[str, str] = {}

    def run(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def verilog_to_json(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def json_to_verilog(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def optimize_full(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def abc_optimize_verilog(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def abc_optimize_with_gates(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def materialize_and_not(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def prove_signal_constant(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def prove_signals_equal(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def constant_sweep(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def sat_check_assertion(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def gate_count_from_json(self, *_args, **_kwargs):
        raise RuntimeError(_YOSYS_UNAVAILABLE)

    def check_equiv(self, *_args, **_kwargs):
        return EquivResult("UNKNOWN", _YOSYS_UNAVAILABLE, "yosys-equiv", 0.0)

    def check_equiv_abc(self, *_args, **_kwargs):
        return EquivResult("UNKNOWN", _YOSYS_UNAVAILABLE, "abc-cec", 0.0)


class YosysBackend:
    """
    Subprocess-based Yosys driver.

    ``use_external_abc`` (default False) injects ``abc -exe <bin/yosys-abc>``
    so the miss-path depth search can run when ``lib/yosys-abc`` has no +x.
    compress / remap / full_cleanup must not set it.

    Parameters
    ----------
    yosys_bin : str
        Path to the Yosys binary (default: "yosys", resolved from $PATH).
    """

    available = True

    def __init__(
        self,
        yosys_bin: str = "yosys",
        default_timeout_sec: int = 600,
        equiv_timeout_sec: int = 600,
    ) -> None:
        self.yosys_bin = yosys_bin
        self.default_timeout_sec = int(default_timeout_sec)
        self.equiv_timeout_sec = int(equiv_timeout_sec)
        # R35: miss-path only.  compress / remap / seed safety-margin
        # must leave this False so public test36 bytes stay locked.
        self.use_external_abc = False
        self.available = True
        self._env = self._build_env()
        self._check_available()


    def run(self, script: str, check: bool = True,
            timeout: Optional[int] = None) -> str:
        """
        Execute a Yosys Tcl/command script string.

        Parameters
        ----------
        script : str
            Yosys commands separated by semicolons, e.g.:
            "read_verilog top.v; flatten; write_json out.json"
        check : bool
            Raise RuntimeError on non-zero exit if True (default).

        Returns
        -------
        str
            Combined stdout from the Yosys run.
        """
        effective_timeout = int(timeout or self.default_timeout_sec)
        # Yosys launches ABC as a *child* process.  ``subprocess.run`` with a
        # timeout only signals the direct child (yosys); an ABC grandchild
        # keeps running and holds the stdout pipe open, so the timeout does
        # not actually unblock until ABC finishes on its own -- which is how a
        # single request could run for thousands of seconds past its limit.
        # Launch yosys in its own process group/session and, on timeout, kill
        # the entire tree so the wall-clock budget is genuinely enforced.
        popen_kwargs: dict = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._env,
        )
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        else:
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        proc = subprocess.Popen(
            [self.yosys_bin, "-Q", "-T", "-p", script], **popen_kwargs
        )
        try:
            stdout, stderr = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired as e:
            self._kill_process_tree(proc)
            # The tree is dead now, so this second drain returns promptly.
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except Exception:
                stdout, stderr = "", ""
            partial = ""
            if stdout:
                partial += str(stdout)[-1000:]
            if stderr:
                partial += str(stderr)[-1000:]
            detail = f"Yosys timed out after {effective_timeout}s."
            if partial:
                detail += "\n" + partial
            raise YosysTimeoutError(detail) from e
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Yosys exited with code {proc.returncode}.\n"
                f"--- stderr (last 2000 chars) ---\n"
                f"{(stderr or '')[-2000:]}"
            )
        return (stdout or "") + (stderr or "")

    def _kill_process_tree(self, proc: "subprocess.Popen") -> None:
        """Terminate a yosys process together with any ABC grandchildren.

        Do NOT early-return when the direct child has already exited: yosys
        often exits while an ABC grandchild that inherited the stdout/stderr
        pipes is still running, and communicate() blocks until that pipe
        closes.  Always attempt the group/tree kill so the grandchild dies and
        the follow-up communicate() in run() returns promptly.
        """
        if os.name == "posix":
            import signal as _signal
            try:
                os.killpg(os.getpgid(proc.pid), _signal.SIGKILL)
                return
            except Exception:
                pass
        else:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=10,
                )
                return
            except Exception:
                pass
        try:
            proc.kill()
        except Exception:
            pass


    def verilog_to_json(self, verilog_path: str, json_path: str,
                        top: Optional[str] = None,
                        timeout: Optional[int] = None) -> None:
        """
        Read a gate-level Verilog file, normalize it (hierarchy + proc + flatten),
        and dump to Yosys JSON format.

        The JSON is the canonical interchange format used by NetlistGraph.
        """
        source_path = verilog_path
        preprocessed_path = self._preprocess_out_of_range_vector_refs(verilog_path)
        if preprocessed_path:
            source_path = preprocessed_path
        top_name = self._detect_design_top(source_path, top)
        try:
            with self._optional_dff_lib(source_path) as dff_lib:
                lib_cmd = f"read_verilog -sv {_q(dff_lib)}; " if dff_lib else ""
                self.run(
                    f"{lib_cmd}"
                    f"read_verilog -sv {_q(source_path)}; "
                    f"hierarchy -check -top {top_name}; "
                    f"proc; "
                    f"flatten; "
                    f"write_json {_q(json_path)}",
                    timeout=timeout,
                )
        finally:
            if preprocessed_path and os.path.exists(preprocessed_path):
                os.unlink(preprocessed_path)

    def json_to_verilog(self, json_path: str, verilog_path: str) -> None:
        """Convert a Yosys JSON dump back to clean gate-level Verilog."""
        self.run(
            f"read_json {_q(json_path)}; "
            f"write_verilog -noattr -noexpr {_q(verilog_path)}"
        )


    def optimize_full(self, json_in: str, json_out: str,
                      passes: str = "") -> None:
        """
        Run standard Yosys optimization passes on the full design.
        Useful for global dangling removal and constant propagation before
        cone-level work.

        Parameters
        ----------
        json_in  : str  path to input Yosys JSON
        json_out : str  path to output Yosys JSON
        passes   : str  additional Yosys passes to run before write_json
        """
        self.run(
            f"read_json {_q(json_in)}; "
            f"opt_expr; opt_merge; opt_clean; "
            f"opt; "
            f"{passes + ';' if passes else ''}"
            f"write_json {_q(json_out)}"
        )

def abc_optimize_verilog(self, vin: str, vout: str,
                               max_depth: Optional[int] = None,
                               top: str = "cone_top",
                               timeout: Optional[int] = None) -> None:
        """
        Run ABC logic optimization on a standalone Verilog module.

        Parameters
        ----------
        vin       : str  input Verilog path
        vout      : str  output Verilog path
        max_depth : int | None
            If set, passes -D <n> to ABC to enforce a maximum depth bound.
        top       : str  top module name in vin (default "cone_top")
        """
        depth_flag = f"-D {max_depth}" if max_depth is not None else ""
        gate_set   = "AND,OR,NAND,NOR,XOR,XNOR"
        self.run(
            f"read_verilog -sv {_q(vin)}; "
            f"hierarchy -top {top}; "
            f"proc; flatten; "
            f"abc -g {gate_set} {depth_flag}; "
            f"write_verilog -noattr {_q(vout)}",
            timeout=timeout,
)

# Depth-oriented resyn with -l (level-aware) flags.  Verified offline in
# aig_depth_search.py (R11 F1): the shipped resyn2-style variants leave
# depth on the table for XOR-heavy strict-AIG designs, and this family
# squeezes out the remaining levels.  Old-space commands only -- on the
# contest OSS-CAD 20260618 build ``&mfs2`` is an unknown command (the
# historical ABC9 segfault is no longer reproduced; the exclusion stays).
_RESYN_L = (
    "balance -l; rewrite -l; refactor -l; balance -l; "
    "rewrite -lz; refactor -lz; balance -l"
)


def _abc_command(
    self,
    gate_set: str,
    max_depth: Optional[int] = None,
    variant: str = "default",
    use_ci: bool = False,
) -> str:
    """Build the full abc subcommand string including -script for a variant.

    Variants:
      default    — bare abc call (current behaviour, safe fallback)
      area       — structural hashing + don't-care + choice + remap
      depth      — delay-aware mapping with timing-driven fanout opt
      iterative  — two-pass: area optimisation first, then depth
      aggressive — heavy resynthesis + fraig + refactor
    """
    depth_flag = f"-D {max_depth}" if max_depth is not None else ""
    # ``-ci`` is not a Yosys ``abc`` pass option (it is an option of some
    # standalone ABC commands).  Passing it made every cone optimization fail
    # before synthesis on current OSS-CAD builds.  Cone extraction already
    # exposes shared external drivers as module inputs, so no pass flag is
    # required here.
    ci_flag = ""
    exe_flag = ""
    if getattr(self, "use_external_abc", False):
        exe = resolve_external_abc()
        if exe:
            exe_flag = f"-exe {shlex.quote(exe)} "
    base = f"abc {exe_flag}-g {gate_set}{ci_flag} {depth_flag}".strip()
    variant = (variant or "default").strip().lower()
    # Do not depend on aliases from abc.rc (not installed by every Windows
    # OSS-CAD build).  This is the built-in-command equivalent of resyn2.
    resyn = "balance; rewrite; refactor; balance; rewrite -z; refactor -z; balance"
    # R39 A7: the bundled 20260618 yosys-abc build was probed variant by
    # variant (tmp/probe_variants.py).  `choice` and `compress2rs` are
    # abc.rc aliases absent from that binary, and `sweep` aborts on an
    # AIG network, so scripts below use only built-in commands.

    if variant == "area":
        # structural hashing → functional reduction → don't-care →
        # if -y factorization → remap → sweep (mapped network)
        script = "+strash; fraig; dc2; dch; map; if -y; map; sweep"
        return f'{base} -script "{script}"'
    elif variant == "depth":
        # enhanced depth: compress2rs → resyn2 → balance →
        # don't-care → delay-optimal priority mapping → topo → retime
        script = f"+strash; {resyn}; dc2; dch; map -p; topo; retime"
        return f'{base} -script "{script}"'
    elif variant == "depth_aggressive":
        # aggressive depth: compress2rs → resyn2 → fraig → balance →
        # don't-care → priority mapping → remap priority → topo → retime
        script = (
            f"+strash; {resyn}; fraig; balance; "
            "dc2; dch; map -p; map -p; topo; retime"
        )
        return f'{base} -script "{script}"'
    elif variant == "iterative":
        # two-pass: area optimisation, then depth clean-up
        script = (
            "+strash; fraig; dc2; dch; map; map; sweep;"
            " strash; dch; map -p; topo; retime"
        )
        return f'{base} -script "{script}"'
    elif variant == "aggressive":
        # heavy resynthesis + mapping: compress2rs → resyn2 → fraig → refactor → map
        script = f"+strash; {resyn}; fraig; refactor; dch; map; sweep"
        return f'{base} -script "{script}"'
    elif variant == "remap":
        # Direct technology mapping for constrained gate libraries.
        # Double-pass AIG optimization → map to target gates → sweep.
        # Designed for remap_design operations (e.g. NAND+NOT, AND+NOT).
        script = (
            f"+strash; {resyn}; {resyn}; "
            "dch; map -p; sweep; "
            "strash; dch; map -p; sweep"
        )
        return f'{base} -script "{script}"'
    elif variant == "aig_native":
        # AIG-native optimization: for AND+NOT style, ABC's internal
        # representation IS the target. Skip mapping, just optimize.
        # (`sweep` is invalid on an AIG network — R39 A7 probe.)
        script = f"+strash; {resyn}; {resyn}; dch"
        return f'{base} -script "{script}"'
    elif variant == "depth_compress":
        # Delay-oriented 2-level compression for depth
        script = f"+strash; {resyn}; balance; dch; map -p; topo; retime"
        return f'{base} -script "{script}"'
    elif variant == "depth_buffered":
        # Depth optimization with strategic buffer insertion:
        # resyn2 → balance → map → topo → buffer_opt splits long paths
        script = f"+strash; {resyn}; balance; dch; map -p; topo; buffer; retime"
        return f'{base} -script "{script}"'
    elif variant == "depth_ultra":
        # Ultra-aggressive depth: depth_aggressive plus old-space LUT
        # mapping.  ABC9 "&if/&mfs2/&st" is excluded: 20260618 ``&mfs2``
        # is unknown (historical segfault no longer reproduced).
        # R39 A7: after the old-space LUT mapping the network is an AIG
        # again; `topo`/`retime` only apply to logic networks, so the
        # script ends at `strash` like lut2_map.
        script = (
            f"+strash; {resyn}; fraig; balance; "
            "dc2; dch; map -p; map -p; "
            "if -y -K 8; mfs2; strash"
        )
        return f'{base} -script "{script}"'
    elif variant == "balance_depth":
        # Balanced depth: explicit balance before and after resyn2 for
        # improved tree depth on highly unbalanced designs.
        script = f"+strash; balance; {resyn}; balance; dch; map -p; topo; retime"
        return f'{base} -script "{script}"'
    elif variant == "collapse_depth":
        # Deep reconvergent control cones can hide a much shallower factored
        # function.  Collapse one bounded cone, rebuild its AIG, then run a
        # second delay-oriented cleanup.  This is intentionally used only by
        # the shared-critical-bottleneck path in the backend.
        script = (
            "+strash; collapse; strash; balance -d -s; rewrite; "
            "refactor -N 15; balance -d -s; dch; map -p; "
            "strash; balance; rewrite; refactor; balance; dch; map -p"
        )
        return f'{base} -script "{script}"'
    elif variant == "depth_resyn3":
        # Triple resyn2 + aggressive structural hashing for ultimate depth.
        # Designed to push beyond the diminishing returns of double-resyn.
        script = (
            f"+strash; {resyn}; {resyn}; {resyn}; "
            "fraig; balance; dc2; dch; map -p; topo; retime"
        )
        return f'{base} -script "{script}"'
    elif variant == "depth_choice":
        # Choice-based depth: double resyn2 plus a second don't-care
        # mapping pass.  (`choice` itself is an abc.rc alias missing
        # from the bundled 20260618 yosys-abc — R39 A7 probe.)
        script = (
            f"+strash; {resyn}; {resyn}; "
            "fraig; dc2; dch; map -p; topo; retime"
        )
        return f'{base} -script "{script}"'
    elif variant == "depth_focused_v2":
        # Depth-focused v2: extra rewrite iterations with zero-cost rewrite
        # and aggressive balance for designs close to depth threshold.
        # Targets XOR-heavy AND/NOT designs where standard resyn2 leaves
        # depth on the table.
        script = (
            f"+strash; balance; rewrite -z; refactor -z; balance; "
            f"rewrite -z; refactor -z; balance; "
            f"dc2; dch; map -p; topo; retime"
        )
        return f'{base} -script "{script}"'
    elif variant == "depth_focused_v3":
        # Depth-focused v3: double resyn2 with interleaved balance -d -s
        # (depth-aware structural hashing) before priority mapping.
        # Targets designs where reconvergent paths hide shallower logic.
        script = (
            f"+strash; balance -d -s; {resyn}; balance -d -s; "
            f"{resyn}; balance -d -s; "
            "fraig; dc2; dch; map -p; topo; retime"
        )
        return f'{base} -script "{script}"'
    elif variant == "depth_focused_v4":
        # Depth-focused v4: resyn-heavy with repeated dc2/dch for
        # maximum satisfiability-based depth reduction on deep pipelines.
        # (`compress2rs` is an abc.rc alias missing from the bundled
        # yosys-abc — R39 A7 probe.)
        script = (
            f"+strash; {resyn}; {resyn}; {resyn}; "
            "dc2; dch; dc2; dch; "
            "balance; map -p; topo; retime"
        )
        return f'{base} -script "{script}"'
    elif variant == "area_aggressive":
        # Heavy area-oriented resynthesis: structural hashing + don't-care +
        # technology-independent area mapping (amap) in two passes.
        script = (
            f"+strash; {resyn}; fraig; dc2; dch; amap; sweep; "
            "strash; dch; amap; sweep"
        )
        return f'{base} -script "{script}"'
    elif variant == "level_resyn3":
        # R11 F1: level-aware (-l) resyn family, verified offline in
        # aig_depth_search.py.  Triple level-aware rewriting; every stage
        # keeps the AIG shaped for depth before mapping.
        script = f"+strash; {_RESYN_L}; {_RESYN_L}; {_RESYN_L}"
        return f'{base} -script "{script}"'
    elif variant == "level_dch":
        # R11 F1: level-aware resyn interleaved with dch (don't-care based
        # depth hashing).
        script = f"+strash; {_RESYN_L}; dch; strash; {_RESYN_L}"
        return f'{base} -script "{script}"'
    elif variant == "dc2_level":
        # R11 F1: dc2-based depth reduction sandwiching level-aware resyn.
        script = f"+strash; dc2; {_RESYN_L}; dc2; {_RESYN_L}"
        return f'{base} -script "{script}"'
    elif variant == "lut2_map":
        # R11 F1: old-space 2-LUT mapping (if -K 2; mfs2) followed by
        # level-aware resyn.  Old-space "if"/"mfs2" only: ABC9
        # "&"-prefixed equivalents are excluded (``&mfs2`` unknown on
        # OSS-CAD 20260618).
        script = f"+strash; dch; if -K 2; mfs2; strash; {_RESYN_L}"
        return f'{base} -script "{script}"'
    elif variant == "shake":
        # R11 F1: level-aware resyn with fraig shake-up and dc2 finishing.
        script = f"+strash; {_RESYN_L}; fraig; {_RESYN_L}; dc2; {_RESYN_L}"
        return f'{base} -script "{script}"'
    elif variant == "depth_resyn2l":
        # Engines: level-aware rewrite then a classic resyn2 depth pass.
        # Expand the abc.rc alias so Windows / contest OSS-CAD builds
        # that do not ship abc.rc still run the same built-in sequence.
        script = f"+strash; {_RESYN_L}; {resyn}; map -p; topo; sweep"
        return f'{base} -script "{script}"'
    elif variant == "level_compress":
        # Engines: level-aware resyn with interleaved dch compression.
        script = (
            f"+strash; {_RESYN_L}; dch; strash; {_RESYN_L}; "
            f"dch; map -p; topo; sweep"
        )
        return f'{base} -script "{script}"'
    elif variant == "depth_lut3":
        # Engines: old-space 3-LUT mapping before level-aware resyn.
        script = f"+strash; dch; if -K 3; mfs2; strash; {_RESYN_L}"
        return f'{base} -script "{script}"'
    else:
        # default: bare ABC call as before (safe fallback)
        return base


def abc_optimize_with_gates(self, vin: str, vout: str,
                             gate_set: str,
                             max_depth: Optional[int] = None,
                             top: str = "top",
                             objective: str = "balanced",
                             variant: str = "default",
                             timeout: Optional[int] = None,
                             use_ci: bool = False) -> None:
    """Like abc_optimize_verilog but with a custom gate set and script variant.

    External ABC (``abc -exe bin/yosys-abc``) is used only when
    ``self.use_external_abc`` is True — miss-path design depth / deep
    cones.  Gate-count compress must leave the flag False.
    """
    objective = (objective or "balanced").strip().lower()
    variant = (variant or "default").strip().lower()

    # Resolve variant from objective when not explicitly set
    if variant == "default" and objective != "balanced":
        if objective in {"min_gates", "area"}:
            variant = "area"
        elif objective in {"min_depth", "depth"}:
            variant = "depth"
        elif objective in {"remap", "remap_gates"}:
            variant = "remap"

    abc_cmd = self._abc_command(gate_set, max_depth, variant, use_ci)

    if variant == "area":
        opt_script = (
            f"opt; opt_merge; opt_clean; "
            f"{abc_cmd}; "
            f"opt; opt_merge; opt_clean; "
            f"{abc_cmd}; "
            f"opt_clean"
        )
    elif variant == "depth":
        opt_script = (
            f"opt_expr; opt_reduce; opt_clean; "
            f"{abc_cmd}; "
            f"opt; "
            f"{abc_cmd}; "
            f"opt_clean"
        )
    elif variant == "depth_aggressive":
        opt_script = (
            f"opt_expr; opt_reduce; opt_clean; "
            f"{abc_cmd}; "
            f"opt; opt_reduce; opt_clean; "
            f"{abc_cmd}; "
            f"opt_clean"
        )
    elif variant == "depth_ultra":
        # Ultra-aggressive: 3-pass Yosys flow with full re-optimization between ABC calls
        opt_script = (
            f"opt_expr; opt_reduce; opt_clean; "
            f"{abc_cmd}; "
            f"opt_expr; opt_reduce; opt_merge; opt_clean; "
            f"{abc_cmd}; "
            f"opt; opt_reduce; opt_clean; "
            f"{abc_cmd}; "
            f"opt_clean"
        )
    elif variant == "balance_depth":
        opt_script = (
            f"opt_expr; opt_reduce; opt_clean; "
            f"{abc_cmd}; "
            f"opt; opt_reduce; opt_clean; "
            f"{abc_cmd}; "
            f"opt_clean"
        )
    elif variant in {
        "level_resyn3", "level_dch", "dc2_level", "lut2_map", "shake",
        "depth_resyn2l", "level_compress", "depth_lut3",
    }:
        # R11 F1 + engines: level-aware / LUT depth variants use the same
        # two-pass wrapper as depth_aggressive; the ABC payload itself
        # carries the level-aware rewriting.
        opt_script = (
            f"opt_expr; opt_reduce; opt_clean; "
            f"{abc_cmd}; "
            f"opt; opt_reduce; opt_clean; "
            f"{abc_cmd}; "
            f"opt_clean"
        )
    elif variant == "share":
        opt_script = (
            f"opt; share; opt; "
            f"{abc_cmd}; "
            f"opt_merge; opt_clean"
        )
    elif variant == "remap":
        # Direct technology mapping: minimal Yosys preprocessing,
        # let ABC do the heavy lifting with double-pass optimization.
        opt_script = (
            f"opt_expr; opt_merge; "
            f"{abc_cmd}; "
            f"opt_clean; "
            f"{abc_cmd}; "
            f"opt_clean"
        )
    elif variant == "aig_native":
        # AIG-native: skip Yosys optimization, ABC optimizes AIG directly.
        # No map step since AIG = AND+NOT.
        opt_script = (
            f"{abc_cmd}; "
            f"opt_clean"
        )
    else:
        opt_script = f"opt; {abc_cmd}; opt_clean"
    self.run(
        f"read_verilog -sv {_q(vin)}; "
        f"hierarchy -top {top}; "
        # Lower coarse $xor/$mux/etc. cells before ABC.  Without techmap ABC
        # silently leaves them outside its network and write_verilog emits
        # unresolved escaped module instances such as \\$xor.
        f"proc; flatten; techmap; opt; "
        f"{opt_script}; "
        f"write_verilog -noattr {_q(vout)}"
        ,
        timeout=timeout,
    )


def materialize_and_not(self, vin: str, vout: str,
                        top: str = "top",
                        timeout: Optional[int] = None) -> None:
    """Lower a Yosys/ABC result to a strict AIG primitive netlist.

    ABC commonly writes compact truth-table expressions such as
    ``4'h8 >> {b, a}``.  Reconstructing those vector expressions in Python is
    fragile because several selector bits share one Verilog port name.  Let
    Yosys preserve the expression semantics while ``aigmap`` materializes the
    result as AND and NOT cells.  Sequential/black-box cells are kept as
    boundaries by these combinational passes.
    """
    self.run(
        f"read_verilog -sv {_q(vin)}; "
        f"hierarchy -top {top}; "
        f"proc; flatten; techmap; opt; "
        f"aigmap; opt_clean; "
        f"write_verilog -noattr {_q(vout)}",
        timeout=timeout,
    )


def check_equiv(self, gold_v: str, gate_v: str,
                    gold_top: str = "cone_top",
                    gate_top: str = "cone_top",
                    timeout: Optional[int] = None) -> EquivResult:
        """
        Combinational equivalence check between two Verilog files using
        Yosys equivalence passes (equiv_make ->equiv_simple ->equiv_induct).

        Returns
        -------
        (equivalent: bool, counterexample_str | None)
            counterexample_str is None when equivalent is True.
        """
        t0 = time.monotonic()
        total_budget = max(1, int(timeout or self.equiv_timeout_sec))
        # The primary proof and SAT fallback share one deadline.
        equiv_budget = max(1, int(total_budget * 2 / 3))
        try:
            with self._optional_dff_lib(gold_v, gate_v) as dff_lib:
                lib_cmd = f"read_verilog -sv {_q(dff_lib)}; " if dff_lib else ""
                script = (
                    f"design -reset; "
                    f"{lib_cmd}"
                    f"read_verilog -sv {_q(gold_v)}; "
                    f"hierarchy -top {gold_top}; proc; flatten; "
                    f"rename {gold_top} gold; "
                    f"design -stash gold_design; "
                    f"design -reset; "
                    f"{lib_cmd}"
                    f"read_verilog -sv {_q(gate_v)}; "
                    f"hierarchy -top {gate_top}; proc; flatten; "
                    f"rename {gate_top} gate; "
                    f"design -stash gate_design; "
                    f"design -reset; "
                    f"design -copy-from gold_design -as gold gold; "
                    f"design -copy-from gate_design -as gate gate; "
                    f"equiv_make gold gate equiv; "
                    f"hierarchy -top equiv; "
                    f"equiv_struct; "
                    f"equiv_simple; "
                    f"equiv_induct -undef -seq 4; "
                    f"equiv_status -assert"
                )
                out = self.run(script, check=False,
                               timeout=equiv_budget)
        except YosysTimeoutError as e:
            elapsed = time.monotonic() - t0
            sat_budget = max(0, int(total_budget - elapsed))
            if sat_budget >= 1:
                sat_result = self._check_equiv_sat_miter(
                    gold_v, gate_v, gold_top, gate_top, timeout=sat_budget
                )
                if sat_result.status in {"PASS", "FAIL"}:
                    return sat_result
                return EquivResult(
                    sat_result.status,
                    f"equiv timeout: {e}; SAT fallback: {sat_result.message}",
                    sat_result.engine,
                    elapsed + sat_result.elapsed_sec,
                )
            return EquivResult("TIMEOUT", str(e), "yosys-equiv", elapsed)
        except RuntimeError as e:
            return EquivResult("ERROR", str(e), "yosys-equiv", time.monotonic() - t0)
        classified = self._classify_equiv_output(out, "yosys-equiv", time.monotonic() - t0)
        if classified.status == "UNKNOWN":
            elapsed = time.monotonic() - t0
            sat_budget = max(0, int(total_budget - elapsed))
            if sat_budget < 1:
                return EquivResult(
                    "TIMEOUT", "shared equivalence deadline exhausted",
                    "yosys-equiv", elapsed,
                )
            sat_result = self._check_equiv_sat_miter(
                gold_v, gate_v, gold_top, gate_top,
                timeout=sat_budget,
            )
            if sat_result.status in {"PASS", "FAIL"}:
                return sat_result
        return classified


def _check_equiv_sat_miter(
    self,
    gold_v: str,
    gate_v: str,
    gold_top: str,
    gate_top: str,
    timeout: Optional[int] = None,
) -> EquivResult:
        """Resolve an inconclusive equiv pass with an explicit SAT miter."""
        t0 = time.monotonic()
        try:
            with self._optional_dff_lib(gold_v, gate_v) as dff_lib:
                lib_cmd = f"read_verilog -sv {_q(dff_lib)}; " if dff_lib else ""
                script = (
                    "design -reset; "
                    f"{lib_cmd}read_verilog -sv {_q(gold_v)}; "
                    f"hierarchy -top {gold_top}; proc; flatten; rename {gold_top} gold; "
                    "design -stash gold_design; design -reset; "
                    f"{lib_cmd}read_verilog -sv {_q(gate_v)}; "
                    f"hierarchy -top {gate_top}; proc; flatten; rename {gate_top} gate; "
                    "design -stash gate_design; design -reset; "
                    "design -copy-from gold_design -as gold gold; "
                    "design -copy-from gate_design -as gate gate; "
                    "miter -equiv -flatten gold gate miter; hierarchy -top miter; "
                    "sat -verify -prove trigger 0 -show-inputs -show-outputs"
                )
                out = self.run(script, check=False, timeout=timeout or self.equiv_timeout_sec)
        except YosysTimeoutError as exc:
            return EquivResult("TIMEOUT", str(exc), "yosys-sat-miter", time.monotonic() - t0)
        except RuntimeError as exc:
            return EquivResult("ERROR", str(exc), "yosys-sat-miter", time.monotonic() - t0)
        low = (out or "").lower()
        if "no model found" in low and "success" in low:
            return EquivResult("PASS", "", "yosys-sat-miter", time.monotonic() - t0)
        if "sat model found" in low or "proof did fail" in low:
            return EquivResult(
                "FAIL", self._extract_equiv_detail(out),
                "yosys-sat-miter", time.monotonic() - t0,
            )
        if "error:" in low:
            return EquivResult("ERROR", (out or "")[-1000:], "yosys-sat-miter", time.monotonic() - t0)
        return EquivResult("UNKNOWN", (out or "")[-1000:], "yosys-sat-miter", time.monotonic() - t0)

def check_equiv_abc(self, gold_v: str, gate_v: str,
                        top: str = "top",
                        timeout: Optional[int] = None) -> EquivResult:
        """
        Faster equivalence check via ABC's built-in combinational equivalence
        checker (cec).  Recommended for small/medium cones.

        Returns (equivalent: bool, message | None).
        """
        t0 = time.monotonic()
        total_budget = max(1, int(timeout or self.equiv_timeout_sec))
        deadline = t0 + total_budget
        prep_budget = max(1, total_budget // 3)
        stage = "setup"
        try:
            with tempfile.TemporaryDirectory(dir=safe_temp_dir()) as tmp:
                gold_aig = os.path.join(tmp, "gold.aig")
                gate_aig = os.path.join(tmp, "gate.aig")
                stage = "gold-aig"
                self.run(
                    f"read_verilog -sv {_q(gold_v)}; hierarchy -top {top}; "
                    f"proc; flatten; techmap; opt; aigmap; write_aiger {_q(gold_aig)}",
                    check=True,
                    timeout=min(prep_budget, max(1, int(deadline - time.monotonic()))),
                )
                stage = "gate-aig"
                self.run(
                    f"read_verilog -sv {_q(gate_v)}; hierarchy -top {top}; "
                    f"proc; flatten; techmap; opt; aigmap; write_aiger {_q(gate_aig)}",
                    check=True,
                    timeout=min(prep_budget, max(1, int(deadline - time.monotonic()))),
                )
                abc_bin = shutil.which("yosys-abc", path=self._env.get("PATH"))
                if not abc_bin:
                    candidate = Path(self.yosys_bin).resolve().with_name("yosys-abc.exe")
                    abc_bin = str(candidate) if candidate.is_file() else ""
                if not abc_bin:
                    raise RuntimeError("yosys-abc executable is not available")
                command = f'cec "{Path(gold_aig).as_posix()}" "{Path(gate_aig).as_posix()}"'
                stage = "abc-cec"
                abc_timeout = max(1, int(deadline - time.monotonic()))
                completed = run_in_process_group(
                    [abc_bin, "-c", command],
                    timeout=abc_timeout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=self._env,
                    text=True,
                )
                if completed is None:
                    return EquivResult(
                        "TIMEOUT",
                        f"{stage}: timeout after {abc_timeout}s",
                        "abc-cec",
                        time.monotonic() - t0,
                    )
                out = completed.stdout or ""
                # R38 B4b: a crashed ABC (non-zero exit, no verdict text) is
                # an engine ERROR, not a proof outcome; keep fail-closed.
                if (
                    getattr(completed, "returncode", 0) not in (0, None)
                    and not out.strip()
                ):
                    return EquivResult(
                        "ERROR",
                        f"{stage}: abc exited {completed.returncode} with no output",
                        "abc-cec",
                        time.monotonic() - t0,
                    )
        except subprocess.TimeoutExpired as e:
            return EquivResult("TIMEOUT", f"{stage}: {e}", "abc-cec", time.monotonic() - t0)
        except YosysTimeoutError as e:
            return EquivResult("TIMEOUT", f"{stage}: {e}", "abc-cec", time.monotonic() - t0)
        except RuntimeError as e:
            return EquivResult("ERROR", str(e), "abc-cec", time.monotonic() - t0)
        return self._classify_equiv_output(out, "abc-cec", time.monotonic() - t0)

def _classify_equiv_output(self, out: str, engine: str,

                               elapsed_sec: float) -> EquivResult:
        low = (out or "").lower()
        tail = (out or "")[-1000:]
        if "equivalence successfully proven" in low:
            return EquivResult("PASS", "", engine, elapsed_sec)
        # Packaged ABC cec prints "Networks are equivalent." on PASS and
        # "Networks are UNDECIDED." plus "structurally equivalent nodes"
        # on an incomplete proof.  The old wide "equivalent" and not
        # "not equivalent" substring treated UNDECIDED as PASS.
        if "undecided" in low:
            return EquivResult("UNKNOWN", self._extract_equiv_detail(out), engine, elapsed_sec)
        if "networks are equivalent" in low and "not equivalent" not in low:
            return EquivResult("PASS", "", engine, elapsed_sec)
        if "unproven" in low or "not proven" in low or "can't prove" in low:
            # An unproved $equiv cell means the chosen engine did not close
            # the proof.  It is not a functional counterexample.
            return EquivResult("UNKNOWN", self._extract_equiv_detail(out), engine, elapsed_sec)
        if (
            "successfully proven" in low
            # R38 B4: word-boundary "proved" so progress text such as
            # "improved"/"approved" can never be read as a proof verdict.
            or (
                re.search(r"\bproved\b", low)
                and "unproven" not in low
                and "failed" not in low
            )
        ):
            return EquivResult("PASS", "", engine, elapsed_sec)
        if any(mark in low for mark in (
            "not equivalent",
            "miter failed",
            "counterexample",
            "equivalence checking failed",
            "failed equivalence",
        )) or re.search(r"\bcex\b", low):
            # R38 B4: "cex" is a FAIL marker only as a whole word, so
            # incidental substrings (file names, "cexsave") cannot force a
            # spurious counterexample verdict.
            return EquivResult("FAIL", self._extract_equiv_detail(out), engine, elapsed_sec)
        if "error:" in low or "syntax error" in low or "can't open" in low:
            return EquivResult("ERROR", tail.strip(), engine, elapsed_sec)
        return EquivResult("UNKNOWN", tail.strip(), engine, elapsed_sec)

def _extract_equiv_detail(self, out: str) -> str:

        lines = [
            line.strip()
            for line in (out or "").splitlines()
            if any(mark in line.lower() for mark in (
                "unproven",
                "failed",
                "witness",
                "counterexample",
                "cex",
                "not equivalent",
                "undecided",
                "error:",
            ))
        ]
        detail = "\n".join(lines[:20])
        return detail or (out or "")[-1000:].strip()


def prove_signal_constant(

        self,
        verilog_path: str,
        signal: str,
        value: int,
        assume_zero_signals: Optional[list[str]] = None,
        top: str = "top",
        timeout: Optional[int] = None,
) -> Optional[bool]:
        """
        Use Yosys SAT to prove that signal is a constant value.

        Returns True when proved, False when a counterexample exists, and None
        when Yosys cannot run the proof for this signal.
        """
        base = (
            f"read_verilog -sv {_q(verilog_path)}; "
            f"hierarchy -top {top}; "
            f"proc; flatten"
        )
        assumptions = " ".join(
            f"-set {self._sat_sig(sig)} 0"
            for sig in (assume_zero_signals or [])
            if sig and sig != signal
        )
        # -ignore_unknown_cells lets `sat` model a design containing DFFs:
        # blackbox outputs (DFF-Q) become free inputs, and the explicit
        # -set constraints above still pin them to 0 for the initial-state
        # proof semantics (R9, aligned with answer_recheck AV30).
        script = (
            f"{base}; sat -ignore_unknown_cells {assumptions} "
            f"-prove {self._sat_sig(signal)} {1 if int(value) else 0} "
            f"-show-inputs -show-outputs"
        )
        out = self.run(script, check=False, timeout=timeout)
        low = out.lower()
        if "syntax error" in low or "error:" in low or "can't perform" in low:
            return None
        # "no model found" (proved) must be checked before the generic
        # "model found" counterexample marker -- and the frontend banner
        # "Successfully finished" must never count as a proof (it appears on
        # both proof outcomes and used to turn every non-constant signal into
        # a false "constant").
        if "no model found" in low:
            return True
        if "model found" in low or "proof did fail" in low:
            return False
        return None


def prove_signals_equal(

        self,
        verilog_path: str,
        signal_a: str,
        signal_b: str,
        top: str = "top",
        timeout: Optional[int] = None,
) -> Optional[bool]:
        """
        Use Yosys SAT to prove that two signals compute the same function
        (miter semantics: signal_a XOR signal_b is always 0, expressed as
        ``sat -prove signal_a signal_b``).

        Returns True when proved equal, False when a counterexample exists,
        and None when Yosys cannot run or classify the proof.
        """
        base = (
            f"read_verilog -sv {_q(verilog_path)}; "
            f"hierarchy -top {top}; "
            f"proc; flatten"
        )
        # Same single-design query shape as prove_signal_constant: DFF-Q
        # becomes a free input, which is exactly the contest boundary
        # semantics for a combinational signal-equality question (R9).
        script = (
            f"{base}; sat -ignore_unknown_cells "
            f"-prove {self._sat_sig(signal_a)} {self._sat_sig(signal_b)} "
            f"-show-inputs -show-outputs"
        )
        out = self.run(script, check=False, timeout=timeout or 30)
        low = out.lower()
        if "syntax error" in low or "error:" in low or "can't perform" in low:
            return None
        # "no model found" (proved) must be checked before the generic
        # "model found" counterexample marker -- and the frontend banner
        # "Successfully finished" must never count as a proof.
        if "no model found" in low:
            return True
        if "model found" in low or "proof did fail" in low:
            return False
        return None


def constant_sweep(
    self,
    verilog_path: str,
    top: str,
    timeout: Optional[int] = None,
) -> dict[str, int]:
        """Run one whole-design constant fold and return constant net names."""
        with tempfile.TemporaryDirectory(dir=safe_temp_dir()) as tmp:
            json_path = os.path.join(tmp, "constant_sweep.json")
            with self._optional_dff_lib(verilog_path) as dff_lib:
                lib_cmd = f"read_verilog -sv {_q(dff_lib)}; " if dff_lib else ""
                self.run(
                    f"{lib_cmd}read_verilog -sv {_q(verilog_path)}; "
                    f"hierarchy -top {top}; proc; flatten; "
                    "opt_expr; opt_reduce; opt_merge; opt_clean; "
                    f"write_json {_q(json_path)}",
                    timeout=timeout or self.default_timeout_sec,
                )
            data = json.loads(Path(json_path).read_text(encoding="utf-8", errors="replace"))
            modules = data.get("modules", {})
            module = modules.get(top) or modules.get("\\" + top)
            if module is None and len(modules) == 1:
                module = next(iter(modules.values()))
            result: dict[str, int] = {}
            for name, row in (module or {}).get("netnames", {}).items():
                bits = list(row.get("bits") or [])
                if len(bits) == 1 and bits[0] in {0, 1, "0", "1"}:
                    result[str(name).lstrip("\\")] = int(bits[0])
            return result


def sat_check_assertion(self, verilog_path: str,

                            signal: str,
                            when_true_signals: list[str],
                            when_false_signals: list[str],
                            top: str = "top",
                            timeout: Optional[int] = None,
                            assume_zero_signals: Optional[list[str]] = None,
                            tracks: Optional[list[Optional[list[str]]]] = None) -> object:
        """
        Use Yosys SAT to check: signal=1 implies (all when_true=1 AND all when_false=0).

        All violation queries are solved in ONE yosys process (echo-marked
        segments) instead of one process per constraint.

        ``tracks`` optionally pins a per-track set of signals to 0 (the
        contest DFF initial-state semantics, Q&A A21.1) and returns a list
        of ``(holds, counterexample_str | None)`` tuples, one per track;
        without ``tracks`` a single tuple is returned for
        ``assume_zero_signals``.
        """
        base = (
            f"read_verilog -sv {_q(verilog_path)}; "
            f"hierarchy -top {top}; "
            f"proc; flatten"
        )
        assume_lists: list[list[str]] = (
            [list(al) if al else [] for al in tracks]
            if tracks is not None
            else [list(assume_zero_signals or [])]
        )
        queries: list[tuple[str, int]] = []
        for s in when_true_signals:
            queries.append((s, 0))
        for s in when_false_signals:
            queries.append((s, 1))

        # One yosys process solves every violation query of every track;
        # per-query output is delimited by `log` markers (this yosys build's
        # `echo` only toggles command echoing, while `log <text>` prints the
        # given string to the transcript).
        script = base + "; "
        for t in range(len(assume_lists)):
            for i, (violated, value) in enumerate(queries):
                sig = self._sat_sig(signal)
                other = self._sat_sig(violated)
                # Single-design property query: -ignore_unknown_cells treats
                # DFF-Q as free inputs, matching the exhaustive branch of
                # verify_assertion (same-cycle combinational semantics; R9).
                sets = [f"-set {sig} 1", f"-set {other} {int(value)}"]
                for z in assume_lists[t]:
                    sets.append(f"-set {self._sat_sig(z)} 0")
                script += (
                    f"log __SATQ_{t}_{i}_BEGIN__; "
                    f"sat -ignore_unknown_cells {' '.join(sets)} "
                    f"-show-inputs -show-outputs -show {sig},{other}; "
                    f"log __SATQ_{t}_{i}_END__; "
                )
        out = self.run(script, check=False, timeout=timeout)

        segments: dict[tuple[int, int], str] = {}
        # Line-anchored markers: the transcript also echoes the whole -p
        # script inside the "-- Running command" header, and unanchored
        # markers would match that echo instead of the real delimiters.
        for m in re.finditer(
            r"^__SATQ_(\d+)_(\d+)_BEGIN__\n(.*?)^__SATQ_\1_\2_END__\n?",
            out, re.S | re.M,
        ):
            segments[(int(m.group(1)), int(m.group(2)))] = m.group(3)

        results: list = []
        for t in range(len(assume_lists)):
            track_holds = True
            track_cex: Optional[str] = None
            for i, (violated, value) in enumerate(queries):
                seg = segments.get((t, i), "")
                low = seg.lower()
                if any(marker in low for marker in (
                    "syntax error", "error:", "can't perform", "failed to import",
                )):
                    raise RuntimeError(seg[-400:])
                # Check the more specific UNSAT phrase before "model found"
                # because the former contains the latter as a substring.
                if "no model found" in low:
                    continue
                if "model found" in low:
                    track_cex = self._extract_sat_signal_values(
                        seg, signal, violated,
                        violated_true=(value == 0),
                    )
                    track_holds = False
                    break
                raise RuntimeError(
                    "Yosys SAT result was not classifiable: " + seg[-400:]
                )
            results.append((track_holds, track_cex))
        if tracks is not None:
            return results
        return results[0]

def _extract_sat_signal_values(self, output: str, signal: str,

                                    violated_signal: str,
                                    violated_true: bool) -> str:
        """Parse Yosys SAT output into a human-readable counterexample."""
        values: dict[str, str] = {}
        for line in output.splitlines():
            line = line.strip()
            table = re.match(
                r"^\\?([^\s]+)\s+[-+]?\d+\s+\S+\s+([01xXzZ]+)$",
                line,
            )
            if table:
                values[table.group(1).lstrip("\\")] = table.group(2)
                continue
            for part in line.split(","):
                part = part.strip()
                if "=" in part:
                    name, _, val = part.partition("=")
                    name = name.strip().lstrip("\\")
                    values[name] = val.strip()
        signal_val = values.get(signal, "1")
        violated_val = values.get(violated_signal, "?")

        def _short(s: str) -> str:
            return s.rsplit("$", 1)[-1] if s.startswith("$") else s

        cex = (
            f"Counterexample: {_short(signal)}={signal_val}, "
            f"{_short(violated_signal)}={violated_val}"
            f" (expected {'1' if violated_true else '0'}, got {violated_val})"
        )
        extras = [
            f"{_short(k)}={v}" for k, v in sorted(values.items())
            if k not in {signal, violated_signal}
        ][:16]
        if extras:
            cex += "\n  Additional signals: " + ", ".join(extras[:20])
        return cex

@staticmethod
def _sat_sig(signal: str) -> str:

        sig = str(signal or "").strip()
        if not sig:
            return sig
        if sig.startswith("\\") or re.match(r"^1'b[01xz]$", sig, re.I):
            return sig
        if re.match(r"^[A-Za-z_][A-Za-z0-9_$]*(\[[0-9]+\])?$", sig):
            return sig
        return "\\" + sig


def gate_count_from_json(self, json_path: str) -> int:

        """Return total cell count reported by Yosys stat on a JSON file."""
        out = self.run(f"read_json {_q(json_path)}; stat", check=False)
        for line in out.splitlines():
            if "Number of cells:" in line:
                parts = line.split()
                try:
                    return int(parts[-1])
                except ValueError:
                    pass
        return -1


def _check_available(self) -> None:

        # Cold starts (AV scans, slow filesystems with many temp dirs) can
        # exceed a tight timeout, so allow a generous limit plus retries.
        if not shutil.which(self.yosys_bin) and not os.path.isfile(self.yosys_bin):
            raise RuntimeError(
                f"Yosys binary '{self.yosys_bin}' not found. "
                f"Install with: apt install yosys  or  pip install yowasp-yosys"
            )
        attempts = 3
        for attempt in range(1, attempts + 1):
            completed = run_in_process_group(
                [self.yosys_bin, "--version"],
                timeout=15,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
                text=True,
            )
            if completed is None:
                if attempt < attempts:
                    continue
                raise RuntimeError(
                    f"Yosys binary '{self.yosys_bin}' timed out on --version "
                    f"after {attempts} attempts."
                )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"Yosys binary '{self.yosys_bin}' returned non-zero.")
            return

def _build_env(self) -> dict[str, str]:

        env = os.environ.copy()
        # Yosys invokes ABC through a temporary BLIF directory.  Non-ASCII
        # Windows user-profile paths are corrupted by some ABC builds, so use
        # the same project-local ASCII-safe directory as all other artifacts.
        temp_root = Path(os.path.abspath(safe_temp_dir())).as_posix()
        env["TEMP"] = temp_root
        env["TMP"] = temp_root
        env["TMPDIR"] = temp_root
        bin_dir = self._resolve_yosys_bin_dir()
        if bin_dir and bin_dir.exists():
            root = bin_dir.parent
            lib_dir = root / "lib"
            path_parts = [str(bin_dir)]
            if lib_dir.exists():
                path_parts.append(str(lib_dir))
            path_parts.append(env.get("PATH", ""))
            env["PATH"] = os.pathsep.join(path_parts)
            env.setdefault("YOSYSHQ_ROOT", str(root))
            cert = root / "etc" / "cacert.pem"
            if cert.exists():
                env.setdefault("SSL_CERT_FILE", str(cert))
        return env

def _resolve_yosys_bin_dir(self) -> Optional[Path]:

        candidate = Path(self.yosys_bin)
        if candidate.parent != Path(".") and candidate.exists():
            return candidate.parent
        resolved = shutil.which(self.yosys_bin)
        if resolved:
            return Path(resolved).parent
        return None

def _optional_dff_lib(self, *verilog_paths: str):

        return _DffLibContext(*verilog_paths)

def _detect_design_top(self, verilog_path: str, top: Optional[str]) -> str:

        if top:
            return top
        try:
            text = Path(verilog_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "top"
        modules = [
            m.group(1)
            for m in re.finditer(r"(?m)^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b", text)
        ]
        design_modules = [name for name in modules if name != "dff"]
        if "top" in design_modules:
            return "top"
        if len(design_modules) == 1:
            return design_modules[0]
        return design_modules[0] if design_modules else "top"

def _preprocess_out_of_range_vector_refs(self, verilog_path: str) -> Optional[str]:

        """Replace invalid vector bit references with internal wires before Yosys reads."""
        try:
            text = Path(verilog_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        ranges: dict[str, tuple[int, int]] = {}
        decl_re = re.compile(
            r"\b(?:input|output|wire|reg)\s+\[(\d+)\s*:\s*(\d+)\]\s+([^;]+);",
            re.MULTILINE,
        )
        for msb_s, lsb_s, names_s in decl_re.findall(text):
            lo, hi = sorted((int(msb_s), int(lsb_s)))
            for raw_name in names_s.split(","):
                name = raw_name.strip().split()[-1].strip()
                name = name.lstrip("\\").split()[0]
                if re.match(r"^[A-Za-z_]\w*$", name):
                    ranges[name] = (lo, hi)

        replacements: dict[tuple[str, int], str] = {}
        if ranges:
            # One linear scan for every ``name[idx]`` reference keeps this
            # O(text).  Compiling a fresh regex per declared vector and
            # rescanning the whole file for each was O(vectors * text) and
            # burned hundreds of seconds on large ABC-generated candidate
            # netlists (the dominant cost of a full-design depth request).
            ref_re = re.compile(r"(?<![\w$])([A-Za-z_]\w*)\[(\d+)\]")
            for match in ref_re.finditer(text):
                bounds = ranges.get(match.group(1))
                if bounds is None:
                    continue
                lo, hi = bounds
                idx = int(match.group(2))
                if idx < lo or idx > hi:
                    replacements[(match.group(1), idx)] = f"__oob_{match.group(1)}_{idx}"

        fixed = self._normalize_positional_dff4(text)
        for (name, idx), wire in sorted(replacements.items(), key=lambda item: (-len(item[0][0]), item[0][1])):
            fixed = re.sub(rf"(?<![\w$]){re.escape(name)}\[{idx}\]", wire, fixed)

        if replacements:
            decls = "".join(f"  wire {wire};\n" for wire in sorted(replacements.values()))
            insert = re.search(r"(?m)^\s*(?:and|or|nand|nor|xor|xnor|not|buf|dff)\s+", fixed)
            if insert:
                fixed = fixed[:insert.start()] + decls + fixed[insert.start():]
            else:
                fixed = fixed.replace("endmodule", decls + "endmodule", 1)

        if fixed == text:
            return None

        fd, path = tempfile.mkstemp(
            suffix="_preprocessed.v",
            text=True,
            dir=safe_temp_dir(),
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(fixed)
        return path

def _normalize_positional_dff4(self, text: str) -> str:

        # R17: also normalize escaped instance names (\foo ) so a 4-arg
        # positional dff is not mis-bound to the blackbox RN/SN/CK/D pins.
        inst_re = re.compile(
            r"(?m)^(\s*)dff\s+((?:\\[^\s]+\s)|(?:[A-Za-z_$][A-Za-z0-9_$]*))\s*\(([^;]*?)\)\s*;"
        )

        def repl(match: re.Match[str]) -> str:
            args = [part.strip() for part in match.group(3).split(",")]
            if len(args) != 4 or any(not arg for arg in args):
                return match.group(0)
            if any(arg.startswith(".") for arg in args):
                return match.group(0)
            indent, inst = match.group(1), match.group(2)
            clk, rst_n, d, q = args
            return (
                f"{indent}dff {inst} "
                f"(.clk({clk}), .rst_n({rst_n}), .d({d}), .q({q}));"
            )

        return inst_re.sub(repl, text)


def _q(path: str) -> str:
    return '"' + Path(path).as_posix().replace('"', '\\"') + '"'


class _DffLibContext:
    """Create a temporary blackbox dff module only when the source needs it."""

    _TEXT = """\
(* blackbox *) module dff(
    input RN, input SN, input CK, input D, output Q,
    input clk, input rst_n, input d, output q
);
endmodule
"""

    def __init__(self, *verilog_paths: str) -> None:
        self.verilog_paths = verilog_paths
        self.path: Optional[str] = None

    def __enter__(self) -> Optional[str]:
        needs_lib = False
        for verilog_path in self.verilog_paths:
            try:
                text = Path(verilog_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # R17: also catch dff instances starting at column 0 (or with any
            # indentation) that the " dff "/"\tdff " substring checks miss.
            has_dff_inst = (
                " dff " in text
                or "\tdff " in text
                or re.search(r"(?m)^\s*dff\s+\S", text) is not None
            )
            if has_dff_inst and not re.search(r"\bmodule\s+dff\b", text):
                needs_lib = True
                break
        if not needs_lib:
            return None
        fd, path = tempfile.mkstemp(
            suffix="_dff_blackbox.v",
            text=True,
            dir=safe_temp_dir(),
        )
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(self._TEXT)
        self.path = path
        return path

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.path and os.path.exists(self.path):
            os.unlink(self.path)


YosysBackend._check_available = _check_available
YosysBackend._build_env = _build_env
YosysBackend._resolve_yosys_bin_dir = _resolve_yosys_bin_dir
YosysBackend._optional_dff_lib = _optional_dff_lib
YosysBackend._detect_design_top = _detect_design_top
YosysBackend._preprocess_out_of_range_vector_refs = _preprocess_out_of_range_vector_refs
YosysBackend._normalize_positional_dff4 = _normalize_positional_dff4
YosysBackend.abc_optimize_verilog = abc_optimize_verilog
YosysBackend.abc_optimize_with_gates = abc_optimize_with_gates
YosysBackend.materialize_and_not = materialize_and_not
YosysBackend._abc_command = _abc_command
YosysBackend.check_equiv = check_equiv
YosysBackend._check_equiv_sat_miter = _check_equiv_sat_miter
YosysBackend.check_equiv_abc = check_equiv_abc
YosysBackend._classify_equiv_output = _classify_equiv_output
YosysBackend._extract_equiv_detail = _extract_equiv_detail
YosysBackend.prove_signal_constant = prove_signal_constant
YosysBackend.prove_signals_equal = prove_signals_equal
YosysBackend.constant_sweep = constant_sweep
YosysBackend.sat_check_assertion = sat_check_assertion
YosysBackend._extract_sat_signal_values = _extract_sat_signal_values
YosysBackend._sat_sig = _sat_sig
YosysBackend.gate_count_from_json = gate_count_from_json
