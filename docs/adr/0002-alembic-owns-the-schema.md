# 0002. Alembic owns the database schema

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-14 |
| Deciders | Susheem Koul |

## Context

Schema changes were hand-rolled: a `CREATE TABLE IF NOT EXISTS` script, ad-hoc `ALTER TABLE`
guards and a version number kept in `PRAGMA user_version`. Chronicle's envelope table had its own
inspect-and-rebuild migration in a second file. The service is moving from one SQLite file to a
database chosen by URL, and it needs ordered, reviewable migrations that work the same on every
database.

## Options considered

### Option A: keep the hand-rolled migrations

- Good: no new dependency.
- Bad: SQLite-specific (`PRAGMA`), hard to review, and no downgrade path.

### Option B: Liquibase

- Good: mature, database-agnostic change sets.
- Bad: needs a JVM at install, build and run time, and runs as a separate operator CLI, which does
  not fit a Python package that migrates at startup.

### Option C: versioned SQL files and a small runner

- Good: no ORM, easy to read.
- Bad: forward-only and hand-written per dialect; the runner and its tests are ours to maintain.

### Option D: Alembic

- Good: the Python standard; ordered revision files with upgrade and downgrade; a version table;
  batch mode for SQLite; works with SQLAlchemy, which is also how the service stays
  database-neutral.
- Bad: adds SQLAlchemy and Alembic as dependencies.

## Decision

We will use Alembic. The tables are defined once as SQLAlchemy Core tables
(`control_plane.db.tables`), every change is a new revision under
`control_plane/db/migrations/versions`, and an applied revision is never edited. Migrations ship in
the wheel and run at startup (`CONTROL_PLANE_AUTO_MIGRATE`) or with `control-plane db upgrade`. A
test compares the migrated schema with the table definitions, so they cannot drift.

## Consequences

- **Good:** one reviewable history, a downgrade path, and a schema check in CI.
- **Bad or risky:** revisions must avoid dialect-specific SQL and use batch mode on SQLite; the
  baseline replaces the 0.2 schema, so there is no in-place data migration from 0.2.
- **Follow-ups:** run the test suite against a second database in CI.

## Impact on other repositories

None. Clients see only the HTTP API.

## Links

[ADR 0003](0003-database-neutral-service.md); issue discussion of Liquibase versus Alembic.
