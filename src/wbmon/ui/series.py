"""Pure UI logic: header, run log, picker helpers and the honest-gap series of the position chart.

No Streamlit, no database, no clock: every function takes ``now`` explicitly so it is testable
with fixed values. Stored times are UTC ``YYYY-MM-DDTHH:MM:SSZ``; the screen shows Moscow time.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from wbmon.clock import floor_hour, iso_z, parse_iso_z
from wbmon.ui import texts

MSK = "Europe/Moscow"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

GREEN_MAX_S = 90 * 60
YELLOW_MAX_S = 3 * 3600
RUNNING_ALIVE_S = 60 * 60

# Point states of the position chart (CTX-2.3, D3-A5).
FOUND = "found"
ABSENT = "absent"
UNKNOWN = "unknown"
MAX_SELECTED = 15
PERIOD_DELTAS: dict[str, timedelta] = {"24 ч": timedelta(hours=24), "7 дней": timedelta(days=7)}
HALF_RUN = pd.Timedelta(minutes=30)
NAME_LIMIT = 40

RUNLOG_COLUMNS = (
    "Начало (МСК)",
    "Длительность",
    "Тип",
    "Статус",
    "Успешных",
    "Частичных",
    "Заблок.",
    "Ошибок",
    "Товаров сохранено",
)
_COUNT_SOURCES = {
    "Успешных": "n_ok",
    "Частичных": "n_partial",
    "Заблок.": "n_blocked",
    "Ошибок": "n_error",
    "Товаров сохранено": "n_items",
}


def to_msk(s: pd.Series) -> pd.Series:
    """UTC ``Z`` strings to tz-naive Moscow wall-clock datetimes (plotly ignores UTC offsets)."""
    parsed = pd.to_datetime(s, format=_TS_FORMAT, utc=True)
    return parsed.dt.tz_convert(MSK).dt.tz_localize(None)


def to_msk_one(ts: str | None) -> datetime | None:
    """One UTC ``Z`` string to a naive Moscow datetime; unknown stays ``None``."""
    if ts is None:
        return None
    return parse_iso_z(ts).astimezone(ZoneInfo(MSK)).replace(tzinfo=None)


def _age_s(ts: str, now: datetime) -> float:
    return max(0.0, (now - parse_iso_z(ts)).total_seconds())


def freshness(last_ok_finished: str | None, now: datetime) -> tuple[str, str]:
    """Badge colour (green|yellow|red|gray) and text for the newest successful run."""
    if last_ok_finished is None:
        return "gray", texts.FRESHNESS_NONE
    age = _age_s(last_ok_finished, now)
    if age < GREEN_MAX_S:
        colour, emoji = "green", "🟢"
    elif age < YELLOW_MAX_S:
        colour, emoji = "yellow", "🟡"
    else:
        colour, emoji = "red", "🔴"
    when = texts.fmt_dt_short(to_msk_one(last_ok_finished))  # type: ignore[arg-type]
    return colour, texts.FRESHNESS_TEMPLATE.format(emoji=emoji, when=when, age=texts.fmt_age(age))


def _young_running(latest_run: Mapping | None, now: datetime) -> bool:
    if latest_run is None or latest_run.get("status") != "running":
        return False
    try:
        return _age_s(str(latest_run["started_at"]), now) < RUNNING_ALIVE_S
    except (KeyError, ValueError):
        return False


def heartbeat_line(
    hb_ts: datetime | None,
    now: datetime,
    latest_run: Mapping | None,
    *,
    stale_s: int = 600,
) -> str:
    """The collector line: a young running run wins, then the heartbeat age, then 'no data'."""
    if _young_running(latest_run, now):
        started = to_msk_one(str(latest_run["started_at"]))  # type: ignore[index]
        return texts.HEARTBEAT_RUNNING.format(hm=texts.fmt_hm(started))  # type: ignore[arg-type]
    if hb_ts is None:
        return texts.HEARTBEAT_ABSENT
    age = max(0.0, (now - hb_ts).total_seconds())
    if age <= stale_s:
        return texts.HEARTBEAT_LIVE.format(age=texts.fmt_span(age))
    return texts.HEARTBEAT_STALE.format(age=texts.fmt_span(age))


def next_attempt(now: datetime) -> str:
    """The next full Moscow hour after ``now`` as ``HH:00``."""
    upcoming = floor_hour(now.astimezone(UTC)) + timedelta(hours=1)
    return upcoming.astimezone(ZoneInfo(MSK)).strftime("%H:00")


def _int(value: object) -> int:
    return 0 if value is None or pd.isna(value) else int(value)  # type: ignore[arg-type]


def banner(
    latest_run: Mapping | None,
    n_ok_runs: int,
    blocked_http: int | None,
    now: datetime,
) -> tuple[str, str] | None:
    """The single status banner (kind, text) by the UI-SPEC rules, first match wins."""
    if latest_run is None:
        return "info", texts.BANNER_NO_RUNS
    status = latest_run.get("status")
    following = next_attempt(now)
    if status == "running":
        started = to_msk_one(str(latest_run["started_at"]))
        return "info", texts.BANNER_RUNNING.format(hm=texts.fmt_hm(started))  # type: ignore[arg-type]
    if status == "partial":
        return "warning", texts.BANNER_PARTIAL.format(
            ok=_int(latest_run.get("n_ok")),
            n=texts.queries_genitive(_int(latest_run.get("n_queries"))),
            blocked=_int(latest_run.get("n_blocked")),
            error=_int(latest_run.get("n_error")),
            next_attempt=following,
        )
    if status == "blocked":
        if _int(n_ok_runs) == 0:
            http = "" if blocked_http is None else f", HTTP {blocked_http}"
            return "warning", texts.BANNER_ALL_BLOCKED.format(http=http)
        http = "" if blocked_http is None else f" (HTTP {blocked_http})"
        return "warning", texts.BANNER_BLOCKED.format(
            http=http,
            blocked=_int(latest_run.get("n_blocked")),
            n=texts.queries_genitive(_int(latest_run.get("n_queries"))),
            next_attempt=following,
        )
    if status == "error":
        return "error", texts.BANNER_ERROR.format(next_attempt=following)
    if status == "abandoned":
        return "warning", texts.BANNER_ABANDONED.format(next_attempt=following)
    return None


def _duration_text(status: object, started: object, finished: object) -> str:
    if status == "running":
        return texts.RUNLOG_RUNNING
    if finished is None or pd.isna(finished) or started is None or pd.isna(started):
        return texts.MISSING
    seconds = (parse_iso_z(str(finished)) - parse_iso_z(str(started))).total_seconds()
    return texts.fmt_duration(seconds)


def run_log_frame(run_log: pd.DataFrame) -> pd.DataFrame:
    """Shape the run-log loader frame into the display table of the run-log tab."""
    if run_log.empty:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in RUNLOG_COLUMNS})
    running = run_log["status"].eq("running").fillna(False).astype(bool)
    out = pd.DataFrame(index=run_log.index)
    out["Начало (МСК)"] = to_msk(run_log["started_at"])
    spans = zip(run_log["status"], run_log["started_at"], run_log["finished_at"], strict=True)
    out["Длительность"] = [_duration_text(s, a, b) for s, a, b in spans]
    out["Тип"] = [texts.TRIGGER_LABELS.get(str(t), str(t)) for t in run_log["trigger"]]
    out["Статус"] = [texts.STATUS_LABELS.get(str(s), str(s)) for s in run_log["status"]]
    for label, source in _COUNT_SOURCES.items():
        out[label] = run_log[source].astype("Int64").mask(running)
    return out.reset_index(drop=True)


STATUS_ORDER = ("ok", "partial", "blocked", "rate_limited", "error", "abandoned", "running")


def status_counts(run_log: pd.DataFrame, last: int = 24) -> dict[str, int]:
    """Runs per status among the newest ``last`` rows of the run log (newest first); no zeros."""
    if run_log.empty:
        return {}
    seen = run_log["status"].head(int(last)).astype(str).value_counts()
    known = {status: int(seen[status]) for status in STATUS_ORDER if status in seen.index}
    other = {str(s): int(n) for s, n in seen.items() if s not in STATUS_ORDER}
    return {**known, **other}


def totals_lines(overview: Mapping) -> list[str]:
    """The five sidebar lines; numbers are ours, so the ``**`` emphasis is safe markdown."""
    first = to_msk_one(overview.get("first_started"))
    first_line = (
        texts.TOTALS_FIRST_NONE
        if first is None
        else texts.TOTALS_FIRST.format(when=texts.fmt_dt(first))
    )
    return [
        texts.TOTALS_RUNS.format(n=texts.fmt_int(overview.get("n_runs") or 0)),
        texts.TOTALS_OK_RUNS.format(n=texts.fmt_int(overview.get("n_ok_runs") or 0)),
        first_line,
        texts.TOTALS_OBS.format(n=texts.fmt_int(overview.get("n_obs") or 0)),
        texts.TOTALS_PRODUCTS.format(n=texts.fmt_int(overview.get("n_products") or 0)),
    ]


# ----------------------------------------------------------------------- honest-gap series


def period_since(last_started: str | None, period: str | None) -> str:
    """Lower bound (UTC ``Z``) of a period, anchored on the latest run start; empty = all time."""
    delta = PERIOD_DELTAS.get(period or "")
    if delta is None or last_started is None:
        return ""
    return iso_z(parse_iso_z(last_started) - delta)


def prepare_grid(grid: pd.DataFrame) -> pd.DataFrame:
    """Add Moscow time, a filled ``q_status`` and the ``complete`` flag to the run grid.

    ``complete`` is the only proof that a product was really not in the results: the attempt is
    ``ok`` AND not cut short. A missing attempt row (abandoned run) reads as ``missing``.
    """
    g = grid.copy()
    g["t_msk"] = to_msk(g["started_at"])
    status = g["q_status"].astype("object")
    g["q_status"] = status.where(status.notna(), "missing")
    flag = g["is_complete"].fillna(0).astype("int64") == 1
    g["complete"] = ((g["q_status"] == "ok") & flag).fillna(False).astype(bool)
    return g


def _prepared(grid: pd.DataFrame) -> pd.DataFrame:
    return grid if "complete" in grid.columns and "t_msk" in grid.columns else prepare_grid(grid)


def build_points(grid: pd.DataFrame, obs: pd.DataFrame, nm_ids: Sequence[int]) -> pd.DataFrame:
    """One row per run of the grid and requested product, each with an explicit state.

    FOUND: an observation exists. ABSENT: no observation and the attempt is complete.
    UNKNOWN: no observation and the attempt is not complete (blocked, error, partial, cut short,
    never recorded). Nothing is interpolated: only FOUND rows carry a position.
    """
    g = _prepared(grid)
    ids = pd.Index([int(i) for i in nm_ids], dtype="int64")
    index = pd.MultiIndex.from_product(
        [g["run_id"].astype("int64"), ids], names=["run_id", "nm_id"]
    )
    columns = ["run_id", "t_msk", "q_status", "complete", "items_collected"]
    cross = index.to_frame(index=False).merge(g[columns], on="run_id", how="left")
    seen = obs.drop(columns=["collected_at"], errors="ignore").copy()
    seen["run_id"] = seen["run_id"].astype("int64")
    seen["nm_id"] = seen["nm_id"].astype("int64")
    # A (run, product) pair may repeat in the data: keep the best (smallest) position.
    seen = seen.sort_values("position").drop_duplicates(["run_id", "nm_id"])
    out = cross.merge(seen, on=["run_id", "nm_id"], how="left")
    out["complete"] = out["complete"].fillna(False).astype(bool)
    found = out["position"].notna()
    out["state"] = (
        pd.Series(UNKNOWN, index=out.index).mask(out["complete"], ABSENT).mask(found, FOUND)
    )
    return out.sort_values(["nm_id", "t_msk", "run_id"], ignore_index=True)


def band_times(grid: pd.DataFrame) -> pd.Series:
    """Moscow start times of the runs whose attempt for the query was not complete."""
    g = _prepared(grid)
    return g.loc[~g["complete"], "t_msk"].reset_index(drop=True)


def x_range(grid: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The visible time span: first and last run, padded by half a run on each side."""
    times = _prepared(grid)["t_msk"]
    if times.empty:
        raise ValueError("an empty run grid has no time range")
    return times.min() - HALF_RUN, times.max() + HALF_RUN


