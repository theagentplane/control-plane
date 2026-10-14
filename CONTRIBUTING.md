# Contributing to the control plane

The control plane stores Chronicle traces and serves TokenOps governance. Two other products depend
on its HTTP API, so changes are reviewed with that in mind.

## Setup

Uses [uv](https://docs.astral.sh/uv/) and Python 3.10 to 3.13.

```bash
git clone https://github.com/theagentplane/control-plane.git
cd control-plane
uv sync
uv run pre-commit install    # optional
```

## Checks (CI runs the same ones)

```bash
uv run ruff check .
uv run ruff format --check .
uv run python -m mypy                 # --strict, on src and tests
uv run python -m pytest --cov
uv build && uv run twine check dist/*
```

On Windows use `python -m pytest`; it is the reliable invocation.

CI also runs Chronicle's own end-to-end tests against this checkout (the `chronicle-e2e` job,
pinned to a Chronicle release in `ci.yml`). To run them locally, install this checkout into a
Chronicle clone and run `tests/e2e/test_real_control_plane.py` there.

## Ground rules

- **Read [AGENTS.md](AGENTS.md) and the [ADRs](docs/adr/README.md) first.** Never rewrite an
  accepted ADR; supersede it.
- **Schema changes are Alembic revisions.** Add a revision, never edit an applied one, and keep
  revisions free of dialect-specific SQL. The drift test fails if the tables and the migrations
  disagree.
- **No `sqlite3`, no raw SQL, no dialect-specific SQL** outside the engine factory. Tests enforce
  it.
- **Every query is tenant-scoped.** A new table has `tenant_id` in its primary key.
- **Do not copy the entities.** `Trace`, `Span` and `Envelope` come from `agentplane-primitives`.
- **Every change needs tests** and a CHANGELOG entry classified as compatible or breaking.

## Change size

- **Estimate before you start**: lines added plus removed, not counting generated files,
  lockfiles, migrations or pure moves and renames.
- **Above about 400 lines, plan first**: logical components, each reviewable on its own and passing
  CI. Put the plan in the issue or pull request and open one pull request per component.
- **An exception needs a stated reason** in the pull request description (boilerplate, a mechanical
  change, a maintainer decision to deliver one change).

## Developer Certificate of Origin (DCO)

Sign off every commit: `git commit -s -m "Add the span reader"`.

## Commits and pull requests

- Imperative mood ("Add the span reader").
- A summary, a compatibility section and the checklist from the template; link the issue.
- CI must be green and CODEOWNERS review is required to merge.

## Reviewer checklist

- **Tenant isolation:** every query filters on `tenant_id`; no cross-tenant read is possible.
- **Atomicity:** counters are single atomic updates; a batch is all or nothing; replayed
  idempotency keys are not re-applied.
- **Immutability:** a closed span or envelope is never reopened or changed.
- **Portability:** nothing dialect-specific outside the engine factory; revisions work on SQLite
  batch mode.
- **Wire contract:** request and response shapes are in `docs/chronicle-api.md` and
  `docs/governance-api.md`; a change updates them and the CHANGELOG.
- **Secrets:** API keys are stored hashed and never logged; the full envelope body is not logged at
  info level.
