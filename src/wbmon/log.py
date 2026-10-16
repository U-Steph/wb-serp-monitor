"""One ``key=value`` line per event on stdout (friendly to ``docker compose logs``)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import TextIO

from wbmon.clock import iso_z

LOGGER_NAME = "wbmon"
MAX_VALUE_LENGTH = 300
_BARE_EXTRA = frozenset("-_.:/@+%,")


def fmt_value(value: object) -> str:
    """Render one log value: bare when safe, JSON-quoted otherwise, truncated to 300 chars.

    Quoting stops query text or remote error bodies from forging fields or extra lines.
    """
    if value is None:
        return "-"
    text = str(value)
    if len(text) > MAX_VALUE_LENGTH:
        text = text[: MAX_VALUE_LENGTH - 1] + "…"
    if text and all(ch.isalnum() or ch in _BARE_EXTRA for ch in text):
        return text
    return json.dumps(text, ensure_ascii=False)


class KeyValueFormatter(logging.Formatter):
    """Format records as ``ts=... level=... event=... key=value ...``."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = iso_z(datetime.fromtimestamp(record.created, tz=UTC))
        parts = [
            f"ts={stamp}",
            f"level={record.levelname}",
            f"event={fmt_value(record.getMessage())}",
        ]
        fields: dict[str, object] = getattr(record, "fields", None) or {}
        parts.extend(f"{key}={fmt_value(val)}" for key, val in fields.items())
        return " ".join(parts)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Attach one key=value stream handler to the ``wbmon`` logger (replacing earlier ones)."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(KeyValueFormatter())
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper()))
    logger.propagate = False


def log_event(event: str, /, *, level: int = logging.INFO, **fields: object) -> None:
    """Log one structured event; ``fields`` keep their insertion order."""
    logging.getLogger(LOGGER_NAME).log(level, event, extra={"fields": fields})