def _text_or_dash(value: object) -> str:
    if value is None or pd.isna(value):  # type: ignore[arg-type]
        return texts.MISSING
    return str(value) or texts.MISSING


def product_options_frame(options: pd.DataFrame, latest_data_run_id: int | None) -> pd.DataFrame:
    """Picker rows ``nm_id, label, in_latest`` ordered by current position, then best position."""
    columns = ["nm_id", "label", "in_latest"]
    if options.empty:
        return pd.DataFrame({"nm_id": pd.Series(dtype="int64"), "label": [], "in_latest": []})[
            columns
        ]
    frame = options.copy()
    frame["nm_id"] = frame["nm_id"].astype("int64")
    if latest_data_run_id is None:
        frame["in_latest"] = False
    else:
        frame["in_latest"] = (frame["last_run_id"] == latest_data_run_id).fillna(False).astype(bool)
    frame["_last"] = frame["last_position"].where(frame["in_latest"]).astype("float64")
    frame = frame.sort_values(
        ["in_latest", "_last", "best_position", "nm_id"],
        ascending=[False, True, True, True],
        na_position="last",
        ignore_index=True,
    )
    labels = []
    for nm_id, brand, name, in_latest in zip(
        frame["nm_id"], frame["brand"], frame["name"], frame["in_latest"], strict=True
    ):
        text = f"{nm_id} · {_text_or_dash(brand)} · {_text_or_dash(name)[:NAME_LIMIT]}"
        labels.append(text if in_latest else text + texts.NOT_IN_LATEST_SUFFIX)
    frame["label"] = labels
    return frame[columns]


