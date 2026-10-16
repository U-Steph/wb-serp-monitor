"""Scenario database builder for the UI tests.

Synthetic rows may exist ONLY in temporary databases: every entry point calls
``assert_temporary`` first, which refuses any path outside the system temp directory and any
path inside the repository. Rows go in through the real Phase 1 ``Writer`` (the same code path as
the collector), so statuses, counters and constraints are the production ones.

Runnable as a script: ``python tests/ui/dbbuilder.py --scenario {mixed,all_blocked} PATH``.
"""

from __future__ import annotations

import argparse
import contextlib
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from wbmon.clock import floor_hour, iso_z, parse_iso_z, utc_now
from wbmon.models import ObservationRow, QueryResult
from wbmon.storage.db import connect_rw
from wbmon.storage.migrations import init_db
from wbmon.storage.writer import Writer

REPO_ROOT = Path(__file__).resolve().parents[2]

Q1 = "постельное бельё 2-спальное"
Q2 = "комплект постельного белья 160х200"  # Cyrillic "х"
Q3 = "подушка 50x70"  # Latin "x"
TEST_QUERIES: tuple[str, ...] = (Q1, Q2, Q3)

NM: dict[str, int] = {
    "A": 900000101,
    "B": 900000102,
    "C": 900000103,
    "D": 900000104,
    "E": 900000105,
    "F": 900000106,
}
Q2_IDS: tuple[int, ...] = (900000201, 900000202, 900000203)
Q3_IDS: tuple[int, ...] = (900000301, 900000302, 900000303)

QUERY_STEP_S = 20  # each query of a run starts this many seconds after the previous one


def assert_temporary(path: str | Path) -> Path:
    """Return the resolved ``path`` or raise ``ValueError`` unless it is a temporary location."""
    resolved = Path(path).resolve()
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if not any(resolved.is_relative_to(root) for root in temp_roots):
        raise ValueError(f"refusing to write synthetic rows outside the temp directory: {resolved}")
    if resolved.is_relative_to(REPO_ROOT):
        raise ValueError(f"refusing to write synthetic rows inside the repository: {resolved}")
    return resolved


def obs_row(position: int, nm_id: int, collected_at: str, **overrides: object) -> ObservationRow:
    """One synthetic observation; every field is explicit and unknown means None, never 0."""
    values: dict[str, object] = {
        "collected_at": collected_at,
        "position": position,
        "nm_id": nm_id,
        "name": f"ТЕСТ товар {nm_id}",
        "brand": "ТЕСТ-БРЕНД",
        "subject_id": None,
        "root_id": None,
        "price_rub": 1000.0 + position,
        "price_basic_rub": None,
        "price_total_rub": None,
        "logistics_rub": None,
        "rating": 4.7,
        "feedbacks": 10 + position,
        "nm_rating": None,
        "nm_feedbacks": None,
        "time1_h": None,
        "time2_h": None,
        "delivery_hours": None,
        "delivery_days": 2,
        "seller_name": "ТЕСТ-Продавец",
        "seller_id": None,
        "is_promoted": 0,
        "ad_promo_position": None,
        "ad_organic_position": None,
        "ad_tp": None,
        "ad_cpm": None,
        "total_quantity": None,
    }
    values.update(overrides)
    return ObservationRow(**values)  # type: ignore[arg-type]


def ok_result(
    started_at: str,
    nm_ids: Sequence[int],
    *,
    complete: bool = True,
    promoted: Iterable[int] = (),
    price_none: Iterable[int] = (),
    status: str = "ok",
    http_status: int | None = None,
    error_class: str | None = None,
) -> QueryResult:
    """A query result carrying ``nm_ids`` at positions 1..n in list order."""
    promoted_set, price_none_set = set(promoted), set(price_none)
    rows = tuple(
        obs_row(
            position,
            nm_id,
            started_at,
            is_promoted=1 if nm_id in promoted_set else 0,
            **({"price_rub": None} if nm_id in price_none_set else {}),
        )
        for position, nm_id in enumerate(nm_ids, start=1)
    )
    if status == "partial" and (complete or error_class is None):
        raise ValueError("a partial result is incomplete and carries an error_class")
    return QueryResult(
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        duration_ms=900,
        observations=rows,
        error_class=error_class,
        http_status=http_status,
        pages_fetched=1,
        is_complete=complete,
    )


