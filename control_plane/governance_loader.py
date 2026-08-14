"""Governance YAML loading for the control plane seed."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# Packaged copy (wheel) then repo-root config/default.yaml (editable checkout)
_PKG_DEFAULT = Path(__file__).resolve().parent / "default.yaml"
_REPO_DEFAULT = Path(__file__).resolve().parent.parent / "config" / "default.yaml"


def _default_path() -> Path:
    env = os.environ.get("CONTROL_PLANE_CONFIG") or os.environ.get("TOKENOPS_CONFIG")
    if env:
        return Path(env)
    if _PKG_DEFAULT.exists():
        return _PKG_DEFAULT
    return _REPO_DEFAULT


def load_governance_yaml(path: Path | str | None = None) -> dict:
    """Return the ``governance:`` block from the config YAML (budgets + policies)."""
    config_path = Path(path) if path else _default_path()
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text()) or {}
    block = data.get("governance")
    return block if isinstance(block, dict) else {}
