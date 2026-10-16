"""Pure normalization of WB search payloads into observation rows. No I/O.

Unknown stays ``None``: a missing or malformed field is never turned into 0 or a guess.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import tzinfo
from decimal import ROUND_HALF_UP, Decimal

from wbmon.clock import parse_iso_z
from wbmon.delivery import delivery_days
from wbmon.models import ObservationRow

# SQLite stores INTEGER as a signed 64-bit value; anything outside that range is "unknown" (NULL)
# rather than an OverflowError that would abort the whole query write.
INT64_MAX = 2**63 - 1
_INT_TEXT = re.compile(r"-?[0-9]+")


def to_int(v: object) -> int | None:
    """Coerce an int-like value (int, whole float, digit string) to int; otherwise ``None``.

    Values outside the signed 64-bit range cannot be stored by SQLite and become ``None``.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        result = v
    elif isinstance(v, float):
        if not v.is_integer():
            return None
        result = int(v)
    elif isinstance(v, str):
        text = v.strip()
        if _INT_TEXT.fullmatch(text) is None:  # ASCII only: no "²", "٣", "--5", "1_0", "+5"
            return None
        try:
            result = int(text)
        except ValueError:  # e.g. more digits than the interpreter's int-conversion limit
            return None
    else:
        return None
    return result if -INT64_MAX <= result <= INT64_MAX else None


def to_float(v: object) -> float | None:
    """Coerce a finite number or numeric string to float; otherwise ``None``."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            result = float(v)
        except OverflowError:  # a JSON integer with hundreds of digits
            return None
    elif isinstance(v, str):
        try:
            result = float(v)
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def to_str(v: object) -> str | None:
    """Return ``v`` only when it is a string SQLite can store as UTF-8 (no lone surrogates)."""
    if not isinstance(v, str):
        return None
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:  # JSON "\ud800" decodes to an unpaired surrogate
        return None
    return v


def extract_products(payload: object) -> list | None:
    """Return the products list from either known envelope, or ``None`` when absent/not a list."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict) and "products" in data:
        products = data["products"]
        return products if isinstance(products, list) else None
    if "products" in payload:
        products = payload["products"]
        return products if isinstance(products, list) else None
    return None


def payload_shape(payload: object) -> str:
    """Name the envelope: ``data.products``, ``products`` or ``unknown``."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and "products" in data:
            return "data.products"
        if "products" in payload:
            return "products"
    return "unknown"


def extract_normquery(payload: object) -> str | None:
    """Return WB's normalised query (``metadata.normquery``) when present."""
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and to_str(metadata.get("normquery")) is not None:
        return to_str(metadata.get("normquery"))
    data = payload.get("data")
    if isinstance(data, dict):
        inner = data.get("metadata")
        if isinstance(inner, dict):
            return to_str(inner.get("normquery"))
    return None


def rub(kopecks: object) -> float | None:
    """Convert an integer kopeck amount to rubles rounded half-up to 2 places; else ``None``."""
    k = to_int(kopecks)
    if k is None:
        return None
    return float((Decimal(k) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP))


def pick_size(raw: Mapping) -> Mapping | None:
    """Return the cheapest size whose ``price.product`` is positive (ties: first), else ``None``."""
    sizes = raw.get("sizes")
    if not isinstance(sizes, list):
        return None
    best: tuple[int, Mapping] | None = None
    for size in sizes:
        if not isinstance(size, dict) or not isinstance(size.get("price"), dict):
            continue
        product_price = to_int(size["price"].get("product"))
        if product_price is None or product_price <= 0:
            continue
        if best is None or product_price < best[0]:
            best = (product_price, size)
    return best[1] if best else None


def _ad_log(raw: object) -> Mapping | None:
    """Return the ``log`` object of an ad item, or ``None`` for organic items."""
    if not isinstance(raw, dict):
        return None
    log = raw.get("log")
    return log if isinstance(log, dict) and log else None