def default_selection(top_ids: Sequence[int], option_ids: Sequence[int], n: int) -> list[int]:
    """The first ``min(n, 15)`` products of the latest run that the picker really offers."""
    offered = {int(i) for i in option_ids}
    kept = [int(i) for i in top_ids if int(i) in offered]
    return kept[: max(0, min(int(n), MAX_SELECTED))]


def series_names(options: pd.DataFrame) -> dict[int, str]:
    """Legend names ``ID {nm_id} · {brand or dash}`` for every product of the picker frame."""
    return {
        int(nm_id): f"ID {int(nm_id)} · {_text_or_dash(brand)}"
        for nm_id, brand in zip(options["nm_id"], options["brand"], strict=True)
    }


# --------------------------------------------------------------------- tables, CSV and snapshot

CHARACTERISTICS_COLUMNS = (
    texts.COL_ID,
    texts.COL_NAME,
    texts.COL_BRAND,
    texts.COL_SELLER,
    texts.COL_POSITION_NOW,
    texts.COL_DELTA,
    texts.COL_BEST,
    texts.COL_WORST,
    texts.COL_SHARE,
    texts.COL_PRICE,
    texts.COL_RATING,
    texts.COL_FEEDBACKS,
    texts.COL_DELIVERY,
    texts.COL_AD,
    texts.COL_LAST_SEEN,
)
HISTORY_COLUMNS = (
    texts.COL_TIME,
    texts.COL_ID,
    texts.COL_POSITION,
    texts.COL_PRICE,
    texts.COL_RATING,
    texts.COL_FEEDBACKS,
    texts.COL_DELIVERY,
    texts.COL_SELLER,
    texts.COL_AD,
)
CSV_COLUMNS = (texts.COL_QUERY, *HISTORY_COLUMNS)
SNAPSHOT_COLUMNS = (
    texts.COL_POSITION,
    texts.COL_AD,
    texts.COL_ID,
    texts.COL_NAME,
    texts.COL_BRAND,
    texts.COL_PRICE,
    texts.COL_RATING,
    texts.COL_FEEDBACKS,
    texts.COL_DELIVERY,
    texts.COL_SELLER,
)
ATTEMPTS_COLUMNS = (
    texts.COL_TIME,
    texts.COL_STATUS,
    texts.COL_HTTP,
    texts.COL_PAGES,
    texts.COL_ITEMS,
    texts.COL_REASON,
    texts.COL_DURATION,
)
# Cells that start with one of these are read as a formula by spreadsheet programs (CWE-1236).
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_INT = "Int64"
_FLOAT = "Float64"


