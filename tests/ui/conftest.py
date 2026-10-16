"""UI test configuration: skip the suite when the web stack is not installed, isolate caches."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

WEB_STACK = all(importlib.util.find_spec(m) is not None for m in ("streamlit", "pandas", "plotly"))

# CI installs only the dev extra: skip the UI suite there instead of failing on imports.
collect_ignore_glob = [] if WEB_STACK else ["test_*.py"]


@pytest.fixture(autouse=True)
def _fresh_streamlit_cache():
    """st.cache_data is process-global: clear it so tests cannot see each other's frames."""
    if not WEB_STACK:
        yield
        return
    import streamlit as st

    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture
def db_path() -> Path:
    """The per-test temporary database path (set by the root conftest inside tmp_path)."""
    return Path(os.environ["WBMON_DB_PATH"])
