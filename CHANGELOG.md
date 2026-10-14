# Changelog

## [Unreleased]

## [1.0.0] - unreleased

A rework of the service around the shared primitives schema, with the database owned by Alembic.
**This release is breaking** (the maintainer chose to drop the pre-1.0 surface rather than carry
it). Every entry is classified; the migration each consumer needs is given. Pairs with
`agent-chronicle` 1.0. The TokenOps client is expected to be reworked against this API; until it
is, TokenOps 0.4 cannot talk to this service.

### Changed (breaking)
- **Chronicle speaks the `agentplane-primitives` schema** (new dependency,
  `agentplane-primitives==0.2.*`). Traces, spans and envelopes are validated with the shared models
  and served back as valid primitives. An envelope in the pre-1.0 Chronicle shape is rejected with
  `400`. *Migration:* upgrade to `agent-chronicle` 1.0.
- **A run is a trace.** `run_id` is gone everywhere; `trace_id` is the only run identifier. Halts,
  per-run state and per-run policy statistics are keyed by `trace_id`; the segment `dimension`
  `run` is now `trace`; the conventional segment key is `trace:<trace_id>`. Events carry
  `trace_id`. *Migration:* TokenOps sends `trace_id`.
- **One database setting.** `CONTROL_PLANE_DATABASE_URL` (a SQLAlchemy URL) replaces
  `CONTROL_PLANE_DB` and `TOKENOPS_DB`; `--db` is `--database-url`. *Migration:* set the URL
  (`sqlite:///path/to.db` for an existing SQLite file).
- **Alembic owns the schema.** The ad-hoc `PRAGMA user_version` migrations are replaced by a
  versioned baseline. The tables are renamed or reshaped for the new schema (see Removed). There is
  no in-place data migration from 0.2: start a new database, or re-seed the governance
  configuration with `control-plane db seed`.
- **Every table is tenant-scoped**, including segments, budgets and policy instances (they were
  global before) and the ledger counters.
- **Ingest is acknowledged after commit.** The `Durability: sync|queued` header and the `durability`
  field of the response are removed (`queued` was never a queue).
- **Trace responses** use `metadata` (labels) instead of `dims`, and carry a derived `state`.
- **The ledger events and halt routes** are under `/v1/ledger/traces/{trace_id}`.
- Python 3.13 is supported; the package is typed (`py.typed`) and builds with a `src` layout.

### Added
- **Chronicle 1.0 endpoints** (see [docs/chronicle-api.md](docs/chronicle-api.md)):
  `POST /v1/traces` (idempotent, labels written once), `PUT /v1/traces/{t}/spans/{s}` (a closed span
  is immutable), `GET /v1/traces/{t}/spans/{s}/envelopes` (replay), and `POST /v1/envelopes:batch`
  validating primitives envelopes whole-batch, as one record mutable until closed.
- **Schema versions:** the current and the previous major of `schema_version` are accepted.
- **Governance pull** with an `ETag` and `If-None-Match` (`304`), per tenant.
- `GET /v1/ledger/traces`: the TokenOps run list, a trace list joined with halt, spend and steps.
- `control-plane db upgrade | current | seed | prune`. `prune` implements retention.
- `GET /ready` checks the database answers and is at the newest migration.
- The web page renders envelope sections (input, output, error, usage, metadata) and the spans
  of a trace.
- Repository conventions: `AGENTS.md`, ADRs, SHA-pinned CI with a Windows job, zizmor, Dependabot,
  PR-size notice, `mypy --strict`.

### Removed (breaking)
- **`step` ledger events, `window` in `precheck`, `state` and `policy_stats` on
  `GET /v1/ledger/traces/{id}`, and the `steps` field of the run list** (tables `trace_state`
  and `trace_policy_stats`). The run list carries `span_count` instead. ADR 0009.
- **Run registration and run records:** `POST /v1/runs`, `GET /v1/runs/{id}/registration`,
  `PUT`, `PATCH` and `GET /v1/run-records*`, and the `runs` and `run_registrations` tables. They
  duplicated what a Chronicle trace is. *Migration:* put the run's identity in trace labels at
  registration; read the run list from `GET /v1/ledger/traces`.
- **Single-operation ledger routes:** `/v1/ledger/spent*`, `/v1/ledger/inflight*` and
  `/v1/ledger/halt*`. *Migration:* `events:batch`, `precheck` and the trace halt routes.
- **Legacy Chronicle ingest** (`node_id`, `dims`, `attributes`, the 2.0 envelope shape) and the
  pre-2.0 envelope key migration.
