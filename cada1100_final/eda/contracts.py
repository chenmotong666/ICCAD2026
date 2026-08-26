"""Typed request and mutation constraints shared by the agent and EDA backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


Scope = Literal["design", "cone", "net"]
CostMetric = Literal["depth", "gate_count", "fanout"]


@dataclass(frozen=True)
class StyleConstraint:
    """A persistent primitive-style requirement on a design or one cone."""

    style: str
    scope: Scope = "design"
    target: str = ""

    def normalized(self) -> "StyleConstraint":
        return StyleConstraint(
            style=self.style.strip().lower().replace("-", "_"),
            scope=self.scope,
            target=self.target.strip(),
        )


@dataclass(frozen=True)
class FanoutConstraint:
    """A maximum fanout bound, optionally restricted to one buffer tree."""

    max_fanout: int
    scope: Scope = "design"
    target: str = ""
    include_primary_inputs: bool = True


@dataclass(frozen=True)
class CostObjective:
    """The only metric that should lead candidate selection for a request."""

    metric: CostMetric
    scope: Scope = "design"
    target: str = ""
    direction: Literal["min"] = "min"
    # Hard upper bound declared by the request (e.g. "depth ≤ 5").  When set
    # on a depth objective it is persisted as a cumulative hard constraint
    # (Q&A A63); None means unconstrained.
    threshold: Optional[int] = None


@dataclass(frozen=True)
class ForbiddenPrimitiveConstraint:
    """Standing library exclusion: the netlist must not contain these primitives.

    Distinct from MutationContract.excluded_types, which is a one-request
    "do not touch existing XOR gates" scope invariant and must not persist
    (F-12).  A later style remap that *requires* a forbidden primitive
    overrides this constraint.
    """

    primitives: frozenset[str]


@dataclass(frozen=True)
class RenameConstraint:
    """A renamed identifier that must survive later transformations.

    ``anchor`` locates the renamed object in later (possibly regenerated)
    netlists: for a gate it is the gate's output net name at rename time,
    for a wire it is the pre-rename driver node id.  Per Q&A A61 the
    constraint is satisfied either when the identifier exists or when the
    anchored object has been eliminated by the transformation itself.
    """

    kind: Literal["gate", "wire"]
    name: str
    anchor: str = ""
    old_name: str = ""


@dataclass
class MutationContract:
    """Validation contract attached to one state-changing user request."""

    preserve_function: bool = True
    style_constraints: list[StyleConstraint] = field(default_factory=list)
    fanout_constraint: Optional[FanoutConstraint] = None
    cost_objective: Optional[CostObjective] = None
    # Gate primitives the request explicitly asked NOT to modify
    # (e.g. "but do not replace OR gates").  Enforced as a scope invariant
    # after the batch: any count change on these types rolls the batch back.
    excluded_types: frozenset[str] = frozenset()
    # Standing "must not contain XOR" library exclusions (A63).  Not the
    # same as excluded_types.
    forbidden_primitives: frozenset[str] = frozenset()
    label: str = ""
    before_digest: str = ""
    after_digest: str = ""
    validated: bool = False
    validation_detail: str = ""

    def summary(self) -> str:
        pieces = [f"preserve={int(self.preserve_function)}"]
        if self.style_constraints:
            pieces.append(
                "styles=" + ",".join(
                    f"{row.scope}:{row.target or '*'}={row.style}"
                    for row in self.style_constraints
                )
            )
        if self.fanout_constraint is not None:
            row = self.fanout_constraint
            pieces.append(
                f"fanout={row.scope}:{row.target or '*'}<={row.max_fanout}"
            )
        if self.excluded_types:
            pieces.append("exclude=" + ",".join(sorted(self.excluded_types)))
        if self.cost_objective is not None:
            row = self.cost_objective
            pieces.append(f"cost={row.scope}:{row.target or '*'}:{row.metric}")
        pieces.append(f"validated={int(self.validated)}")
        return " ".join(pieces)
