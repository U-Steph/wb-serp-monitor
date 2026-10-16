"""Query list sources. Query wording is never stripped or normalised (byte-for-byte, QRY-02)."""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

from wbmon.models import QuerySet

if TYPE_CHECKING:
    import httpx

    from wbmon.config import Settings

BUNDLED_RESOURCE = "data/queries_bundled.txt"


def load_bundled() -> tuple[str, ...]:
    """Return the 70 bundled queries, one per line, exactly as stored in the package data."""
    raw = resources.files("wbmon").joinpath(BUNDLED_RESOURCE).read_bytes()
    text = raw.decode("utf-8")
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return tuple(lines)


def load_queries(settings: Settings, *, transport: httpx.BaseTransport | None = None) -> QuerySet:
    """Return the queries for a run.

    Skeleton slice: always the bundled copy, with no network access. The Google Sheet source with
    a plausibility fallback replaces this body later and keeps the signature.
    """
    return QuerySet(load_bundled(), "bundled", "skeleton_bundled_only")
