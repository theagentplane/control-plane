# 0008. Spend and in-flight counts stay as ledger tables

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-15 |
| Deciders | Susheem Koul |

## Context

Spend and in-flight counts could be derived from the envelopes Chronicle already stores. The
question came up during the table-by-table review of the schema.

## Options considered

### Option A: derive both from envelopes

- Good: no ledger tables; one source of truth.
- Bad: envelopes carry tokens and a model, not dollars, so the plane would need its own price
  table that can differ from TokenOps' one; pre-call envelopes are only written when
  `CHRONICLE_EMIT_OPEN` is on (default off), so in-flight counts would need that on everywhere;
  and envelopes arrive in asynchronous batches, so a derived total lags the call it must gate.

### Option B: keep `ledger_spent` and `ledger_inflight` as counters

- Good: single atomic updates, read in one precheck; cost in the unit policies are written in;
  no dependence on Chronicle's emit settings.
- Bad: two stores of "what happened" that can disagree with the envelopes.

## Decision

We will keep `ledger_spent` and `ledger_inflight` as counter tables written through the ledger
events, and read by `precheck`. They are not derived from envelopes.

## Consequences

- **Good:** a governor gets an exact, current number in one read.
- **Bad or risky:** the counters and the stored envelopes can drift (see ADR 0007); the
  counters are the governor's view, the envelopes are the record.
- **Follow-ups:** revisit when TokenOps expresses policies in tokens, or when Chronicle emits
  pre-call envelopes by default.

## Impact on other repositories

- tokenops: keeps writing `spent_add`, `admit` and `complete`.