def wb_url(nm_id: object) -> str:
    """The product page, built only from the integer id (no stored URL is ever rendered)."""
    return f"https://www.wildberries.ru/catalog/{int(nm_id)}/detail.aspx"  # type: ignore[call-overload]


def delta_text(prev_position: int | None, prev_absent: bool, cur_position: int | None) -> str:
    """Change against the previous complete run: ``▲ n`` (up), ``▼ n``, ``= 0``, new or dash."""
    if cur_position is None or pd.isna(cur_position):
        return texts.MISSING
    if prev_position is not None and not pd.isna(prev_position):
        moved = int(prev_position) - int(cur_position)
        if moved > 0:
            return f"▲ {moved}"
        if moved < 0:
            return f"▼ {-moved}"
        return "= 0"
    return texts.DELTA_NEW if prev_absent else texts.MISSING


def _na(value: object) -> object:
    """``pd.NA`` for any missing value, the value itself otherwise."""
    return pd.NA if value is None or bool(pd.isna(value)) else value  # type: ignore[arg-type]


def _num(values: pd.Series, dtype: str) -> pd.Series:
    """A nullable numeric column; unknown stays ``<NA>``, never 0."""
    return pd.to_numeric(values, errors="coerce").astype(dtype).reset_index(drop=True)


def _text(values: Sequence[object]) -> pd.Series:
    """A text column where unknown is the dash."""
    return pd.Series([_text_or_dash(v) for v in values], dtype="str")


