"""``AppState``: what the HTTP layer needs, built once at startup and kept on the app."""

from dataclasses import dataclass, field

from sqlalchemy import Engine

from control_plane.auth import Principal
from control_plane.repositories import Repositories
from control_plane.settings import Settings


@dataclass(frozen=True)
class AppState:
    """Settings, the engine and the repositories over it."""

    settings: Settings
    engine: Engine
    repos: Repositories
    env_keys: dict[str, Principal] = field(default_factory=dict)
    """API keys from ``CONTROL_PLANE_API_KEYS``, parsed once."""
