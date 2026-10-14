"""Alembic environment: runs migrations against the URL in the Alembic config.

The URL is set by ``control_plane.db.migration_utils`` (from ``CONTROL_PLANE_DATABASE_URL``), so
the same code path serves ``control-plane db upgrade``, startup and the tests. The engine comes
from the shared factory so SQLite gets the same setup it gets at runtime.
"""

from alembic import context

from control_plane.db import tables  # noqa: F401  (registers every table on the metadata)
from control_plane.db.engine_utils import create_db_engine
from control_plane.db.metadata_types import metadata

config = context.config


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a connection (``alembic upgrade --sql``)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations on a live connection, in batch mode so SQLite can alter tables."""
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_db_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
