"""WR-01: a stop request cancels waits, retries and pages; the grace periods are ordered honestly.

Everything is offline: ``FakeWB`` behind ``httpx.MockTransport`` and recording/fake sleeps.
"""

from __future__ import annotations

import httpx
import pytest

from factories import (
    FakeWB,
    RecordingSleep,
    build_wb_client,
    json_response,
    make_page,
    make_settings,
)
from wbmon.collector import collect_query
from wbmon.signals import StopFlag, interruptible_sleep
from wbmon.wb_client import MAX_ATTEMPT_SECONDS, StopRequested

Q = "тестовый запрос"


def ok_page(first_id: int = 900000001, n: int = 100) -> httpx.Response:
    return json_response(make_page(first_id, n))


class StopAfterSleeps:
    """A sleep that raises the stop flag once it has been called ``n`` times."""

    def __init__(self, flag: StopFlag, n: int = 1) -> None:
        self.flag, self.n, self.calls = flag, n, []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if len(self.calls) >= self.n:
            self.flag.request()


# ------------------------------------------------------------------ interruptible_sleep


def test_interruptible_sleep_naps_in_steps_and_totals_the_request():
    naps: list[float] = []
    interruptible_sleep(lambda: False, base=naps.append, step=0.5)(1.75)
    assert naps == [0.5, 0.5, 0.5, 0.25]


def test_interruptible_sleep_ends_early_when_the_stop_is_set():
    flag = StopFlag()
    naps: list[float] = []

    def base(seconds: float) -> None:
        naps.append(seconds)
        if len(naps) == 2:
            flag.request()

    interruptible_sleep(lambda: flag.requested, base=base, step=0.5)(60.0)

    assert naps == [0.5, 0.5]  # not 120 naps


def test_interruptible_sleep_does_not_sleep_when_already_stopped():
    naps: list[float] = []
    interruptible_sleep(lambda: True, base=naps.append)(30.0)
    assert naps == []


# ------------------------------------------------------------------ WbClient waits


def test_stop_during_the_pacing_wait_sends_no_request():
    flag = StopFlag()
    fake = FakeWB({(Q, 1): [ok_page(), ok_page()]})
    sleep = StopAfterSleeps(flag)
    client = build_wb_client(fake, make_settings(), sleep=sleep, should_stop=lambda: flag.requested)
    client.fetch_page(Q, 1)  # first request: no pacing wait

    with pytest.raises(StopRequested):
        client.fetch_page(Q, 1)  # pacing wait, the stop arrives in it

    assert len(fake.requests) == 1


def test_stop_during_the_backoff_returns_the_failed_fetch_without_a_retry():
    flag = StopFlag()
    fake = FakeWB({(Q, 1): [httpx.Response(502), ok_page()]})
    sleep = StopAfterSleeps(flag)
    client = build_wb_client(fake, make_settings(), sleep=sleep, should_stop=lambda: flag.requested)

    fetch = client.fetch_page(Q, 1)

    assert fetch.status_code == 502
    assert fetch.attempts == 1
    assert len(fake.requests) == 1
    assert sleep.calls == [2.0]


def test_stop_during_the_429_wait_returns_the_429_without_a_retry():
    flag = StopFlag()
    fake = FakeWB({(Q, 1): [httpx.Response(429, headers={"retry-after": "55"}), ok_page()]})
    sleep = StopAfterSleeps(flag)
    client = build_wb_client(fake, make_settings(), sleep=sleep, should_stop=lambda: flag.requested)

    fetch = client.fetch_page(Q, 1)

    assert fetch.status_code == 429
    assert len(fake.requests) == 1


def test_stop_already_requested_skips_the_wait_entirely():
    fake = FakeWB({(Q, 1): [httpx.Response(502), ok_page()]})
    sleep = RecordingSleep()
    stops = iter([False, True, True, True])  # fetch_page entry passes, the backoff wait is skipped
    client = build_wb_client(fake, make_settings(), sleep=sleep, should_stop=lambda: next(stops))

    fetch = client.fetch_page(Q, 1)

    assert fetch.status_code == 502
    assert sleep.calls == []


def test_fetch_page_with_a_pending_stop_sends_nothing():
    fake = FakeWB({(Q, 1): [ok_page()]})
    client = build_wb_client(fake, make_settings(), should_stop=lambda: True)

    with pytest.raises(StopRequested):
        client.fetch_page(Q, 1)

    assert fake.requests == []


# ------------------------------------------------------------------ collect_query


def collect(fake: FakeWB, flag: StopFlag, sleep):
    settings = make_settings()
    client = build_wb_client(fake, settings, sleep=sleep, should_stop=lambda: flag.requested)
    return collect_query(
        client, Q, top_n=200, tz=settings.tzinfo(), should_stop=lambda: flag.requested
    )


def test_stop_between_pages_keeps_page_one_as_partial_terminated():
    flag = StopFlag()
    fake = FakeWB({(Q, 1): [ok_page(900000001)], (Q, 2): [ok_page(900000101)]})

    # the stop arrives in the pacing wait in front of page 2
    result = collect(fake, flag, StopAfterSleeps(flag))

    assert len(fake.requests) == 1
    assert result.status == "partial"
    assert result.error_class == "terminated"
    assert result.is_complete is False
    assert result.pages_fetched == 1
    assert result.items_collected == 100
    assert result.http_status is None


def test_stop_before_page_one_is_error_terminated_without_a_request():
    flag = StopFlag()
    flag.request()
    fake = FakeWB({(Q, 1): [ok_page()]})

    result = collect(fake, flag, RecordingSleep())

    assert fake.requests == []
    assert (result.status, result.error_class, result.pages_fetched) == ("error", "terminated", 0)
    assert result.items_collected == 0


def test_stop_in_the_page_one_backoff_ends_the_query_with_the_real_failure():
    flag = StopFlag()
    fake = FakeWB({(Q, 1): [httpx.Response(502), ok_page()]})

    result = collect(fake, flag, StopAfterSleeps(flag))

    assert len(fake.requests) == 1
    assert (result.status, result.error_class) == ("error", "http_5xx")


# ------------------------------------------------------------------ grace ordering


def test_stop_grace_covers_one_in_flight_attempt_plus_a_finish_margin():
    from wbmon import scheduler

    assert scheduler.STOP_FINISH_MARGIN_S > 0
    assert scheduler.STOP_GRACE_S >= MAX_ATTEMPT_SECONDS + scheduler.STOP_FINISH_MARGIN_S
    # compose stop_grace_period > STOP_GRACE_S is asserted in test_packaging_cross.py


def test_one_attempt_bound_covers_connect_write_headers_and_one_body_read():
    from wbmon.wb_client import HTTP_CONNECT_S, HTTP_TIMEOUT_S, REQUEST_TOTAL_SECONDS

    assert MAX_ATTEMPT_SECONDS >= HTTP_CONNECT_S + 3 * HTTP_TIMEOUT_S
    assert MAX_ATTEMPT_SECONDS >= REQUEST_TOTAL_SECONDS + HTTP_TIMEOUT_S
