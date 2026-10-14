"""``Settings``: everything the service reads from its environment.

There is one database setting, a SQLAlchemy URL, so the service is not tied to one engine:
``sqlite:///control_plane.db`` for a single host, or any other SQLAlchemy URL.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Defaults are for a single trusted local host."""

    database_url: str = "sqlite:///control_plane.db"
    """SQLAlchemy URL of the database. Env: ``CONTROL_PLANE_DATABASE_URL``."""
    auto_migrate: bool = True
    """Run ``alembic upgrade head`` at startup. Env: ``CONTROL_PLANE_AUTO_MIGRATE``."""
    seed_on_start: bool = True
    """Seed the default governance config for tenant ``local`` if it has none."""
    api_keys: str = ""
    """Comma-separated ``key:tenant:scope+scope``. Empty (and no stored keys) disables auth."""
    max_batch: int = 100
    """Largest accepted ``envelopes:batch`` or ``events:batch``."""
    max_body_bytes: int = 4 * 1024 * 1024
    retention_days: int | None = None
    """When set, ``control-plane db prune`` defaults to this age. Nothing runs in the background."""
