# Agent instructions

The control plane is the shared HTTP service of the AgentPlane suite. It **stores** what Chronicle
records (traces, spans, envelopes), **serves** it back (query, visualization, replay reads), and
holds the **governance** state TokenOps enforces (budgets, policies, segments and the shared
ledger). Agents and browsers talk HTTP; only this process opens the database.

This file is read by AI coding agents (Claude Code, Codex, Cursor, Copilot and others). Humans
may find it useful too.

## Source of truth

- The entities (`Trace`, `Span`, `Envelope`, ids, enums) come from
  [`agentplane-primitives`](https://github.com/theagentplane/primitives) (pinned `==0.1.*`).
  **Never define a local copy of an entity.** A gap there is an issue in that repository.
- Chronicle's design is in
  [chronicle `docs/rfcs/chronicle-technical-design.md`](https://github.com/theagentplane/chronicle/blob/main/docs/rfcs/chronicle-technical-design.md),
  section 10.2 lists what this service must do for it.
- The HTTP request and response wrappers belong to **this** repository (primitives ADR 0003).
- `trace_id` is the only run identifier. There is no `run_id` (chronicle ADR 0009).

## Architecture decisions

Decisions that shape this repository are recorded as ADRs in [`docs/adr/`](docs/adr/README.md).

- **Before changing architecture,** read the ADRs: structure, public interfaces, data formats,
  dependencies or anything shared with another repository. Do not undo an accepted decision
  silently. If a change conflicts with one, say so and propose a new ADR that supersedes it.
- **When asked to record a decision,** or when you make one that meets the criteria in
  `docs/adr/README.md`, write an ADR: copy `docs/adr/template.md`, use the next free number,
  keep it to about a page, and add it to the index.
- **Do not invent reasoning.** If you do not know why something was decided or which alternatives
  were weighed, ask. Leave the status as *Proposed*: the people named as deciders accept it.
- **Never rewrite an accepted ADR.** Supersede it with a new one.

## Database rules

- **SQLAlchemy Core only, and Alembic owns the schema.** The tables are defined once in
  `control_plane.db.tables`; every schema change is a new Alembic revision in
  `control_plane/db/migrations/versions`. Never edit an applied revision.
- **No `sqlite3` import outside the engine factory** (`db/engine_utils.py`), and no raw SQL
  strings. The database is chosen by `CONTROL_PLANE_DATABASE_URL`; code must work on any dialect
  SQLAlchemy supports. Dialect-specific behaviour lives in the engine factory only.
- **Every table is tenant-scoped** (`tenant_id` in the key) and every query filters on it.
- A revision must not use dialect-specific SQL; use `op.batch_alter_table` for SQLite.

## Layers

Each layer imports only from the ones before it.

| Layer | Package | Rule |
|---|---|---|
| Settings and auth types | `settings`, `auth` | No I/O. |
| Database | `db` | Tables, engine, migrations. The only place that knows a dialect. |
| Repositories | `repositories` | The only code that runs queries. One repository per concept. |
| API | `api` | FastAPI routers. Validate with primitives models; no SQL. |
| Interfaces | `web`, `cli` | The browser page and the command line. |

## Code layout and style

- `src` layout, typed package (`py.typed`), Python 3.10 to 3.13 (no syntax newer than 3.10).
- One concept per file, with a module docstring that says what it is. Folders are named for what
  they hold; helper modules are suffixed for what they are (`*_utils.py`, `*_types.py`).
  `__init__.py` re-exports the public names with `__all__`.
- Types and functions live apart: no functions in model modules, no models in utils.
- Must work on Windows and Linux. On Windows use `python -m pytest`.
- Tooling: `uv`, `ruff` (line length 100), `mypy --strict`, `pytest`. Run the checks listed in
  [CONTRIBUTING.md](CONTRIBUTING.md) before you finish.

## Change size

- **Estimate before writing code:** lines added plus removed, not counting generated files,
  lockfiles, migrations or pure moves and renames.
- **If the estimate is above 400 lines, stop and plan.** Propose to the user a short plan that
  splits the work into logical components (each reviewable on its own, passing CI, in order), and
  wait for agreement before implementing. Open one pull request per component.
- **Keep one change as one thing.** Do not mix unrelated changes into a pull request.
- Staying above 400 lines is acceptable for boilerplate, mechanical changes or generated code, or
  when the maintainer has decided on one change (as for the 1.0 rework), but say why in the pull
  request description and keep the work reviewable commit by commit.

## Related repositories

[chronicle](https://github.com/theagentplane/chronicle) (edge SDK: record, save, replay) ·
[primitives](https://github.com/theagentplane/primitives) (shared schemas) ·
[control-plane](https://github.com/theagentplane/control-plane) (storage, query, UI) ·
[tokenops](https://github.com/theagentplane/tokenops) (cost governance)
