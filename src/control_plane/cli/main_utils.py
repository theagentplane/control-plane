"""The ``control-plane`` command line.

control-plane serve [--host H] [--port P] [--database-url URL]
control-plane start | stop | status        (a background instance)
control-plane db upgrade [--revision head] (apply migrations)
control-plane db current                   (the revision the database is at)
control-plane db seed [--file F] [--tenant T] [--reset]
control-plane db prune --older-than-days N [--tenant T]
"""

import argparse
import os
import pathlib
import sys
from datetime import timedelta

from control_plane.settings import load_settings
from control_plane.time_utils import utc_now


def _prog() -> str:
    """Name this invocation by how it was reached, so hints stay copy-pasteable."""
    if pathlib.Path(sys.argv[0]).name == "__main__.py":
        return "python -m control_plane"
    return "control-plane"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=_prog())
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Run the control plane (foreground)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8800)
    serve.add_argument("--database-url", default=None, help="SQLAlchemy URL of the database")
    serve.add_argument("--reload", action="store_true")

    start = sub.add_parser("start", help="Start a background instance (run again to check it)")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8800)
    start.add_argument("--database-url", default=None)
    sub.add_parser("stop", help="Stop the background instance started with `start`")
    sub.add_parser("status", help="Show whether the background instance is running")

    db = sub.add_parser("db", help="Database migrations, seeding and retention")
    db_sub = db.add_subparsers(dest="db_cmd", required=True)
    upgrade = db_sub.add_parser("upgrade", help="Apply migrations")
    upgrade.add_argument("--revision", default="head")
    db_sub.add_parser("current", help="Show the revision the database is at")
    seed = db_sub.add_parser("seed", help="Seed the governance configuration from YAML")
    seed.add_argument("--file", default=None, help="YAML file (default: the packaged one)")
    seed.add_argument("--tenant", default="local")
    seed.add_argument("--reset", action="store_true", help="replace existing configuration")
    prune = db_sub.add_parser("prune", help="Delete traces older than N days")
    prune.add_argument("--older-than-days", type=int, default=None)
    prune.add_argument("--tenant", default=None)
    return parser


def _db_command(args: argparse.Namespace) -> int:
    from control_plane.db.engine_utils import create_db_engine
    from control_plane.db.migration_utils import current_revision, head_revision, upgrade
    from control_plane.repositories import build_repositories
    from control_plane.seed import load_governance_yaml, seed_governance

    settings = load_settings()
    if args.db_cmd == "upgrade":
        upgrade(settings.database_url, args.revision)
        print(f"database is at {args.revision}")
        return 0
    engine = create_db_engine(settings.database_url)
    try:
        if args.db_cmd == "current":
            at = current_revision(engine)
            print(f"{at or 'not migrated'} (head: {head_revision()})")
            return 0 if at == head_revision() else 1
        repos = build_repositories(engine)
        if args.db_cmd == "seed":
            governance = load_governance_yaml(args.file)
            written = seed_governance(repos.governance, args.tenant, governance, reset=args.reset)
            print(f"seeded {written} item(s) for tenant {args.tenant!r}")
            return 0
        if args.db_cmd == "prune":
            days = args.older_than_days or settings.retention_days
            if not days:
                print("give --older-than-days or set CONTROL_PLANE_RETENTION_DAYS", file=sys.stderr)
                return 2
            removed = repos.traces.prune_before(utc_now() - timedelta(days=days), args.tenant)
            print(f"removed {removed} trace(s) older than {days} day(s)")
            return 0
    finally:
        engine.dispose()
    return 2


def main() -> None:
    """Entry point for ``control-plane`` and ``python -m control_plane``."""
    args = _build_parser().parse_args()
    if args.cmd == "serve":
        if args.database_url:
            os.environ["CONTROL_PLANE_DATABASE_URL"] = args.database_url
        import uvicorn

        uvicorn.run(
            "control_plane.api:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
    elif args.cmd == "start":
        from control_plane.cli import service_control_utils as service

        try:
            service.start(host=args.host, port=args.port, database_url=args.database_url)
        except service.ServiceError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
    elif args.cmd == "stop":
        from control_plane.cli import service_control_utils as service

        service.stop()  # idempotent: "was not running" is not an error
    elif args.cmd == "status":
        from control_plane.cli import service_control_utils as service

        raise SystemExit(0 if service.status().get("running") else 1)
    elif args.cmd == "db":
        raise SystemExit(_db_command(args))


if __name__ == "__main__":
    main()
