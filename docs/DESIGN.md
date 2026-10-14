# Control plane design

The control plane is one HTTP service in front of one database. Chronicle and TokenOps are its
clients; agents and browsers never receive a database path or a connection string.

## Layers

Each layer imports only from the ones before it.

| Layer | Package | Responsibility |
|---|---|---|
| Settings and auth types | `settings`, `auth` | Environment, principals, scopes |
| Database | `db` | Table definitions, engine factory, Alembic migrations |
| Repositories | `repositories` | All queries, one repository per concept, tenant-scoped |
| API | `api` | FastAPI routers; validation with primitives models |
| Interfaces | `web`, `cli` | The browser page and the command line |

## Callers

| Caller | Who | Scopes |
|---|---|---|
| `agent-chronicle` | The SDK in the agent | `ingest` to write, `read` for replay |
| `agent-tokenops` | The sidecar in the agent | `read` to pull configuration and precheck, `ingest` to write ledger events |
| the web page | Admin, Chronicle and TokenOps tabs | `read` and `admin` |

## Chronicle

- **Ingest:** trace registration (idempotent, labels once), span upsert, `envelopes:batch`. An
  envelope is one record, mutable until closed. Acknowledged after commit.
- **Replay:** `GET /v1/traces/{t}/spans/{s}/envelopes` is the one read replay needs.
- **UI:** trace search, a trace with its spans, and the envelope waterfall.
- Entities are the `agentplane-primitives` models; payload versions `major.minor` are accepted for
  the current and the previous major.

## TokenOps

- **The pull:** `GET /v1/governance/{agent}` returns the exact dict `build_governor` consumes, with
  an ETag. The configuration (segments, budgets, policy instances) is per tenant and lives in the
  database; it is seeded from YAML and edited through the API or the TokenOps tab.
- **The ledger:** `precheck` (one read) and `events:batch` (one idempotent write per crossing)
  hold spend, in-flight counts, halts, per-trace step windows and per-policy statistics.
- **A run is a trace.** There is no run registration or run record; the run list is the trace
  list joined with ledger facts.

## Database

- One SQLAlchemy URL (`CONTROL_PLANE_DATABASE_URL`). SQLAlchemy Core only; no raw SQL strings and
  no dialect-specific SQL outside `db/engine_utils.py`.
- **Alembic owns the schema.** Migrations ship in the package and run at startup (and with
  `control-plane db upgrade`). Revisions avoid dialect-specific SQL and use batch mode for SQLite.
- Counters are single atomic `UPDATE ... SET n = n + delta` statements; upserts are "update, then
  insert in a SAVEPOINT". Both are portable, so concurrent writers on any database cannot lose an
  increment.
- Times are stored as naive UTC and returned as aware UTC (`UtcDateTime`), so every dialect behaves
  the same.

## Keys

Keys are stored hashed (prefix shown after create). `CONTROL_PLANE_API_KEYS` seeds environment
keys (`name:tenant:scope+scope`). With no environment keys and no stored keys the service is open:
anonymous, tenant `local`, all scopes, for a single trusted local host only.

## Retention

Replay reads traces from the database, so retention matters. `control-plane db prune
--older-than-days N` deletes traces last touched before the cutoff and everything keyed by them.
Nothing prunes in the background.
