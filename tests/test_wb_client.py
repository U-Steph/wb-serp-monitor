"""WB client behaviour: honest identity, cookies, proxies, redirects, pacing and bounded retries."""

from __future__ import annotations

import re

import httpx
import pytest

from factories import (
    FakeWB,
    RecordingSleep,
    build_wb_client,
    html_response,
    json_response,
    make_page,
    make_settings,
)

Q = "тестовый запрос"
UA_PATTERN = r"^Mozilla/5\.0 \(compatible; wbmon/[0-9.]+; \+https?://\S+\)$"


def make_client(items, *, page=1, **settings_overrides):
    fake = FakeWB({(Q, page): list(items)})
    sleep = RecordingSleep()
    client = build_wb_client(fake, make_settings(**settings_overrides), sleep=sleep)
    return client, fake, sleep


def ok_page() -> httpx.Response:
    return json_response(make_page(900000001, 3))


# --------------------------------------------------------------------------- identity


def test_request_identity_is_honest_and_plain():
    client, fake, _ = make_client([ok_page()])
    settings = make_settings()

    client.fetch_page(Q, 1)

    (request,) = fake.wb_requests()
    headers = request.headers
    assert headers["user-agent"] == settings.user_agent
    assert re.match(UA_PATTERN, headers["user-agent"])
    assert headers["accept"] == "application/json"
    assert headers["accept-language"] == "ru-RU,ru;q=0.9"
    for forbidden in ("cookie", "referer", "origin"):
        assert forbidden not in headers
    assert dict(request.url.params) == settings.search_params(Q, 1)


def test_set_cookie_is_never_replayed():
    cookie = httpx.Response(200, json=make_page(900000001, 1), headers={"set-cookie": "sid=abc"})
    client, fake, _ = make_client([cookie, ok_page()])

    client.fetch_page(Q, 1)
    client.fetch_page(Q, 1)

    first, second = fake.wb_requests()
    assert "cookie" not in first.headers
    assert "cookie" not in second.headers
    assert len(list(client._http.cookies.jar)) == 0


def test_proxy_environment_is_ignored(monkeypatch):
    from wbmon.wb_client import make_http_client

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    fake = FakeWB({(Q, 1): [ok_page()]})
    http = make_http_client(make_settings(), transport=httpx.MockTransport(fake))
    assert http.trust_env is False

    client = build_wb_client(fake)
    assert client.fetch_page(Q, 1).status_code == 200


def test_redirect_is_returned_not_followed():
    redirect = httpx.Response(307, headers={"location": "https://search.wb.ru/other"})
    client, fake, sleep = make_client([redirect])

    fetch = client.fetch_page(Q, 1)

    assert fetch.status_code == 307
    assert len(fake.requests) == 1
    assert sleep.calls == []


def test_wb_client_never_follows_redirects():
    from wbmon.wb_client import make_http_client

    http = make_http_client(make_settings())
    assert http.follow_redirects is False


# --------------------------------------------------------------------------- pacing


def test_first_fetch_does_not_sleep_and_later_fetches_use_the_fixed_delay():
    client, _, sleep = make_client([ok_page(), ok_page(), ok_page()])

    for _ in range(3):
        client.fetch_page(Q, 1)

    assert sleep.calls == [1.5, 1.5]


def test_pacing_follows_request_delay_setting():
    client, _, sleep = make_client([ok_page(), ok_page()], request_delay=2.0)

    client.fetch_page(Q, 1)
    client.fetch_page(Q, 1)

    assert sleep.calls == [2.0]


# --------------------------------------------------------------------------- retries


@pytest.mark.parametrize(
    "response",
    [
        html_response(403, headers={"server": "wbaas"}),
        httpx.Response(498),
        httpx.Response(404, text="x"),
        httpx.Response(307, headers={"location": "/x"}),
    ],
    ids=["403", "498", "404", "307"],
)
def test_blocks_and_client_errors_are_never_retried(response):
    client, fake, sleep = make_client([response])

    fetch = client.fetch_page(Q, 1)

    assert fetch.status_code == response.status_code
    assert fetch.attempts == 1
    assert len(fake.requests) == 1
    assert sleep.calls == []


def test_persistent_502_is_retried_twice_with_fixed_backoff():
    items = [httpx.Response(502), httpx.Response(502), httpx.Response(502)]
    client, fake, sleep = make_client(items)

    fetch = client.fetch_page(Q, 1)

    assert fetch.status_code == 502
    assert fetch.attempts == 3
    assert len(fake.requests) == 3
    assert sleep.calls == [2.0, 5.0]


def test_backoff_constant():
    from wbmon.wb_client import RETRY_BACKOFF

    assert RETRY_BACKOFF == (2.0, 5.0)


