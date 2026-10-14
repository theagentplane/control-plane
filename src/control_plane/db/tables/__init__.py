"""The tables, one module per group. Importing this package registers all of them."""

from control_plane.db.tables.auth_tables import api_keys
from control_plane.db.tables.chronicle_tables import envelopes, spans, trace_labels, traces
from control_plane.db.tables.governance_tables import (
    budgets,
    ledger_events,
    ledger_halt,
    ledger_inflight,
    ledger_spent,
    policy_instances,
    segments,
    trace_policy_stats,
    trace_state,
)

__all__ = [
    "api_keys",
    "budgets",
    "envelopes",
    "ledger_events",
    "ledger_halt",
    "ledger_inflight",
    "ledger_spent",
    "policy_instances",
    "segments",
    "spans",
    "trace_labels",
    "trace_policy_stats",
    "trace_state",
    "traces",
]