def _ad_marks(values: Sequence[object]) -> pd.Series:
    return pd.Series(
        [texts.AD_MARK if not pd.isna(v) and int(v) == 1 else "" for v in values], dtype="str"
    )


def _links(values: Sequence[object]) -> pd.Series:
    return pd.Series([wb_url(v) for v in values], dtype="str")


def _times(values: Sequence[object]) -> pd.Series:
    if len(values) == 0:
        return pd.Series([], dtype="datetime64[ns]")
    return pd.to_datetime(pd.Series(list(values))).reset_index(drop=True)


POINT_VALUE_COLUMNS = (
    "position",
    "is_promoted",
    "price_rub",
    "rating",
    "feedbacks",
    "delivery_days",
    "seller_name",
    "name",
    "brand",
)


def _full(points: pd.DataFrame) -> pd.DataFrame:
    """The points with every value column present (a missing one reads as unknown)."""
    return points.reindex(
        columns=[*points.columns, *(c for c in POINT_VALUE_COLUMNS if c not in points.columns)]
    )


def _found_rows(points: pd.DataFrame) -> pd.DataFrame:
    """The FOUND points, newest first, then by position (one time rule: the run start)."""
    found = _full(points)[lambda f: f["state"] == FOUND]
    return found.sort_values(["t_msk", "position"], ascending=[False, True], ignore_index=True)


