# AgentPlane Control Plane

Shared HTTP control plane for **Chronicle** (envelope ingest / query) and **TokenOps**
(governance config, run history, ledger) — including the Admin + Dashboard UIs.

Design pillars (Chronicle RFC): **fast · authenticated · durable**.

## What's in here

| Surface | Source | Role |
|---|---|---|
| `POST /v1/envelopes` (+ batch) | Chronicle RFC | Agent envelope ingest (idempotent on `envelope_id`) |
| `GET /v1/traces…` | Chronicle RFC | Dims / trace lookup for dashboard |
| `/v1/segments\|budgets\|policies\|runs\|ledger…` | TokenOps cp-split | Governance + ledger API |
| Streamlit Admin + Dashboard | TokenOps `ui/` | Manage configs; inspect runs |

Governors / policy *builders* stay in TokenOps. This service stores config and
spend state; agents pull `GET /v1/governance/{agent}` and build governors locally.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# API (auth off when CONTROL_PLANE_API_KEYS unset — local only)
control-plane serve --port 8800 --db control_plane.db

# Admin + Dashboard (same DB by default, or point at the API)
CONTROL_PLANE_DB=control_plane.db control-plane ui
# or: CONTROL_PLANE_URL=http://127.0.0.1:8800 CONTROL_PLANE_API_KEY=… control-plane ui
```

### Auth

```bash
export CONTROL_PLANE_API_KEYS='agent:tenant-a:ingest,dash:tenant-a:read+admin'
# clients: Authorization: Bearer agent
```

### Chronicle agent

Point Chronicle `RemoteStore` at `http://host:8800` (`POST /v1/envelopes` or legacy
`POST /envelopes`). Use a key with `ingest` scope.

## Layout

```
control_plane/   FastAPI app, SqliteStore, EnvelopeStore, auth
ui/              Streamlit Admin + Dashboard
config/          default governance seed YAML
tests/
```

## Env vars

| Var | Meaning |
|---|---|
| `CONTROL_PLANE_DB` / `TOKENOPS_DB` | SQLite path |
| `CONTROL_PLANE_URL` / `TOKENOPS_CONTROL_PLANE_URL` | UI/agents use HTTP RemoteStore |
| `CONTROL_PLANE_API_KEYS` | `key:tenant:scopes,...` |
| `CONTROL_PLANE_API_KEY` | Client bearer for RemoteStore / UI |
| `CONTROL_PLANE_CONFIG` / `TOKENOPS_CONFIG` | Governance seed YAML |

## Status

Scaffold: SQLite durability (`sync` commit; `queued` is accepted but currently
same as sync). Postgres + real durable queue come next.
