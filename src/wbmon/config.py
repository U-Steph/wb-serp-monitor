"""Frozen runtime settings loaded from ``WBMON_*`` environment variables."""

from __future__ import annotations

import dataclasses
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from wbmon import REPO_URL, __version__

DEFAULT_DB_PATH = "data/wbmon.sqlite3"
DEFAULT_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v18/search"
DEFAULT_SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/1K0m4229w_1LONgrNzWpx8lkkDcAlbdZH5ifHenZV3x8"
    "/export?format=csv"
)
DEFAULT_DEST = "-1257786"  # Moscow delivery region
FIXED_SEARCH_PARAMS: Mapping[str, str] = MappingProxyType(
    {
        "ab_testing": "false",
        "appType": "1",
        "curr": "rub",
        "lang": "ru",
        "locale": "ru",
        "resultset": "catalog",
        "sort": "popular",
        "spp": "30",
        "suppressSpellcheck": "false",
    }
)

_TOP_N_RANGE = (1, 1000)
_MIN_REQUEST_DELAY = 0.5  # polite floor: protects WB from a misconfigured run
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(ValueError):
    """Invalid configuration; the message names the offending ``WBMON_*`` variable."""


def default_user_agent() -> str:
    """Return the honest bot-style User-Agent that identifies this project."""
    return f"Mozilla/5.0 (compatible; wbmon/{__version__}; +{REPO_URL})"


def _check_url(var: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"{var}: must be an http(s) URL with a host, got {value!r}")


def _parse_int(var: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{var}: not an integer: {raw!r}") from None


def _parse_float(var: str, raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{var}: not a number: {raw!r}") from None


@dataclass(frozen=True, slots=True)
class Settings:
    """All tunables of one collector process. Validated on construction."""

    db_path: Path
    top_n: int
    dest: str
    search_url: str
    request_delay: float
    run_deadline: float
    sheet_csv_url: str
    user_agent: str
    timezone: str
    log_level: str

    def __post_init__(self) -> None:
        low, high = _TOP_N_RANGE
        if isinstance(self.top_n, bool) or not isinstance(self.top_n, int):
            raise ConfigError("WBMON_TOP_N: must be an integer")
        if not low <= self.top_n <= high:
            raise ConfigError(f"WBMON_TOP_N: must be between {low} and {high}, got {self.top_n}")
        try:
            int(self.dest)
        except (TypeError, ValueError):
            raise ConfigError(f"WBMON_DEST: must be an integer, got {self.dest!r}") from None
        _check_url("WBMON_SEARCH_URL", self.search_url)
        _check_url("WBMON_SHEET_CSV_URL", self.sheet_csv_url)
        if not math.isfinite(self.request_delay) or self.request_delay < _MIN_REQUEST_DELAY:
            raise ConfigError(
                f"WBMON_REQUEST_DELAY: must be at least {_MIN_REQUEST_DELAY} seconds, "
                f"got {self.request_delay}"
            )
        if not math.isfinite(self.run_deadline) or self.run_deadline <= 0:
            raise ConfigError("WBMON_RUN_DEADLINE: must be a positive number of seconds")
        if not self.user_agent.strip() or any(c in self.user_agent for c in "\r\n"):
            raise ConfigError("WBMON_USER_AGENT: must be a non-empty single line")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ConfigError(f"WBMON_TIMEZONE: unknown time zone {self.timezone!r}") from None
        if self.log_level not in _LOG_LEVELS:
            raise ConfigError(
                f"WBMON_LOG_LEVEL: must be one of {', '.join(_LOG_LEVELS)}, got {self.log_level!r}"
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from ``environ`` (default ``os.environ``); empty values mean unset."""
        if environ is None:
            environ = os.environ

        def get(name: str, default: str) -> str:
            value = environ.get(name, "")
            return value if value != "" else default

        return cls(
            db_path=Path(get("WBMON_DB_PATH", DEFAULT_DB_PATH)),
            top_n=_parse_int("WBMON_TOP_N", get("WBMON_TOP_N", "200")),
            dest=get("WBMON_DEST", DEFAULT_DEST),
            search_url=get("WBMON_SEARCH_URL", DEFAULT_SEARCH_URL),
            request_delay=_parse_float("WBMON_REQUEST_DELAY", get("WBMON_REQUEST_DELAY", "1.5")),
            run_deadline=_parse_float("WBMON_RUN_DEADLINE", get("WBMON_RUN_DEADLINE", "2400")),
            sheet_csv_url=get("WBMON_SHEET_CSV_URL", DEFAULT_SHEET_CSV_URL),
            user_agent=get("WBMON_USER_AGENT", default_user_agent()),
            timezone=get("WBMON_TIMEZONE", "Europe/Moscow"),
            log_level=get("WBMON_LOG_LEVEL", "INFO"),
        )

    def with_overrides(self, **changes: object) -> Settings:
        """Return a copy with some fields replaced (validated like a fresh instance)."""
        return dataclasses.replace(self, **changes)

    def search_params(self, query: str, page: int) -> dict[str, str]:
        """Return the WB request parameters; ``query`` is passed through byte-for-byte."""
        return {**FIXED_SEARCH_PARAMS, "dest": self.dest, "page": str(page), "query": query}

    def params_json(self) -> str:
        """Return the pinned request configuration as JSON, stored on every run."""
        return json.dumps(
            {
                "endpoint": self.search_url,
                "params": {**FIXED_SEARCH_PARAMS, "dest": self.dest},
                "top_n": self.top_n,
                "request_delay": self.request_delay,
                "user_agent": self.user_agent,
                "timezone": self.timezone,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def tzinfo(self) -> ZoneInfo:
        """Return the configured display/delivery time zone."""
        return ZoneInfo(self.timezone)
