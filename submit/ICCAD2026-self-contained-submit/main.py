"""
main.py
=======
Contest entry point for ICCAD 2026 Problem A.

Invocation (per contest spec):
    ./cada0606_alpha -config <config_file_path>

Behaviour
---------
  1. Parse -config argument; load YAML config.
  2. Initialise YosysBackend, EDABackend, LLMClient, ReactAgent.
  3. Read natural-language requests from stdin, one per line.
  4. For each request:
       a. Call agent.run(request) to get the answer text.
       b. Print #RESPONSE <id> / answer / #END <id> to stdout.
       c. Mirror the same output to <case_name>.log.
  5. The contest evaluator sends the next request only after it sees #END <id>
     on stdout, so we flush stdout after every response.

Testcase initialisation
-----------------------
  The first request in every testcase looks like:
    "This is the beginning of testcase case28. Please output a copy of the log into case28.log."
  We detect this pattern, extract the case name, and open the log file.
  If the pattern is not detected, we use "unknown_case" as the fallback name.

Signal handling
---------------
  SIGTERM / SIGINT: flush log and exit cleanly.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add project root to path when run as a script
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config, SystemConfig
from eda.backend import EDABackend
from agent.llm_client import LLMClient
from agent.react_agent import ReactAgent


# Log writer

class LogWriter:
    """Mirrors stdout #RESPONSE blocks to a log file."""

    def __init__(self) -> None:
        self._fh = None
        self._path: str = ""

    def open(self, path: str) -> None:
        if self._fh:
            self._fh.close()
        self._path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._fh = open(path, "w", encoding="utf-8")

    def write(self, text: str) -> None:
        if self._fh:
            self._fh.write(text)
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    @property
    def path(self) -> str:
        return self._path


# Response formatter

def emit_response(response_id: int, text: str, log: LogWriter) -> None:
    """
    Print a #RESPONSE / #END block to stdout and mirror it to the log.
    Flushes stdout so the evaluator receives #END immediately.
    """
    block = (
        f"#RESPONSE {response_id}\n"
        f"{text.strip()}\n"
        f"#END {response_id}\n"
    )
    print(block, end="", flush=True)
    log.write(block)


# Testcase name extraction

_CASE_NAME_PATTERNS = [
    re.compile(r"\bcase\s+name\s*(?:is|:)\s*['\"]?([\w\-]+)", re.IGNORECASE),
    re.compile(r"\bcase(?:name|_name)\s*(?:is|:)?\s*['\"]?([\w\-]+)", re.IGNORECASE),
    re.compile(r"\bbeginning of (?:testcase|test case)\s+['\"]?([\w\-]+)", re.IGNORECASE),
]
_LOG_NAME_RE = re.compile(r"([\w\-]+\.log)", re.IGNORECASE)


def extract_case_info(request: str) -> tuple[Optional[str], Optional[str]]:
    """
    Try to extract (case_name, log_file_name) from the first request.
    Returns (None, None) if the pattern is not found.
    """
    case_name: Optional[str] = None
    log_name:  Optional[str] = None

    for pattern in _CASE_NAME_PATTERNS:
        m = pattern.search(request)
        if m:
            case_name = m.group(1)
            break

    m2 = _LOG_NAME_RE.search(request)
    if m2:
        log_name = m2.group(1)
    elif case_name:
        log_name = f"{case_name}.log"

    return case_name, log_name


# Main loop

def build_system(cfg: SystemConfig) -> tuple[EDABackend, ReactAgent]:
    """Initialise all subsystems from config."""
    backend = EDABackend(yosys_bin=cfg.yosys_bin)
    llm     = LLMClient(
        provider          = cfg.llm.provider,
        api_key           = cfg.llm.api_key,
        base_url          = cfg.llm.base_url,
        model             = cfg.llm.model,
        temperature       = cfg.llm.temperature,
        max_output_tokens = cfg.llm.max_output_tokens,
    )
    agent = ReactAgent(llm, backend, verbose=cfg.verbose)
    return backend, agent


def run(cfg: SystemConfig) -> None:
    backend, agent = build_system(cfg)
    log = LogWriter()

    response_id = 0
    case_name   = "unknown_case"

    def _shutdown(sig, frame):
        log.close()
        _emit_token_usage(agent)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    for raw_line in sys.stdin:
        request = raw_line.rstrip("\n")
        if not request:
            continue

        response_id += 1

        if response_id == 1:
            agent.reset()
            extracted_name, log_file = extract_case_info(request)

            if extracted_name:
                case_name = extracted_name
            if log_file:
                log.open(log_file)
            else:
                log.open(f"{case_name}.log")

            # Produce a direct acknowledgement for response 1
            ack = f"OK. Testcase '{case_name}' ready. Log: {log.path}."
            emit_response(response_id, ack, log)

            # Backend/log state carries testcase context; avoid replaying the
            # acknowledgement in later LLM turns.
            continue

        t0 = time.monotonic()
        try:
            answer = agent.run(request)
        except Exception as e:
            answer = f"Internal error processing request: {e}"

        elapsed = time.monotonic() - t0
        if cfg.verbose:
            print(f"[TIMING] response {response_id}: {elapsed:.2f}s", file=sys.stderr)

        emit_response(response_id, answer, log)

    log.close()
    _emit_token_usage(agent)


def _emit_token_usage(agent: ReactAgent) -> None:
    usage = agent.llm.usage_summary()
    print(
        "TOKEN_USAGE "
        f"prompt={usage['prompt_tokens']} "
        f"completion={usage['completion_tokens']} "
        f"total={usage['total_tokens']}",
        file=sys.stderr,
        flush=True,
    )


# Entry point

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ICCAD 2026 Contest Problem A - LLM-Assisted Netlist Exploration"
    )
    parser.add_argument(
        "-config", required=True, metavar="CONFIG_FILE",
        help="Path to the YAML configuration file (provider, API keys, model).",
    )
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    run(cfg)


if __name__ == "__main__":
    main()