def test_502_then_200_recovers():
    client, fake, sleep = make_client([httpx.Response(502), ok_page()])

    fetch = client.fetch_page(Q, 1)

    assert fetch.status_code == 200
    assert fetch.attempts == 2
    assert len(fake.requests) == 2
    assert sleep.calls == [2.0]


def test_repeated_timeouts_end_as_transport_timeout():
    items = [httpx.ReadTimeout("t"), httpx.ReadTimeout("t"), httpx.ReadTimeout("t")]
    client, fake, sleep = make_client(items)

    fetch = client.fetch_page(Q, 1)

    assert fetch.transport_error == "timeout"
    assert fetch.status_code is None
    assert fetch.content == b""
    assert fetch.attempts == 3
    assert len(fake.requests) == 3
    assert sleep.calls == [2.0, 5.0]


def test_connect_error_then_200_recovers():
    client, _, sleep = make_client([httpx.ConnectError("boom"), ok_page()])

    fetch = client.fetch_page(Q, 1)

    assert fetch.status_code == 200
    assert fetch.transport_error is None
    assert sleep.calls == [2.0]


def test_connection_error_is_classified_connection():
    items = [httpx.ConnectError("x")] * 3
    client, _, _ = make_client(items)
    assert client.fetch_page(Q, 1).transport_error == "connection"


def test_429_with_retry_after_is_retried_once():
    limited = httpx.Response(429, headers={"retry-after": "7"})
    client, fake, sleep = make_client([limited, ok_page()])

    fetch = client.fetch_page(Q, 1)

    assert fetch.status_code == 200
    assert fetch.attempts == 2
    assert len(fake.requests) == 2
    assert sleep.calls == [7.0]


def test_429_without_header_waits_default_and_gives_up_after_one_retry():
    client, fake, sleep = make_client([httpx.Response(429), httpx.Response(429)])

    fetch = client.fetch_page(Q, 1)

    assert fetch.status_code == 429
    assert fetch.attempts == 2
    assert len(fake.requests) == 2
    assert sleep.calls == [30.0]


def test_429_retry_after_is_capped_at_sixty_seconds():
    limited = httpx.Response(429, headers={"retry-after": "120"})
    client, _, sleep = make_client([limited, ok_page()])

    client.fetch_page(Q, 1)

    assert sleep.calls == [60.0]


def test_429_with_retry_after_zero_does_not_fall_back_to_default():
    limited = httpx.Response(429, headers={"retry-after": "0"})
    client, _, sleep = make_client([limited, ok_page()])

    client.fetch_page(Q, 1)

    assert sleep.calls == [0.0]


def test_retry_false_sends_a_single_request():
    client, fake, sleep = make_client([httpx.Response(502)])

    fetch = client.fetch_page(Q, 1, retry=False)

    assert fetch.status_code == 502
    assert fetch.attempts == 1
    assert len(fake.requests) == 1
    assert sleep.calls == []


def test_retry_false_does_not_retry_429_either():
    client, fake, sleep = make_client([httpx.Response(429, headers={"retry-after": "5"})])

    client.fetch_page(Q, 1, retry=False)

    assert len(fake.requests) == 1
    assert sleep.calls == []


def test_pacing_sleep_precedes_each_page_not_each_retry():
    client, _, sleep = make_client([ok_page(), httpx.Response(502), ok_page()])

    client.fetch_page(Q, 1)
    client.fetch_page(Q, 1)

    assert sleep.calls == [1.5, 2.0]
    assert client.requests_made == 3


# --------------------------------------------------------------------------- PageFetch


def test_page_fetch_fields():
    body = make_page(900000001, 2)
    client, _, _ = make_client([json_response(body, headers={"server": "nginx"})])

    fetch = client.fetch_page(Q, 1)

    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", fetch.received_at)
    assert isinstance(fetch.elapsed_ms, int)
    assert fetch.elapsed_ms >= 0
    assert fetch.query == Q
    assert fetch.page == 1
    assert fetch.server == "nginx"
    assert "application/json" in fetch.content_type
    assert fetch.content == httpx.Response(200, json=body).content
    assert fetch.attempts == 1


def test_retry_after_header_is_preserved_on_the_fetch():
    client, _, _ = make_client([httpx.Response(429, headers={"retry-after": "9"})] * 2)
    assert client.fetch_page(Q, 1).retry_after == "9"


def test_wb_client_source_has_no_jitter_and_no_redirect_following():
    from factories import SRC_DIR

    source = (SRC_DIR / "wb_client.py").read_text(encoding="utf-8")
    assert "random" not in source
    assert "follow_redirects=True" not in source
