# Governance API (what TokenOps pulls and writes)

Governance is the configuration an agent running TokenOps fetches, and the shared ledger it writes
across processes. A run is a Chronicle trace, so everything per run is keyed by `trace_id`; there
is no run registration and no run record. Everything is tenant-scoped.

## The pull

`GET /v1/governance/{agent}` (scope `read`) returns exactly the dict `build_governor` consumes
for that agent:

```json
{"governance": {
  "budgets":  [{"id": "run_llm_cap", "limit_micros": 2000000, "dimension": "trace", "period": "lifetime"}],
  "policies": {"cost_budget": {"budget": "run_llm_cap", "data_scope": "local"},
               "step_cap":    {"max_steps": 20, "data_scope": "local"}}
}}
```

- Every **enabled** policy instance scoped to this agent, or to all agents, is included, with
  the budgets it references. An attached segment is resolved into `dimension` and `tag_key`. One
  instance per template (the last wins).
- The response has an `ETag`. Send it back as `If-None-Match` to get `304` when nothing changed.
- `data_scope` is `local` (the SDK reads its own state) or `global` (it reads the plane through
  `precheck`).

### Where the configuration comes from

It is in the database. Three ways to put it there:

| Way | How |
|---|---|
| First start | The packaged `default.yaml` is seeded for tenant `local` (`CONTROL_PLANE_SEED_ON_START`) |
| CLI | `control-plane db seed [--file my.yaml] [--tenant acme] [--reset]` |
| API or web page | `PUT /v1/budgets`, `/v1/policies`, `/v1/segments` (scope `admin`), or the TokenOps tab |

## Configuration CRUD

| Resource | Routes |
|---|---|
| Segments | `GET /v1/segments`, `PUT /v1/segments`, `GET` and `DELETE /v1/segments/{id}` |
| Budgets | `GET /v1/budgets`, `PUT /v1/budgets`, `GET` and `DELETE /v1/budgets/{id}` |
| Policies | `GET /v1/policies`, `PUT /v1/policies`, `GET` and `DELETE /v1/policies/{id}` |

`dimension` is one of `trace`, `user`, `agent`, `tenant`, `tag`. A policy `template` must be one
of the known TokenOps templates, otherwise the write is a `400`.

## The ledger

| Route | Scope | Purpose |
|---|---|---|
| `POST /v1/ledger/precheck` | read | One read for a pre-call pass: `halted`, `spent`, `inflight`, `window` |
| `POST /v1/ledger/events:batch` | ingest | One write per crossing |
| `GET /v1/ledger/traces` | read | Recent traces with halt, spend and step count (the TokenOps run list); `halted_only` |
| `GET /v1/ledger/traces/{trace_id}` | read | One trace's halt, state and per-policy statistics |
| `GET`, `POST`, `DELETE /v1/ledger/traces/{trace_id}/halt` | read, ingest, admin | Read, set and clear a halt |

### `precheck`

```json
{"trace_id": "<32 hex>", "segment_keys": ["trace:<id>"],
 "budgets": [{"budget_id": "run_llm_cap", "segment_key": "trace:<id>", "period": "lifetime"}],
 "want": ["spent", "inflight", "halt", "window"]}
```

### `events:batch`

`{"events": [...]}`. Applied **in array order, in one transaction**; a malformed event is a `400`
and nothing in the batch is applied. Every event needs an `idempotency_key`; a replayed key (per
tenant) is counted in `deduped` and not applied again. The response has `accepted`, `deduped`,
`atomic`, `totals` (post-commit spend for every accumulator a `spent_add` names) and `halted`.

| `kind` | Fields |
|---|---|
| `spent_add` | `trace_id`, `delta_micros`, `targets: [{budget_id, segment_key, period}]` |
| `admit`, `complete` | `trace_id`, `segment_key` (in-flight counter, never below zero) |
| `step` | `trace_id`, `agent`, `seq`, `cost_micros`, `cum_spent_micros`, `compaction?` ... |
| `halt_mark` | `trace_id`, `reason` |
| `halt_clear` | `trace_id` |

`step` events feed the per-trace window (the last 64 steps and a spend velocity) and, when they
carry `compaction`, the per-policy statistics.

## Segment keys

Segment keys are opaque to the service (the client computes them). The convention is
`trace:<trace_id>`, `user:<user>`, `agent:<service>`, `tenant:<tenant>`, `tag:<k>=<v>`.
`GET /v1/ledger/traces` reads spend from the `trace:<trace_id>` key.
