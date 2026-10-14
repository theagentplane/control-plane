"""Create the SQLAlchemy engine from a URL. The only module that knows about a dialect.

The rest of the service uses SQLAlchemy Core and works on whatever the URL names. Dialect
behaviour that cannot be expressed portably is applied here and nowhere else.

SQLite needs three things: foreign keys switched on, a busy timeout, and the documented
recipe that stops the ``sqlite3`` driver from deciding when a transaction begins, so that
SQLAlchemy's transactions and SAVEPOINTs behave as they do on other databases.
"""

from typing import Any

from sqlalchemy import Engine, create_engine, event


def create_db_engine(url: str) -> Engine:
    """Return an engine for ``url`` with dialect-specific setup applied."""
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        _configure_sqlite(engine)
    return engine


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None  # SQLAlchemy emits BEGIN itself (see below)
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        if engine.url.database not in (None, "", ":memory:"):
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection: Any) -> None:
        connection.exec_driver_sql("BEGIN")
