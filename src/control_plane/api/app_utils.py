"""Build the FastAPI application.

``create_app`` wires settings, the engine, the repositories and the routers. At startup it
upgrades the schema (``auto_migrate``) and seeds the default governance configuration for tenant
``local`` when it has none (``seed_on_start``). The ASGI factory entry point is::

    uvicorn control_plane.api:create_app --factory
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from control_plane import __version__
from control_plane.api import (
    admin_routes,
    chronicle_routes,
    governance_routes,
    health_routes,
    ledger_routes,
)
from control_plane.api.state_types import AppState
from control_plane.auth import parse_api_keys
from control_plane.db.engine_utils import create_db_engine
from control_plane.db.migration_utils import upgrade
from control_plane.repositories import build_repositories
from control_plane.seed import seed_governance
from control_plane.settings import Settings, load_settings
from control_plane.web import mount_web


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Create the application. Pass ``engine`` to use a database you manage (tests)."""
    cfg = settings or load_settings()
    owns_engine = engine is None
    if cfg.auto_migrate:
        upgrade(cfg.database_url)
    db = engine or create_db_engine(cfg.database_url)
    repos = build_repositories(db)
    if cfg.seed_on_start:
        seed_governance(repos.governance, "local")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_engine:
            db.dispose()

    app = FastAPI(
        title="agentplane-control-plane",
        version=__version__,
        description=(
            "Stores Chronicle traces and serves TokenOps governance. "
            "Callers: agent-chronicle, agent-tokenops, the built-in web page."
        ),
        lifespan=lifespan,
    )
    app.state.cp = AppState(
        settings=cfg, engine=db, repos=repos, env_keys=parse_api_keys(cfg.api_keys)
    )
    for module in (
        health_routes,
        chronicle_routes,
        governance_routes,
        ledger_routes,
        admin_routes,
    ):
        app.include_router(module.router)
    mount_web(app)
    return app
