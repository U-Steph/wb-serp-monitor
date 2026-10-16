"""Pure UI logic: header, run log, picker helpers and the honest-gap series of the position chart.

No Streamlit, no database, no clock: every function takes ``now`` explicitly so it is testable
with fixed values. Stored times are UTC ``YYYY-MM-DDTHH:MM:SSZ``; the screen shows Moscow time.
"""

from __future__ import annotations

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
