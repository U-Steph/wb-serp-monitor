"""Shared frozen contracts passed between collector, normalizer and storage. Pure: no I/O.

``ObservationRow`` field order equals the ``observations`` columns after ``id``, ``run_id`` and
``query_id``; a test keeps the dataclass and the table from drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal

QueryStatus = Literal["ok", "partial", "blocked", "rate_limited", "error"]
RunStatus = Literal["running", "ok", "partial", "blocked", "error", "abandoned"]
Trigger = Literal["manual", "scheduler", "probe"]
QuerySource = Literal["sheet", "bundled"]
PageOutcome = Literal["ok", "end", "blocked", "rate_limited", "error"]

ERROR_CLASSES: frozenset[str] = frozenset(
    {
        "http_403",
        "http_498",
        "redirect",
        "http_429",
        "html_at_200",
        "bad_json",
        "schema_drift",
        "products_null",
        "empty_suspect",
        "http_5xx",
        "http_4xx",
        "http_other",
        "timeout",
        "connection",
        "body_too_large",
        "circuit_open",
        "deadline",
        "db_write_failed",
        "exception",
    }
)


@dataclass(frozen=True, slots=True)
class ObservationRow:
    """One product at one SERP position. ``None`` means unknown, never zero."""

    collected_at: str
    position: int
    nm_id: int
    name: str | None
    brand: str | None
    subject_id: int | None
    root_id: int | None
    price_rub: float | None
    price_basic_rub: float | None
    price_total_rub: float | None
    logistics_rub: float | None
    rating: float | None
    feedbacks: int | None
    nm_rating: float | None
    nm_feedbacks: int | None
    time1_h: int | None
    time2_h: int | None
    delivery_hours: int | None
    delivery_days: int | None
    seller_name: str | None
    seller_id: int | None
    is_promoted: int
    ad_promo_position: int | None
    ad_organic_position: int | None
    ad_tp: str | None
    ad_cpm: int | None
    total_quantity: int | None

    def as_params(self, run_id: int, query_id: int) -> dict[str, object]:
        """Return named SQL bind parameters for an ``observations`` INSERT."""
        return {
            "run_id": run_id,
            "query_id": query_id,
            **{name: getattr(self, name) for name in OBSERVATION_COLUMNS},
        }


OBSERVATION_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(ObservationRow))


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Outcome of collecting one query: the ``run_queries`` row plus its observations."""

    status: QueryStatus
    started_at: str
    duration_ms: int
    observations: tuple[ObservationRow, ...] = ()
    error_class: str | None = None
    http_status: int | None = None
    pages_fetched: int = 0
    is_complete: bool = False
    normquery: str | None = None
    ad_slot_mismatch: int = 0
    duplicates_dropped: int = 0
    error_message: str | None = None

    @property
    def items_collected(self) -> int:
        """Number of observations carried by this result."""
        return len(self.observations)


@dataclass(frozen=True, slots=True)
class QuerySet:
    """The query texts of a run and where they came from."""

    texts: tuple[str, ...]
    source: QuerySource
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PageFetch:
    """Raw result of one HTTP request for one SERP page."""

    query: str
    page: int
    status_code: int | None
    content_type: str | None
    server: str | None
    retry_after: str | None
    content: bytes
    received_at: str
    elapsed_ms: int
    attempts: int
    transport_error: str | None = None  # "timeout" | "connection" | None


@dataclass(frozen=True, slots=True)
class PageVerdict:
    """Classification of a fetched page."""

    outcome: PageOutcome
    error_class: str | None
    products: tuple[object, ...] | None
    payload: dict | None
    message: str | None
