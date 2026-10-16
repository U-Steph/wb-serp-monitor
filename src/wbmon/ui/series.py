"""Pure header logic and run-log shaping: freshness, collector line, banner, run table, totals.

No Streamlit, no database, no clock: every function takes ``now`` explicitly so it is testable
with fixed values. Stored times are UTC ``YYYY-MM-DDTHH:MM:SSZ``; the screen shows Moscow time.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from wbmon.clock import floor_hour, parse_iso_z
from wbmon.ui import texts

MSK = "Europe/Moscow"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

GREEN_MAX_S = 90 * 60
YELLOW_MAX_S = 3 * 3600
RUNNING_ALIVE_S = 60 * 60

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