def failed_result(
    started_at: str,
    status: str = "blocked",
    http_status: int | None = 403,
    error_class: str | None = "http_403",
) -> QueryResult:
    """A query attempt without observations (blocked, rate_limited or error)."""
    return QueryResult(
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        duration_ms=300,
        error_class=error_class,
        http_status=http_status,
        pages_fetched=1,
    )


@dataclass(frozen=True)
class RunSpec:
    """One run: its start, per-query results (keyed by query text) and how it ends."""

    started_at: str
    results: Mapping[str, QueryResult]
    finish: bool = True
    trigger: str = "scheduler"
    top_n: int = 200
    n_queries: int | None = None
    duration_s: int = 240


@dataclass
class Built:
    """What ``build_db`` created."""

    query_ids: dict[str, int] = field(default_factory=dict)
    run_ids: list[int] = field(default_factory=list)
    started: list[str] = field(default_factory=list)


def _retime(result: QueryResult, started_at: str) -> QueryResult:
    rows = tuple(replace(row, collected_at=started_at) for row in result.observations)
    return replace(result, started_at=started_at, observations=rows)


def _write_run(
    writer: Writer, spec: RunSpec, queries: Sequence[str], query_ids: Mapping[str, int]
) -> int:
    start = parse_iso_z(spec.started_at)
    slot = iso_z(floor_hour(start)) if spec.trigger == "scheduler" else None
    run_id = writer.start_run(
        trigger=spec.trigger,
        started_at=spec.started_at,
        query_source="bundled",
        params_json="{}",
        top_n=spec.top_n,
        n_queries=len(queries) if spec.n_queries is None else spec.n_queries,
        slot_utc=slot,
    )
    for index, text in enumerate(queries):
        result = spec.results.get(text)
        if result is None:
            continue
        at = iso_z(start + timedelta(seconds=QUERY_STEP_S * index))
        writer.record_query(run_id, query_ids[text], _retime(result, at))
    if spec.finish:
        writer.finish_run(run_id, iso_z(start + timedelta(seconds=spec.duration_s)))
    return run_id


def build_db(
    path: str | Path,
    runs: Sequence[RunSpec],
    queries: Sequence[str] = TEST_QUERIES,
    *,
    abandon_at: str | None = None,
) -> Built:
    """Create a new temporary database and write ``runs`` through the real Writer."""
    target = assert_temporary(path)
    if target.exists():
        raise FileExistsError(str(target))
    init_db(target)
    built = Built()
    with contextlib.closing(connect_rw(target)) as con:
        writer = Writer(con)
        built.query_ids = writer.upsert_queries(list(queries), iso_z(utc_now()))
        for spec in runs:
            built.run_ids.append(_write_run(writer, spec, queries, built.query_ids))
            built.started.append(spec.started_at)
        if abandon_at is not None:
            writer.abandon_stale_runs(abandon_at)
    return built


def append_run(path: str | Path, spec: RunSpec, queries: Sequence[str] = TEST_QUERIES) -> int:
    """Append one run to an existing temporary database and return its id."""
    target = assert_temporary(path)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    with contextlib.closing(connect_rw(target)) as con:
        writer = Writer(con)
        query_ids = writer.upsert_queries(list(queries), iso_z(utc_now()))
        return _write_run(writer, spec, queries, query_ids)


# ------------------------------------------------------------------------------- scenarios


