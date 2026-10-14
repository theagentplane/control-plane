"""Known TokenOps policy template names (the allow-list for configuration writes).

The governors themselves live in TokenOps; the control plane only validates the template name so
a typo fails at write time instead of silently never applying.
"""

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
