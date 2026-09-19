# TokenOps ⇄ control-plane wire contract (0.2.0)

**Status:** authoritative for the 0.2.0 build.
**Applies to:** `agentplane-control-plane` **0.2.0** ⇄ `tokenops` **`<next>`** (remote-only).
**0.2.0 is additive** — new `precheck` / `events:batch` routes, new `ledger_events` /
`run_state` tables, `data_scope` column. 0.1-era clients keep working. The breaking
fold (drop `run_registrations`, remove `create_run`, narrow `PATCH`) is **0.3.0**,
shipped after `tokenops <next>`. See §13, §15.

Epic: theagentplane/control-plane#10 · TokenOps epic: theagentplane/tokenops#118.

---

## 1. Ratified decisions

| # | Decision |
|---|---|
| D1 | Consolidated read: `POST /v1/ledger/precheck`. |
| D2 | Batch write: `POST /v1/ledger/events:batch`. Single-op ledger routes become thin wrappers over the same apply path. |
| D3 | Idempotency key **required** on every write event; plane dedupes. |
| D4 | Run state (`steps`, `window`, `velocity`) lives on the plane (`run_state` table), fed by `step` events. The SDK keeps a per-process `LocalRunState` cache. |
| D5 | Policies carry `data_scope ∈ {local, global}`. `local` reads the SDK cache only; `global` reads the plane via `precheck`. |
| D6 | No client-side event buffer yet. `apply_events(list)` is the only write path so a buffer can wrap it later. |
| D7 | No accuracy-loss bounding. Budgets remain a soft cap. |
| D8 | `trajectory_hint` disabled → **no `trajectory/*` routes**. |
| D10 | Budget fan-out = **one** `spent_add` event carrying all `targets`; plane applies them in one transaction. |
| D11 | Inflight counter safety (call-id set) is out of scope → tokenops#116. This contract assumes synchronous `admit`/`complete`. |
| D12 | Identity columns move to `runs`, write-once; `create_run` removed — **staged**: deprecated shims in 0.2.x, `run_registrations` dropped and `create_run` removed in 0.3.0, after `tokenops <next>` (tokenops#117). |
| D13 | One `runs` row per `run_id` — no per-agent participation rows. Per-agent spend = an `agent`-dimension accumulator in `ledger_spent`. |
| D14 | `span_id` removed from TokenOps. Cross-hop link = `X-TokenOps-Run-Id` only. No `X-TokenOps-Parent-Span-Id`. |

---

## 2. Auth (unchanged from 0.1.x)

Bearer token → `Principal{tenant_id, scopes ⊆ {ingest, read, admin}}`.
Empty key table ⇒ anonymous `tenant="local"`, all scopes (local/dev/test only).
Isolation is **per-tenant**; no per-run ownership check (out of scope — see #6).

Route scopes: reads = `read`, ledger/run writes = `ingest`, halt-clear + admin = `admin`.

---

## 3. Conventions

- **Money:** micro-USD integers. `$1.00 == 1_000_000`. Field suffix `_micros`.
- **`segment_key`** (client-computed, opaque to the plane): `run:<run_id>`,
  `user:<user>`, `agent:<service>`, `tenant:<tenant>`, `tag:<k>=<v>`.
- **`spent` map key** in responses: `"<budget_id>|<segment_key>|<period>"`, `period`
  is `"lifetime"` for v1.
- **`run_id`** is the only cross-hop identifier. Header `X-TokenOps-Run-Id`.
- **`seq`** (in event keys): the emitting process's step counter at emit time —
  per-`(run_id, agent)` monotonic, regenerated identically on replay.
- Timestamps: client wall-clock float (`ts`), used for ordering/display only.

---

## 4. `POST /v1/ledger/precheck`  *(new — scope: read)*

The single read a `pre_call` pass needs. Request shape is fixed per governor, computed
once from the registered `global` detectors.

### Request
```jsonc
{
  "run_id": "run_abc",
  "segment_keys": ["run:run_abc", "agent:writer"],
  "budgets": [
    { "budget_id": "run_llm_cap", "segment_key": "run:run_abc", "period": "lifetime" }
  ],
  "want": ["spent", "inflight", "halt", "window"]   // window optional
}
```

### Response `200`
```jsonc
{
  "server_ts": 1732200000.12,
  "halted": false,
  "halt_reason": null,
  "spent":    { "run_llm_cap|run:run_abc|lifetime": 10500 },
  "inflight": { "run:run_abc": 0 },
  "window":   { "step_count": 3, "recent": [ /* BoundaryStep */ ],
                "velocity_micros_per_step": 3500 }   // only if "window" in want
}
```

- Read-only, no idempotency.
- `observe`-phase reads are **not** a second `precheck` — the `events:batch` ack
  returns fresh `totals`.

---

## 5. `POST /v1/ledger/events:batch`  *(new — scope: ingest)*

Mirrors the `envelopes:batch` shape. All ledger writes go through here.

### Headers
`Durability: sync | queued` — default `sync`. `queued` acks after the SQLite commit
(same as `sync` in 0.2.0; real queue is later).

### Request
```jsonc
{ "events": [ LedgerEvent, ... ] }   // applied strictly in array order
```

### `LedgerEvent` union
```jsonc
// common to every kind
{ "kind": "...", "idempotency_key": "<uuid|deterministic>", "ts": 1732200000.1, "run_id": "run_abc" }

// spent_add — one per priced crossing, carries ALL budget targets (D10)
{ "kind": "spent_add", "delta_micros": 10500,
  "targets": [ { "budget_id": "__run_total__", "segment_key": "run:run_abc", "period": "lifetime" },
               { "budget_id": "run_llm_cap",   "segment_key": "run:run_abc", "period": "lifetime" } ] }

// admit / complete — concurrency; synchronous only (D11)
{ "kind": "admit",    "segment_key": "run:run_abc" }
{ "kind": "complete", "segment_key": "run:run_abc" }

// step — the priced BoundaryStep; moves run window to the plane (D4)
{ "kind": "step", "agent": "researcher", "seq": 3, "node_type": "llm",
  "boundary_id": "researcher.chat", "cost_micros": 10500, "cum_spent_micros": 10500,
  "tool_signature": null, "result_hash": null,
  "usage": { "input": 1000, "output": 500 },
  "tags": { "provider": "anthropic", "model": "claude-sonnet-4-6" },
  "compaction": { "tokens_before": 12000, "tokens_after": 8000, "tokens_saved": 4000 } }  // optional (0.2.2)

// halt_mark / halt_clear
{ "kind": "halt_mark",  "reason": "step_cap: 20 steps", "detector": "step_cap" }
{ "kind": "halt_clear" }
```

### Response `201`
```jsonc
{
  "accepted": 3, "deduped": 0, "atomic": true,
  "totals": { "run_llm_cap|run:run_abc|lifetime": 15300 },
  "halted": false
}
```

### Semantics
- Events applied **in order**, in **one SQLite transaction** per batch (all-or-nothing).
  `"atomic": false` in the ack if the server had to fall back to per-event apply.
- `spent_add` → upsert every `target` in `ledger_spent`.
- `step` → upsert the `(tenant_id, run_id)` row in `run_state` (`step_count`, bounded
  `window_json` ring, velocity inputs, `last_ts`).
  If the step carries the optional `compaction` object, also sum its numeric fields
  (plus a `calls` count) into `run_policy_stats.stats_json` for policy `context_compaction`,
  keyed `(tenant_id, run_id, policy)`, in the same transaction. `runs` is not touched.
  Exposed as `policy_stats` on `GET /v1/run-records/{run_id}`. Deduped
  replays never reach this, so they do not double count. Values are chars/4 estimates.
- `halt_mark` / `halt_clear` → `ledger_halt` (+ flip `runs.status` when the run row
  exists).
- Zero-cost crossings emit **only** a `step` — no `spent_add` with `delta_micros: 0`.
  `ledger_spent` moves on priced events only.
- `len(events) == 1` ⇒ immediate flush.
- `413` if body exceeds `max_body`; `400` if `len(events) > max_batch`, unknown `kind`,
  missing `idempotency_key`, or bad `Durability`.

---

## 6. Idempotency  *(D3)*

- Table `ledger_events(tenant_id, idempotency_key PRIMARY KEY, applied_at)`.
- A replayed key is counted in `deduped`, **not re-applied**; the ack still returns
  current `totals`.
- Deterministic key recipe (so a retried flush regenerates identical keys):
  - `spent_add` / `step`: `f"{run_id}:{agent}:{seq}:{kind}"`
  - `admit` / `complete`: `f"{run_id}:{segment_key}:{kind}:{monotonic_seq}"`
  - `halt_mark` / `halt_clear`: `f"{run_id}:{kind}:{detector or ''}:{monotonic_seq}"`
- Legacy single-op routes accept an optional `Idempotency-Key` header routed through
  the same table.

---

## 7. `/v1/ledger/runs/{run_id}/halt`  *(new shape — replaces `/v1/ledger/halt/*`)*

| Method | Scope | Body | Response |
|---|---|---|---|
| `GET` | read | — | `{ "halted": bool, "halt_reason": str\|null }` |
| `POST` | ingest | `{ "reason": str, "detector"?: str }` | `{ "halted": true }` — flips `runs.status="halted"` |
| `DELETE` | admin | — | `{ "halted": false }` — explicit resume |

Legacy `POST /v1/ledger/halt/mark` · `/clear` · `GET /v1/ledger/halt/{run_id}` kept as
wrappers for one release, then removed.

---

## 8. Run identity  *(#117 / D12)*

### `POST /v1/runs`  *(changed return — scope: ingest)*
Request unchanged: `{ run_id?, intent, user_dims, mode }`.
**Response returns the full record** (kills register-then-resolve):
```jsonc
{ "run_id": "run_abc", "status": "registered",
  "intent": "research-brief", "user_dims": { "user_id": "alice" },
  "mode": "enforce", "registered_at": 1732200000.0 }
```
`409` if `run_id` already registered. The SDK binds this directly.

### `GET /v1/runs/{run_id}/registration`  *(unchanged surface — scope: read)*
Serves identity columns from `runs`. `404` `{ "error": "run '<id>' is not registered" }`.

### `PATCH /v1/run-records/{run_id}`  *(scope: ingest)*
Preferred allow-list: `status`, `ended_at`, `halt_reason`, `detector`,
`governance_events`. In **0.2.x** `steps` / `cost_micros` are still accepted but
**ignored** (logged as deprecated) — they are derived from `run_state` / `ledger_spent`.
**0.3.0** narrows to the allow-list and rejects the rest.

### Deprecated in 0.2.x, removed in 0.3.0
- `PUT /v1/run-records` (`create_run`) — still works (writes `runs`) but the SDK must
  stop calling it; registration is the run-row creator.
- `run_registrations` table — still present. The 0.3.0 fold drops it.
- Legacy `/v1/ledger/halt/*` and single-op `/v1/ledger/{spent,inflight}/*` writes —
  wrappers over the new paths / `apply_events`.

**0.2.0 is additive on the wire and in the schema.** A 0.1-era client keeps working;
the break is deferred to 0.3.0, which ships after `tokenops <next>`.

---

## 9. `run_state` table (Tier-2)  *(new — D4)*

| column | note |
|---|---|
| `tenant_id, run_id` | PK |
| `step_count` | monotonic; sum of `step` events across all agents on the run |
| `window_json` | bounded ring of the last N `BoundaryStep`s (N configurable, default 64) |
| `velocity_inputs` | enough to compute `velocity_micros_per_step` over the ring |
| `last_ts` | latest `step.ts` seen |

Exposed only through `precheck want:["window"]`. `runs.steps` = read of
`run_state.step_count`; `runs.cost_micros` = read of
`ledger_spent[__run_total__|run:<id>|lifetime]`.

---

## 10. `data_scope` on policies  *(D5)*

- Column `policy_instances.data_scope TEXT NOT NULL DEFAULT 'local'` (`'local'` | `'global'`).
- Serde + included in `GET /v1/governance/{agent}` output:
  ```jsonc
  { "governance": { "budgets": [...],
    "policies": { "cost_budget": { "budget": "run_llm_cap", "data_scope": "global" },
                  "output_runaway": { "repeats": 4, "data_scope": "local" } } } }
  ```
- Code default per template lives in the SDK; admin may override per instance.

---

## 11. Error model + client retry rules

| Situation | HTTP | Client action |
|---|---|---|
| run already registered | `409` | `RunAlreadyRegisteredError` (join instead) |
| registration not found | `404` | `RunNotRegisteredError` — fail closed |
| bad event / batch too large / bad `Durability` | `400` | bug — do not retry; raise |
| auth | `401` / `403` | config error — raise, no retry |
| `5xx` / connect / timeout on `precheck` | — | retry ≤2× over ~250 ms, then trip circuit breaker |
| `5xx` / connect on `events:batch` | — | retry ≤2×, then retain + flush on breaker close; never raise (crossing already happened) |
| `5xx` / connect on `POST /v1/runs` | — | retry ≤2×, then per mode |

`max_batch` / `max_body` advertised on `GET /health`.

---

## 12. Plane-unreachable — client contract  *(SDK-side, informational here)*

`TOKENOPS_PLANE_UNREACHABLE ∈ {fail-fast, passthrough, local-policies-only}` +
`TOKENOPS_FALLBACK_RUN_CAP_MICROS`. A per-process circuit breaker in the SDK trips on
first failure (or a failed `GET /ready`), short-circuits all plane calls until a probe
closes it. Plane side: keep `GET /ready` cheap.

---

## 13. Schema & migrations

Tracked in `PRAGMA user_version` (`SqliteStore.SCHEMA_VERSION`). `_SCHEMA`
(`CREATE TABLE IF NOT EXISTS`) runs on every open; `_apply_migrations()` does the rest
forward-only.

### v2 — 0.2.0 (additive, auto-applied)
- `ledger_events (tenant_id, idempotency_key PRIMARY KEY, applied_at)` — new table.
- `run_state (tenant_id, run_id PK, step_count, window_json,
  velocity_micros_per_step, last_ts)` — new table.
- `policy_instances.data_scope TEXT NOT NULL DEFAULT 'local'` — new column (`_migrate`
  `ALTER` for existing DBs).
- `runs`, `run_registrations`, `ledger_spent` / `ledger_inflight` / `ledger_halt`
  **unchanged**. Opening a 0.1 DB just adds the new tables/column and bumps
  `user_version` to 2 — **no data loss, no break.**

### v2.1 — 0.2.2 (additive, auto-applied)
- `run_policy_stats (tenant_id, run_id, policy, stats_json)` — new table, PK
  `(tenant_id, run_id, policy)`. `user_version` stays 2.

### v3 — 0.3.0 (destructive, ships after `tokenops <next>` — issue #11)
- Fold `run_registrations` identity columns into `runs`; `DROP TABLE run_registrations`.
- Make `runs` identity columns write-once; `steps` / `cost_micros` derived only.
- Remove `PUT /v1/run-records` (`create_run`) and the legacy ledger wrappers.
- Historical `runs.cost_micros` / `steps` recomputed from `ledger_spent` where possible,
  else nulled (pre-0.2 values are known-incoherent for multi-agent runs).
- Runs automatically as a `_apply_migrations()` step on first open of a v2 DB.

---

## 14. Out of scope

- `trajectory/*` routes (D8).
- Envelope ingest from the TokenOps SDK.
- Client-side buffering.
- Inflight-as-call-id-set (#116) — synchronous counter assumed.
- Server-side pricing / deriving the ledger from `envelopes:batch`.
- Per-run authorization (#6).
- Real `Durability: queued` queue.

---

## 15. Compatibility

| tokenops | agentplane-control-plane | agent-chronicle | notes |
|---|---|---|---|
| ≤ 0.2.1 | 0.1.x **or 0.2.x** | ≥ 0.3.0 | 0.2.0 is additive — old clients keep working against it |
| `<next>` (remote-only) | ≥ 0.2.0 | ≥ 0.3.0 | uses `precheck` / `events:batch` |
| `<next>` | **not** 0.3.0+ until it drops `create_run` | ≥ 0.3.0 | 0.3.0 removes the deprecated shims |

The hard break is **control-plane 0.3.0**, released only after `tokenops <next>` stops
calling `PUT /v1/run-records`. Publish this table in both repo READMEs with a
**Breaking changes** subsection.
