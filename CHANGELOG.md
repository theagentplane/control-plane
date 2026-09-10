# Changelog

## [Unreleased]

### 0.2.0 (in progress) — TokenOps remote-only (#10)

- Wire contract: `docs/api-contract.md`.
- `POST /v1/ledger/events:batch` — batched ledger writes (`spent_add` with multi-target
  fan-out, `admit`/`complete`, `step`, `halt_mark`/`halt_clear`), applied in array order
  in one transaction, idempotent per `idempotency_key` (`ledger_events` table).
- `run_state` table — per-run `step_count` / bounded window / velocity, fed by `step`
  events; `SqliteStore.get_run_state`.
- `POST /v1/ledger/precheck` — one read for a pre_call pass: halt + requested
  spent / inflight / window slices (`SqliteStore.precheck`).

## [0.1.0] — 2026-08-14

- First PyPI release of the shared AgentPlane control plane.
- SQLite stays in this process; Chronicle and TokenOps talk over HTTP.
- HTML UI (Admin, Chronicle waterfall, TokenOps) served with the API.
