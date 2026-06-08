"""
eda/tool_metadata.py
====================
Decorator-based tool metadata registry.

Usage
-----
    from eda.tool_metadata import tool

    class EDABackend:
        @tool(description="Load a gate-level Verilog netlist.",
              category=ToolCategory.IO, history_limit=600)
        def read_design(self, path: str) -> str: ...

The ``@tool`` decorator attaches a ``_tool_meta`` dict to the method.
After the class is defined, ``collect_tool_metadata(cls)`` scans all
methods and returns a dict keyed by method name.

This single source of truth replaces three previously-manual registries:
  1. TOOL_SPECS          (agent/tool_schema.py)  -LLM tool definitions
  2. _DISPATCH_MAP        (agent/react_agent.py)  -tool->method dispatch
  3. _TOOL_CATEGORY_LIMITS (agent/react_agent.py) -history truncation limits
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional


# Sentinel for "not set"
_UNSET = object()

# Default history-truncation limit when not specified
DEFAULT_HISTORY_LIMIT = 800


def tool(
    description: str,
    *,
    category: str = "analysis",
    history_limit: int = DEFAULT_HISTORY_LIMIT,
    tool_name: Optional[str] = None,
) -> Callable:
    """Decorator that attaches tool metadata to a backend method.

    Parameters
    ----------
    description : str
        Human-readable description shown to the LLM in the tool definition.
    category : str
        One of the ``ToolCategory`` values.  Used to derive the three-tier
        tool subsets (basic / analysis-only / full-transform).
    history_limit : int
        Character limit when truncating this tool's result for conversation
        history.  Default 800.
    tool_name : str or None
        Optional override for the LLM-facing tool name.  When None (default),
        the Python method name is used directly -eliminating the old
        name-mapping gap between TOOL_SPECS and _DISPATCH_MAP.
    """
    def decorator(fn: Callable) -> Callable:
        meta = {
            "description": description,
            "category": category,
            "history_limit": history_limit,
            "tool_name": tool_name if tool_name is not None else fn.__name__,
            "method_name": fn.__name__,
            "takes_kwargs": _infer_takes_kwargs(fn),
            "parameters": _infer_parameters(fn),
        }
        fn._tool_meta = meta  # type: ignore[attr-defined]
        return fn
    return decorator


def _infer_takes_kwargs(fn: Callable) -> bool:
    """Return True when the method has parameters beyond `self`."""
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return False
    params = list(sig.parameters.keys())
    # Remove 'self' (or 'cls' for classmethods)
    if params and params[0] in ("self", "cls"):
        params = params[1:]
    return len(params) > 0


def _infer_parameters(fn: Callable) -> dict[str, dict[str, Any]]:
    """Infer parameter schema from a function's signature.

    Returns a dict mapping param_name ->{type, description} suitable for
    merging into a TOOL_SPECS entry.  If inference fails, returns {}.

    Only parameters beyond ``self`` are included.
    """
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return {}

    params = list(sig.parameters.values())
    # Skip self/cls
    if params and params[0].name in ("self", "cls"):
        params = params[1:]

    result: dict[str, dict[str, Any]] = {}
    for p in params:
        info: dict[str, Any] = {"type": _python_type_to_json_type(p.annotation)}
        if p.default is not inspect.Parameter.empty:
            info["default"] = p.default
        result[p.name] = info
    return result


def _python_type_to_json_type(annotation: Any) -> str:
    """Map Python type annotations to JSON Schema type strings."""
    if annotation is inspect.Parameter.empty:
        return "string"
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        return "array"
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
    }
    return type_map.get(annotation, "string")



def collect_tool_metadata(cls: type) -> dict[str, dict]:
    """Scan all methods of *cls* for ``@tool`` decorators.

    Returns a dict mapping ``method_name`` ->``_tool_meta`` dict.
    Only includes methods that were decorated with ``@tool``.
    """
    result: dict[str, dict] = {}
    for name in dir(cls):
        if name.startswith("_"):
            continue
        attr = getattr(cls, name, None)
        if attr is None:
            continue
        meta = getattr(attr, "_tool_meta", None)
        if meta is not None:
            result[name] = meta
    return result


def build_tool_specs(cls: type) -> list[dict]:
    """Build TOOL_SPECS list from ``@tool``-decorated methods on *cls*.

    Returns a list of canonical tool-spec dicts, exactly matching the
    structure previously maintained manually in ``agent/tool_schema.py``.
    """
    all_meta = collect_tool_metadata(cls)
    specs: list[dict] = []
    for method_name, meta in sorted(all_meta.items(), key=lambda x: x[0]):
        # Build required list from parameters that have no default
        required: list[str] = []
        for pname, pinfo in meta["parameters"].items():
            if "default" not in pinfo:
                required.append(pname)

        spec: dict[str, Any] = {
            "name": meta["tool_name"],
            "description": meta["description"],
            "parameters": {
                pname: _clean_param_info(pinfo)
                for pname, pinfo in meta["parameters"].items()
            },
            "required": required,
        }
        # Attach internal metadata (not sent to LLM, used by agent layer)
        spec["_method_name"] = meta["method_name"]
        spec["_category"] = meta["category"]
        spec["_history_limit"] = meta["history_limit"]
        specs.append(spec)
    return specs


def build_dispatch_map(cls: type) -> dict[str, tuple[str, bool]]:
    """Build dispatch map from ``@tool``-decorated methods on *cls*.

    Returns ``{tool_name: (method_name, takes_kwargs)}`` -the same
    structure as the old manually-maintained ``_DISPATCH_MAP``.
    """
    all_meta = collect_tool_metadata(cls)
    result: dict[str, tuple[str, bool]] = {}
    for method_name, meta in all_meta.items():
        result[meta["tool_name"]] = (meta["method_name"], meta["takes_kwargs"])
    return result


def build_category_limits(cls: type) -> dict[str, int]:
    """Build per-tool history-truncation limits from ``@tool`` decorators.

    Returns ``{tool_name: history_limit}`` -the same structure as the
    old manually-maintained ``_TOOL_CATEGORY_LIMITS``.
    """
    all_meta = collect_tool_metadata(cls)
    return {
        meta["tool_name"]: meta["history_limit"]
        for meta in all_meta.values()
    }


def build_analysis_only_set(cls: type) -> set[str]:
    """Return the set of tool names that are safe for analysis-only requests.

    Excludes transform and optimize tools.
    """
    from .constants import ANALYSIS_CATEGORIES

    all_meta = collect_tool_metadata(cls)
    return {
        meta["tool_name"]
        for meta in all_meta.values()
        if meta["category"] in ANALYSIS_CATEGORIES
    }


def build_basic_set(cls: type) -> set[str]:
    """Return the set of tool names for basic informational requests."""
    from .constants import BASIC_CATEGORIES

    all_meta = collect_tool_metadata(cls)
    return {
        meta["tool_name"]
        for meta in all_meta.values()
        if meta["category"] in BASIC_CATEGORIES
    }


def _clean_param_info(pinfo: dict) -> dict:
    """Remove internal fields from parameter info before sending to LLM."""
    return {
        k: v for k, v in pinfo.items()
        if k not in ("default",)
    }
