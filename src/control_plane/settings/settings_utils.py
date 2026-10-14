"""Load ``Settings`` from environment variables (all prefixed ``CONTROL_PLANE_``)."""

import os
from collections.abc import Mapping

from control_plane.settings.settings_types import Settings

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _flag(value: str, name: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"{name} expects a boolean, got {value!r}")


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Read the settings from ``environ`` (the process environment by default)."""
    env = os.environ if environ is None else environ
    defaults = Settings()
    retention = env.get("CONTROL_PLANE_RETENTION_DAYS")
    return Settings(
        database_url=env.get("CONTROL_PLANE_DATABASE_URL", defaults.database_url),
        auto_migrate=_flag(env["CONTROL_PLANE_AUTO_MIGRATE"], "CONTROL_PLANE_AUTO_MIGRATE")
        if "CONTROL_PLANE_AUTO_MIGRATE" in env
        else defaults.auto_migrate,
        seed_on_start=_flag(env["CONTROL_PLANE_SEED_ON_START"], "CONTROL_PLANE_SEED_ON_START")
        if "CONTROL_PLANE_SEED_ON_START" in env
        else defaults.seed_on_start,
        api_keys=env.get("CONTROL_PLANE_API_KEYS", defaults.api_keys),
        max_batch=int(env.get("CONTROL_PLANE_MAX_BATCH", defaults.max_batch)),
        max_body_bytes=int(env.get("CONTROL_PLANE_MAX_BODY", defaults.max_body_bytes)),
        retention_days=int(retention) if retention else None,
    )
