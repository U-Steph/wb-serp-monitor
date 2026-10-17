"""Synthetic demo database for the UI: separate temp file, never the working database.

Wildberries currently answers every automated request with 403, so the live database holds no
products. To let a reviewer see the interface working, demo mode reads THIS database instead:
6 real query texts, 48 hourly runs, a top-30 per query, a few gaps, ad slots and failed runs. It
is written through the same migrations and the same ``Writer`` as the collector, so every screen
takes the same code path. Every run carries ``{"demo": true}`` in ``params_json``.

Safety rules (kept by ``check_demo_path`` and the guard tests):

* the file lives only under ``tempfile.gettempdir()/wbmon-demo-<uid>/``;
* it can never be the configured working database, and a symlinked parent directory is refused;
* this is the only UI module that opens a database for writing, and it has no Streamlit import.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from wbmon.clock import floor_hour, iso_z, utc_now
from wbmon.models import ObservationRow, QueryResult
from wbmon.queries import load_bundled
from wbmon.storage.db import connect_rw
from wbmon.storage.migrations import init_db
from wbmon.storage.writer import Writer

N_QUERIES = 6
N_RUNS = 48
TOP_N = 30
POOL = 40  # synthetic products per query; the top-30 is drawn from them
QUERY_STEP_S = 20

KINDS = ("Постельное бельё", "Комплект постельного белья", "КПБ")
DESIGNS = (
    "Лаванда",
    "Nordic",
    "Royal",
    "Морской бриз",
    "Графит",
    "Ромашки",
    "Облако",
    "Classic",
    "Эвкалипт",
    "Пионы",
    "Шалфей",
    "Мрамор",
    "Сакура",
    "Звёздная ночь",
    "Лён натуральный",
    "Клетка",
)
FABRICS = (
    "сатин",
    "поплин",
    "бязь",
    "перкаль",
    "страйп-сатин",
    "тенсел",
    "мако-сатин",
    "лён",
)
SIZES = ("2-сп.", "евро", "1,5-сп.", "семейный")
BRANDS = (
    "Текстиль Дом",
    "Иваново Опт",
    "Nordic Home",
    "Royal Sleep",
    "Бриз",
    "Urban Linen",
    "Soft Cloud",
    "Сонный кот",
    "Ecotex",
    "Cleo",
)
SELLERS = (
    "ООО Текстиль Дом",
    "ИП Иванов",
    "Nordic Home",
    "ООО Роял",
    "ИП Бризова",
    "Urban Linen",
    "ООО Софт",
    "ИП Котов",
    "Ecotex",
    "ООО Клио",
)


def demo_db_path() -> Path:
    """``<tmp>/wbmon-demo-<uid>/demo.sqlite3``: one private directory per user."""
    uid = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f"wbmon-demo-{uid}" / "demo.sqlite3"


def check_demo_path(demo: str | Path, real: str | Path) -> Path:
    """Return the resolved demo path or raise ``ValueError`` if it could touch anything else."""
    candidate = Path(demo)
    resolved = candidate.resolve()
    if resolved == Path(real).resolve():
        raise ValueError("the demo database must not be the working database")
    if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError("the demo database must live in the temporary directory")
    if candidate.parent.is_symlink():
        raise ValueError("the demo directory must not be a symlink")
    return resolved


def _row(position: int, p: int, qi: int, k: int, at: str) -> ObservationRow:
    price = 990 + ((p * 389 + qi * 97) % 50) * 100 + ((k + p) % 3) * 10
    return ObservationRow(
        collected_at=at,
        position=position,
        nm_id=150_000_000 + qi * 1_000_003 + p * 7919,
        name=f"{KINDS[p % 3]} «{DESIGNS[p % 16]}», {FABRICS[(p + qi) % 8]}, {SIZES[qi % 4]}",
        brand=BRANDS[(p * 7 + qi) % 10],
        subject_id=None,
        root_id=None,
        price_rub=float(price),
        price_basic_rub=float(round(price * 1.45, -1)),
        price_total_rub=None,
        logistics_rub=None,
        rating=4.3 + ((p * 3 + qi) % 6) / 10,
        feedbacks=40 + (p * 733 + qi * 91) % 5000,
        nm_rating=None,
        nm_feedbacks=None,
        time1_h=None,
        time2_h=None,
        delivery_hours=None,
        delivery_days=1 + p % 3,
        seller_name=SELLERS[(p * 5 + qi) % 10],
        seller_id=None,
        is_promoted=1 if position % 11 == 0 else 0,
        ad_promo_position=None,
        ad_organic_position=None,
        ad_tp=None,
        ad_cpm=None,
        total_quantity=None,
    )


def _order(qi: int, k: int) -> list[int]:
    """Pool indices in position order: stable leaders and a fringe that moves between runs."""

    def score(p: int) -> int:
        return p * 10 + ((p * 37 + k * 53 + qi * 11) % 301) - 150

    return sorted(range(POOL), key=score)


def _ok(at: str, qi: int, k: int, *, partial: bool = False) -> QueryResult:
    depth = 15 if partial else TOP_N
    rows = tuple(
        _row(position, p, qi, k, at) for position, p in enumerate(_order(qi, k)[:depth], start=1)
    )
    return QueryResult(
        status="partial" if partial else "ok",
        started_at=at,
        duration_ms=900,
        observations=rows,
        error_class="http_5xx" if partial else None,
        http_status=503 if partial else None,
        pages_fetched=1,
        is_complete=not partial,
    )


def _blocked(at: str) -> QueryResult:
    return QueryResult(
        status="blocked",
        started_at=at,
        duration_ms=300,
        error_class="http_403",
        http_status=403,
        pages_fetched=1,
    )


def _result(at: str, qi: int, k: int) -> QueryResult:
    """The attempt of query ``qi`` (1-based) in run ``k``: mostly ok, with a few failures."""
    if k == 12 or (k == 31 and qi >= 4):
        return _blocked(at)
    if k == 40 and qi == 2:
        return _ok(at, qi, k, partial=True)
    return _ok(at, qi, k)


def build_demo_db(path: str | Path, now: datetime) -> None:
    """Build the demo database at ``path`` (a new file) with 48 hourly runs ending at ``now``."""
    target = Path(path)
    init_db(target)
    queries: Sequence[str] = list(load_bundled()[:N_QUERIES])
    top_of_hour = floor_hour(now)
    with contextlib.closing(connect_rw(target)) as con:
        writer = Writer(con)
        ids = writer.upsert_queries(list(queries), iso_z(now))
        for k in range(1, N_RUNS + 1):
            started = top_of_hour - timedelta(hours=N_RUNS - k)
            run_id = writer.start_run(
                trigger="scheduler",
                started_at=iso_z(started),
                query_source="bundled",
                params_json=json.dumps({"demo": True}),
                top_n=TOP_N,
                n_queries=N_QUERIES,
                slot_utc=iso_z(started),
            )
            for qi, text in enumerate(queries, start=1):
                at = iso_z(started + timedelta(seconds=QUERY_STEP_S * (qi - 1)))
                writer.record_query(run_id, ids[text], _result(at, qi, k))
            writer.finish_run(run_id, iso_z(min(started + timedelta(minutes=4), now)))


def ensure_demo_db(real_db_path: str | Path, now: datetime | None = None) -> Path:
    """Build a fresh demo database in the private temp directory and return its path."""
    path = check_demo_path(demo_db_path(), real_db_path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = check_demo_path(demo_db_path(), real_db_path)  # the directory exists now: re-check
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    build_demo_db(path, now or utc_now())
    return path
