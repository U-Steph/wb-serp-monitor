"""Streamlit entrypoint of the read-only monitor: ``streamlit run src/wbmon/ui/app.py``.

Run it from the repository root so ``.streamlit/config.toml`` (read from the process cwd) applies.
Imports are absolute on purpose: Streamlit executes this file as a script, where relative imports
fail. The page only reads the database, through the loaders in ``wbmon.ui.data``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

import pandas as pd
import streamlit as st

from wbmon import clock
from wbmon.config import ConfigError, Settings
from wbmon.ui import data, series, texts

logger = logging.getLogger("wbmon.ui")

_COUNT_COLUMNS = ("Успешных", "Частичных", "Заблок.", "Ошибок", "Товаров сохранено")


def _guarded(render: Callable[..., None], *args: object) -> None:
    """Run one tab body; an unexpected failure shows the generic copy and keeps the page alive."""
    try:
        render(*args)
    except Exception:
        logger.exception("tab %s failed", getattr(render, "__name__", "?"))
        st.error(texts.ERROR_BLOCK)


def render_header(
    overview: Mapping,
    latest: Mapping | None,
    blocked_http: int | None,
    settings: Settings,
) -> None:
    """Subtitle, freshness badge, collector line and at most one status banner."""
    now = clock.utc_now()
    st.caption(
        texts.subtitle(overview["n_queries"] or 70, overview["top_n_latest"] or settings.top_n)
    )
    badge_col, collector_col = st.columns([3, 2], gap="small")
    with badge_col:
        colour, text = series.freshness(overview["last_ok_finished"], now)
        st.badge(text, color=colour)
    with collector_col:
        heartbeat = data.read_heartbeat(settings.db_path)
        st.caption(series.heartbeat_line(heartbeat, now, latest, stale_s=data.HEARTBEAT_STALE_S))
    shown = series.banner(latest, overview["n_ok_runs"], blocked_http, now)
    if shown is not None:
        kind, message = shown
        # icon="" keeps the leading emoji of the UI-SPEC copy inside the text instead of
        # Streamlit lifting it into the alert icon.
        {"info": st.info, "warning": st.warning, "error": st.error}[kind](message, icon="")


def render_sidebar_totals(overview: Mapping) -> None:
    """The five totals under a divider (plan 03-02 adds the filters above)."""
    with st.sidebar:
        st.divider()
        with st.container(border=True):
            for line in series.totals_lines(overview):
                st.caption(line)


def render_runlog_tab(run_log: pd.DataFrame, latest: Mapping | None) -> None:
    """The run table: the latest 200 runs, newest first."""
    st.subheader(texts.RUNLOG_TITLE)
    if run_log.empty or latest is None:
        st.info(texts.NO_RUNS_RUNLOG)
        return
    st.caption(texts.RUNLOG_COUNTS_CAPTION.format(n=int(latest["n_queries"])))
    config: dict[str, object] = {
        "Начало (МСК)": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
    }
    for name in _COUNT_COLUMNS:
        config[name] = st.column_config.NumberColumn(format="%d")
    st.dataframe(
        series.run_log_frame(run_log),
        hide_index=True,
        width="stretch",
        height=420,
        placeholder=texts.MISSING,
        column_config=config,
    )
    st.caption(texts.RUNLOG_FOOTER)


def main() -> None:
    """Build the page: header, sidebar totals and the run-log tab."""
    st.set_page_config(page_title=texts.PAGE_TITLE, page_icon=texts.PAGE_ICON, layout="wide")
    st.title(texts.TITLE)
    try:
        settings = Settings.from_env()
    except ConfigError:
        logger.exception("invalid configuration")
        st.error(texts.ERROR_DB_READ)
        st.stop()
    path = str(settings.db_path)

    status = data.db_status(path)
    if status in ("missing", "uninitialized"):
        st.caption(texts.subtitle(70, settings.top_n))
        st.info(texts.EMPTY_NO_DB)
        st.stop()
    if status == "error":
        st.error(texts.ERROR_DB_READ)
        st.stop()

    try:
        ver = data.data_version(path)
        overview = data.load_overview(path, ver)
        run_log = data.load_run_log(path, ver)
        latest = None if run_log.empty else run_log.iloc[0].to_dict()
        blocked_http = None
        if latest is not None and latest["status"] == "blocked":
            detail = data.load_latest_run_detail(path, ver, int(latest["run_id"]))
            blocked_http = detail["blocked_http"]
    except data.READ_ERRORS:
        logger.exception("database read failed")
        st.error(texts.ERROR_DB_READ)
        st.stop()

    render_header(overview, latest, blocked_http, settings)
    render_sidebar_totals(overview)
    (runlog_tab,) = st.tabs([texts.TAB_RUNLOG])
    with runlog_tab:
        _guarded(render_runlog_tab, run_log, latest)


main()
