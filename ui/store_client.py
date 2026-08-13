"""Shared Store handle for Streamlit Admin + Dashboard.

Prefers HTTP RemoteStore when CONTROL_PLANE_URL / TOKENOPS_CONTROL_PLANE_URL is set;
otherwise opens local SqliteStore (dev).
"""

from __future__ import annotations

import os

import streamlit as st

from control_plane.store_factory import open_store
from control_plane.store_protocol import ControlStore


@st.cache_resource
def get_store() -> ControlStore:
    """One store per UI process. ``auto_seed=False`` so Admin edits are not overwritten."""
    return open_store(auto_seed=False)


def clear_store_cache() -> None:
    get_store.clear()
