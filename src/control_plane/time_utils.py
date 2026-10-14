"""Time helpers: UTC now and ISO-8601 text for JSON responses."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """The current time as an aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    """``value`` as an ISO-8601 string in UTC with a ``Z`` suffix, or ``None``."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
