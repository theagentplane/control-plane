"""Known TokenOps policy template names (allowlist for Admin / store upsert).

Governor builders stay in TokenOps; the plane only validates template names.
"""

from __future__ import annotations

POLICY_TEMPLATES: frozenset[str] = frozenset(
    {
        "cost_budget",
        "pre_call_worst_case",
        "step_cap",
        "concurrency_cap",
        "tool_fix",
        "tool_output_cap",
        "progress_guard",
        "cost_guard",
        "context_compaction",
        "output_runaway",
    }
)
