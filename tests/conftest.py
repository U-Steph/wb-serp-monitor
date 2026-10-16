"""Test isolation: every test runs offline, in a temporary cwd, against a temporary database."""

from __future__ import annotations

import os
import socket

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Keep tests away from the real DB path and the network.

    - cwd becomes ``tmp_path`` so the default ``./data/wbmon.sqlite3`` can never be the repo's file;
    - every ``WBMON_*`` variable is dropped and ``WBMON_DB_PATH`` points into ``tmp_path``;
    - DNS and socket connects raise, so an httpx client without a MockTransport fails loudly.
    """
    monkeypatch.chdir(tmp_path)
    for key in [k for k in os.environ if k.startswith("WBMON_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("WBMON_DB_PATH", str(tmp_path / "test.sqlite3"))

    def _blocked(*_args, **_kwargs):
        raise RuntimeError("network access is forbidden in tests")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
