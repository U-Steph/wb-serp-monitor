"""Pure classification of one fetched SERP page. No I/O.

Every outcome of a WB request is classified here *before* anything is parsed, so an anti-bot wall
(403 / 498 / redirect / HTML at 200), a rate limit (429) and a genuine failure are never confused
with "no results". The decision table is documented in 01-RESEARCH.md ("classification matrix").
"""

from __future__ import annotations

import email.utils
import json
import unicodedata
from datetime import UTC, datetime

from wbmon.models import PageFetch, PageVerdict
from wbmon.normalize import extract_products

MAX_BODY_BYTES = 8 * 1024 * 1024
DEFAULT_RETRY_AFTER = 30.0
RETRY_AFTER_CAP = 60.0
BODY_PREFIX_CHARS = 200
HEADER_VALUE_CHARS = 100


def parse_retry_after(
    value: str | None, now: datetime, cap: float = RETRY_AFTER_CAP
) -> float | None:
    """Return the wait in seconds from a ``Retry-After`` value, capped, or ``None`` if unusable.

    Accepts delta-seconds (also fractional) and HTTP-dates; negative or past values become 0.0.
    """
    if value is None:
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - now).total_seconds()
    if seconds != seconds:  # NaN
        return None
    return min(max(seconds, 0.0), cap)


def sanitize_text(text: str, limit: int) -> str:
    """Return at most ``limit`` characters of untrusted remote text, safe to store and log.

    Every control, format, surrogate, private-use or unassigned character (Unicode category
    ``C*``, which includes newlines, ESC, NUL, C1 controls and bidi overrides) and the Unicode
    line/paragraph separators become a space, so the text stays on one line. HTML or markdown is
    *not* altered: consumers must render it as plain text.
    """
    return "".join(
        " "
        if unicodedata.category(ch)[0] == "C" or unicodedata.category(ch) in ("Zl", "Zp")
        else ch
        for ch in text[:limit]
    )


def describe(fetch: PageFetch) -> str:
    """Return a one-line diagnostic: status, server, content type, attempts, transport, body."""
    status = fetch.status_code if fetch.status_code is not None else "-"
    server = sanitize_text(fetch.server or "", HEADER_VALUE_CHARS) or "-"
    ctype = sanitize_text(fetch.content_type or "", HEADER_VALUE_CHARS) or "-"
    body = sanitize_text(
        fetch.content[: 4 * BODY_PREFIX_CHARS].decode("utf-8", errors="replace"), BODY_PREFIX_CHARS
    )
    return (
        f"http={status} server={server} ctype={ctype} "
        f"attempts={fetch.attempts} transport={fetch.transport_error or '-'} body={body}"
    )


def _verdict(
    fetch: PageFetch, outcome: str, error_class: str, products=None, payload=None
) -> PageVerdict:
    return PageVerdict(
        outcome=outcome,  # type: ignore[arg-type]
        error_class=error_class,
        products=products,
        payload=payload,
        message=describe(fetch),
    )


def _classify_status(fetch: PageFetch) -> PageVerdict | None:
    """Classify by transport error / HTTP status; ``None`` means a 200 that needs body checks."""
    if fetch.transport_error is not None:
        error_class = "timeout" if fetch.transport_error == "timeout" else "connection"
        return _verdict(fetch, "error", error_class)
    status = fetch.status_code
    if status == 403:
        return _verdict(fetch, "blocked", "http_403")
    if status == 498:
        return _verdict(fetch, "blocked", "http_498")
    if status is not None and 300 <= status < 400:
        return _verdict(fetch, "blocked", "redirect")
    if status == 429:
        return _verdict(fetch, "rate_limited", "http_429")
    if status is not None and 500 <= status < 600:
        return _verdict(fetch, "error", "http_5xx")
    if status is not None and 400 <= status < 500:
        return _verdict(fetch, "error", "http_4xx")
    if status != 200:
        return _verdict(fetch, "error", "http_other")
    return None


def _products_is_null(payload: dict) -> bool:
    """True when the products key exists (``data.products`` first, else top level) and is null."""
    data = payload.get("data")
    if isinstance(data, dict) and "products" in data:
        return data["products"] is None
    return "products" in payload and payload["products"] is None


def classify_page(fetch: PageFetch) -> PageVerdict:
    """Classify one fetched page into ok / end / blocked / rate_limited / error."""
    early = _classify_status(fetch)
    if early is not None:
        return early
    content = fetch.content
    if len(content) > MAX_BODY_BYTES:
        return _verdict(fetch, "error", "body_too_large")
    content_type = (fetch.content_type or "").lower()
    if "html" in content_type or content.lstrip()[:1] == b"<":
        return _verdict(fetch, "blocked", "html_at_200")
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except ValueError:  # JSONDecodeError and UnicodeDecodeError are both ValueErrors
        return _verdict(fetch, "error", "bad_json")
    if not isinstance(payload, dict):
        return _verdict(fetch, "error", "bad_json")
    products = extract_products(payload)
    if products is None:
        error_class = "products_null" if _products_is_null(payload) else "schema_drift"
        return _verdict(fetch, "error", error_class)
    if not products:
        if fetch.page <= 1:
            return _verdict(fetch, "error", "empty_suspect", payload=payload)
        return PageVerdict("end", None, (), payload, None)
    return PageVerdict("ok", None, tuple(products), payload, None)