- **Admin routes** `seed-if-empty`, `clear-all`, `clear-governance` and `reseed-governance`;
  replaced by `/v1/admin/governance/reseed` and `/clear` and the CLI.
- **TokenOps client code** that lived here (`RemoteStore`, `open_store`, the store protocol) and
  the **Streamlit dashboard** (the `ui` extra). The built-in web page keeps the TokenOps tab.

### Database
- Baseline revision `0001`: `traces`, `trace_labels`, `spans`, `envelopes`, `segments`, `budgets`,
  `policy_instances`, `ledger_spent`, `ledger_inflight`, `api_keys`. A halt is stored in `traces.attributes` (the primitives `Trace.attributes` dict,
  ADR 0006); there is no halt table and no event deduplication table (ADR 0007).

## [0.2.2] — 2026-09-19 — persist compaction token savings (#18)

**Additive on the wire and schema.** `0.2.0`/`0.2.1` clients keep working; a client that
does not send `compaction` sees no change. The new table is created automatically.

Fixed
- `step` events carrying `compaction` (`tokens_before` / `tokens_after` / `tokens_saved`,
  sent by TokenOps for `context_compaction`) were accepted with `201` and silently dropped.
  They are now persisted (#19).

Added
- `run_policy_stats (tenant_id, run_id, policy, stats_json)` — per-run, per-policy
  aggregate. Numeric metrics are summed and `calls` counts contributions, in the same
  transaction and behind the same idempotency check as the step, so a deduped replay
  cannot double count. `user_version` stays 2.
- `policy_stats` on `GET /v1/run-records/{run_id}` and in the run detail UI.
- Wire contract (`docs/api-contract.md`) documents the optional `step.compaction` field.

Changed
- The admin `context_compaction` policy template no longer suggests the removed
  `has_hook` flag.

## [0.2.1] — 2026-09-13 — reach the CLI without PATH (#14)

**Docs and a second entrypoint only.** No wire, schema, or API change; `0.2.0`
clients and the `control-plane` console script behave exactly as before.

Added
- `python -m control_plane` — the CLI as a module, for when the interpreter's
  scripts directory is not on `PATH`. `pip` installs the console script there and
  cannot add it to `PATH` (wheels have no install-time hooks), which is the default
  outcome on Windows under the Python Install Manager: only the `python.exe` shim is
  on `PATH`, so `control-plane` installs successfully and is then not found.
- `tests/test_cli_entrypoints.py` — the module form is a supported entrypoint, not a
  convenience, so it is covered.

Changed
- `argparse` `prog` is derived from `argv[0]`, so usage and hints name the form you
  invoked. `control-plane ui` previously printed `Run: control-plane serve --port
  8800` — naming the very command a PATH-less user could not run.
- README `Install` gains *If `control-plane` isn't found* (`pipx` / `uv tool`, the
  module form, and a `sysconfig` one-liner that prints the directory to add) and
  *After upgrading Python* (installs belong to one interpreter, so both forms stop
  working after an upgrade; a hand-added scripts directory is version-scoped).

## [0.2.0] — 2026-09-13 — TokenOps remote-only + background CLI (#10)

**Additive on the wire and schema.** 0.1-era clients keep working against 0.2.x; the
breaking fold (drop `run_registrations`, remove `create_run`, narrow `PATCH`) is
deferred to 0.3.0 (#11), released after `tokenops <next>`.

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
  bumps the version — no data loss. The 0.3.0 destructive fold (#11) slots in as v3.

- `control-plane start` / `stop` / `status` — run the plane detached from the
  terminal as one managed background instance (PID + state file under a
  `platformdirs` user-state dir; logs redirected to a file since the process is no
  longer attached to a console). `start` waits for `/health` before returning and
  reaps the process if it never comes up; `stop` escalates `terminate()` → `kill()`
  on a timeout; both are idempotent (safe to call when already in the target state).
  Cross-platform: POSIX detaches via `start_new_session`, Windows via
  `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` — Windows has no SIGTERM, so `stop`
  there is a hard stop, not a graceful drain.
  New deps: `psutil`, `platformdirs`.
- `Dockerfile` + `docker-compose.yml` — the repo's first container image. Builds from
  source (`pip install .`), runs as a non-root user, SQLite on a `/data` volume,
  built-in `HEALTHCHECK` against `/health`. Entrypoint is `control-plane serve`
  (foreground) — the container is its own process supervisor, so the new
  `start`/`stop`/`status` background mode is for local dev only, not for images.

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
