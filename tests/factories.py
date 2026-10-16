"""Shared offline test harness: a scripted MockTransport handler and WB-shaped payload factories.

All payload values are synthetic and test-only (ids 9000000xx, names prefixed ТЕСТ, opaque
strings TEST-OPAQUE-*). This module must never be imported from ``src/``.
wbmon is imported lazily inside functions so test collection never fails on a missing module.
"""

from __future__ import annotations

import ast
import csv
import io
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src" / "wbmon"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
PURE_FORBIDDEN_IMPORTS = frozenset({"httpx", "sqlite3", "socket", "os", "time", "subprocess"})

_SHEET_NOT_SCRIPTED = "sheet not scripted (test)"


def module_imports(path: Path) -> set[str]:
    """Return the top-level module names imported by the Python file at ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def json_response(
    payload: object, status: int = 200, headers: dict | None = None
) -> httpx.Response:
    """Build a JSON ``httpx.Response``."""
    return httpx.Response(status, json=payload, headers=headers)


def html_response(
    status: int = 403, body: str | None = None, headers: dict | None = None
) -> httpx.Response:
    """Build an HTML response, as served by an anti-bot wall."""
    text = body if body is not None else "<!DOCTYPE html><html><body>blocked (test)</body></html>"
    merged = {"content-type": "text/html; charset=utf-8", **(headers or {})}
    return httpx.Response(status, content=text.encode("utf-8"), headers=merged)


class FakeWB:
    """Callable handler for ``httpx.MockTransport``.

    ``scripts`` maps ``(query, page)`` to a queue of items popped in order; an item is an
    ``httpx.Response``, an exception instance (raised) or a callable ``request -> Response``.
    Requests to Google Sheet hosts pop from the ``sheet`` queue (same item kinds, or raw CSV
    ``bytes``); an empty queue yields a 404. An unscripted WB request raises AssertionError.
    """

    def __init__(
        self,
        scripts: dict[tuple[str, int], list] | None = None,
        *,
        sheet: list | None = None,
    ) -> None:
        self.scripts = {key: list(items) for key, items in (scripts or {}).items()}
        self.sheet = list(sheet) if sheet else []
        self.requests: list[httpx.Request] = []

    @staticmethod
    def _is_sheet_host(host: str) -> bool:
        return host == "docs.google.com" or host.endswith(".googleusercontent.com")

    @staticmethod
    def _resolve(item: object, request: httpx.Request) -> httpx.Response:
        if isinstance(item, Exception):
            item.request = request  # type: ignore[attr-defined]
            raise item
        if callable(item):
            return item(request)
        if isinstance(item, bytes):
            return httpx.Response(
                200, content=item, headers={"content-type": "text/csv; charset=utf-8"}
            )
        assert isinstance(item, httpx.Response), f"bad script item: {item!r}"
        return item

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._is_sheet_host(request.url.host):
            if not self.sheet:
                return httpx.Response(404, text=_SHEET_NOT_SCRIPTED)
            return self._resolve(self.sheet.pop(0), request)
        query = request.url.params["query"]
        page = int(request.url.params["page"])
        queue = self.scripts.get((query, page))
        if not queue:
            raise AssertionError(f"unscripted WB request: query={query!r} page={page}")
        return self._resolve(queue.pop(0), request)

    def wb_requests(self) -> list[httpx.Request]:
        """Requests that were sent to a non-sheet host."""
        return [r for r in self.requests if not self._is_sheet_host(r.url.host)]

    def sheet_requests(self) -> list[httpx.Request]:
        """Requests that were sent to a Google Sheet host."""
        return [r for r in self.requests if self._is_sheet_host(r.url.host)]


def make_product(
    nm_id: int, *, price_kop: int = 150000, ad: dict | int | None = None, **overrides: object
) -> dict:
    """Build a minimal valid WB product; ``ad`` int is the promoPosition, dict is the raw log."""
    size = {
        "name": "",
        "origName": "0",
        "rank": 0,
        "optionId": nm_id + 20,
        "wh": 900001,
        "time1": 3,
        "time2": 26,
        "dtype": 4,
        "price": {
            "basic": 2 * price_kop,
            "product": price_kop,
            "total": price_kop,
            "logistics": 0,
            "return": 0,
        },
        "saleConditions": 134217728,
        "payload": "TEST-OPAQUE-PAYLOAD",
    }
    product: dict = {
        "time1": 3,
        "time2": 26,
        "id": nm_id,
        "root": nm_id - 100000000,
        "brand": "ТЕСТ-БРЕНД",
        "subjectId": 2300,
        "name": f"ТЕСТ товар {nm_id}",
        "supplier": "ТЕСТ-Продавец",
        "supplierId": 500001,
        "reviewRating": 4.7,
        "nmReviewRating": 4.6,
        "feedbacks": 120,
        "nmFeedbacks": 40,
        "sizes": [size],
        "totalQuantity": 25,
    }
    if isinstance(ad, dict):
        product["log"] = dict(ad)
    elif ad is not None:
        product["log"] = {
            "cpm": 1000,
            "promotion": 1,
            "promoPosition": ad,
            "position": ad + 10,
            "advertId": 99000001,
            "tp": "c",
        }
    product.update(overrides)
    return product


def make_products(
    first_id: int,
    n: int,
    *,
    ads: dict[int, int] | None = None,
    ids: Sequence[int] | None = None,
) -> list[dict]:
    """Build ``n`` products; ``ads`` maps a 1-based index in the list to a promoPosition."""
    id_list = list(ids) if ids is not None else [first_id + i for i in range(n)]
    ads = ads or {}
    return [make_product(nm_id, ad=ads.get(index)) for index, nm_id in enumerate(id_list, start=1)]


def make_page(
    first_id: int = 900000001,
    n: int = 100,
    *,
    ads: dict[int, int] | None = None,
    ids: Sequence[int] | None = None,
    shape: str = "data.products",
    normquery: str | None = "тест запрос",
) -> dict:
    """Build a WB search page payload in the ``data.products`` or top-level ``products`` shape."""
    products = make_products(first_id, n, ads=ads, ids=ids)
    metadata = {"name": "тест-запрос", "normquery": normquery, "rs": 100}
    if shape == "products":
        return {"_test_only": True, "metadata": metadata, "total": 9999, "products": products}
    return {
        "_test_only": True,
        "metadata": metadata,
        "state": 0,
        "data": {"total": 9999, "products": products},
    }


def sheet_csv_bytes(texts: Sequence[str], *, bom: bool = True, crlf: bool = True) -> bytes:
    """Encode one CSV row per text the way a Google Sheet export does."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n" if crlf else "\n")
    for text in texts:
        writer.writerow([text])
    return buffer.getvalue().encode("utf-8-sig" if bom else "utf-8")