def promo_position(raw: object) -> int | None:
    """Return ``log.promoPosition`` when the item carries a non-empty ``log`` object."""
    log = _ad_log(raw)
    return to_int(log.get("promoPosition")) if log is not None else None


def is_promoted(raw: object) -> int:
    """Return 1 when the item carries a non-empty ``log`` object (an ad slot), else 0."""
    return 1 if _ad_log(raw) is not None else 0


def _price_columns(raw: Mapping) -> tuple[float | None, float | None, float | None, float | None]:
    """Return ``(price, basic, total, logistics)`` in rubles from one size; NULL if unpriced."""
    size = pick_size(raw)
    if size is not None:
        price = size["price"]
        return (
            rub(price.get("product")),
            rub(price.get("basic")),
            rub(price.get("total")),
            rub(price.get("logistics")),
        )
    sale = to_int(raw.get("salePriceU"))
    if sale is not None and sale > 0:
        return rub(sale), rub(raw.get("priceU")), None, None
    return None, None, None, None


def _delivery_times(raw: Mapping) -> tuple[int | None, int | None]:
    """Return raw ``(time1, time2)``: product level, else the fastest complete size."""
    time1 = to_int(raw.get("time1"))
    time2 = to_int(raw.get("time2"))
    if time1 is not None and time2 is not None:
        return time1, time2
    best: tuple[int, int] | None = None
    sizes = raw.get("sizes")
    for size in sizes if isinstance(sizes, list) else []:
        if not isinstance(size, dict):
            continue
        s1, s2 = to_int(size.get("time1")), to_int(size.get("time2"))
        if s1 is None or s2 is None:
            continue
        if best is None or s1 + s2 < best[0] + best[1]:
            best = (s1, s2)
    if best is not None:
        return best[0], best[1]
    return time1, time2


def normalize_product(
    raw: object, *, position: int, collected_at: str, tz: tzinfo
) -> ObservationRow | None:
    """Map one raw WB product to an observation row, or ``None`` without an integer ``id``.

    Every unavailable or malformed value becomes ``None``; nothing is defaulted to 0 or guessed.
    ``delivery_days`` is derived from the stored ``collected_at`` and ``delivery_hours`` so it can
    always be re-computed from the row.
    """
    if not isinstance(raw, dict):
        return None
    nm_id = to_int(raw.get("id"))
    if nm_id is None:
        return None
    price, basic, total, logistics = _price_columns(raw)
    rating = to_float(raw.get("reviewRating"))
    if rating == 0:
        rating = None
    time1, time2 = _delivery_times(raw)
    hours = time1 + time2 if time1 is not None and time2 is not None else None
    if hours is not None and (time1 < 0 or time2 < 0 or hours > INT64_MAX):
        hours = None
    days = delivery_days(parse_iso_z(collected_at), hours, tz)
    log = _ad_log(raw)
    return ObservationRow(
        collected_at=collected_at,
        position=position,
        nm_id=nm_id,
        name=to_str(raw.get("name")),
        brand=to_str(raw.get("brand")),
        subject_id=to_int(raw.get("subjectId")),
        root_id=to_int(raw.get("root")),
        price_rub=price,
        price_basic_rub=basic,
        price_total_rub=total,
        logistics_rub=logistics,
        rating=rating,
        feedbacks=to_int(raw.get("feedbacks")),
        nm_rating=to_float(raw.get("nmReviewRating")),
        nm_feedbacks=to_int(raw.get("nmFeedbacks")),
        time1_h=time1,
        time2_h=time2,
        delivery_hours=hours,
        delivery_days=days,
        seller_name=to_str(raw.get("supplier")),
        seller_id=to_int(raw.get("supplierId")),
        is_promoted=is_promoted(raw),
        ad_promo_position=to_int(log.get("promoPosition")) if log is not None else None,
        ad_organic_position=to_int(log.get("position")) if log is not None else None,
        ad_tp=to_str(log.get("tp")) if log is not None else None,
        ad_cpm=to_int(log.get("cpm")) if log is not None else None,
        total_quantity=to_int(raw.get("totalQuantity")),
    )
