# 0009. The plane stores no step window or per-policy statistics

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-15 |
| Deciders | Susheem Koul |

## Context

The ported TokenOps ledger kept two more tables: `trace_state` (a step counter, a window of the
last 64 steps and a spend velocity, fed by `step` events) and `trace_policy_stats` (per-policy
numeric aggregates such as context-compaction savings). Both are derived from calls that
Chronicle already records as spans and envelopes, and the review of the schema asked which tables
need to exist at all.

## Options considered

### Option A: keep both tables

- Good: TokenOps gets a window and statistics in one precheck.
- Bad: a second copy of what the envelopes hold; a `step` event per call; a table per trace to
  prune.

### Option B: drop both

- Good: two tables, one event kind and one precheck slice fewer; the trace, span and envelope
  tables stay the only record of what happened.
- Bad: TokenOps policies that need a window (velocity, step caps by count) must derive it from
  traces or keep it in process.

## Decision

We will drop `trace_state` and `trace_policy_stats`, the `step` event kind, the `window` slice of
`precheck`, and the `state` and `policy_stats` fields of the per-trace view. The run list shows
`span_count` where it showed `steps`. An unknown event kind is a `400`.

## Consequences

- **Good:** the ledger is spend, in-flight counts and the halt, nothing else.
- **Bad or risky:** TokenOps 0.4 cannot send `step` events or read a window; its rework decides
  whether it needs a derived read route.
- **Follow-ups:** a read route over spans and envelopes for velocity if TokenOps needs one.

## Impact on other repositories

- tokenops: stop sending `step`; compute step counts and velocity from traces if needed.
