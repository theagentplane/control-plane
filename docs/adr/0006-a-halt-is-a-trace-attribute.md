# 0006. A halt is an attribute of the trace, not a table

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-15 |
| Deciders | Susheem Koul |

## Context

TokenOps halts a run when a policy trips. The control plane first kept that in a `ledger_halt`
table keyed by `trace_id`. A halt is one flag and a reason for one trace, so a second table meant a
second place to keep in sync with `traces` (and to prune). Chronicle must not write it: it records
what happened, it does not govern.

## Options considered

### Option A: a `ledger_halt` table

- Good: no change in primitives.
- Bad: drifts from the trace; one more table to prune and to join for every run list.

### Option B: columns on `traces` that exist only in this service

- Good: one row per trace.
- Bad: the stored trace and the primitives `Trace` entity disagree, so the table cannot stay in
  sync with the entity.

### Option C: `Trace.attributes`, a general-purpose dict added to primitives (ADR 0007 there)

- Good: the state is part of the entity and travels with it; no side table.
- Bad: needs a primitives release; untyped values.

## Decision

We will store the halt in `traces.attributes` (a JSON column), under the keys `tokenops.halted`
(boolean) and `tokenops.halt_reason` (string). The halt routes and the `halt_mark` and `halt_clear`
events write it; `precheck`, the run list and `halted_only` read it. Setting a halt on a trace
that has no row yet creates the row. The column mirrors the primitives `Trace.attributes` field;
this service stores and serves the dict itself, so it works on primitives 0.1 and the pin moves to
0.2 when that is released.

## Consequences

- **Good:** one fewer table; the halt is visible on `GET /v1/traces/{id}`; pruning a trace removes
  its halt.
- **Bad or risky:** the halt is read-modify-write on a dict, so two writers of different keys on
  one trace can overwrite each other (last write wins); `halted_only` filters a JSON value and
  is not indexed.
- **Follow-ups:** an index on the flag if the halted list becomes slow on a large database.

## Impact on other repositories

- primitives: adds `Trace.attributes` (primitives ADR 0007). Pin moves to `0.2.*` on release.
- tokenops: reads the halt through the ledger routes as before; the keys are namespaced to it.
- chronicle: none; it never writes attributes.
