"""Runtime settings from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = "control_plane.db"
    # Comma-separated api_key:tenant:scopes  e.g. agent1:t1:ingest,dash1:t1:read+admin
    # Empty = auth disabled (local/dev only).
    api_keys: str = ""
    default_durability: str = "sync"  # sync | queued
    max_batch: int = 100
    max_body_bytes: int = 4 * 1024 * 1024
    version: str = "0.1.0"


def load_settings() -> Settings:
    return Settings(
        db_path=os.environ.get("CONTROL_PLANE_DB")
        or os.environ.get("TOKENOPS_DB")
        or "control_plane.db",
        api_keys=os.environ.get("CONTROL_PLANE_API_KEYS", ""),
        default_durability=os.environ.get("CONTROL_PLANE_DURABILITY", "sync"),
        max_batch=int(os.environ.get("CONTROL_PLANE_MAX_BATCH", "100")),
        max_body_bytes=int(os.environ.get("CONTROL_PLANE_MAX_BODY", str(4 * 1024 * 1024))),
        version=os.environ.get("CONTROL_PLANE_VERSION", "0.1.0"),
    )
