# 0004. A run is a Chronicle trace; there is no run table

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-14 |
| Deciders | Susheem Koul |

Mirrors chronicle ADR 0009 ("`trace_id` is the only run identifier").

## Context

The service held two parallel models of "one request": Chronicle's traces (with spans and
envelopes) and TokenOps' runs (`runs`, `run_registrations`, run records, `run_id`). They carried the
same facts twice: identity and labels (`intent`, `user_dims`), status and timing, cost and step
counts. The two could disagree, and a client had to register a run and a trace separately.

## Options considered

### Option A: keep both and link them

- Good: no change for TokenOps.
- Bad: two sources of truth, a mapping nothing needs, and a second identifier.

### Option B: the run is the trace

- Good: one identifier; identity and labels are trace labels, written once at registration;
  status and timing are derived from spans; spend, steps and halts are ledger facts keyed by
  `trace_id`.
- Bad: breaks the TokenOps client until it is reworked.

## Decision

We will remove run registration and run records. Everything per run in the ledger is keyed by
`trace_id`; the TokenOps run list is the trace list joined with ledger facts
(`GET /v1/ledger/traces`). A governance event (a halt, a breach) is recorded where it happened:
the failing envelope carries `AbortCall` in `output.error`, and the halt reason is in the ledger.

## Consequences

- **Good:** one id across Chronicle, the control plane and TokenOps; no duplicated state.
- **Bad or risky:** TokenOps must send `trace_id` and stop using run registration; a run's
  "status" is derived rather than set by the client.
- **Follow-ups:** the TokenOps rework (separate repository).

## Impact on other repositories

- tokenops: replace `run_id` with `trace_id`; drop `POST /v1/runs` and run-record writes; read the
  run list from `GET /v1/ledger/traces`.
- chronicle: none.

## Links

Chronicle ADR 0009.