class RecordingSleep:
    """Drop-in for ``time.sleep`` that records the requested durations and never waits."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class SteppingClock:
    """Fake monotonic clock: each call returns the current time, then advances by ``step``."""

    def __init__(self, start: float = 0.0, step: float = 0.0) -> None:
        self.current = start
        self.step = step

    def __call__(self) -> float:
        value = self.current
        self.current += self.step
        return value

    def advance(self, seconds: float) -> None:
        """Move the clock forward by ``seconds``."""
        self.current += seconds


def make_settings(**overrides: object):
    """Build ``Settings`` from the (isolated) environment with optional field overrides."""
    from wbmon.config import Settings

    settings = Settings.from_env()
    return settings.with_overrides(**overrides) if overrides else settings


def build_wb_client(
    fake: FakeWB,
    settings=None,
    *,
    sleep: Callable[[float], None] | None = None,
    now: Callable | None = None,
    monotonic: Callable[[], float] | None = None,
    should_stop: Callable[[], bool] | None = None,
):
    """Wire a ``WbClient`` to a ``FakeWB`` through an ``httpx.MockTransport``."""
    from wbmon import clock
    from wbmon.wb_client import WbClient, make_http_client

    settings = settings if settings is not None else make_settings()
    http = make_http_client(settings, transport=httpx.MockTransport(fake))
    kwargs: dict[str, object] = {
        "sleep": sleep if sleep is not None else RecordingSleep(),
        "now": now if now is not None else clock.utc_now,
    }
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    if should_stop is not None:
        kwargs["should_stop"] = should_stop
    return WbClient(http, settings, **kwargs)
