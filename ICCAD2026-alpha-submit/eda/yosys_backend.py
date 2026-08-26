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
import tempfile
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def safe_temp_dir() -> str:
    """Use an ASCII project-local temp directory for Yosys/ABC artifacts."""
    base = Path(os.environ.get("CADA_YOSYS_TMPDIR", "") or (Path.cwd() / ".yosys_tmp"))
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


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


class YosysBackend:
    """
    Subprocess-based Yosys driver.

    Parameters
    ----------
    yosys_bin : str
        Path to the Yosys binary (default: "yosys", resolved from $PATH).
    """

    def __init__(
        self,
        yosys_bin: str = "yosys",
        default_timeout_sec: int = 600,
        equiv_timeout_sec: int = 600,
    ) -> None:
        self.yosys_bin = yosys_bin
        self.default_timeout_sec = int(default_timeout_sec)
        self.equiv_timeout_sec = int(equiv_timeout_sec)
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
        try:
            proc = subprocess.run(
                [self.yosys_bin, "-Q", "-T", "-p", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._env,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as e:
            partial = ""
            if e.stdout:
                partial += str(e.stdout)[-1000:]
            if e.stderr:
                partial += str(e.stderr)[-1000:]
            detail = f"Yosys timed out after {effective_timeout}s."
            if partial:
                detail += "\n" + partial
            raise YosysTimeoutError(detail) from e
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Yosys exited with code {proc.returncode}.\n"
                f"--- stderr (last 2000 chars) ---\n"
                f"{proc.stderr[-2000:]}"
            )
        return proc.stdout + (proc.stderr or "")


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
            f"write_verilog -noattr -noexpr {_q(vout)}",
            timeout=timeout,
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
    ci_flag = " -ci" if use_ci else ""
    base = f"abc -g {gate_set}{ci_flag} {depth_flag}".strip()
    variant = (variant or "default").strip().lower()

    if variant == "area":
        # structural hashing → functional reduction → don't-care →
        # delay-aware choice → if -y factorization → choice mapping → sweep
        script = "+strash; fraig; dc2; dch; map; if -y; choice; map; sweep"
        return f'{base} -script "{{{script}}}"'
    elif variant == "depth":
        # enhanced depth: compress2rs → resyn2 → balance →
        # don't-care → delay-optimal priority mapping → topo → retime
        script = "+strash; compress2rs; resyn2; balance; dc2; dch; map -p; topo; retime"
        return f'{base} -script "{{{script}}}"'
    elif variant == "depth_aggressive":
        # aggressive depth: compress2rs → resyn2 → fraig → balance →
        # don't-care → priority mapping → choice → remap priority → topo → retime
        script = (
            "+strash; compress2rs; resyn2; fraig; balance; "
            "dc2; dch; map -p; choice; map -p; topo; retime"
        )
        return f'{base} -script "{{{script}}}"'
    elif variant == "iterative":
        # two-pass: area optimisation, then depth clean-up
        script = (
            "+strash; fraig; dc2; dch; map; choice; map; sweep;"
            " strash; dch; map -p; topo; retime"
        )
        return f'{base} -script "{{{script}}}"'
    elif variant == "aggressive":
        # heavy resynthesis + mapping: compress2rs → resyn2 → fraig → refactor → map
        script = "+strash; compress2rs; resyn2; fraig; refactor; dch; map; sweep"
        return f'{base} -script "{{{script}}}"'
    elif variant == "remap":
        # Direct technology mapping for constrained gate libraries.
        # Double-pass AIG optimization → map to target gates → sweep.
        # Designed for remap_design operations (e.g. NAND+NOT, AND+NOT).
        script = (
            "+strash; compress2rs; resyn2; compress2rs; "
            "dch; map -p; sweep; "
            "strash; dch; map -p; sweep"
        )
        return f'{base} -script "{{{script}}}"'
    elif variant == "aig_native":
        # AIG-native optimization: for AND+NOT style, ABC's internal
        # representation IS the target. Skip mapping, just optimize.
        script = "+strash; compress2rs; resyn2; compress2rs; dch; sweep"
        return f'{base} -script "{{{script}}}"'
    elif variant == "depth_compress":
        # Delay-oriented 2-level compression for depth
        script = "+strash; compress2rs -d; resyn2; balance; dch; map -p; topo; retime"
        return f'{base} -script "{{{script}}}"'
    elif variant == "depth_buffered":
        # Depth optimization with strategic buffer insertion:
        # resyn2 → balance → map → topo → buffer_opt splits long paths
        script = "+strash; compress2rs; resyn2; balance; dch; map -p; topo; buffer_opt; retime"
        return f'{base} -script "{{{script}}}"'
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
    """Like abc_optimize_verilog but with a custom gate set and script variant."""
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
        f"proc; flatten; "
        f"{opt_script}; "
        f"write_verilog -noattr -noexpr {_q(vout)}"
        ,
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
        try:
            with self._optional_dff_lib(gold_v, gate_v) as dff_lib:
                lib_cmd = f"read_verilog -sv {_q(dff_lib)}; " if dff_lib else ""
                script = (
                    f"{lib_cmd}"
                    f"read_verilog -sv {_q(gold_v)}; "
                    f"hierarchy -top {gold_top}; proc; flatten; "
                    f"rename {gold_top} gold; "
                    f"read_verilog -sv {_q(gate_v)}; "
                    f"rename {gate_top} gate; "
                    f"proc; flatten; "
                    f"equiv_make gold gate equiv; "
                    f"hierarchy -top equiv; "
                    f"equiv_struct; "
                    f"equiv_simple; "
                    f"equiv_status"
                )
                out = self.run(script, check=False,
                               timeout=timeout or self.equiv_timeout_sec)
        except YosysTimeoutError as e:
            return EquivResult("TIMEOUT", str(e), "yosys-equiv", time.monotonic() - t0)
        except RuntimeError as e:
            return EquivResult("ERROR", str(e), "yosys-equiv", time.monotonic() - t0)
        return self._classify_equiv_output(out, "yosys-equiv", time.monotonic() - t0)

def check_equiv_abc(self, gold_v: str, gate_v: str,
                        top: str = "top",
                        timeout: Optional[int] = None) -> EquivResult:
        """
        Faster equivalence check via ABC's built-in combinational equivalence
        checker (cec).  Recommended for small/medium cones.

        Returns (equivalent: bool, message | None).
        """
        t0 = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(dir=safe_temp_dir()) as tmp:
                gold_aig = os.path.join(tmp, "gold.aig")
                gate_aig = os.path.join(tmp, "gate.aig")
                self.run(
                    f"read_verilog -sv {_q(gold_v)}; hierarchy -top {top}; proc; flatten; techmap; abc -g AND; write_aiger {_q(gold_aig)}",
                    check=False,
                    timeout=timeout or self.equiv_timeout_sec,
                )
                self.run(
                    f"read_verilog -sv {_q(gate_v)}; hierarchy -top {top}; proc; flatten; techmap; abc -g AND; write_aiger {_q(gate_aig)}",
                    check=False,
                    timeout=timeout or self.equiv_timeout_sec,
                )
                out = self.run(
                    f"abc -c \"cec {_q(gold_aig)} {_q(gate_aig)}\"",
                    check=False,
                    timeout=timeout or self.equiv_timeout_sec,
                )
        except YosysTimeoutError as e:
            return EquivResult("TIMEOUT", str(e), "abc-cec", time.monotonic() - t0)
        except RuntimeError as e:
            return EquivResult("ERROR", str(e), "abc-cec", time.monotonic() - t0)
        return self._classify_equiv_output(out, "abc-cec", time.monotonic() - t0)

def _classify_equiv_output(self, out: str, engine: str,

                               elapsed_sec: float) -> EquivResult:
        low = (out or "").lower()
        tail = (out or "")[-1000:]
        if (
            "equivalence successfully proven" in low
            or "successfully proven" in low
            or ("proved" in low and "unproven" not in low and "failed" not in low)
            or ("equivalent" in low and "not equivalent" not in low and "not-equiv" not in low)
        ):
            return EquivResult("PASS", "", engine, elapsed_sec)
        if any(mark in low for mark in (
            "not equivalent",
            "miter failed",
            "counterexample",
            "cex",
            "equivalence checking failed",
            "failed equivalence",
        )):
            return EquivResult("FAIL", self._extract_equiv_detail(out), engine, elapsed_sec)
        if "unproven" in low or "not proven" in low or "can't prove" in low:
            return EquivResult("UNKNOWN", self._extract_equiv_detail(out), engine, elapsed_sec)
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
        script = (
            f"{base}; sat {assumptions} "
            f"-prove {self._sat_sig(signal)} {1 if int(value) else 0} "
            f"-show-inputs -show-outputs"
        )
        out = self.run(script, check=False, timeout=timeout)
        low = out.lower()
        if "syntax error" in low or "error:" in low or "can't perform" in low:
            return None
        if "no model found" in low or "success" in low:
            return True
        if "model found" in low or "fail" in low:
            return False
        return None


def sat_check_assertion(self, verilog_path: str,

                            signal: str,
                            when_true_signals: list[str],
                            when_false_signals: list[str],
                            top: str = "top",
                            timeout: Optional[int] = None) -> tuple[bool, Optional[str]]:
        """
        Use Yosys SAT to check: signal=1 implies (all when_true=1 AND all when_false=0).

        Returns (holds: bool, counterexample_str | None).
        """
        base = (
            f"read_verilog -sv {_q(verilog_path)}; "
            f"hierarchy -top {top}; "
            f"proc; flatten"
        )
        for s in when_true_signals:
            script = f"{base}; sat -set {signal} 1 -set {s} 0 -prove-assertions"
            out = self.run(script, check=False, timeout=timeout)
            if out and ("SAT" in out or "Signal" in out):
                cex = self._extract_sat_signal_values(out, signal, s, violated_true=True)
                return False, cex
        for s in when_false_signals:
            script = f"{base}; sat -set {signal} 1 -set {s} 1 -prove-assertions"
            out = self.run(script, check=False, timeout=timeout)
            if out and ("SAT" in out or "Signal" in out):
                cex = self._extract_sat_signal_values(out, signal, s, violated_true=False)
                return False, cex
        return True, None

def _extract_sat_signal_values(self, output: str, signal: str,

                                    violated_signal: str,
                                    violated_true: bool) -> str:
        """Parse Yosys SAT output into a human-readable counterexample."""
        values: dict[str, str] = {}
        for line in output.splitlines():
            line = line.strip()
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
        extras = [f"{_short(k)}={v}" for k, v in values.items()
                  if k not in {signal, violated_signal} and k.startswith("$")]
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

        try:
            proc = subprocess.run(
                [self.yosys_bin, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                env=self._env,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Yosys binary '{self.yosys_bin}' returned non-zero.")
        except FileNotFoundError:
            raise RuntimeError(
                f"Yosys binary '{self.yosys_bin}' not found. "
                f"Install with: apt install yosys  or  pip install yowasp-yosys"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Yosys binary '{self.yosys_bin}' timed out on --version.")

def _build_env(self) -> dict[str, str]:

        env = os.environ.copy()
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
            text = Path(verilog_path).read_text(encoding="utf-8")
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
            text = Path(verilog_path).read_text(encoding="utf-8")
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
        for name, (lo, hi) in ranges.items():
            bit_re = re.compile(rf"(?<![\w$]){re.escape(name)}\[(\d+)\]")
            for match in bit_re.finditer(text):
                idx = int(match.group(1))
                if idx < lo or idx > hi:
                    replacements[(name, idx)] = f"__oob_{name}_{idx}"

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

        inst_re = re.compile(
            r"(?m)^(\s*)dff\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\(([^;]*?)\)\s*;"
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
                text = Path(verilog_path).read_text(encoding="utf-8")
            except OSError:
                continue
            if (" dff " in text or "\tdff " in text) and not re.search(r"\bmodule\s+dff\b", text):
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
YosysBackend._abc_command = _abc_command
YosysBackend.check_equiv = check_equiv
YosysBackend.check_equiv_abc = check_equiv_abc
YosysBackend._classify_equiv_output = _classify_equiv_output
YosysBackend._extract_equiv_detail = _extract_equiv_detail
YosysBackend.prove_signal_constant = prove_signal_constant
YosysBackend.sat_check_assertion = sat_check_assertion
YosysBackend._extract_sat_signal_values = _extract_sat_signal_values
YosysBackend._sat_sig = _sat_sig
YosysBackend.gate_count_from_json = gate_count_from_json
