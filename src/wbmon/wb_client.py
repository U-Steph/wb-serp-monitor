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
from wbmon.config import Settings
from wbmon.models import PageFetch


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
        """Fetch one SERP page with a single attempt.

        Sleeps ``request_delay`` before every request except the first of this client. The
        ``retry`` flag is reserved for the bounded-retry policy added later.
        """
        if self._requests_made > 0:
            self._sleep(self._settings.request_delay)
        self._requests_made += 1
        started = self._monotonic()
        status_code: int | None = None
        content_type = server = retry_after = transport_error = None
        content = b""
        try:
            response = self._http.get(
                self._settings.search_url, params=self._settings.search_params(query, page)
            )
        except httpx.TimeoutException:
            transport_error = "timeout"
        except httpx.TransportError:
            transport_error = "connection"
        else:
            status_code = response.status_code
            content_type = response.headers.get("content-type")
            server = response.headers.get("server")
            retry_after = response.headers.get("retry-after")
            content = response.content
        received_at = clock.iso_z(self._now())
        return PageFetch(
            query=query,
            page=page,
            status_code=status_code,
            content_type=content_type,
            server=server,
            retry_after=retry_after,
            content=content,
            received_at=received_at,
            elapsed_ms=int((self._monotonic() - started) * 1000),
            attempts=1,
            transport_error=transport_error,
        )