def characteristics_frame(
    points: pd.DataFrame, nm_ids: Sequence[int], options: pd.DataFrame
) -> pd.DataFrame:
    """One row per requested product, derived only from the honest-gap points of the period.

    Current position and Δ come from the newest known (FOUND or ABSENT) point; Δ compares with
    the newest EARLIER complete run. Best/worst use every FOUND point; the share of complete
    runs counts only FOUND points of complete runs, so it can never exceed 100 %.
    """
    ids = list(dict.fromkeys(int(i) for i in nm_ids))
    points = _full(points)
    n_complete = (
        0 if points.empty else int(points.loc[points["complete"].astype(bool), "run_id"].nunique())
    )
    fallback = {}
    if not options.empty:
        fallback = {
            int(n): (b, m)
            for n, b, m in zip(options["nm_id"], options["brand"], options["name"], strict=True)
        }
    rows: list[dict[str, object]] = []
    for nm_id in ids:
        mine = points[points["nm_id"] == nm_id].sort_values(["t_msk", "run_id"])
        known = mine[mine["state"].isin([FOUND, ABSENT])]
        found = mine[mine["state"] == FOUND]
        current: object = pd.NA
        delta = texts.MISSING
        if not known.empty and known.iloc[-1]["state"] == FOUND:
            current = int(known.iloc[-1]["position"])
            earlier = known.iloc[:-1]
            earlier = earlier[earlier["complete"].astype(bool)]
            if earlier.empty:
                delta = delta_text(None, False, current)  # type: ignore[arg-type]
            else:
                prev = earlier.iloc[-1]
                prev_pos = None if prev["state"] == ABSENT else int(prev["position"])
                delta = delta_text(prev_pos, prev["state"] == ABSENT, current)  # type: ignore[arg-type]
        last = found.iloc[-1] if not found.empty else None
        brand, name = fallback.get(nm_id, (None, None))
        row: dict[str, object] = {
            "_nm": nm_id,
            texts.COL_ID: wb_url(nm_id),
            texts.COL_NAME: name if last is None else _na(last["name"]),
            texts.COL_BRAND: brand if last is None else _na(last["brand"]),
            texts.COL_SELLER: None if last is None else _na(last["seller_name"]),
            texts.COL_POSITION_NOW: current,
            texts.COL_DELTA: delta,
            texts.COL_BEST: found["position"].min() if not found.empty else pd.NA,
            texts.COL_WORST: found["position"].max() if not found.empty else pd.NA,
            texts.COL_SHARE: (
                pd.NA
                if n_complete == 0
                else 100.0 * int((found["complete"].astype(bool)).sum()) / n_complete
            ),
            texts.COL_PRICE: pd.NA if last is None else _na(last["price_rub"]),
            texts.COL_RATING: pd.NA if last is None else _na(last["rating"]),
            texts.COL_FEEDBACKS: pd.NA if last is None else _na(last["feedbacks"]),
            texts.COL_DELIVERY: pd.NA if last is None else _na(last["delivery_days"]),
            texts.COL_AD: pd.NA if last is None else _na(last["is_promoted"]),
            texts.COL_LAST_SEEN: pd.NaT if last is None else last["t_msk"],
        }
        rows.append(row)
    return _characteristics_from_rows(rows)


