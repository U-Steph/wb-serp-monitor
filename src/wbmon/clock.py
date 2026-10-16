"""UTC time helpers. Pure: no I/O. Every stored timestamp is ``YYYY-MM-DDTHH:MM:SSZ``."""

from __future__ import annotations

import re
from datetime import UTC, datetime

_ISO_Z_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime truncated to whole seconds."""
    return datetime.now(UTC).replace(microsecond=0)


def _require_aware(dt: datetime) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("naive datetime is not allowed; use an aware datetime")


def iso_z(dt: datetime) -> str:
    """Format an aware datetime as UTC ``YYYY-MM-DDTHH:MM:SSZ`` (naive input is rejected)."""
    _require_aware(dt)
    return dt.astimezone(UTC).strftime(_ISO_Z_FORMAT)


def parse_iso_z(text: str) -> datetime:
    """Strict inverse of :func:`iso_z`; any other format raises ``ValueError``."""
    if not _ISO_Z_RE.match(text):
        raise ValueError(f"not a YYYY-MM-DDTHH:MM:SSZ timestamp: {text!r}")
    return datetime.strptime(text, _ISO_Z_FORMAT).replace(tzinfo=UTC)


def floor_hour(dt: datetime) -> datetime:
    """Truncate a datetime to the start of its hour."""
    return dt.replace(minute=0, second=0, microsecond=0)


def parse_slot(text: str) -> str:
    """Normalise an aware ISO-8601 time to the floored-hour ``iso_z`` slot (UTC).

    Naive or unparsable input raises ``ValueError`` so a slot can never be guessed.
    """
    candidate = text.strip()
    if candidate[-1:] in ("Z", "z"):
        candidate = candidate[:-1] + "+00:00"
    parsed = datetime.fromisoformat(candidate)
    _require_aware(parsed)
    return iso_z(floor_hour(parsed.astimezone(UTC)))
