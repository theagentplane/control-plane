"""Service settings, read from the environment."""

from control_plane.settings.settings_types import Settings
from control_plane.settings.settings_utils import load_settings

__all__ = ["Settings", "load_settings"]
