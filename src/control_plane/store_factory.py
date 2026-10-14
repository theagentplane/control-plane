"""Open the configured control-store backend (local SQLite or remote HTTP)."""

from __future__ import annotations

import os

from control_plane.remote_store import RemoteStore
from control_plane.store import SqliteStore
from control_plane.store_protocol import ControlStore


def control_plane_url() -> str | None:
    url = (
        os.environ.get("CONTROL_PLANE_URL")
        or os.environ.get("TOKENOPS_CONTROL_PLANE_URL")
        or ""
    ).strip()
    return url.rstrip("/") if url else None


def open_store(*, auto_seed: bool = True, db: str | None = None) -> ControlStore:
    """Return a store handle for the active backend.

    * ``CONTROL_PLANE_URL`` / ``TOKENOPS_CONTROL_PLANE_URL`` → :class:`RemoteStore`
    * otherwise → :class:`SqliteStore` at ``CONTROL_PLANE_DB`` / ``TOKENOPS_DB``
    """
    remote = control_plane_url()
    if remote:
        return RemoteStore(remote, api_key=__import__('os').environ.get('CONTROL_PLANE_API_KEY') or __import__('os').environ.get('TOKENOPS_API_KEY'))
    path = db or os.environ.get("CONTROL_PLANE_DB") or os.environ.get("TOKENOPS_DB") or "control_plane.db"
    return SqliteStore(path, auto_seed=auto_seed)


def registration_base_url(agent_url: str) -> str:
    """URL for ``POST /v1/runs`` — control plane when split, else the entry agent."""
    return control_plane_url() or agent_url.rstrip("/")
