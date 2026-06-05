"""
config.py
=========
Parse the contest YAML configuration file.

Expected format (from contest spec Section 6.2):

  provider: "openai"   # or "anthropic"
  openai:
    api_key: <YOUR_API_KEY>
    base_url: <OPTIONAL_OPENAI_COMPATIBLE_URL>
    model:   "gpt-4o-mini"
  anthropic:
    api_key: <YOUR_API_KEY>
    model:   "claude-haiku-4-5"
  generation:
    temperature:        0.2
    max_output_tokens:  4096

Additional optional fields:
  yosys_bin: "/usr/bin/yosys"   # default: "yosys" (resolved from $PATH)
  verbose:   true               # enable agent debug output
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None   # type: ignore


@dataclass
class LLMConfig:
    provider:          str   = "anthropic"
    api_key:           str   = ""
    base_url:          str   = ""
    model:             str   = "claude-haiku-4-5"
    temperature:       float = 0.2
    max_output_tokens: int   = 4096


@dataclass
class SystemConfig:
    llm:       LLMConfig = field(default_factory=LLMConfig)
    yosys_bin: str       = "yosys"
    verbose:   bool      = False


def load_config(path: str) -> SystemConfig:
    """
    Parse the YAML config file and return a SystemConfig.

    Falls back to environment variables if the YAML key is absent:
      OPENAI_API_KEY / ANTHROPIC_API_KEY
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = _parse_yaml(path)

    provider = raw.get("provider", "anthropic").lower()
    gen      = raw.get("generation", {})

    llm = LLMConfig(
        provider          = provider,
        api_key           = _resolve_key(raw, provider),
        base_url          = _resolve_base_url(raw, provider),
        model             = _resolve_model(raw, provider),
        temperature       = float(gen.get("temperature", 0.2)),
        max_output_tokens = int(gen.get("max_output_tokens", 4096)),
    )

    cfg = SystemConfig(
        llm       = llm,
        yosys_bin = raw.get("yosys_bin", "yosys"),
        verbose   = bool(raw.get("verbose", False)),
    )
    return cfg


# ── private helpers ───────────────────────────────────────────────────────────

def _parse_yaml(path: str) -> dict:
    if yaml is not None:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    # Minimal fallback parser for key: value (no nesting support)
    # Install PyYAML with: pip install pyyaml
    result: dict = {}
    current_section: Optional[str] = None
    with open(path) as f:
        for line in f:
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("  ") or line.startswith("\t"):
                # Nested key under current_section
                stripped = line.strip()
                if ":" in stripped and current_section:
                    k, _, v = stripped.partition(":")
                    result.setdefault(current_section, {})[k.strip()] = _coerce(v.strip())
            else:
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if v == "":
                    current_section = k
                    result[k] = {}
                else:
                    result[k] = _coerce(v)
                    current_section = None
    return result


def _coerce(v: str):
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v.strip('"').strip("'")


def _resolve_key(raw: dict, provider: str) -> str:
    section = raw.get(provider, {})
    if isinstance(section, dict):
        key = section.get("api_key", "")
        if key and not _is_placeholder(key):
            return key
    # Fall back to environment
    env_map = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
    env_key = os.environ.get(env_map.get(provider, ""), "")
    if not env_key:
        raise ValueError(
            f"No API key found for provider '{provider}'. "
            f"Set it in the config file or via the "
            f"{env_map.get(provider, 'API_KEY')} environment variable."
        )
    return env_key


def _resolve_base_url(raw: dict, provider: str) -> str:
    section = raw.get(provider, {})
    if isinstance(section, dict):
        base_url = section.get("base_url", "")
        if base_url and not _is_placeholder(base_url):
            return str(base_url)
    return ""


def _resolve_model(raw: dict, provider: str) -> str:
    section = raw.get(provider, {})
    if isinstance(section, dict) and "model" in section:
        return section["model"]
    defaults = {"openai": "gpt-4o-mini", "anthropic": "claude-haiku-4-5"}
    return defaults.get(provider, "gpt-4o-mini")


def _is_placeholder(value: str) -> bool:
    value = str(value).strip()
    return value.startswith("<YOUR_") or value in {"<YOUR_API_KEY>", ""}
