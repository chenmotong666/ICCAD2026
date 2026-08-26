"""
eda/decorators.py
=================
Shared decorators for the EDA backend that reduce boilerplate across
the ~68 public methods.

Usage
-----
    from eda.decorators import requires_design, catch_keyerror

    class EDABackend:
        @requires_design
        @catch_keyerror
        def get_max_depth(self, from_signal: str, to_signal: str) -> str:
            depth, path = self.graph.get_max_depth(from_signal, to_signal)
            ...
"""

from __future__ import annotations

import functools
from typing import Callable


def _fail(kind: str, message: str) -> str:
    """Format an error string, matching EDABackend._fail()."""
    if kind == "NOT_FOUND":
        return f"Not found: {message}"
    return f"UNKNOWN[{kind}]: {message}"


def requires_design(fn: Callable) -> Callable:
    """Decorator: call self._need_design() before invoking the method.

    Eliminates 68 manual ``self._need_design()`` calls across EDABackend.
    """
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        self._need_design()
        return fn(self, *args, **kwargs)
    return wrapper


def catch_keyerror(fn: Callable) -> Callable:
    """Decorator: convert KeyError from graph lookups to formatted error strings.

    Eliminates ~34 manual ``except KeyError as e: return self._fail(...)`` blocks.
    Only catches KeyError and ValueError -other exceptions propagate normally.
    """
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except KeyError as e:
            return _fail("NOT_FOUND", str(e))
        except ValueError as e:
            return _fail("INVALID", str(e))
    return wrapper
