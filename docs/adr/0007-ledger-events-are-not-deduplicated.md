# 0007. Ledger events are not deduplicated

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-15 |
| Deciders | Susheem Koul |

## Context

The first design required an `idempotency_key` on every ledger event and recorded each key in a
`ledger_events` table, so a retried batch would not count twice. The table grows with every event
and has to be pruned, and it only protects clients that reuse keys across retries.

## Options considered

### Option A: keep the `ledger_events` table

- Good: a retried batch after a lost response does not double count.
- Bad: one row per event, forever; another table to prune; every client must generate stable keys.

### Option B: no deduplication

- Good: no table; no key to generate; a smaller write path.
- Bad: a batch retried after a lost response counts twice.

## Decision

We will remove `ledger_events` and the `idempotency_key` requirement. A batch is still applied
atomically, in order, in one transaction, so a failed request applies nothing and is safe to
retry; only a request whose response was lost is applied again.

## Consequences

- **Good:** fewer tables; simpler events.
- **Bad or risky:** the spend ledger can over-count by the cost of a replayed batch. Over-counting
  makes a budget trip early, not late, which is the safe direction for a cost guard.
- **Follow-ups:** if exact accounting is ever needed, derive spend from envelopes instead of
  adding keys back.

## Impact on other repositories

- tokenops: stops sending `idempotency_key` (it is ignored if present); the `deduped` field is
  gone from the response.
