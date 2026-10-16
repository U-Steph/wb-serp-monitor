"""Field mapping: fixture rows equal the expected table; unknown stays NULL; purity."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import sqlite3

import httpx
import pytest

from factories import (
    FIXTURES_DIR,
    PURE_FORBIDDEN_IMPORTS,
    SRC_DIR,
    FakeWB,
    RecordingSleep,
    json_response,
    module_imports,
)
from wbmon.clock import parse_iso_z
from wbmon.delivery import MSK, delivery_days
from wbmon.normalize import (
    extract_normquery,
    extract_products,
    is_promoted,
    normalize_product,
    payload_shape,
    pick_size,
    promo_position,
    rub,
)

COLLECTED_AT = "2026-10-16T07:00:00Z"  # 10:00 Europe/Moscow


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _rows(name: str) -> list:
    products = extract_products(_load(name))
    assert products is not None
    rows = [
        normalize_product(raw, position=i, collected_at=COLLECTED_AT, tz=MSK)
        for i, raw in enumerate(products, start=1)
    ]
    assert all(row is not None for row in rows)
    return rows


def _row(raw: object, position: int = 1):
    return normalize_product(raw, position=position, collected_at=COLLECTED_AT, tz=MSK)


def _expected(nm_id: int, position: int, **fields: object) -> dict:
    """Expected row for a product shaped like P1; ``fields`` override the P1 defaults."""
    base = {
        "collected_at": COLLECTED_AT,
        "position": position,
        "nm_id": nm_id,
        "name": f"ТЕСТ товар {nm_id}",
        "brand": "ТЕСТ-БРЕНД",
        "subject_id": 2300,
        "root_id": nm_id - 100000000,
        "price_rub": 1500.0,
        "price_basic_rub": 3000.0,
        "price_total_rub": 1500.0,
        "logistics_rub": 0.0,
        "rating": 4.7,
        "feedbacks": 120,
        "nm_rating": 4.6,
        "nm_feedbacks": 40,
        "time1_h": 3,
        "time2_h": 26,
        "delivery_hours": 29,
        "delivery_days": 1,
        "seller_name": "ТЕСТ-Продавец",
        "seller_id": 500001,
        "is_promoted": 0,
        "ad_promo_position": None,
        "ad_organic_position": None,
        "ad_tp": None,
        "ad_cpm": None,
        "total_quantity": 25,
    }
    base.update(fields)
    return base


P2_AD = {"is_promoted": 1, "ad_promo_position": 2, "ad_organic_position": 7, "ad_tp": "c"}
P3 = {
    "subject_id": 2301,
    "price_rub": 1399.0,
    "price_basic_rub": 4750.0,
    "price_total_rub": 1478.0,
    "logistics_rub": 79.0,
    "rating": 4.9,
    "feedbacks": 15,
    "nm_rating": 4.8,
    "nm_feedbacks": 5,
    "time1_h": 2,
    "time2_h": 107,
    "delivery_hours": 109,
    "delivery_days": 4,
    "seller_name": "ТЕСТ-Продавец-2",
    "seller_id": 500002,
    "total_quantity": 7,
}
NO_SELLER = {"seller_name": None, "seller_id": None}
EXPECTED_MAIN = [
    _expected(900000001, 1),
    _expected(900000002, 2, ad_cpm=1025, **P2_AD),
    _expected(900000003, 3, **P3),
]
EXPECTED_MISSING = [
    _expected(
        900000011,
        1,
        price_rub=None,
        price_basic_rub=None,
        price_total_rub=None,
        logistics_rub=None,
        rating=None,
        feedbacks=0,
        nm_rating=0.0,
        nm_feedbacks=0,
        time1_h=None,
        time2_h=None,
        delivery_hours=None,
        delivery_days=None,
    ),
    _expected(
        900000012,
        2,
        price_rub=None,
        price_basic_rub=None,
        price_total_rub=None,
        logistics_rub=None,
    ),
    _expected(
        900000013,
        3,
        rating=None,
        feedbacks=None,
        nm_rating=None,
        nm_feedbacks=None,
        total_quantity=None,
        **NO_SELLER,
    ),
    _expected(900000014, 4),
    _expected(900000015, 5),
    _expected(
        900000016,
        6,
        is_promoted=1,
        ad_promo_position=6,
        ad_organic_position=3,
        ad_tp="b",
        ad_cpm=0,
    ),
]
EXPECTED_LEGACY = [
    _expected(
        900000010,
        1,
        subject_id=None,
        price_rub=721.0,
        price_basic_rub=1290.0,
        price_total_rub=None,
        logistics_rub=None,
        rating=4.2,
        feedbacks=3,
        nm_rating=None,
        nm_feedbacks=None,
        seller_id=500010,
        total_quantity=None,
    )
]


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("search_data_products.json", EXPECTED_MAIN),
        ("search_top_level_products.json", EXPECTED_MAIN),
        ("search_missing_fields.json", EXPECTED_MISSING),
        ("search_legacy_prices.json", EXPECTED_LEGACY),
    ],
)
def test_fixture_rows_equal_expected_table(fixture, expected):
    assert [dataclasses.asdict(row) for row in _rows(fixture)] == expected


def test_both_envelope_shapes_give_identical_rows():
    assert _rows("search_data_products.json") == _rows("search_top_level_products.json")


def test_envelope_helpers_on_fixtures():
    data, top, legacy = (
        _load("search_data_products.json"),
        _load("search_top_level_products.json"),
        _load("search_legacy_prices.json"),
    )
    assert (payload_shape(data), payload_shape(top)) == ("data.products", "products")
    assert extract_normquery(data) == extract_normquery(top) == "тест запрос"
    assert extract_normquery(legacy) is None
    assert extract_products(top) == extract_products(data)


@pytest.mark.parametrize(
    "fixture",
    [
        "search_data_products.json",
        "search_top_level_products.json",
        "search_missing_fields.json",
        "search_legacy_prices.json",
    ],
)
def test_stored_delivery_days_can_be_rederived_from_the_row(fixture):
    """Pattern 4: delivery_days is a pure function of collected_at and delivery_hours."""
    for row in _rows(fixture):
        rederived = delivery_days(parse_iso_z(row.collected_at), row.delivery_hours)
        assert row.delivery_days == rederived
        assert (row.delivery_days is None) == (row.delivery_hours is None)


@pytest.mark.parametrize("raw", [{}, {"name": "no id"}, {"id": "abc"}, {"id": True}, "x", 5, None])
def test_product_without_integer_id_is_skipped(raw):
    assert _row(raw) is None


def test_string_typed_numbers_are_coerced():
    row = _row({"id": "900000099", "feedbacks": "12", "reviewRating": "4.5", "name": 123})
    assert row is not None
    assert (row.nm_id, row.feedbacks, row.rating, row.name) == (900000099, 12, 4.5, None)


def test_odd_types_become_null_never_an_exception():
    row = _row(
        {
            "id": 1,
            "sizes": "oops",
            "supplier": 5,
            "supplierId": [],
            "subjectId": {"a": 1},
            "root": None,
            "reviewRating": "n/a",
            "time1": "x",
            "log": ["not", "a", "dict"],
        }
    )
    assert row is not None
    assert row.price_rub is None and row.delivery_hours is None and row.delivery_days is None
    assert (row.seller_name, row.seller_id, row.subject_id, row.root_id, row.rating) == (
        None,
        None,
        None,
        None,
        None,
    )
    assert row.is_promoted == 0


@pytest.mark.parametrize(
    ("kopecks", "expected"),
    [(99999, 999.99), (150000, 1500.0), (1, 0.01), (0, 0.0), ("12900", 129.0)],
)
def test_rub_conversion(kopecks, expected):
    assert rub(kopecks) == expected


@pytest.mark.parametrize("bad", [None, True, "x", 1.5, [], {}])
def test_rub_of_unusable_value_is_none(bad):
    assert rub(bad) is None


def test_price_99999_kopecks_is_999_99_rubles():
    raw = {"id": 1, "sizes": [{"price": {"basic": 99999, "product": 99999}}]}
    row = _row(raw)
    assert row is not None
    assert (row.price_rub, row.price_basic_rub) == (999.99, 999.99)
    assert (row.price_total_rub, row.logistics_rub) == (None, None)


def test_pick_size_ties_pick_first_and_ignore_unpriced():
    cheap_a = {"name": "A", "price": {"product": 100, "total": 111}}
    cheap_b = {"name": "B", "price": {"product": 100, "total": 222}}
    sizes = [None, {"price": {"product": 0}}, {"price": {"product": -5}}, cheap_a, cheap_b]
    assert pick_size({"sizes": sizes})["name"] == "A"
    assert pick_size({"sizes": [None, {"price": None}, {"price": {"product": "x"}}]}) is None
    assert pick_size({"sizes": None}) is None
    assert pick_size({}) is None


def test_negative_product_price_is_unpriced():
    row = _row({"id": 1, "sizes": [{"price": {"basic": 10, "product": -100, "total": -100}}]})
    assert row is not None
    assert row.price_rub is None and row.price_basic_rub is None


def test_legacy_prices_not_used_when_a_size_is_priced():
    raw = {"id": 1, "priceU": 999, "salePriceU": 888, "sizes": [{"price": {"product": 5000}}]}
    row = _row(raw)
    assert row is not None and row.price_rub == 50.0


def test_zero_sale_price_is_unpriced():
    row = _row({"id": 1, "priceU": 999, "salePriceU": 0})
    assert row is not None
    assert row.price_rub is None and row.price_basic_rub is None


def test_rating_zero_is_null_but_feedbacks_zero_is_kept():
    row = _row({"id": 1, "reviewRating": 0, "feedbacks": 0, "nmReviewRating": 0.0})
    assert row is not None
    assert (row.rating, row.feedbacks, row.nm_rating) == (None, 0, 0.0)


def test_legacy_integer_rating_key_is_ignored():
    row = _row({"id": 1, "rating": 5})
    assert row is not None and row.rating is None


def test_delivery_prefers_product_level_over_size_level():
    raw = {"id": 1, "time1": 4, "time2": 10, "sizes": [{"time1": 1, "time2": 1}]}
    row = _row(raw)
    assert row is not None
    assert (row.time1_h, row.time2_h, row.delivery_hours, row.delivery_days) == (4, 10, 14, 1)


def test_delivery_falls_back_to_fastest_size_first_on_ties():
    raw = {
        "id": 1,
        "time1": 4,
        "sizes": [
            {"time1": 9, "time2": 9},
            {"time1": 2, "time2": 6},
            {"time1": 5, "time2": 3},
            {"time1": None, "time2": 0},
        ],
    }
    row = _row(raw)
    assert row is not None
    assert (row.time1_h, row.time2_h, row.delivery_hours) == (2, 6, 8)


def test_partial_delivery_times_keep_raw_value_and_null_hours():
    row = _row({"id": 1, "time1": 4, "sizes": [{"time1": 1}]})
    assert row is not None
    assert (row.time1_h, row.time2_h, row.delivery_hours, row.delivery_days) == (
        4,
        None,
        None,
        None,
    )


def test_negative_delivery_time_gives_null_hours():
    row = _row({"id": 1, "time1": -3, "time2": 26})
    assert row is not None
    assert (row.time1_h, row.time2_h) == (-3, 26)
    assert row.delivery_hours is None and row.delivery_days is None


def test_absurd_delivery_hours_do_not_crash():
    row = _row({"id": 1, "time1": 10**9, "time2": 5})
    assert row is not None
    assert row.delivery_hours == 10**9 + 5
    assert row.delivery_days is None


@pytest.mark.parametrize(
    ("log", "promoted", "promo", "organic", "tp", "cpm"),
    [
        (None, 0, None, None, None, None),
        ({}, 0, None, None, None, None),
        ("x", 0, None, None, None, None),
        ({"cpm": 0, "promoPosition": 6, "position": 3, "tp": "b"}, 1, 6, 3, "b", 0),
        ({"cpm": "bad", "tp": 7}, 1, None, None, None, None),
    ],
)
def test_ad_fields(log, promoted, promo, organic, tp, cpm):
    raw = {"id": 1, "log": log}
    row = _row(raw)
    assert row is not None
    assert is_promoted(raw) == row.is_promoted == promoted
    assert promo_position(raw) == promo
    assert (row.ad_promo_position, row.ad_organic_position, row.ad_tp, row.ad_cpm) == (
        promo,
        organic,
        tp,
        cpm,
    )


def test_ad_helpers_on_non_dict_input():
    assert is_promoted("x") == 0
    assert promo_position(None) is None


def test_opaque_fields_are_never_stored():
    values = {
        str(v) for row in _rows("search_data_products.json") for v in dataclasses.astuple(row)
    }
    assert not any("OPAQUE" in v for v in values)


def test_normalize_and_delivery_modules_are_pure():
    for name in ("normalize.py", "delivery.py"):
        assert not module_imports(SRC_DIR / name) & PURE_FORBIDDEN_IMPORTS


def test_collect_cli_stores_complete_rows_from_a_wb_shaped_page():
    """Vertical slice: payload -> CLI -> SQLite columns equal the expected table."""
    from wbmon import cli
    from wbmon.queries import load_bundled

    query = load_bundled()[0]
    fake = FakeWB({(query, 1): [json_response(_load("search_data_products.json"))]})
    code = cli.main(
        ["collect", "--limit-queries", "1", "--top-n", "3"],
        _transport=httpx.MockTransport(fake),
        _sleep=RecordingSleep(),
    )
    assert code == 0
    columns = (
        "position, nm_id, price_rub, price_basic_rub, price_total_rub, logistics_rub, rating,"
        " feedbacks, seller_name, is_promoted, ad_promo_position, ad_organic_position, ad_tp,"
        " ad_cpm, delivery_hours, delivery_days, collected_at"
    )
    with contextlib.closing(sqlite3.connect(os.environ["WBMON_DB_PATH"])) as con:
        rows = con.execute(f"SELECT {columns} FROM observations ORDER BY position").fetchall()
    assert len(rows) == 3
    expected = [
        (1, 900000001, 1500.0, 3000.0, 1500.0, 0.0, 4.7, 120, "ТЕСТ-Продавец", 0, None, None),
        (2, 900000002, 1500.0, 3000.0, 1500.0, 0.0, 4.7, 120, "ТЕСТ-Продавец", 1, 2, 7),
        (3, 900000003, 1399.0, 4750.0, 1478.0, 79.0, 4.9, 15, "ТЕСТ-Продавец-2", 0, None, None),
    ]
    assert [row[:12] for row in rows] == expected
    assert [row[12:14] for row in rows] == [(None, None), ("c", 1025), (None, None)]
    assert [row[14] for row in rows] == [29, 29, 109]
    for row in rows:
        assert row[15] == delivery_days(parse_iso_z(row[16]), row[14])


# ------------------------------------------------------------------ CR-01: out-of-range numbers


def test_to_int_bounds_values_to_what_sqlite_can_store():
    from wbmon.normalize import INT64_MAX, to_int

    assert to_int(INT64_MAX) == INT64_MAX
    assert to_int(-INT64_MAX) == -INT64_MAX
    assert to_int(INT64_MAX + 1) is None
    assert to_int(10**30) is None
    assert to_int(str(10**30)) is None
    assert to_int(1e30) is None
    assert to_int(float("inf")) is None


def test_to_float_survives_a_huge_json_integer():
    from wbmon.normalize import to_float

    assert to_float(10**400) is None
    assert to_float("1e999") is None


def test_to_str_rejects_unpaired_surrogates():
    from wbmon.normalize import to_str

    assert to_str("\ud800") is None
    assert to_str("ТЕСТ ё") == "ТЕСТ ё"


def test_rub_of_an_out_of_range_amount_is_null_not_an_exception():
    assert rub(10**30) is None
    assert rub(2**63 - 1) is not None


def test_out_of_range_fields_become_null_but_the_product_is_kept():
    raw = {
        "id": 900000001,
        "feedbacks": 10**30,
        "supplierId": 2**70,
        "time1": 2**63 - 1,
        "time2": 2**63 - 1,
        "sizes": [{"price": {"product": 10**30, "basic": 10**30}}],
    }
    row = normalize_product(raw, position=1, collected_at=COLLECTED_AT, tz=MSK)
    assert row is not None
    assert row.nm_id == 900000001
    assert row.feedbacks is None
    assert row.seller_id is None
    assert row.price_rub is None
    assert row.delivery_hours is None  # the sum would not fit an SQLite INTEGER
    assert row.delivery_days is None
    assert row.time1_h == 2**63 - 1


def test_out_of_range_product_id_is_skipped_not_fatal():
    assert normalize_product({"id": 10**30}, position=1, collected_at=COLLECTED_AT, tz=MSK) is None


# ------------------------------------------------------------ WR-01: unparseable digit strings


@pytest.mark.parametrize("text", ["²", "--5", "٣", "1_0", "+5", "", "-", "5.0", "1" * 5000])
def test_to_int_rejects_strings_that_only_look_numeric(text):
    from wbmon.normalize import to_int

    assert to_int(text) is None


@pytest.mark.parametrize(("text", "expected"), [("42", 42), (" 7 ", 7), ("-3", -3), ("007", 7)])
def test_to_int_still_accepts_plain_ascii_integers(text, expected):
    from wbmon.normalize import to_int

    assert to_int(text) == expected


def test_odd_digit_string_in_one_field_does_not_discard_the_product():
    raw = {"id": 900000001, "feedbacks": "²", "supplierId": "--5", "totalQuantity": "12"}
    row = normalize_product(raw, position=1, collected_at=COLLECTED_AT, tz=MSK)
    assert row is not None
    assert (row.feedbacks, row.seller_id, row.total_quantity) == (None, None, 12)
