"""Fail-closed, once-per-process host EDA binary probe.

Results live only in memory.  Probe failures never write to stdout
(the contest #RESPONSE stream) and never raise into a testcase.

Host User Manual (2026): ``module load dc ; dc_shell-t`` and
``module load conformal ; lec``.  DC is available when the binary is
found after that preload (no ``-x exit`` license checkout).  LEC
startup (license checkout) is lazy: init is which-only; the first CEC
demand runs a 10s ``-nogui -dofile`` probe.  Setup-mode ``exit -f``
returns rc=2 on CONFRML 24.10, so availability is decided from the
transcript, never from the exit code.

Probe and DC wrappers run in a new process group and SIGKILL the tree
on timeout so a hung checkout cannot outlive the deadline.

Execute-bit convention for the bundled OSS-CAD suite: ``bin/*`` and
``lib/ld-linux-x86-64.so.2`` must be +x.  ``lib/yosys-abc`` ships
without +x so Yosys' internal abc pass errors out (it is not a silent
skip).  ``libexec/*`` is loaded by ld-linux and does not need +x.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_PROBE_TIMEOUT_SEC = 3.0
_LEC_PROBE_TIMEOUT_SEC = 10.0
_MODULE_WHICH_TIMEOUT_SEC = 8.0
_PROBE_DEADLINE_SEC = 25.0

_DEFAULT_DC_PRELOAD = (
    ". /etc/bashrc >/dev/null 2>&1; "
    ". /etc/profile.d/modules.sh >/dev/null 2>&1; "
    "module load dc lc >/dev/null 2>&1; "
)
_DEFAULT_LEC_PRELOAD = (
    ". /etc/bashrc >/dev/null 2>&1; "
    ". /etc/profile.d/modules.sh >/dev/null 2>&1; "
    "module load conformal >/dev/null 2>&1; "
)

_LEC_STARTED_RE = re.compile(
    r"conformal|\bcheck out conformal|version\s+\d+\.\d+-p\d+",
    re.IGNORECASE,
)
_LEC_LICENSE_FAIL_RE = re.compile(
    r"cannot checkout|license (checkout )?(fail|denied|unavailable|error)"
    r"|failed to get license|unable to obtain a license|license server",
    re.IGNORECASE,
)


def _float_env(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0.0 else default


def module_which_timeout_sec() -> float:
    return _float_env("CADA_MODULE_WHICH_TIMEOUT_SEC", _MODULE_WHICH_TIMEOUT_SEC)


def lec_probe_timeout_sec() -> float:
    return _float_env("CADA_LEC_PROBE_TIMEOUT_SEC", _LEC_PROBE_TIMEOUT_SEC)


def probe_deadline_sec() -> float:
    return _float_env("CADA_PROBE_DEADLINE_SEC", _PROBE_DEADLINE_SEC)


_REPROBE_BACKOFF_SEC_DEFAULT = 120.0
_REPROBE_BACKOFF_CAP_SEC_DEFAULT = 600.0
# kind ("dc" / "lec") -> monotonic time of the last FAILED forced re-probe.
_LAST_REPROBE_FAIL: dict[str, float] = {}
# kind -> consecutive failed forced re-probes (R43 escalating backoff).
_REPROBE_FAIL_RUN: dict[str, int] = {}


def reprobe_backoff_sec() -> float:
    return _float_env("CADA_REPROBE_BACKOFF_SEC", _REPROBE_BACKOFF_SEC_DEFAULT)


def reprobe_backoff_cap_sec() -> float:
    return _float_env("CADA_REPROBE_BACKOFF_CAP_SEC", _REPROBE_BACKOFF_CAP_SEC_DEFAULT)


def _effective_reprobe_backoff_sec(kind: str) -> float:
    """R43: consecutive failed re-probes double their backoff window (capped
    at CADA_REPROBE_BACKOFF_CAP_SEC), so a license that never comes back
    stops burning the probe fuse at every window edge.  A success resets the
    run.  An explicit CADA_REPROBE_BACKOFF_SEC keeps the historical fixed
    window for config-level overrides."""
    if "CADA_REPROBE_BACKOFF_SEC" in os.environ:
        return reprobe_backoff_sec()
    runs = _REPROBE_FAIL_RUN.get(kind, 1)
    return min(
        reprobe_backoff_sec() * (2 ** max(0, runs - 1)),
        reprobe_backoff_cap_sec(),
    )


def reprobe_suppressed(kind: str) -> bool:
    """True while a failed forced re-probe is inside its backoff window.

    R42 F4: with a persistently absent license, re-probing on every
    request would burn up to the 25s probe fuse each time.  A successful
    re-probe clears the record.  Pytest keeps the historical per-request
    semantics (never suppress).
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return False
    last = _LAST_REPROBE_FAIL.get(kind)
    if last is None:
        return False
    return (time.monotonic() - last) < _effective_reprobe_backoff_sec(kind)


