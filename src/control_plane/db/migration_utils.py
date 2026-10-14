"""Run and inspect Alembic migrations from code (startup, the CLI and the tests).

The migration scripts ship inside the package (``control_plane/db/migrations``), so a wheel can
upgrade a database without an ``alembic.ini`` on disk.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(database_url: str) -> Config:
    """An Alembic ``Config`` pointing at the packaged scripts and ``database_url``."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # ConfigParser treats % as interpolation; a URL may contain percent-encoded characters.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    """Bring the database at ``database_url`` up to ``revision``."""
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    """Move the database down to ``revision`` (``base`` removes everything)."""
    command.downgrade(alembic_config(database_url), revision)


def head_revision(database_url: str = "sqlite://") -> str:
    """The newest revision in the packaged scripts."""
    head = ScriptDirectory.from_config(alembic_config(database_url)).get_current_head()
    assert head is not None
    return head


def current_revision(engine: Engine) -> str | None:
    """The revision the database is at, or ``None`` if it has never been migrated."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()
