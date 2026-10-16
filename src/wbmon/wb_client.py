"""HTTP access to the public WB search JSON. The only module (with queries) that imports httpx.

Identity is deliberately plain: honest bot-style User-Agent, no cookies kept, no environment
proxies, redirects never followed, fixed polite delay between sequential requests.
"""

from __future__ import annotations

import http.cookiejar
import time
from collections.abc import Callable
from datetime import datetime

import httpx

from wbmon import clock
from wbmon.classify import (
    DEFAULT_RETRY_AFTER,
    MAX_BODY_BYTES,
    RETRY_AFTER_CAP,
    parse_retry_after,
)
from wbmon.config import Settings
from wbmon.models import PageFetch

RETRY_BACKOFF = (2.0, 5.0)  # fixed sleeps before the 1st and 2nd retry of a transient failure
REQUEST_TOTAL_SECONDS = 60.0  # wall-clock cap per request; httpx's own timeouts are per socket op


def read_capped(
    response: httpx.Response,
    *,
    max_bytes: int,
    max_seconds: float,
    monotonic: Callable[[], float],
    started: float,
) -> bytes:
    """Read a streamed body, stopping at ``max_bytes`` + 1 bytes or after ``max_seconds`` in total.

    The (decoded) body is never buffered beyond the cap, so a huge or gzip-bombed response cannot
    spike memory. Returning more than ``max_bytes`` bytes tells the caller the body was too large.
    A body that is still arriving after ``max_seconds`` (a slow drip that keeps every individual
    socket read under the httpx timeout) raises ``httpx.ReadTimeout``.
    """
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size > max_bytes:
            return b"".join(chunks)[: max_bytes + 1]
        if monotonic() - started > max_seconds:
            raise httpx.ReadTimeout(f"response not complete after {max_seconds:g} s")
    return b"".join(chunks)


class NoCookiesPolicy(http.cookiejar.CookiePolicy):
    """Cookie policy that rejects every cookie, so ``Set-Cookie`` is never stored or replayed."""

    netscape = True
    rfc2965 = False
    hide_cookie2 = False

    def set_ok(self, cookie, request) -> bool:
        return False

    def return_ok(self, cookie, request) -> bool:
        return False

    def domain_return_ok(self, domain, request) -> bool:
        return False

    def path_return_ok(self, path, request) -> bool:
        return False


def make_http_client(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """Build the plain HTTP client used for WB requests (tests inject a MockTransport)."""
    client = httpx.Client(
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=False,  # a 3xx from WB is data to classify, never followed
        trust_env=False,  # ignore ambient proxy and certificate environment variables
        transport=transport,
    )
    client.cookies.jar.set_policy(NoCookiesPolicy())
    return client


class WbClient:
    """Sequential, paced page fetcher. One instance per run; sleep/clock are injectable."""

    def __init__(
        self,
        http: httpx.Client,
        settings: Settings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = clock.utc_now,
    ) -> None:
        self._http = http
        self._settings = settings
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now
        self._requests_made = 0

    @property
    def requests_made(self) -> int:
        """Number of requests sent so far."""
        return self._requests_made

    def fetch_page(self, query: str, page: int, *, retry: bool = True) -> PageFetch:
        """Fetch one SERP page, politely and with bounded, status-specific retries.

        Sleeps ``request_delay`` before the request (not before this client's first request, and
        no jitter). With ``retry`` enabled: a transport error or 5xx is retried at most twice
        after ``RETRY_BACKOFF`` seconds; a 429 is retried once after ``min(Retry-After or 30, 60)``
        seconds. 403 / 498 / 3xx / other 4xx are never retried: they are data, not glitches.
        ``attempts`` on the result counts the requests sent for this page.
        """
        if self._requests_made > 0:
            self._sleep(self._settings.request_delay)
        attempts = 0
        transient_retries = 0
        rate_limit_retried = False
        while True:
            attempts += 1
            fetch = self._send(query, page, attempts)
            if not retry:
                return fetch
            transient = fetch.transport_error is not None or (
                fetch.status_code is not None and 500 <= fetch.status_code < 600
            )
            if transient and transient_retries < len(RETRY_BACKOFF):
                self._sleep(RETRY_BACKOFF[transient_retries])
                transient_retries += 1
                continue
            if fetch.status_code == 429 and not rate_limit_retried:
                wait = parse_retry_after(fetch.retry_after, self._now())
                self._sleep(min(DEFAULT_RETRY_AFTER if wait is None else wait, RETRY_AFTER_CAP))
                rate_limit_retried = True
                continue
            return fetch

    def _send(self, query: str, page: int, attempts: int) -> PageFetch:
        """Send one request and wrap whatever came back (or the transport failure)."""
        self._requests_made += 1
        started = self._monotonic()
        status_code: int | None = None
        content_type = server = retry_after = transport_error = None
        content = b""
        try:
            with self._http.stream(
                "GET",
                self._settings.search_url,
                params=self._settings.search_params(query, page),
            ) as response:
                status_code = response.status_code
                content_type = response.headers.get("content-type")
                server = response.headers.get("server")
                retry_after = response.headers.get("retry-after")
                content = read_capped(
                    response,
                    max_bytes=MAX_BODY_BYTES,
                    max_seconds=REQUEST_TOTAL_SECONDS,
                    monotonic=self._monotonic,
                    started=started,
                )
        except httpx.TimeoutException:
            transport_error = "timeout"
            status_code = content_type = server = retry_after = None
            content = b""
        except httpx.TransportError:
            transport_error = "connection"
            status_code = content_type = server = retry_after = None
            content = b""
        return PageFetch(
            query=query,
            page=page,
            status_code=status_code,
            content_type=content_type,
            server=server,
            retry_after=retry_after,
            content=content,
            received_at=clock.iso_z(self._now()),
            elapsed_ms=max(0, int((self._monotonic() - started) * 1000)),
            attempts=attempts,
            transport_error=transport_error,
        )