def reprobe_recovery_due(kind: str) -> bool:
    """True when a recorded failed re-probe has aged past the backoff.

    R42 F4: lets a once-per-process DC re-probe recover when the license
    becomes available mid-session, without re-opening the window after a
    success (no recorded failure -> never due).  Pytest keeps the
    historical one-shot semantics.
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return False
    last = _LAST_REPROBE_FAIL.get(kind)
    if last is None:
        return False
    return (time.monotonic() - last) >= _effective_reprobe_backoff_sec(kind)


def note_reprobe_outcome(kind: str, ok: bool) -> None:
    """Record a forced re-probe outcome for the backoff window."""
    if ok:
        _LAST_REPROBE_FAIL.pop(kind, None)
        _REPROBE_FAIL_RUN.pop(kind, None)
    else:
        _LAST_REPROBE_FAIL[kind] = time.monotonic()
        _REPROBE_FAIL_RUN[kind] = _REPROBE_FAIL_RUN.get(kind, 0) + 1


@dataclass(frozen=True)
class HostProbe:
    yosys: bool
    yosys_abc: bool
    lec: bool
    dc_shell: bool
    abc_bin: str
    yosys_bin: str
    lec_bin: str
    dc_bin: str


_PROBE: Optional[HostProbe] = None
_LAST_LEC: Optional[bool] = None


def unavailable_host_probe() -> HostProbe:
    """Fail-closed probe: every binary is treated as missing."""
    return HostProbe(
        yosys=False,
        yosys_abc=False,
        lec=False,
        dc_shell=False,
        abc_bin="",
        yosys_bin="",
        lec_bin="",
        dc_bin="",
    )


def dc_preload() -> str:
    """Shell prefix that loads Design Compiler (and LC) per the host manual."""
    return os.environ.get("CADA_DC_PRELOAD", _DEFAULT_DC_PRELOAD)


def lec_preload() -> str:
    """Shell prefix that loads Conformal LEC per the host manual."""
    return os.environ.get("CADA_LEC_PRELOAD", _DEFAULT_LEC_PRELOAD)


def _kill_process_tree(proc: "subprocess.Popen") -> None:
    """SIGKILL the session so bash -lc grandchildren cannot keep a license."""
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def run_in_process_group(
    argv: list[str],
    *,
    timeout: float,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    text: bool = False,
) -> Optional[subprocess.CompletedProcess]:
    """Run argv in a new session; kill the group on timeout.  Never raises.

    Returns a CompletedProcess on a timely exit, or None on timeout / error.
    Used by host probes and the online DC 90s compile_ultra wrapper so a hung
    license checkout cannot outlive the deadline.
    """
    proc: Optional[subprocess.Popen] = None
    try:
        popen_kwargs: dict = {
            "stdout": stdout,
            "stderr": stderr,
            "cwd": cwd,
            "env": env,
            "text": text,
        }
        if text:
            popen_kwargs["encoding"] = "utf-8"
            popen_kwargs["errors"] = "replace"
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, **popen_kwargs)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            try:
                out, err = proc.communicate(timeout=2.0)
            except Exception:
                out, err = ("" if text else b""), ("" if text else b"")
            return None
        return subprocess.CompletedProcess(argv, int(proc.returncode or 0), out, err)
    except Exception:
        if proc is not None:
            _kill_process_tree(proc)
        return None


def _silent_run(argv: list[str], timeout: float = _PROBE_TIMEOUT_SEC) -> bool:
    """Return True iff the process exits 0 within timeout.  Never raises."""
    completed = run_in_process_group(argv, timeout=timeout)
    return bool(completed is not None and completed.returncode == 0)


def _which_exec(name: str) -> str:
    path = shutil.which(name) or ""
    if path and os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    if os.path.isfile(name) and os.access(name, os.X_OK):
        return os.path.abspath(name)
    return ""


def resolve_external_abc() -> str:
    """Return a +x yosys-abc path.  Never selects ``lib/yosys-abc``.

    The packaged ``lib/yosys-abc`` wrapper ships without +x (R32
    convention).  Miss-path Yosys ``abc -exe`` must use ``bin/yosys-abc``.
    """
    try:
        existing = (os.environ.get("ABC") or "").strip()
        if (
            existing
            and os.path.isfile(existing)
            and os.access(existing, os.X_OK)
            and "/lib/yosys-abc" not in existing.replace("\\", "/")
        ):
            return existing
        candidates: list[str] = []
        yosys = _which_exec("yosys")
        if yosys:
            candidates.append(str(Path(yosys).with_name("yosys-abc")))
        root = Path(__file__).resolve().parents[1]
        candidates.append(str(root / "tools" / "oss-cad-suite" / "bin" / "yosys-abc"))
        abc_which = _which_exec("yosys-abc")
        if abc_which:
            candidates.append(abc_which)
        for cand in candidates:
            if not cand or not os.path.isfile(cand) or not os.access(cand, os.X_OK):
                continue
            norm = cand.replace("\\", "/")
            if norm.endswith("/lib/yosys-abc") or "/lib/yosys-abc" in norm:
                continue
            return cand
        return ""
    except Exception:
        return ""


def apply_abc_path() -> str:
    """Point ABC at a +x yosys-abc if the env var is missing or unusable.

    Prefers the bundled oss-cad-suite binary (has execute bit) over
    ``lib/yosys-abc`` which ships without +x.  The ``ABC`` env var is
    honoured by some standalone ABC wrappers; Yosys' internal ``abc``
    pass ignores it and uses the compile-time ``lib/yosys-abc`` path.
    """
    try:
        found = resolve_external_abc()
        if found:
            os.environ["ABC"] = found
            return found
        return (os.environ.get("ABC") or "").strip()
    except Exception:
        return ""


def _module_which(preload: str, name: str, timeout: Optional[float] = None) -> str:
    """Return an executable path found after ``preload``, else ''.

    A timeout (``run_in_process_group`` returning None, typically a cold
    NFS ``module load``) is retried once.  A completed non-zero ``which``
    is not retried.  ``timeout`` may be clamped by the 25s probe fuse.
    """
    timeout = float(timeout) if timeout is not None else module_which_timeout_sec()
    if timeout < 1.0:
        return ""
    for _attempt in range(2):
        try:
            completed = run_in_process_group(
                ["bash", "-lc", f"{preload}command -v {shlex.quote(name)}"],
                timeout=timeout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            return ""
        if completed is None:
            continue
        path = (
            (completed.stdout or "").strip().splitlines()[-1].strip()
            if completed.stdout
            else ""
        )
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        return ""
    return ""


def _probe_dc_shell(*, timeout: Optional[float] = None) -> str:
    """Return dc_shell-t if which/+x succeed.  Does not run ``-x exit``."""
    direct = _which_exec("dc_shell-t") or _which_exec("dc_shell")
    if direct:
        return direct
    return (
        _module_which(dc_preload(), "dc_shell-t", timeout=timeout)
        or _module_which(dc_preload(), "dc_shell", timeout=timeout)
    )


def _probe_lec_bin(*, timeout: Optional[float] = None) -> str:
    """Return lec if PATH or ``module load conformal`` can see it."""
    direct = _which_exec("lec")
    if direct:
        return direct
    return _module_which(lec_preload(), "lec", timeout=timeout)


def classify_lec_probe_output(text: str) -> bool:
    """True iff the transcript shows LEC started and the license is usable.

    CONFRML 24.10 returns rc=2 for a setup-mode ``exit -f`` dofile, so the
    exit code is ignored.  A license-failure phrase is fail-closed.
    """
    blob = text or ""
    if _LEC_LICENSE_FAIL_RE.search(blob):
        return False
    return bool(_LEC_STARTED_RE.search(blob))


def _probe_lec(lec_bin: str) -> bool:
    """True only if lec starts (license checkout) within the probe timeout.

    A tiny dofile forces the real startup path.  ``-nogui`` keeps the
    process off X11.  Availability is decided from the transcript.
    """
    if not lec_bin:
        return False
    try:
        # R42 F3: honour the safe_temp_dir fallback chain -- a read-only
        # system TMPDIR must not masquerade as an unavailable LEC.  Lazy
        # import: yosys_backend imports host_probe at module level.
        try:
            from eda.yosys_backend import safe_temp_dir
            tmp_parent: Optional[str] = safe_temp_dir()
        except Exception:
            tmp_parent = None
        with tempfile.TemporaryDirectory(prefix="lec_probe_", dir=tmp_parent) as tmp:
            dofile = os.path.join(tmp, "probe.do")
            with open(dofile, "w", encoding="utf-8") as handle:
                handle.write("exit -f\n")
            cmd = (
                f"{lec_preload()}{shlex.quote(lec_bin)} "
                f"-nogui -dofile {shlex.quote(dofile)}"
            )
            completed = run_in_process_group(
                ["bash", "-lc", cmd],
                timeout=lec_probe_timeout_sec(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if completed is None:
                return False
            return classify_lec_probe_output(completed.stdout or "")
    except Exception:
        return False


def probe_host_tools(
    *,
    force: bool = False,
    startup_lec: bool = True,
) -> HostProbe:
    """Probe yosys / yosys-abc / lec / dc_shell-t once.  Fail-closed.

    ``startup_lec=False`` performs which-only LEC discovery (used at
    ``EDABackend`` init so the 60s first-request window is not charged a
    license checkout).  The first CEC demand re-enters with
    ``force=True, startup_lec=True``.
    """
    global _PROBE
    if _PROBE is not None and not force:
        return _PROBE
    # R46 G14: measure probe wall time so license-queue waits are visible
    # in post-run traces without any stdout impact.
    _probe_t0 = time.monotonic()
    deadline = _probe_t0 + probe_deadline_sec()

    def _budget_left() -> float:
        return deadline - time.monotonic()

    abc_bin = apply_abc_path()
    yosys_bin = _which_exec("yosys")
    lec_bin = ""
    dc_bin = ""
    yosys_ok = False
    if _budget_left() > 1.0:
        yosys_ok = bool(yosys_bin) and _silent_run([yosys_bin, "-V"])
    abc_ok = bool(abc_bin) and os.access(abc_bin, os.X_OK)
    in_pytest = "PYTEST_CURRENT_TEST" in os.environ
    lec_ok = False

    def _capped_which_timeout() -> float:
        return min(module_which_timeout_sec(), max(0.0, _budget_left() - 0.5))

    # DC before LEC which: LEC license checkout is already lazy; a cold
    # conformal module load must not starve the 25s fuse of dc_shell.
    try:
        if not in_pytest and _budget_left() > 1.0:
            dc_bin = _probe_dc_shell(timeout=_capped_which_timeout())
    except Exception:
        dc_bin = ""
    try:
        if not in_pytest and _budget_left() > 1.0:
            lec_bin = _probe_lec_bin(timeout=_capped_which_timeout())
            if lec_bin and startup_lec and _budget_left() > 1.0:
                lec_ok = _probe_lec(lec_bin)
    except Exception:
        lec_bin = ""
        lec_ok = False
    # R42 F4: a DC-only re-probe (startup_lec=False) must not erase a
    # previously confirmed LEC availability; the real license checkout is
    # still exercised (and budget-bounded) by every actual LEC attempt.
    if not startup_lec and lec_bin and _PROBE is not None and _PROBE.lec:
        lec_ok = True
    global _LAST_LEC
    if (
        _LAST_LEC is False
        and lec_ok
        and not in_pytest
    ):
        print(
            f"[host_probe] lec became available after earlier miss "
            f"(probe_wall_s={time.monotonic() - _probe_t0:.1f}, "
            f"force={int(bool(force))})",
            file=sys.stderr,
        )
    _LAST_LEC = bool(lec_ok)
    _PROBE = HostProbe(
        yosys=bool(yosys_ok),
        yosys_abc=bool(abc_ok),
        lec=bool(lec_ok),
        dc_shell=bool(dc_bin),
        abc_bin=abc_bin or "",
        yosys_bin=yosys_bin or "",
        lec_bin=lec_bin or "",
        dc_bin=dc_bin or "",
    )
    return _PROBE


def reset_host_probe_for_tests() -> None:
    """Test helper: drop the process-wide cache."""
    global _PROBE, _LAST_LEC
    _PROBE = None
    _LAST_LEC = None
    _LAST_REPROBE_FAIL.clear()
