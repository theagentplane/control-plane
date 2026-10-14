"""Portable upsert and counter helpers (no dialect-specific SQL).

Upsert is "update, and insert if nothing matched", with the insert in a SAVEPOINT so a concurrent
writer that got there first turns into a second update instead of an error. Counters are a single
``UPDATE ... SET col = col + delta``, which is atomic on every database.
"""

from typing import Any

from sqlalchemy import Connection, Table, and_, insert, update
from sqlalchemy.exc import IntegrityError


def _where(table: Table, key: dict[str, Any]) -> Any:
    return and_(*[table.c[name] == value for name, value in key.items()])


def upsert(conn: Connection, table: Table, key: dict[str, Any], values: dict[str, Any]) -> None:
    """Set ``values`` on the row identified by ``key``, creating it if absent."""
    where = _where(table, key)
    if values and conn.execute(update(table).where(where).values(**values)).rowcount:
        return
    try:
        with conn.begin_nested():
            conn.execute(insert(table).values(**key, **values))
    except IntegrityError:
        if values:
            conn.execute(update(table).where(where).values(**values))


def increment(
    conn: Connection,
    table: Table,
    key: dict[str, Any],
    column: str,
    delta: int,
    initial: dict[str, Any] | None = None,
) -> None:
    """Atomically add ``delta`` to ``column`` of the row at ``key``, creating it at ``delta``."""
    where = _where(table, key)
    stmt = update(table).where(where).values({column: table.c[column] + delta})
    if conn.execute(stmt).rowcount:
        return
    try:
        with conn.begin_nested():
            conn.execute(insert(table).values(**key, **(initial or {}), **{column: delta}))
    except IntegrityError:
        conn.execute(stmt)
