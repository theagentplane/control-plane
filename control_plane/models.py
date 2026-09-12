"""Persisted models for governance, run history, and registration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

Dimension = Literal["run", "user", "agent", "tenant", "tag"]
RunStatus = Literal["running", "completed", "halted", "throttled", "error"]


class GovernanceMode(str, Enum):
    ENFORCE = "enforce"
    PREVIEW = "preview"


def parse_governance_mode(value: object) -> GovernanceMode:
    if value is None or value == "":
        return GovernanceMode.ENFORCE
    if isinstance(value, GovernanceMode):
        return value
    key = str(value).strip().lower()
    for mode in GovernanceMode:
        if key == mode.value:
            return mode
    raise ValueError(f"unknown governance mode: {value!r}")


@dataclass(frozen=True, kw_only=True)
class RunRegistration:
    run_id: str
    intent: str = ""
    user_dims: dict[str, str] = field(default_factory=dict)
    mode: GovernanceMode = GovernanceMode.ENFORCE
    registered_at: float = 0.0


class RunNotRegisteredError(LookupError):
    """Telemetry references a run_id that was never registered."""


class RunAlreadyRegisteredError(ValueError):
    """register_run was called twice for the same run_id."""


@dataclass
class Segment:
    id: str
    name: str
    dimension: Dimension = "run"
    tag_key: str | None = None
    match_value: str | None = None


@dataclass
class BudgetSpec:
    id: str
    limit_micros: int | None
    dimension: Dimension = "run"
    tag_key: str | None = None
    period: str = "lifetime"


@dataclass
class PolicyInstance:
    id: str
    template: str
    params: dict[str, Any] = field(default_factory=dict)
    agent: str | None = None
    budget_id: str | None = None
    segment_id: str | None = None
    enabled: bool = True
    #: Where the SDK runs this policy's detector — "local" (per-process LocalRunState)
    #: or "global" (plane aggregate via /v1/ledger/precheck). Contract §10.
    data_scope: str = "local"


@dataclass
class RunRecord:
    run_id: str
    agent: str
    status: RunStatus = "running"
    parent_run: str | None = None
    parent_span: str | None = None
    halt_reason: str | None = None
    detector: str | None = None
    cost_micros: int = 0
    steps: int = 0
    started_at: float = 0.0
    ended_at: float | None = None
    task: str | None = None
    dims: dict[str, str] = field(default_factory=dict)
    governance_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def problematic(self) -> bool:
        return self.status in ("halted", "throttled", "error")


@dataclass
class ApiKeyView:
    id: str
    name: str
    key_prefix: str
    tenant_id: str
    scopes: list[str]
    source: str
    created_at: float
    secret: str | None = None  # only on create / env listing
