"""``UtcDateTime``: a time stamp that is always UTC and time-zone aware in Python.

Databases differ in how they keep time zones (SQLite keeps none). This type stores a naive UTC
value and returns an aware UTC ``datetime``, so every dialect behaves the same and ordering and
``min`` or ``max`` over the column are correct.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """Stores UTC, returns aware UTC. Refuses naive values rather than guessing a zone."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("a time stamp must carry a time zone")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        aware: datetime = value.replace(tzinfo=timezone.utc)
        return aware
