"""Conformal LEC backend: fourth-level combinational CEC fallback.

Used by ``EDABackend._check_graphs_boundary_equiv`` when the monolithic
ABC cec and Yosys equiv chain cannot decide a boundary miter and the
per-request budget still allows a commercial-engine attempt.

Fail-closed contract (identical to yosys_backend.EquivResult usage):
  * PASS  only for an explicit "all compared points equivalent" report;
  * FAIL  only for an explicit non-equivalence report;
  * license failures, startup problems, aborts, unmapped points, and
    unparseable output are UNKNOWN/TIMEOUT/ERROR — never FAIL;
  * never retries (each attempt may burn tens of seconds);
  * never raises.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from .yosys_backend import EquivResult, safe_temp_dir

_DFF_DEF = "module c_dff(input RN, input SN, input CK, input D, output Q); endmodule\n"


def preprocess_for_lec(text: str) -> str:
    """Make a netlist LEC-safe.

    Conformal treats ``dff`` as a built-in primitive, which rejects the
    contest DFF instances (named ports RN/SN/CK/D/Q).  Rename dff instances
    to c_dff, drop any emitted ``module dff ... endmodule`` definition, and
    prepend a blackbox module for c_dff so LEC compares at its pins (Q is a
    source, D is a sink — the submission's boundary semantics).
    """
    out_lines: list[str] = []
    in_dff_module = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if re.match(r"^\s*module\s+dff\b", line):
            in_dff_module = True
            continue
        if in_dff_module:
            if re.match(r"^\s*endmodule\b", line.strip()):
                in_dff_module = False
            continue
        line = re.sub(r"^(\s*)dff(\s+\S+\s*\()", r"\1c_dff\2", line)
        out_lines.append(line)
    out = "\n".join(out_lines) + "\n"
    if not out.startswith(_DFF_DEF):
        out = _DFF_DEF + out
    return out


def build_dofile(gold: str, gate: str, log_path: str) -> str:
    """Minimal two-design combinational CEC dofile."""
    return (
        f'set log file "{log_path}" -replace\n'
        "set system mode setup\n"
        f'read design -verilog2k "{gold}" -golden\n'
        f'read design -verilog2k "{gate}" -revised\n'
        "set flatten model -gated_clock\n"
        "add notranslate module c_dff -both\n"
        "set system mode lec\n"
        "add compare points -all\n"
        "compare\n"
        "report verification\n"
        "exit -f\n"
    )


def classify_lec_output(text: str) -> tuple[str, str]:
    """Map a LEC transcript to (status, message).

    LEC progress lines ("// 5% Comparing 5 out of 99 points, 0
    Non-equivalent") contain the word Non-equivalent even on success, so
    the summary table is the authoritative source; progress lines are only
    consulted for a positive non-equivalent count.
    """
    norm = text.replace("\r", "\n")
    low = norm.lower()
    if re.search(
        r"cannot checkout|license (checkout )?(fail|denied|unavailable|error)"
        r"|failed to get license|unable to obtain a license|license server",
        low,
    ):
        return "UNKNOWN", "lec: license unavailable"
    if re.search(r"\bsyntax error\b|fatal error.*(reading|parsing)", low):
        return "ERROR", "lec: script or netlist error"

    # The transcript contains several "compared points" phrases (command
    # echoes like "// 887 compared points added to compare list" and the
    # progress lines); the authoritative summary table is the LAST header
    # line, printed after compare finishes.
    matches = list(
        re.finditer(
            r"^[ \t]*compared points[^\n]*\n", norm,
            re.IGNORECASE | re.MULTILINE,
        )
    )
    if matches:
        m = matches[-1]
        tail = norm[m.end():m.end() + 3000]

        def row_count(label: str) -> Optional[list[int]]:
            mm = re.search(
                rf"^\s*{label}\s+([\d\s]+)$", tail, re.IGNORECASE | re.MULTILINE
            )
            if not mm:
                return None
            return [int(x) for x in mm.group(1).split()]

        eq = row_count("equivalent")
        ne = row_count(r"non-?equivalent")
        un = row_count("unmapped")
        ab = row_count("aborted")
        nc = row_count(r"not[ -]compared")
        ud = row_count("undetermined")
        uv = row_count("unverified")
        ex = row_count("extra")
        if ne and any(v > 0 for v in ne):
            return "FAIL", f"lec: non-equivalent points {ne}"
        if un and any(v > 0 for v in un):
            return "UNKNOWN", f"lec: unmapped points {un}"
        if ab and any(v > 0 for v in ab):
            return "UNKNOWN", f"lec: aborted points {ab}"
        # Uncovered rows (some Conformal versions split them out) must block
        # a PASS even when the Equivalent row is non-zero.
        if nc and any(v > 0 for v in nc):
            return "UNKNOWN", f"lec: not compared points {nc}"
        if ud and any(v > 0 for v in ud):
            return "UNKNOWN", f"lec: undetermined points {ud}"
        if uv and any(v > 0 for v in uv):
            return "UNKNOWN", f"lec: unverified points {uv}"
        if ex and any(v > 0 for v in ex):
            return "UNKNOWN", f"lec: extra points {ex}"
        if eq and any(v > 0 for v in eq):
            inc = re.search(r"incomplete verification:\s*(\d+)", low)
            if inc and int(inc.group(1)) > 0:
                return "UNKNOWN", f"lec: incomplete verification {inc.group(1)}"
            amb = re.search(r"design ambiguity:\s*(\d+)", low)
            if amb and int(amb.group(1)) > 0:
                return "UNKNOWN", f"lec: design ambiguity {amb.group(1)}"
            return "PASS", f"lec: equivalent ({eq})"
        return "UNKNOWN", "lec: nothing compared"

    # No summary table: fall back to progress lines and markers.
    if re.search(r"\bunverified\b|\bextra points\b|\bnot mapped\b", low):
        return "UNKNOWN", "lec: unverified or extra comparison rows"
    for mm in re.finditer(
        r"comparing \d+ out of \d+ points, (\d+) non-?equivalent", low
    ):
        if int(mm.group(1)) > 0:
            return "FAIL", "lec: reported non-equivalent"
    # No summary table: the transcript is not authoritative proof.  Progress
    # lines print "0 Non-equivalent" even on success, and version-dependent
    # error text may mention "equivalent", so no marker may authorise a PASS.
    # Fail closed.
    return "UNKNOWN", "lec: no verifiable outcome"


def _kill_process_tree(proc: "subprocess.Popen") -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def check_equiv_lec(
    gold_v: str,
    gate_v: str,
    gold_top: str = "boundary_top",
    gate_top: str = "boundary_top",
    timeout: Optional[float] = None,
    lec_bin: str = "lec",
) -> EquivResult:
    """Run Conformal LEC on two gate-level Verilog netlists.

    Never raises.  gold_top/gate_top are accepted for signature symmetry
    with the other engines; both files must already name their top module
    consistently (the boundary writer always uses ``boundary_top``).
    """
    t0 = time.monotonic()
    if timeout is None:
        timeout = 120.0
    bin_path = shutil.which(lec_bin)
    if bin_path is None and os.path.isfile(lec_bin):
        bin_path = lec_bin
    if bin_path is None and "PYTEST_CURRENT_TEST" not in os.environ:
        try:
            from eda.host_probe import probe_host_tools
            probed = probe_host_tools()
            if probed.lec and probed.lec_bin and os.path.isfile(probed.lec_bin):
                bin_path = probed.lec_bin
        except Exception:
            bin_path = None
    if bin_path is None:
        return EquivResult("UNKNOWN", "lec binary not found on PATH", "lec", 0.0)

    workdir = tempfile.TemporaryDirectory(prefix="lec_", dir=safe_temp_dir())
    try:
        wd = Path(workdir.name)
        gold_proc = wd / "gold_proc.v"
        gate_proc = wd / "gate_proc.v"
        try:
            gold_text = Path(gold_v).read_text(encoding="utf-8", errors="replace")
            gate_text = Path(gate_v).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return EquivResult("ERROR", f"lec: cannot read inputs: {e}", "lec", 0.0)
        gold_proc.write_text(preprocess_for_lec(gold_text), encoding="utf-8")
        gate_proc.write_text(preprocess_for_lec(gate_text), encoding="utf-8")

        log_path = wd / "lec.log"
        dofile = wd / "lec.do"
        dofile.write_text(
            build_dofile(str(gold_proc), str(gate_proc), str(log_path)),
            encoding="utf-8",
        )

        env = dict(os.environ)
        env["HOME"] = str(wd)
        env["TMPDIR"] = str(wd)
        env["TMP"] = str(wd)

        argv = [bin_path, "-nogui", "-dofile", str(dofile)]
        if "PYTEST_CURRENT_TEST" not in os.environ:
            try:
                from eda.host_probe import lec_preload
                argv = [
                    "bash", "-lc",
                    f"{lec_preload()}{shlex.quote(bin_path)} "
                    f"-nogui -dofile {shlex.quote(str(dofile))}",
                ]
            except Exception:
                argv = [bin_path, "-nogui", "-dofile", str(dofile)]

        proc = subprocess.Popen(
            argv,
            cwd=str(wd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            proc.wait()
            return EquivResult(
                "TIMEOUT", f"lec timeout after {timeout:.0f}s", "lec",
                round(time.monotonic() - t0, 2),
            )

        text = (out or "") + "\n"
        if log_path.exists():
            text += log_path.read_text(encoding="utf-8", errors="replace")
        status, message = classify_lec_output(text)
        if status == "UNKNOWN" and "license" in message.lower():
            print("lec: license unavailable", file=sys.stderr)
        return EquivResult(
            status, message, "lec", round(time.monotonic() - t0, 2)
        )
    finally:
        workdir.cleanup()
