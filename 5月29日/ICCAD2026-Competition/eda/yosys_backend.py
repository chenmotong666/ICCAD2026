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
from pathlib import Path
from typing import Optional


class YosysBackend:
    """
    Subprocess-based Yosys driver.

    Parameters
    ----------
    yosys_bin : str
        Path to the Yosys binary (default: "yosys", resolved from $PATH).
    """

    def __init__(self, yosys_bin: str = "yosys") -> None:
        self.yosys_bin = yosys_bin
        self._env = self._build_env()
        self._check_available()

    # ── low-level runner ──────────────────────────────────────────────────────

    def run(self, script: str, check: bool = True) -> str:
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
        proc = subprocess.run(
            [self.yosys_bin, "-Q", "-T", "-p", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._env,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Yosys exited with code {proc.returncode}.\n"
                f"--- stderr (last 2000 chars) ---\n"
                f"{proc.stderr[-2000:]}"
            )
        return proc.stdout + (proc.stderr or "")

    # ── I/O ───────────────────────────────────────────────────────────────────

    def verilog_to_json(self, verilog_path: str, json_path: str,
                        top: Optional[str] = None) -> None:
        """
        Read a gate-level Verilog file, normalize it (hierarchy + proc + flatten),
        and dump to Yosys JSON format.

        The JSON is the canonical interchange format used by NetlistGraph.
        """
        top_opt = f"-top {top}" if top else "-top top"
        source_path = verilog_path
        preprocessed_path = self._preprocess_out_of_range_vector_refs(verilog_path)
        if preprocessed_path:
            source_path = preprocessed_path
        try:
            with self._optional_dff_lib(source_path) as dff_lib:
                lib_cmd = f"read_verilog -sv {_q(dff_lib)}; " if dff_lib else ""
                self.run(
                    f"{lib_cmd}"
                    f"read_verilog -sv {_q(source_path)}; "
                    f"hierarchy -check {top_opt}; "
                    f"proc; "
                    f"flatten; "
                    f"write_json {_q(json_path)}"
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

    # ── optimization ─────────────────────────────────────────────────────────

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
                              top: str = "cone_top") -> None:
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
            f"write_verilog -noattr {_q(vout)}"
        )

    # ── equivalence checking ──────────────────────────────────────────────────

    def check_equiv(self, gold_v: str, gate_v: str,
                    gold_top: str = "cone_top",
                    gate_top: str = "cone_top") -> tuple[bool, Optional[str]]:
        """
        Combinational equivalence check between two Verilog files using
        Yosys equivalence passes (equiv_make → equiv_simple → equiv_induct).

        Returns
        -------
        (equivalent: bool, counterexample_str | None)
            counterexample_str is None when equivalent is True.
        """
        with self._optional_dff_lib(gold_v, gate_v) as dff_lib:
            lib_cmd = f"read_verilog -sv {_q(dff_lib)}; " if dff_lib else ""
            script = (
                f"{lib_cmd}"
                f"read_verilog -sv {_q(gold_v)}; "
                f"hierarchy -top {gold_top}; proc; flatten; "
                f"rename {gold_top} gold; "
                f"read_verilog -sv {_q(gate_v)}; "
                f"rename {gate_top} gate; "
                f"proc; "
                f"equiv_make gold gate equiv; "
                f"hierarchy -top equiv; "
                f"equiv_simple; "
                f"equiv_induct; "
                f"equiv_status"
            )
            out = self.run(script, check=False)
        if "Proved" in out:
            return True, None
        cex_lines = [l.strip() for l in out.splitlines()
                     if any(k in l.lower()
                            for k in ("unproved", "failed", "witness", "cex"))]
        cex = "\n".join(cex_lines) if cex_lines else out[-600:]
        return False, cex

    def check_equiv_abc(self, gold_v: str, gate_v: str,
                        top: str = "top") -> tuple[bool, Optional[str]]:
        """
        Faster equivalence check via ABC's built-in combinational equivalence
        checker (cec).  Recommended for small/medium cones.

        Returns (equivalent: bool, message | None).
        """
        with tempfile.TemporaryDirectory() as tmp:
            gold_aig = os.path.join(tmp, "gold.aig")
            gate_aig = os.path.join(tmp, "gate.aig")
            self.run(
                f"read_verilog -sv {_q(gold_v)}; "
                f"synth -flatten -top {top}; write_aiger {_q(gold_aig)}",
                check=False
            )
            self.run(
                f"read_verilog -sv {_q(gate_v)}; "
                f"synth -flatten -top {top}; write_aiger {_q(gate_aig)}",
                check=False
            )
            out = self.run(
                f"read_aiger {_q(gold_aig)}; "
                f"abc -c \"cec {_q(gold_aig)} {_q(gate_aig)}\"",
                check=False
            )
        if "equivalent" in out.lower() and "not" not in out.lower():
            return True, None
        return False, out[-600:]

    # ── stat query ────────────────────────────────────────────────────────────

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

    # ── private ───────────────────────────────────────────────────────────────

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
        bin_dir = Path(self.yosys_bin).parent if self.yosys_bin != "yosys" else None
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

    def _optional_dff_lib(self, *verilog_paths: str):
        return _DffLibContext(*verilog_paths)

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

        if not replacements:
            return None

        fixed = text
        for (name, idx), wire in sorted(replacements.items(), key=lambda item: (-len(item[0][0]), item[0][1])):
            fixed = re.sub(rf"(?<![\w$]){re.escape(name)}\[{idx}\]", wire, fixed)

        decls = "".join(f"  wire {wire};\n" for wire in sorted(replacements.values()))
        insert = re.search(r"(?m)^\s*(?:and|or|nand|nor|xor|xnor|not|buf|dff)\s+", fixed)
        if insert:
            fixed = fixed[:insert.start()] + decls + fixed[insert.start():]
        else:
            fixed = fixed.replace("endmodule", decls + "endmodule", 1)

        fd, path = tempfile.mkstemp(
            suffix="_preprocessed.v",
            text=True,
            dir=os.getcwd(),
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(fixed)
        return path


def _q(path: str) -> str:
    return '"' + Path(path).as_posix().replace('"', '\\"') + '"'


class _DffLibContext:
    """Create a temporary blackbox dff module only when the source needs it."""

    _TEXT = """\
(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
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
            dir=os.getcwd(),
        )
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(self._TEXT)
        self.path = path
        return path

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.path and os.path.exists(self.path):
            os.unlink(self.path)
