"""Advisory run lock (D-A7): at most one collector holds ``{db_path}.lock`` at a time.

a non-blocking exclusive ``fcntl.flock`` on a sidecar file. The kernel drops the lock when the
holder exits for any reason (including SIGKILL), so it can never be stale. The lock file itself
is never deleted: removing it would let two processes lock different inodes. POSIX only
(Linux/macOS targets). ``wbmon collect`` holds it for the whole run, which makes
``Writer.abandon_stale_runs`` safe; Phase 2's scheduler reuses the same helper.
"""

from __future__ import annotations

import contextlib
import fcntl
from collections.abc import Iterator
from pathlib import Path


class LockBusy(RuntimeError):
    """Another process already holds the run lock."""


@contextlib.contextmanager
def run_lock(db_path: str | Path) -> Iterator[Path]:
    """Hold the non-blocking exclusive lock for ``{db_path}.lock``; yield the lock file path."""
    lock_path = Path(str(db_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")  # noqa: SIM115 - closed in the finally block below
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockBusy(str(lock_path)) from None
        try:
            yield lock_path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