def _characteristics_from_rows(rows: list[dict[str, object]]) -> pd.DataFrame:
    def column(name: str) -> list[object]:
        return [r[name] for r in rows]

    frame = pd.DataFrame(
        {
            "_nm": pd.Series(column("_nm"), dtype="int64"),
            texts.COL_ID: pd.Series(column(texts.COL_ID), dtype="str"),
            texts.COL_NAME: _text(column(texts.COL_NAME)),
            texts.COL_BRAND: _text(column(texts.COL_BRAND)),
            texts.COL_SELLER: _text(column(texts.COL_SELLER)),
            texts.COL_POSITION_NOW: pd.Series(column(texts.COL_POSITION_NOW), dtype=_INT),
            texts.COL_DELTA: pd.Series(column(texts.COL_DELTA), dtype="str"),
            texts.COL_BEST: pd.Series(column(texts.COL_BEST), dtype=_INT),
            texts.COL_WORST: pd.Series(column(texts.COL_WORST), dtype=_INT),
            texts.COL_SHARE: pd.Series(column(texts.COL_SHARE), dtype=_FLOAT),
            texts.COL_PRICE: pd.Series(column(texts.COL_PRICE), dtype=_FLOAT),
            texts.COL_RATING: pd.Series(column(texts.COL_RATING), dtype=_FLOAT),
            texts.COL_FEEDBACKS: pd.Series(column(texts.COL_FEEDBACKS), dtype=_INT),
            texts.COL_DELIVERY: pd.Series(column(texts.COL_DELIVERY), dtype=_INT),
            texts.COL_AD: _ad_marks(column(texts.COL_AD)),
            texts.COL_LAST_SEEN: _times(column(texts.COL_LAST_SEEN)),
        }
    )
    frame = frame.sort_values([texts.COL_POSITION_NOW, "_nm"], na_position="last", kind="stable")
    return frame.drop(columns="_nm").reset_index(drop=True)[list(CHARACTERISTICS_COLUMNS)]


def history_frame(points: pd.DataFrame) -> pd.DataFrame:
    """Every observation (FOUND point) of the selection, newest first, then by position."""
    found = _found_rows(points)
    return pd.DataFrame(
        {
            texts.COL_TIME: _times(list(found["t_msk"])),
            texts.COL_ID: _links(found["nm_id"]),
            texts.COL_POSITION: _num(found["position"], _INT),
            texts.COL_PRICE: _num(found["price_rub"], _FLOAT),
            texts.COL_RATING: _num(found["rating"], _FLOAT),
            texts.COL_FEEDBACKS: _num(found["feedbacks"], _INT),
            texts.COL_DELIVERY: _num(found["delivery_days"], _INT),
            texts.COL_SELLER: _text(list(found["seller_name"])),
            texts.COL_AD: _ad_marks(list(found["is_promoted"])),
        }
    )[list(HISTORY_COLUMNS)]


def csv_safe(value: object) -> object:
    """Neutralise a spreadsheet formula: text starting with ``= + - @ TAB CR`` gets a leading ``'``.

    Applied to the export only (CWE-1236); the database and the screen keep the raw text.
    Missing values, numbers and every other string are returned unchanged.
    """
    if isinstance(value, str) and value.startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_bytes(points: pd.DataFrame, query_text: str) -> bytes:
    """The history rows plus a leading query column as Excel-friendly CSV bytes.

    ``;`` separator, decimal comma, CRLF, UTF-8 with BOM. ``to_csv`` without a path returns text
    and ignores ``encoding``, so the BOM comes from encoding the result as ``utf-8-sig``.
    """
    found = _found_rows(points)
    sellers = pd.Series(
        [csv_safe(_na(v)) for v in found["seller_name"]], dtype="object", index=range(len(found))
    )
    frame = pd.DataFrame(
        {
            texts.COL_QUERY: pd.Series([csv_safe(query_text)] * len(found), dtype="object"),
            texts.COL_TIME: pd.Series([texts.fmt_dt(t) for t in found["t_msk"]], dtype="object"),
            texts.COL_ID: _num(found["nm_id"], _INT),
            texts.COL_POSITION: _num(found["position"], _INT),
            texts.COL_PRICE: _num(found["price_rub"], "float64"),
            texts.COL_RATING: _num(found["rating"], "float64"),
            texts.COL_FEEDBACKS: _num(found["feedbacks"], _INT),
            texts.COL_DELIVERY: _num(found["delivery_days"], _INT),
            texts.COL_SELLER: sellers,
            texts.COL_AD: _num(found["is_promoted"].fillna(0) == 1, _INT),
        }
    )[list(CSV_COLUMNS)]
    text = frame.to_csv(None, sep=";", decimal=",", index=False, lineterminator="\r\n")
    return text.encode("utf-8-sig")


