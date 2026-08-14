# Control plane design

SQLite lives **only** inside this process. Chronicle and TokenOps are facets
(agent sidecars + UI tabs), never database clients.

## Storage

One SQLite file (`CONTROL_PLANE_DB`, default `control_plane.db`). WAL.
No Postgres. Agents and the browser never receive a DB path.

Local/dev: run `control-plane serve`. Clients talk HTTP.
`TOKENOPS_EMBEDDED=1` remains TokenOps-only for unit tests that never start this service.

## Callers

Every HTTP route is owned by exactly one caller class:

| Caller | Who | Scopes |
|---|---|---|
| `agent-chronicle` | Chronicle sidecar in the agent | `ingest` write, `read` for fixture replay |
| `agent-tokenops` | TokenOps sidecar in the agent | `ingest` write, `read` for governance/ledger |
| `ui` | Control plane Admin / Chronicle / TokenOps tabs | `read` + `admin` |

## Chronicle

Agent needs two things:

1. **Ingest** — `POST /v1/envelopes:batch` is the **only** ingest API.
   Client `batch_size=1` means flush every envelope (still the batch route).
2. **Replay fixture** — `GET /v1/traces/{trace_id}/envelopes` returns the full
   ordered envelope list for that trace (enough to stub-replay).

UI (same process as the DB, but talks HTTP so the split stays honest):

- `GET /v1/traces` search / filter / sort
- `GET /v1/traces/{trace_id}` summary
- `GET /v1/traces/{trace_id}/envelopes` waterfall

No unbounded `GET /envelopes`. No single-envelope `POST`.

## TokenOps

Agent:

- `POST /v1/runs` register (`intent`, `user_dims`, `mode`)
- `GET /v1/runs/{id}/registration`
- `GET /v1/governance/{agent}`
- `/v1/ledger/*` spend, inflight, halt
- `PUT/PATCH /v1/run-records` dashboard row + governance events

UI:

- CRUD `/v1/segments|budgets|policies`
- `GET /v1/run-records` (+ `problematic_only`)
- `POST /v1/admin/seed-if-empty|reseed-governance|clear-*`

TokenOps SDK uses HTTP for all of the above when `TOKENOPS_URL` /
`CONTROL_PLANE_URL` is set. It must not open SQLite in that mode.

## UI

Same FastAPI process serves a small HTML app (component CSS in
`control_plane/web/static`). No login screen.

| Tab | Role |
|---|---|
| **Admin** | Sidecar API keys (Chronicle / TokenOps / UI). Create, list, revoke. |
| **Chronicle** | Trace search, lookup, waterfall. |
| **TokenOps** | Budgets, policies, segments; run list with breaches. |

## Keys

Keys are stored hashed in SQLite (prefix shown after create). Env
`CONTROL_PLANE_API_KEYS` still seeds. Empty key table = local anonymous
dev (all scopes, tenant `local`).

Format: `name:tenant:scope+scope` in env; generated keys are random tokens.
