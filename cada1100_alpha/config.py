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
import re
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
    fallback_provider: str   = ""
    fallback_api_key:  str   = ""
    fallback_base_url: str   = ""
    fallback_model:    str   = ""


@dataclass
class VerificationConfig:
    yosys_timeout_sec: int = 240
    equiv_timeout_sec: int = 120
    cone_timeout_sec: int = 20
    robust_total_timeout_sec: int = 240
    large_cone_threshold: int = 5000


@dataclass
class SystemConfig:
    llm:          LLMConfig = field(default_factory=LLMConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    yosys_bin:    str       = "yosys"
    verbose:      bool      = False


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
        api_key           = _resolve_key(raw, provider, path),
        base_url          = _resolve_base_url(raw, provider, path),
        model             = _resolve_model(raw, provider),
        temperature       = float(gen.get("temperature", 0.2)),
        max_output_tokens = int(gen.get("max_output_tokens", 4096)),
        fallback_provider = raw.get("fallback_provider", os.environ.get("FALLBACK_PROVIDER", "")),
        fallback_api_key  = raw.get("fallback_api_key", os.environ.get("FALLBACK_API_KEY", "")),
        fallback_base_url = raw.get("fallback_base_url", os.environ.get("FALLBACK_BASE_URL", "")),
        fallback_model    = raw.get("fallback_model", os.environ.get("FALLBACK_MODEL", "")),
    )
    ver_raw = raw.get("verification", {}) if isinstance(raw.get("verification", {}), dict) else {}
    verification = VerificationConfig(
        yosys_timeout_sec = int(ver_raw.get("yosys_timeout_sec", 240)),
        equiv_timeout_sec = int(ver_raw.get("equiv_timeout_sec", 120)),
        cone_timeout_sec  = int(ver_raw.get("cone_timeout_sec", 20)),
        robust_total_timeout_sec = int(ver_raw.get("robust_total_timeout_sec", 240)),
        large_cone_threshold = int(ver_raw.get("large_cone_threshold", 5000)),
    )

    cfg = SystemConfig(
        llm          = llm,
        verification = verification,
        yosys_bin    = raw.get("yosys_bin", "yosys"),
        verbose      = bool(raw.get("verbose", False)),
    )
    return cfg


# Private helpers

def _parse_yaml(path: str) -> dict:
    if yaml is not None:
        with open(path, encoding="utf-8-sig") as f:
            return yaml.safe_load(f) or {}
    # Minimal fallback parser for key: value (no nesting support)
    # Install PyYAML with: pip install pyyaml
    result: dict = {}
    current_section: Optional[str] = None
    with open(path, encoding="utf-8-sig") as f:
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


def _resolve_key(raw: dict, provider: str, config_path: str = "") -> str:
    section = raw.get(provider, {})
    if isinstance(section, dict):
        key = section.get("api_key", "")
        if key and not _is_placeholder(key):
            return key
    key_file_key, _ = _resolve_from_apikey_file(config_path, provider)
    if key_file_key:
        return key_file_key
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


def _resolve_base_url(raw: dict, provider: str, config_path: str = "") -> str:
    section = raw.get(provider, {})
    if isinstance(section, dict):
        base_url = section.get("base_url", "")
        if base_url and not _is_placeholder(base_url):
            return _normalize_openai_base_url(str(base_url), provider)
    _, key_file_url = _resolve_from_apikey_file(config_path, provider)
    if key_file_url:
        return _normalize_openai_base_url(key_file_url, provider)
    if provider == "openai":
        return _normalize_openai_base_url(os.environ.get("OPENAI_BASE_URL", ""), provider)
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
    return ""


def _normalize_openai_base_url(base_url: str, provider: str) -> str:
    base_url = str(base_url or "").strip().rstrip("/")
    if provider != "openai" or not base_url:
        return base_url
    if base_url.endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


def _resolve_model(raw: dict, provider: str) -> str:
    section = raw.get(provider, {})
    if isinstance(section, dict) and "model" in section:
        return section["model"]
    defaults = {"openai": "gpt-4o-mini", "anthropic": "claude-haiku-4-5"}
    return defaults.get(provider, "gpt-4o-mini")


def _resolve_from_apikey_file(config_path: str, provider: str) -> tuple[str, str]:
    """Read local apikey.txt entries such as 'gpt...apikey: sk-...' plus url."""
    candidates = []
    if config_path:
        candidates.append(Path(config_path).resolve().with_name("apikey.txt"))
    candidates.append(Path.cwd() / "apikey.txt")
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        entry = _parse_apikey_file(resolved, provider)
        if entry[0]:
            return entry
    return "", ""


def _parse_apikey_file(path: Path, provider: str) -> tuple[str, str]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return "", ""
    entries: list[dict[str, str]] = []
    for line in text.splitlines():
        keys = re.findall(r"sk-[A-Za-z0-9_-]+", line)
        urls = re.findall(r"https?://[^\s]+", line)
        if keys:
            entries.append({"key": keys[0], "url": urls[0] if urls else "", "label": line.lower()})
            continue
        if urls and entries and not entries[-1].get("url"):
            entries[-1]["url"] = urls[0]
    if not entries:
        return "", ""
    preferred = ("gpt", "openai") if provider == "openai" else ("claude", "anthropic")
    for entry in entries:
        if any(mark in entry.get("label", "") for mark in preferred):
            return entry.get("key", ""), entry.get("url", "")
    return entries[0].get("key", ""), entries[0].get("url", "")


def _is_placeholder(value: str) -> bool:
    value = str(value).strip()
    return value.startswith("<YOUR_") or value in {"<YOUR_API_KEY>", ""}
