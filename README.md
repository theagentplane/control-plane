# AgentPlane Control Plane

Shared HTTP control plane for **Chronicle** and **TokenOps**. SQLite lives
**only** in this process. Agents never open the database file.

Design: [`docs/DESIGN.md`](docs/DESIGN.md) · Issue: [#2](https://github.com/theagentplane/control-plane/issues/2)

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
control-plane serve --port 8800 --db control_plane.db
```

Open http://127.0.0.1:8800/ — Admin, Chronicle, TokenOps tabs. No login.

Sidecars:

```bash
export CONTROL_PLANE_URL=http://127.0.0.1:8800
# Chronicle
chronicle.record("run", store=RemoteStore(os.environ["CONTROL_PLANE_URL"], batch_size=1))
# TokenOps
export TOKENOPS_URL=http://127.0.0.1:8800
# TOKENOPS_EMBEDDED must be unset
```

## API callers

| Route | Caller |
|---|---|
| `POST /v1/envelopes:batch` | Chronicle sidecar (only ingest API; `batch_size=1` = immediate flush) |
| `GET /v1/traces/{id}/envelopes` | Chronicle sidecar (fixture replay) and Chronicle UI waterfall |
| `GET /v1/traces` | Chronicle UI search |
| `POST /v1/runs`, ledger, governance, run-records | TokenOps sidecar |
| segments / budgets / policies, admin keys | UI |

Auth off when no keys exist (local). Create keys in Admin, or:

```bash
export CONTROL_PLANE_API_KEYS='chron:local:ingest+read,tops:local:ingest+read,admin:local:read+admin'
export CONTROL_PLANE_API_KEY=chron   # sidecar
```

## Local SQLite

Default is a file next to the server (`CONTROL_PLANE_DB`). Do not point TokenOps
or Chronicle at that file. Unit tests in those repos keep their own sqlite/jsonl
when `TOKENOPS_EMBEDDED=1` / local Chronicle stores.