def csv_filename(sheet_order: int, now: datetime) -> str:
    """``wb_serp_q01_20261016_1430.csv``: the query's sheet number and the current Moscow time."""
    aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    stamp = aware.astimezone(ZoneInfo(MSK)).strftime("%Y%m%d_%H%M")
    return f"wb_serp_q{int(sheet_order):02d}_{stamp}.csv"


def snapshot_frame(snapshot: pd.DataFrame) -> pd.DataFrame:
    """The stored top-N of one run in position order."""
    ordered = snapshot.sort_values("position", kind="stable", ignore_index=True)
    return pd.DataFrame(
        {
            texts.COL_POSITION: _num(ordered["position"], _INT),
            texts.COL_AD: _ad_marks(list(ordered["is_promoted"])),
            texts.COL_ID: _links(ordered["nm_id"]),
            texts.COL_NAME: _text(list(ordered["name"])),
            texts.COL_BRAND: _text(list(ordered["brand"])),
            texts.COL_PRICE: _num(ordered["price_rub"], _FLOAT),
            texts.COL_RATING: _num(ordered["rating"], _FLOAT),
            texts.COL_FEEDBACKS: _num(ordered["feedbacks"], _INT),
            texts.COL_DELIVERY: _num(ordered["delivery_days"], _INT),
            texts.COL_SELLER: _text(list(ordered["seller_name"])),
        }
    )[list(SNAPSHOT_COLUMNS)]


def snapshot_metrics(snapshot: pd.DataFrame, status: str) -> dict[str, str]:
    """The four header numbers of the snapshot tab; the median ignores unknown prices."""
    prices = pd.to_numeric(snapshot["price_rub"], errors="coerce").dropna()
    median = (
        texts.MISSING if prices.empty else texts.fmt_int(math.floor(float(prices.median()) + 0.5))
    )
    ads = int((pd.to_numeric(snapshot["is_promoted"], errors="coerce") == 1).sum())
    return {
        texts.METRIC_COLLECTED: texts.fmt_int(len(snapshot)),
        texts.METRIC_ADS: texts.fmt_int(ads),
        texts.METRIC_MEDIAN: median,
        texts.METRIC_STATUS: texts.STATUS_LABELS.get(str(status), str(status)),
    }


def attempts_frame(attempts: pd.DataFrame) -> pd.DataFrame:
    """The recent attempts of one query for display (newest first as loaded)."""
    if attempts.empty:
        started = pd.Series([], dtype="datetime64[ns]")
    else:
        started = to_msk(attempts["started_at"])
    seconds = [
        None if pd.isna(ms) else float(ms) / 1000.0  # type: ignore[arg-type]
        for ms in attempts["duration_ms"]
    ]
    return pd.DataFrame(
        {
            texts.COL_TIME: started.reset_index(drop=True),
            texts.COL_STATUS: pd.Series(
                [texts.STATUS_LABELS.get(str(s), str(s)) for s in attempts["status"]], dtype="str"
            ),
            texts.COL_HTTP: _num(attempts["http_status"], _INT),
            texts.COL_PAGES: _num(attempts["pages_fetched"], _INT),
            texts.COL_ITEMS: _num(attempts["items_collected"], _INT),
            texts.COL_REASON: _text(list(attempts["error_class"])),
            texts.COL_DURATION: pd.Series([texts.fmt_duration(s) for s in seconds], dtype="str"),
        }
    )[list(ATTEMPTS_COLUMNS)]