def _mixed_specs(now: datetime) -> list[RunSpec]:
    base = now - timedelta(minutes=10)
    a, b, c, d, e, f = (NM[k] for k in "ABCDEF")
    q1_plans: list[tuple[str, tuple[int, ...] | None, dict[str, object]]] = [
        ("ok", (a, b, c, d, e, f), {}),
        ("blocked", None, {}),
        ("ok", (a, c, d, e, f), {"promoted": (a,)}),
        ("partial", (c, a), {}),
        ("ok", (b, a, c, d, e, f), {"price_none": (c,)}),
        ("ok", (a, b, d, c, e), {"promoted": (e,)}),
    ]
    specs: list[RunSpec] = []
    for k, (kind, ids, extra) in enumerate(q1_plans, start=1):
        started = iso_z(base - timedelta(hours=6 - k))
        if kind == "ok":
            assert ids is not None
            q1 = ok_result(started, ids, **extra)  # type: ignore[arg-type]
        elif kind == "partial":
            assert ids is not None
            q1 = ok_result(
                started,
                ids,
                complete=False,
                status="partial",
                http_status=503,
                error_class="http_5xx",
            )
        else:
            q1 = failed_result(started)
        specs.append(
            RunSpec(
                started_at=started,
                results={
                    Q1: q1,
                    Q2: ok_result(started, Q2_IDS),
                    Q3: ok_result(started, Q3_IDS),
                },
                n_queries=3,
            )
        )
    return specs


def scenario_mixed(path: str | Path) -> Built:
    """Six hourly scheduler runs with ok, blocked and partial attempts for the first query."""
    return build_db(path, _mixed_specs(utc_now()))


def scenario_all_blocked(path: str | Path, n_runs: int = 3) -> Built:
    """Hourly runs in which every query is blocked (403): no observations at all."""
    base = utc_now() - timedelta(minutes=10)
    specs = []
    for k in range(1, n_runs + 1):
        started = iso_z(base - timedelta(hours=n_runs - k))
        specs.append(
            RunSpec(
                started_at=started,
                results={text: failed_result(started) for text in TEST_QUERIES},
                n_queries=3,
            )
        )
    return build_db(path, specs)


def scenario_latest(path: str | Path, final: str) -> Built:
    """``scenario_mixed`` plus a manual run r7 ending as running/partial/blocked/error/abandoned."""
    now = utc_now()
    started = iso_z(now - timedelta(minutes=3))
    results: dict[str, QueryResult]
    finish = True
    abandon_at: str | None = None
    if final == "running":
        results, finish = {Q1: ok_result(started, (NM["A"], NM["B"]))}, False
    elif final == "partial":
        results = {
            Q1: failed_result(started),
            Q2: ok_result(started, Q2_IDS),
            Q3: ok_result(started, Q3_IDS),
        }
    elif final == "blocked":
        results = {text: failed_result(started) for text in TEST_QUERIES}
    elif final == "error":
        results = {text: failed_result(started, "error", 503, "http_5xx") for text in TEST_QUERIES}
    elif final == "abandoned":
        results, finish, abandon_at = {Q1: ok_result(started, (NM["A"],))}, False, iso_z(now)
    else:
        raise ValueError(f"unknown final status: {final!r}")
    r7 = RunSpec(
        started_at=started,
        results=results,
        finish=finish,
        trigger="manual",
        n_queries=3,
        duration_s=120,
    )
    return build_db(path, [*_mixed_specs(now), r7], abandon_at=abandon_at)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: build a scenario database at PATH (temporary locations only)."""
    parser = argparse.ArgumentParser(description="Build a synthetic scenario DB (temp only).")
    parser.add_argument("--scenario", choices=("mixed", "all_blocked"), required=True)
    parser.add_argument("path")
    args = parser.parse_args(argv)
    if args.scenario == "mixed":
        scenario_mixed(args.path)
    else:
        scenario_all_blocked(args.path)
    print(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
