"""Pure normalization of WB search payloads into observation rows. No I/O.

Unknown stays ``None``: a missing or malformed field is never turned into 0 or a guess.
"""

from __future__ import annotations

import math
from datetime import tzinfo

from wbmon.models import ObservationRow


def to_int(v: object) -> int | None:
    """Coerce an int-like value (int, whole float, digit string) to int; otherwise ``None``."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v)
    return None


def to_float(v: object) -> float | None:
    """Coerce a finite number or numeric string to float; otherwise ``None``."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        result = float(v)
    elif isinstance(v, str):
        try:
            result = float(v)
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def to_str(v: object) -> str | None:
    """Return ``v`` only when it is a string."""
    return v if isinstance(v, str) else None


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


def normalize_product(
    raw: object, *, position: int, collected_at: str, tz: tzinfo
) -> ObservationRow | None:
    """Map one raw WB product to an observation row, or ``None`` without an integer ``id``.

    Skeleton slice: identity, name, brand and the promoted flag are mapped; every other column is
    ``None`` (unknown) until the field-mapping slice fills them in. ``tz`` is reserved for the
    delivery-day calculation.
    """
    if not isinstance(raw, dict):
        return None
    nm_id = to_int(raw.get("id"))
    if nm_id is None:
        return None
    log = raw.get("log")
    return ObservationRow(
        collected_at=collected_at,
        position=position,
        nm_id=nm_id,
        name=to_str(raw.get("name")),
        brand=to_str(raw.get("brand")),
        subject_id=None,
        root_id=None,
        price_rub=None,
        price_basic_rub=None,
        price_total_rub=None,
        logistics_rub=None,
        rating=None,
        feedbacks=None,
        nm_rating=None,
        nm_feedbacks=None,
        time1_h=None,
        time2_h=None,
        delivery_hours=None,
        delivery_days=None,
        seller_name=None,
        seller_id=None,
        is_promoted=1 if isinstance(log, dict) and log else 0,
        ad_promo_position=None,
        ad_organic_position=None,
        ad_tp=None,
        ad_cpm=None,
        total_quantity=None,
    )
