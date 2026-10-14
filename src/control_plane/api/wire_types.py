"""Request bodies for the governance and admin routes.

The Chronicle routes take the primitives entities directly. These wrappers belong to the control
plane (primitives ADR 0003).
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from control_plane.models import BudgetSpec, Dimension, PolicyInstance, Segment


class SegmentBody(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    dimension: Dimension = "trace"
    tag_key: str | None = None
    match_value: str | None = None

    def to_model(self) -> Segment:
        return Segment(**self.model_dump())


class BudgetBody(BaseModel):
    id: str = Field(min_length=1)
    limit_micros: int | None = None
    dimension: Dimension = "trace"
    tag_key: str | None = None
    period: str = "lifetime"

    def to_model(self) -> BudgetSpec:
        return BudgetSpec(**self.model_dump())


class PolicyBody(BaseModel):
    id: str = Field(min_length=1)
    template: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    agent: str | None = None
    budget_id: str | None = None
    segment_id: str | None = None
    enabled: bool = True
    data_scope: Literal["local", "global"] = "local"

    def to_model(self) -> PolicyInstance:
        return PolicyInstance(**self.model_dump())


class PrecheckBody(BaseModel):
    trace_id: str = Field(min_length=1)
    segment_keys: list[str] = Field(default_factory=list)
    budgets: list[dict[str, Any]] = Field(default_factory=list)
    want: list[str] = Field(default_factory=lambda: ["spent", "inflight", "halt"])


class EventsBody(BaseModel):
    events: list[dict[str, Any]]


class HaltBody(BaseModel):
    reason: str = ""


class KeyCreateBody(BaseModel):
    name: str = Field(min_length=1)
    tenant_id: str = "local"
    scopes: list[str] = Field(default_factory=lambda: ["ingest", "read"])


class GovernanceSeedBody(BaseModel):
    governance: dict[str, Any] | None = None
