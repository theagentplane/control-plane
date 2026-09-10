# Changelog

## [Unreleased]

## [0.2.0] — TokenOps remote-only (#10)

**Additive on the wire and schema.** 0.1-era clients keep working against 0.2.x; the
breaking fold (drop `run_registrations`, remove `create_run`, narrow `PATCH`) is
deferred to 0.3.0, released after `tokenops <next>`.

Added
- Wire contract: `docs/api-contract.md`.
- `POST /v1/ledger/events:batch` — batched ledger writes (`spent_add` with multi-target
  fan-out, `admit`/`complete`, `step`, `halt_mark`/`halt_clear`), applied in array order
  in one transaction, idempotent per `idempotency_key` (`ledger_events` table).
- `POST /v1/ledger/precheck` — one read for a pre_call pass: halt + requested
  spent / inflight / window slices (`SqliteStore.precheck`).
- `run_state` table — per-run `step_count` / bounded window / velocity, fed by `step`
  events; `SqliteStore.get_run_state`.
- `policy_instances.data_scope` (`local` | `global`, default `local`) — surfaced in
  `GET /v1/governance/{agent}`, `PUT /v1/policies`, and YAML seed.
- `GET /v1/ledger/runs/{run_id}/halt` (GET / POST / DELETE) — run-scoped halt;
  `/v1/ledger/halt/*` kept as aliases.
- `PRAGMA user_version` schema versioning (`SqliteStore.SCHEMA_VERSION = 2`) +
  forward-only `_apply_migrations()`. Opening a 0.1 DB adds the new tables/columns and
  bumps the version — no data loss. The 0.3.0 destructive fold slots in as v3.

Changed
- `POST /v1/runs` response includes `registered_at`; `register_run` returns the
  persisted row so the SDK can bind without a follow-up `GET .../registration`.
- `GET /health` advertises `max_batch` / `max_body_bytes`.
- Version → `0.2.0`.

Deprecated (removed in 0.3.0)
- `PATCH /v1/run-records` — client-sent `steps` / `cost_micros` are now **ignored**
  (logged); they are derived from `run_state` / `ledger_spent`.
- `PUT /v1/run-records` (`create_run`), the `run_registrations` table, and the legacy
  `/v1/ledger/halt/*` + single-op `/v1/ledger/{spent,inflight}/*` writes.

## [0.1.0] — 2026-08-14

- First PyPI release of the shared AgentPlane control plane.
- SQLite stays in this process; Chronicle and TokenOps talk over HTTP.
- HTML UI (Admin, Chronicle waterfall, TokenOps) served with the API.
