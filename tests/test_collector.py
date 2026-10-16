"""Multi-page ``collect_query``: top-200 across pages, partial results, honest page-1 failures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from factories import (
    FakeWB,
    RecordingSleep,
    SteppingClock,
    build_wb_client,
    html_response,
    json_response,
    make_page,
    make_settings,
)

QUERY = "тест запрос коллектора"
P1 = 900000001  # page 1 ids 900000001..900000100
P2 = 900000101  # page 2 ids 900000101..900000200


def collect(fake: FakeWB, *, top_n: int = 200, now=None, monotonic=None):
    from wbmon.collector import collect_query

    settings = make_settings()
    sleep = RecordingSleep()
    client = build_wb_client(fake, settings, sleep=sleep, now=now)
    kwargs = {}
    if now is not None:
        kwargs["now"] = now
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    result = collect_query(client, QUERY, top_n=top_n, tz=settings.tzinfo(), **kwargs)
    return result, sleep


def pages(*responses) -> FakeWB:
    """A FakeWB serving the given responses as page 1, 2, 3 ... of QUERY."""
    return FakeWB({(QUERY, number): [item] for number, item in enumerate(responses, start=1)})


def ok_page(first_id: int = P1, n: int = 100, **kwargs):
    return json_response(make_page(first_id, n, **kwargs))


def test_two_full_pages_give_top_200_in_serp_order():
    fake = pages(ok_page(P1), ok_page(P2))

    result, sleep = collect(fake)

    assert result.status == "ok"
    assert result.items_collected == 200
    assert [row.position for row in result.observations] == list(range(1, 201))
    assert [row.nm_id for row in result.observations] == list(range(P1, P1 + 200))
    assert result.pages_fetched == 2
    assert result.is_complete is True
    assert result.http_status == 200
    assert result.error_class is None
    assert len(fake.wb_requests()) == 2
    assert [r.url.params["page"] for r in fake.wb_requests()] == ["1", "2"]
    assert sleep.calls == [1.5]


def test_page_two_identical_to_page_one_is_a_wrap_and_complete():
    fake = pages(ok_page(P1), ok_page(P1))

    result, _ = collect(fake)

    assert result.status == "ok"
    assert result.items_collected == 100
    assert result.is_complete is True
    assert len(fake.wb_requests()) == 2


def test_empty_page_two_ends_the_results():
    fake = pages(ok_page(P1), ok_page(P2, 0))

    result, _ = collect(fake)

    assert (result.status, result.items_collected, result.is_complete) == ("ok", 100, True)
    assert len(fake.wb_requests()) == 2


def test_short_result_set_then_empty_page_is_complete():
    fake = pages(ok_page(P1, 60), ok_page(P2, 0))

    result, _ = collect(fake)

    assert (result.status, result.items_collected, result.is_complete) == ("ok", 60, True)
    assert [row.position for row in result.observations] == list(range(1, 61))
    assert len(fake.wb_requests()) == 2


def test_short_second_page_is_not_an_end_signal():
    fake = pages(ok_page(P1), ok_page(P2, 97), ok_page(P2 + 97, 3))

    result, _ = collect(fake)

    assert result.items_collected == 200
    assert result.pages_fetched == 3
    assert result.is_complete is True
    assert len(fake.wb_requests()) == 3


def test_page_two_403_keeps_page_one_as_partial():
    fake = pages(ok_page(P1), html_response(403, headers={"server": "wbaas"}))

    result, _ = collect(fake)

    assert result.status == "partial"
    assert result.items_collected == 100
    assert result.error_class == "http_403"
    assert result.http_status == 403
    assert result.pages_fetched == 1
    assert result.is_complete is False
    assert "http=403" in (result.error_message or "")
    assert [row.position for row in result.observations] == list(range(1, 101))
    assert len(fake.wb_requests()) == 2


def test_page_two_429_twice_is_partial_after_one_retry_after_sleep():
    limited = httpx.Response(429, headers={"Retry-After": "7"}, text="slow down")
    fake = FakeWB({(QUERY, 1): [ok_page(P1)], (QUERY, 2): [limited, limited]})

    result, sleep = collect(fake)

    assert result.status == "partial"
    assert result.error_class == "http_429"
    assert result.http_status == 429
    assert result.items_collected == 100
    assert result.pages_fetched == 1
    assert sleep.calls == [1.5, 7.0]
    assert len(fake.wb_requests()) == 3


def test_page_one_403_is_blocked_without_rows_after_one_request():
    fake = pages(html_response(403, headers={"server": "wbaas"}))

    result, sleep = collect(fake)

    assert result.status == "blocked"
    assert result.error_class == "http_403"
    assert result.http_status == 403
    assert result.pages_fetched == 0
    assert result.items_collected == 0
    assert result.is_complete is False
    assert result.error_message
    assert len(fake.wb_requests()) == 1
    assert sleep.calls == []


def test_page_one_429_twice_is_rate_limited_after_two_requests():
    limited = httpx.Response(429, headers={"Retry-After": "3"}, text="slow down")
    fake = pages(limited)
    fake.scripts[(QUERY, 1)] = [limited, limited]

    result, sleep = collect(fake)

    assert (result.status, result.error_class, result.http_status) == (
        "rate_limited",
        "http_429",
        429,
    )
    assert result.items_collected == 0
    assert len(fake.wb_requests()) == 2
    assert sleep.calls == [3.0]


def test_page_one_502_three_times_is_error_with_bounded_retries():
    bad = httpx.Response(502, text="bad gateway")
    fake = FakeWB({(QUERY, 1): [bad, bad, bad]})

    result, sleep = collect(fake)

    assert (result.status, result.error_class, result.http_status) == ("error", "http_5xx", 502)
    assert result.pages_fetched == 0
    assert len(fake.wb_requests()) == 3
    assert sleep.calls == [2.0, 5.0]


def test_page_one_empty_is_empty_suspect_not_no_results():
    fake = pages(ok_page(P1, 0))

    result, _ = collect(fake)

    assert (result.status, result.error_class) == ("error", "empty_suspect")
    assert result.items_collected == 0
    assert len(fake.wb_requests()) == 1


def test_page_one_products_null_is_error():
    payload = make_page(P1, 5)
    payload["data"]["products"] = None
    fake = pages(json_response(payload))

    result, _ = collect(fake)

    assert (result.status, result.error_class) == ("error", "products_null")
    assert result.items_collected == 0


def test_products_without_integer_ids_are_schema_drift():
    payload = make_page(P1, 3)
    payload["data"]["products"] = [{"name": "ТЕСТ без id"}, {"id": "abc"}, {"id": None}]
    # a short page is never an end signal, so the pager asks for page 2, which is empty
    fake = pages(json_response(payload), ok_page(P2, 0))

    result, _ = collect(fake)

    assert result.status == "error"
    assert result.error_class == "schema_drift"
    assert result.error_message == "no product with an integer id"
    assert result.items_collected == 0
    assert result.http_status == 200
    assert result.pages_fetched == 2


def test_product_skipped_event_is_logged_for_items_without_id(capsys):
    from wbmon.log import configure_logging

    configure_logging("INFO")
    payload = make_page(P1, 3)
    payload["data"]["products"][1] = {"name": "ТЕСТ без id"}
    fake = pages(json_response(payload))

    result, _ = collect(fake, top_n=3)

    assert [row.position for row in result.observations] == [1, 3]
    out = capsys.readouterr().out
    assert "event=product_skipped" in out
    assert "count=1" in out


def test_normquery_comes_from_page_one_metadata():
    page_one = make_page(P1, 100, normquery="нормализованный запрос")
    page_two = make_page(P2, 100, normquery="другой запрос")
    fake = pages(json_response(page_one), json_response(page_two))

    result, _ = collect(fake)

    assert result.normquery == "нормализованный запрос"


def test_page_two_ad_slot_mismatch_uses_global_or_page_relative_position():
    matching = pages(ok_page(P1), ok_page(P2, ads={5: 5}))
    result, _ = collect(matching)
    assert result.ad_slot_mismatch == 0
    ad_rows = [row for row in result.observations if row.is_promoted]
    assert [row.position for row in ad_rows] == [105]
    assert ad_rows[0].ad_promo_position == 5

    global_numbering = pages(ok_page(P1), ok_page(P2, ads={5: 105}))
    result, _ = collect(global_numbering)
    assert result.ad_slot_mismatch == 0

    mismatching = pages(ok_page(P1), ok_page(P2, ads={5: 9}))
    result, _ = collect(mismatching)
    assert result.ad_slot_mismatch == 1
    assert result.items_collected == 200


def test_duplicate_id_on_page_two_is_dropped_and_leaves_a_position_gap():
    ids = list(range(P2, P2 + 100))
    ids[4] = P1 + 7  # position 105 repeats an id already seen on page 1
    fake = pages(ok_page(P1), json_response(make_page(ids=ids)))

    result, _ = collect(fake)

    assert result.status == "ok"
    assert result.duplicates_dropped == 1
    assert result.items_collected == 199
    positions = [row.position for row in result.observations]
    assert 105 not in positions
    assert positions[-1] == 200
    assert result.is_complete is True


def test_each_row_carries_its_own_pages_received_at_and_matching_delivery_days():
    from wbmon import clock
    from wbmon.delivery import delivery_days

    base = datetime(2026, 10, 16, 20, 59, 50, tzinfo=UTC)  # 23:59:50 MSK, page 1 lands after 00:00
    ticks = iter(range(100))

    def now() -> datetime:
        return base + timedelta(seconds=10 * next(ticks))

    fake = pages(ok_page(P1), ok_page(P2))

    result, _ = collect(fake, now=now)

    stamps = {row.position: row.collected_at for row in result.observations}
    assert {stamps[1], stamps[100]} == {"2026-10-16T21:00:00Z"}
    assert {stamps[101], stamps[200]} == {"2026-10-16T21:00:10Z"}
    assert result.started_at == "2026-10-16T20:59:50Z"
    tz = make_settings().tzinfo()
    for row in result.observations:
        expected = delivery_days(clock.parse_iso_z(row.collected_at), row.delivery_hours, tz)
        assert row.delivery_days == expected
        assert row.delivery_days is not None


def test_duration_ms_comes_from_the_injected_monotonic_clock():
    fake = pages(ok_page(P1, 5))

    result, _ = collect(fake, top_n=5, monotonic=SteppingClock(step=1.25))

    assert result.duration_ms == 1250


def test_top_n_stops_inside_the_first_page_with_one_request():
    fake = pages(ok_page(P1, 10))

    result, _ = collect(fake, top_n=5)

    assert result.status == "ok"
    assert result.items_collected == 5
    assert [row.position for row in result.observations] == [1, 2, 3, 4, 5]
    assert result.pages_fetched == 1
    assert result.is_complete is True
    assert len(fake.wb_requests()) == 1


@pytest.mark.parametrize("shape", ["data.products", "products"])
def test_both_envelope_shapes_paginate(shape):
    fake = pages(ok_page(P1, shape=shape), ok_page(P2, shape=shape))

    result, _ = collect(fake)

    assert result.items_collected == 200
    assert result.normquery == "тест запрос"
