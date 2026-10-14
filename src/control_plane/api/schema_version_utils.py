"""Accept the current and the previous schema major (Chronicle design section 2.8).

Every persisted payload carries ``schema_version`` (``major.minor``). The control plane accepts
the major its primitives package speaks and the one before it, and rejects anything else with a
clear message rather than storing a payload it cannot read. Minors are compatible within a major
(readers ignore unknown fields). While the major is 0 there is no previous major.
"""

from typing import Any

from agentplane_primitives.types import SCHEMA_VERSION


def _major(version: str) -> int:
    return int(version.split(".", 1)[0])


def supported_majors() -> tuple[int, ...]:
    """The majors this service accepts: current, and previous when there is one."""
    current = _major(SCHEMA_VERSION)
    return (current, current - 1) if current > 0 else (current,)


def ensure_supported_schema(payload: Any) -> None:
    """Raise ``ValueError`` if ``payload`` names a schema major this service does not accept.

    A payload with no ``schema_version`` is read as the current one.
    """
    if not isinstance(payload, dict):
        return
    version = payload.get("schema_version")
    if version is None:
        return
    try:
        major = _major(str(version))
    except ValueError:
        raise ValueError(f"schema_version {version!r} is not major.minor") from None
    if major not in supported_majors():
        accepted = ", ".join(str(m) for m in supported_majors())
        raise ValueError(
            f"schema_version {version!r} is not supported; this service accepts major {accepted}"
        )
