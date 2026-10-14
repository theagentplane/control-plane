# 0003. The service is not bound to one database

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-14 |
| Deciders | Susheem Koul |

## Context

The service opened `sqlite3` directly in several places, took a file path as its only database
setting, and used SQLite-only SQL (`json_extract`, `INSERT OR REPLACE`, `PRAGMA table_info`,
`?` placeholders). Retention and scale make a server database a likely next step, and the code
must not have to be rewritten to get there.

## Options considered

### Option A: keep `sqlite3` and add a Postgres branch later

- Good: no change now.
- Bad: two code paths to keep in step, and every query is a candidate for a second copy.

### Option B: SQLAlchemy Core everywhere, one URL

- Good: one code path; the URL names the database; the same queries run on any dialect
  SQLAlchemy supports.
- Bad: a dependency, and a few things SQLite did implicitly (counters, upserts) must be written
  portably.

## Decision

We will use SQLAlchemy Core everywhere and select the database with one URL,
`CONTROL_PLANE_DATABASE_URL`. No module outside `db/engine_utils.py` imports `sqlite3`, there is
no raw SQL string and no dialect-specific SQL in the repositories (tests enforce both).
Dialect behaviour that cannot be portable lives in the engine factory (SQLite: foreign keys,
busy timeout, WAL, and the transaction recipe that makes SAVEPOINTs work). Upserts are "update,
then insert inside a SAVEPOINT"; counters are a single atomic `UPDATE ... SET n = n + delta`.
Labels live in their own indexed table instead of a JSON column that is searched with a
dialect function. Times are stored as naive UTC and returned as aware UTC.

## Consequences

- **Good:** moving to another database is a URL and a driver, not a rewrite.
- **Bad or risky:** only SQLite is tested today, so other databases are supported by
  construction and not by evidence. The CI matrix should gain a second database before relying on
  it.
- **Follow-ups:** a server database in CI; connection pool settings as configuration.

## Impact on other repositories

None.

## Links

[ADR 0002](0002-alembic-owns-the-schema.md)
