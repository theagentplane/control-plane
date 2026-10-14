"""Governance configuration models: segments, budgets and policy instances.

These are what TokenOps pulls with ``GET /v1/governance/{agent}``. A run is a Chronicle trace,
so the segment dimension that used to be ``run`` is ``trace``.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

Dimension = Literal["trace", "user", "agent", "tenant", "tag"]
DIMENSIONS: tuple[str, ...] = ("trace", "user", "agent", "tenant", "tag")
DATA_SCOPES: tuple[str, ...] = ("local", "global")


@dataclass
class Segment:
    """A named slice of traces a budget or policy applies to."""

    id: str
    name: str
    dimension: Dimension = "trace"
    tag_key: str | None = None
    match_value: str | None = None


@dataclass
class BudgetSpec:
    """A spend cap. ``limit_micros`` is micro-USD; ``None`` is an accumulator with no cap."""

    id: str
    limit_micros: int | None
    dimension: Dimension = "trace"
    tag_key: str | None = None
    period: str = "lifetime"


@dataclass
class PolicyInstance:
    """One configured policy template, optionally scoped to an agent, budget and segment."""

    id: str
    template: str
    params: dict[str, Any] = field(default_factory=dict)
    agent: str | None = None
    budget_id: str | None = None
    segment_id: str | None = None
    enabled: bool = True
    data_scope: str = "local"
    """``local``: the SDK reads its own state; ``global``: it reads the plane via ``precheck``."""
