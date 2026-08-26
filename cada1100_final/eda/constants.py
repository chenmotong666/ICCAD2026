"""
eda/constants.py
================
Canonical constants shared across the eda and agent packages.

Gate primitives
---------------
The contest gate set: AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF, DFF.
Every primitive has exactly one output (2-input for binary ops, 1-input for unary).
"""

from __future__ import annotations


GATE_PRIMITIVES: tuple[str, ...] = (
    "and", "or", "not", "nand", "nor", "xor", "xnor", "buf", "dff",
)

# Binary-input primitives (excludes NOT, BUF, DFF)
BINARY_GATE_PRIMITIVES: tuple[str, ...] = (
    "and", "or", "nand", "nor", "xor", "xnor",
)

# Unary primitives
UNARY_GATE_PRIMITIVES: tuple[str, ...] = ("not", "buf")

# Primitive set as frozenset for O(1) membership
GATE_PRIMITIVES_SET: frozenset[str] = frozenset(GATE_PRIMITIVES)


YOSYS_TO_PRIM: dict[str, str] = {
    "$and":  "and",  "$or":   "or",  "$nand": "nand", "$nor":  "nor",
    "$xor":  "xor",  "$xnor": "xnor","$not":  "not",  "$buf":  "buf",
    "$dff":  "dff",  "$adff": "dff", "$sdff": "dff",  "$dffe": "dff",
    "dff":   "dff",
}

PRIM_TO_YOSYS: dict[str, str] = {
    "and":"$and","or":"$or","nand":"$nand","nor":"$nor",
    "xor":"$xor","xnor":"$xnor","not":"$not","buf":"$buf","dff":"$dff",
}


DFF_TYPES: frozenset[str] = frozenset({"$dff", "$adff", "$sdff", "$dffe", "dff"})

# Canonical DFF data-input port names (R43: moved here from backend.py so
# the transformer can honour the same register boundary when picking a
# balance-buffer edge).  Single source of truth for every depth/boundary
# tool so the register boundary is defined identically everywhere.
DFF_DATA_PORTS: frozenset[str] = frozenset({"D", "DATA", "DIN", "D_IN", "I0"})

# Canonical DFF data-input port names (R43: moved here from backend.py so
# the transformer can honour the same register boundary when picking a
# balance-buffer edge).  Single source of truth for every depth/boundary
# tool so the register boundary is defined identically everywhere.
DFF_DATA_PORTS: frozenset[str] = frozenset({"D", "DATA", "DIN", "D_IN", "I0"})


# Style -> allowed combinational gate types (Yosys $-prefixed).  DFFs are
# always allowed and are checked separately.  Single source of truth for
# every style-compliance check (eda.optimizer and eda.backend).  Iteration
# order matters: _whole_design_style() returns the FIRST matching style.
STYLE_ALLOWED_GATES: dict[str, frozenset[str]] = {
    "nand_not":   frozenset({"$nand", "$not"}),
    "nor_not":    frozenset({"$nor",  "$not"}),
    "and_not":    frozenset({"$and",  "$not"}),
    "and_or_not": frozenset({"$and",  "$or", "$not"}),
}


CONST_0 = "CONST_0"
CONST_1 = "CONST_1"
CONST_X = "CONST_X"
CONST_Z = "CONST_Z"


class ToolCategory:
    """Categories for tool classification in the three-tier subset system."""
    IO = "io"
    SUMMARY = "summary"
    DEPTH = "depth"
    PATH = "path"
    CONE = "cone"
    GATE = "gate"
    STRUCTURAL = "structural"
    RENAME = "rename"
    DFF_CLOCK = "dff_clock"
    TRANSFORM = "transform"
    OPTIMIZE = "optimize"
    VERIFY = "verify"
    MISC = "misc"

# Tools that are safe for analysis-only requests (excludes transforms/optimize)
ANALYSIS_CATEGORIES: frozenset[str] = frozenset({
    ToolCategory.IO,
    ToolCategory.SUMMARY,
    ToolCategory.DEPTH,
    ToolCategory.PATH,
    ToolCategory.CONE,
    ToolCategory.GATE,
    ToolCategory.STRUCTURAL,
    ToolCategory.RENAME,
    ToolCategory.DFF_CLOCK,
    ToolCategory.VERIFY,
    ToolCategory.MISC,
})

# Basic tools for simple informational requests
BASIC_CATEGORIES: frozenset[str] = frozenset({
    ToolCategory.IO,
    ToolCategory.SUMMARY,
    ToolCategory.DEPTH,
    ToolCategory.PATH,
    ToolCategory.CONE,
    ToolCategory.GATE,
    ToolCategory.DFF_CLOCK,
    ToolCategory.RENAME,
    ToolCategory.VERIFY,
})

# Medium tools: basic + structural (for queries about paths, equiv, symmetry)
MEDIUM_CATEGORIES: frozenset[str] = frozenset({
    ToolCategory.IO,
    ToolCategory.SUMMARY,
    ToolCategory.DEPTH,
    ToolCategory.PATH,
    ToolCategory.CONE,
    ToolCategory.GATE,
    ToolCategory.DFF_CLOCK,
    ToolCategory.RENAME,
    ToolCategory.VERIFY,
    ToolCategory.STRUCTURAL,
})
