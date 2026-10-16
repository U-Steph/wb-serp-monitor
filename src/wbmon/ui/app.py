"""Streamlit entrypoint of the read-only monitor: ``streamlit run src/wbmon/ui/app.py``.

Run it from the repository root so ``.streamlit/config.toml`` (read from the process cwd) applies.
Imports are absolute on purpose: Streamlit executes this file as a script, where relative imports
fail. The page only reads the database, through the loaders in ``wbmon.ui.data``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime

import pandas as pd
import streamlit as st

from wbmon import clock
from wbmon.config import ConfigError, Settings
from wbmon.ui import charts, data, series, texts

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
        texts.subtitle(series.sheet_size(overview), overview["top_n_latest"] or settings.top_n)
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


def render_sidebar_filters(queries: pd.DataFrame) -> tuple[Mapping | None, str, int]:
    """Query, period and default product count; returns (query row or None, period, N).

    Query text is shown through ``format_func`` (plain text), never as Markdown.
    """
    with st.sidebar:
        st.markdown(texts.FILTERS_TITLE)
        chosen: Mapping | None = None
        if not queries.empty:
            labels = {
                int(q): texts.query_label(int(o), str(t))
                for q, o, t in zip(
                    queries["query_id"], queries["sheet_order"], queries["text"], strict=True
                )
            }
            query_id = st.selectbox(
                texts.QUERY_LABEL, options=list(labels), format_func=labels.__getitem__, key="query"
            )
            row = queries[queries["query_id"] == query_id].iloc[0]
            chosen = {
                "query_id": int(row["query_id"]),
                "sheet_order": int(row["sheet_order"]),
                "text": str(row["text"]),
            }
        # A deselected segment returns None: it means "all time" (UI-SPEC refinement).
        period = (
            st.segmented_control(
                texts.PERIOD_LABEL,
                texts.PERIOD_OPTIONS,
                default=texts.PERIOD_ALL,
                required=True,
                key="period",
            )
            or texts.PERIOD_ALL
        )
        n = st.number_input(
            texts.TOPN_LABEL,
            min_value=1,
            max_value=20,
            value=5,
            step=1,
            help=texts.TOPN_HELP,
            key="topn",
        )
    return chosen, str(period), int(n)


def render_sidebar_totals(overview: Mapping) -> None:
    """The five totals under a divider, below the filters."""
    with st.sidebar:
        st.divider()
        with st.container(border=True):
            for line in series.totals_lines(overview):
                st.caption(line)


def _link_column(label: str = texts.COL_ID) -> object:
    """Product link that shows only the number; the URL is built from the integer id alone."""
    return st.column_config.LinkColumn(label, display_text=r"catalog/(\d+)/detail")


def _table_config(*, characteristics: bool = False, history: bool = False) -> dict[str, object]:
    """Column config shared by the characteristics, history and snapshot tables."""
    cc = st.column_config
    config: dict[str, object] = {
        texts.COL_ID: _link_column(),
        texts.COL_PRICE: cc.NumberColumn(format="localized"),
        texts.COL_RATING: cc.NumberColumn(format="%.1f"),
        texts.COL_FEEDBACKS: cc.NumberColumn(format="localized"),
        texts.COL_DELIVERY: cc.NumberColumn(format="%d", help=texts.DELIVERY_HELP),
        texts.COL_AD: cc.TextColumn(help=texts.AD_HELP),
        texts.COL_POSITION: cc.NumberColumn(format="%d"),
    }
    if characteristics:
        config[texts.COL_POSITION_NOW] = cc.NumberColumn(format="%d")
        config[texts.COL_BEST] = cc.NumberColumn(format="%d")
        config[texts.COL_WORST] = cc.NumberColumn(format="%d")
        config[texts.COL_SHARE] = cc.NumberColumn(format="%.0f%%")
        config[texts.COL_LAST_SEEN] = cc.DatetimeColumn(format="DD.MM.YYYY HH:mm")
    if history:
        config[texts.COL_TIME] = cc.DatetimeColumn(format="DD.MM.YYYY HH:mm")
    return config


def render_query_attempts(path: str, ver: data.DataVersion, query_id: int) -> None:
    """The recent attempts of one query: explains a query that has no stored observations."""
    attempts = data.load_query_attempts(path, ver, query_id)
    cc = st.column_config
    st.dataframe(
        series.attempts_frame(attempts),
        hide_index=True,
        width="stretch",
        placeholder="—",
        column_config={
            texts.COL_TIME: cc.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
            texts.COL_HTTP: cc.NumberColumn(format="%d"),
            texts.COL_PAGES: cc.NumberColumn(format="%d"),
            texts.COL_ITEMS: cc.NumberColumn(format="%d"),
        },
    )


def render_product_tables(
    points: pd.DataFrame,
    selected: list[int],
    options: pd.DataFrame,
    query: Mapping,
    now: datetime,
) -> None:
    """Characteristics, the CSV export and the history expander for the selected period.

    Names, brands and sellers reach the page only inside dataframe cells (never as markdown).
    """
    st.subheader(texts.CHAR_TITLE)
    st.dataframe(
        series.characteristics_frame(points, selected, options),
        hide_index=True,
        width="stretch",
        placeholder="—",
        column_config=_table_config(characteristics=True),
    )
    st.caption(texts.TABLE_NULL_CAPTION)
    has_rows = bool(selected) and bool((points["state"] == series.FOUND).any())
    st.download_button(
        texts.CSV_LABEL,
        data=series.csv_bytes(points, str(query["text"])) if has_rows else b"",
        file_name=series.csv_filename(int(query["sheet_order"]), now),
        mime="text/csv",
        type="primary",
        icon=":material/download:",
        on_click="ignore",
        disabled=not has_rows,
        help=None if has_rows else texts.CSV_DISABLED_HELP,
        key=f"csv_{query['query_id']}",
    )
    st.caption(texts.CSV_CAPTION)
    with st.expander(texts.HISTORY_EXPANDER):
        st.dataframe(
            series.history_frame(points),
            hide_index=True,
            width="stretch",
            height=400,
            placeholder="—",
            column_config=_table_config(history=True),
        )


def render_positions_tab(
    path: str,
    ver: data.DataVersion,
    overview: Mapping,
    query: Mapping | None,
    period: str,
    n: int,
    top_n: int,
) -> None:
    """Product picker, the honest position chart, its captions, the tables and the CSV."""
    if not overview["n_runs"]:
        st.info(texts.BANNER_NO_RUNS)
        return
    if query is None:
        st.info(texts.EMPTY_QUERY_NO_DATA)
        return
    query_id, sheet_order = query["query_id"], query["sheet_order"]
    since = series.period_since(overview["last_started"], period)
    grid = data.load_run_grid(path, ver, query_id, sheet_order, since)
    if grid.empty:
        st.info(texts.EMPTY_PERIOD if period != texts.PERIOD_ALL else texts.EMPTY_QUERY_NO_DATA)
        return
    options = data.load_product_options(path, ver, query_id)
    latest_run = data.load_latest_data_run(path, ver, query_id)
    frame = series.product_options_frame(options, latest_run)
    ids = [int(i) for i in frame["nm_id"]]
    labels = dict(zip(ids, frame["label"], strict=True))
    top_ids = () if latest_run is None else data.load_top_ids(path, ver, latest_run, query_id, n)
    if not ids:
        st.info(texts.EMPTY_QUERY_NO_DATA)
        render_query_attempts(path, ver, query_id)
    selected = st.multiselect(
        texts.PICKER_LABEL,
        options=ids,
        default=series.default_selection(top_ids, ids, n),
        format_func=labels.__getitem__,
        max_selections=series.MAX_SELECTED,
        placeholder=texts.PICKER_PLACEHOLDER,
        select_all=False,
        disabled=not ids,
        key=f"products_{query_id}_{n}",
    )
    if n > series.MAX_SELECTED:
        st.caption(texts.PICKER_CAP_CAPTION)
    if ids and not selected:
        st.info(texts.EMPTY_NOTHING_SELECTED)
    with st.spinner(texts.SPINNER):
        obs = data.load_observations(path, ver, query_id, tuple(sorted(selected)), since)
        points = series.build_points(grid, obs, selected)
        bands = series.band_times(grid)
        fig = charts.position_figure(
            points, selected, bands, series.series_names(options), series.x_range(grid), top_n
        )
        st.plotly_chart(fig, width="stretch", config=charts.CHART_CONFIG)
    st.caption(texts.CHART_CAPTION.format(top_n=top_n))
    st.caption(texts.CHART_SUMMARY.format(k=len(selected), n=len(grid), u=len(bands)))
    if ids:
        render_product_tables(points, selected, options, query, clock.utc_now())


def render_snapshot_tab(
    path: str, ver: data.DataVersion, overview: Mapping, query: Mapping | None
) -> None:
    """The stored top-N of the selected query at a chosen run, with four headline numbers."""
    st.subheader(texts.SNAPSHOT_TITLE)
    if not overview["n_runs"]:
        st.info(texts.BANNER_NO_RUNS)
        return
    if query is None:
        st.info(texts.EMPTY_QUERY_NO_DATA)
        return
    query_id = int(query["query_id"])
    # The sheet text is untrusted: st.text shows it verbatim, st.caption would render Markdown.
    st.text(texts.query_label(int(query["sheet_order"]), str(query["text"])))
    runs = data.load_snapshot_runs(path, ver, query_id)
    if runs.empty:
        st.info(texts.SNAPSHOT_EMPTY)
        render_query_attempts(path, ver, query_id)
        return
    oldest_first = runs.iloc[::-1]
    run_ids = [int(r) for r in oldest_first["run_id"]]
    labels = {
        int(r): texts.fmt_dt_short(series.to_msk_one(str(t)))  # type: ignore[arg-type]
        for r, t in zip(oldest_first["run_id"], oldest_first["started_at"], strict=True)
    }
    statuses = {
        int(r): str(x) for r, x in zip(oldest_first["run_id"], oldest_first["status"], strict=True)
    }
    run_id = st.select_slider(
        texts.SNAPSHOT_SLIDER_LABEL,
        options=run_ids,
        value=run_ids[-1],
        format_func=labels.__getitem__,
        # The newest run id is part of the key: a run that lands while the page is open resets the
        # slider to "latest" instead of leaving it pinned to an hour-old snapshot.
        key=f"snap_{query_id}_{run_ids[-1]}",
    )
    snapshot = data.load_snapshot(path, ver, int(run_id), query_id)
    metrics = series.snapshot_metrics(snapshot, statuses[int(run_id)])
    for column, (label, value) in zip(st.columns(4, gap="small"), metrics.items(), strict=True):
        column.metric(label, value)
    st.dataframe(
        series.snapshot_frame(snapshot),
        hide_index=True,
        width="stretch",
        height=640,
        placeholder="—",
        column_config=_table_config(),
    )


def render_heatmap_block(path: str, ver: data.DataVersion) -> None:
    """Query x run health grid: title, legend and the figure in a 480 px scroll box."""
    st.markdown(texts.HEATMAP_TITLE)
    st.markdown(texts.HEATMAP_LEGEND)
    runs, matrix = data.load_status_matrix(path, ver)
    if runs.empty or matrix.empty:
        st.info(texts.NO_RUNS_RUNLOG)
        return
    queries = data.load_queries(path, ver)
    with st.container(height=480, border=True):
        st.plotly_chart(
            charts.status_heatmap(runs, matrix, queries),
            width="stretch",
            config=charts.CHART_CONFIG,
        )


def render_runlog_tab(
    path: str, ver: data.DataVersion, run_log: pd.DataFrame, latest: Mapping | None
) -> None:
    """Summary badges, the status heatmap and the run table (the latest 200 runs, newest first)."""
    st.subheader(texts.RUNLOG_TITLE)
    if run_log.empty or latest is None:
        st.info(texts.NO_RUNS_RUNLOG)
        return
    st.markdown(texts.summary_badges(series.status_counts(run_log), min(24, len(run_log))))
    render_heatmap_block(path, ver)
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


def render_about_tab(n_queries: int, top_n: int) -> None:
    """The methodology page: our own copy plus two integers from the data, no database text."""
    st.subheader(texts.ABOUT_TITLE)
    st.markdown(texts.ABOUT_INTRO.format(n=n_queries))
    for title, body in (
        (texts.ABOUT_COLLECTED_TITLE, texts.ABOUT_COLLECTED.format(n=n_queries, top_n=top_n)),
        (texts.ABOUT_READING_TITLE, texts.ABOUT_READING),
        (texts.ABOUT_HONESTY_TITLE, texts.ABOUT_HONESTY),
        (texts.ABOUT_LIMITS_TITLE, texts.ABOUT_LIMITS),
        (texts.ABOUT_SOURCE_TITLE, texts.ABOUT_SOURCE),
    ):
        st.subheader(title)
        st.markdown(body)


def main() -> None:
    """Build the page: header, sidebar filters and totals, positions, snapshot, run-log, about."""
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
        st.caption(texts.subtitle(texts.DEFAULT_N_QUERIES, settings.top_n))
        st.info(texts.EMPTY_NO_DB)
        st.stop()
    if status == "error":
        st.error(texts.ERROR_DB_READ)
        st.stop()

    try:
        ver = data.data_version(path)
        overview = {**data.load_overview(path, ver), **data.load_totals(path, ver)}
        run_log = data.load_run_log(path, ver)
        queries = data.load_queries(path, ver)
        latest = None if run_log.empty else run_log.iloc[0].to_dict()
        blocked_http = None
        if latest is not None and latest["status"] == "blocked":
            detail = data.load_latest_run_detail(path, ver, int(latest["run_id"]))
            blocked_http = detail["blocked_http"]
    except data.READ_ERRORS:
        logger.exception("database read failed")
        st.error(texts.ERROR_DB_READ)
        st.stop()

    _guarded(render_header, overview, latest, blocked_http, settings)
    query, period, n = render_sidebar_filters(queries)
    render_sidebar_totals(overview)
    positions_tab, snapshot_tab, runlog_tab, about_tab = st.tabs(
        [texts.TAB_POSITIONS, texts.TAB_SNAPSHOT, texts.TAB_RUNLOG, texts.TAB_ABOUT]
    )
    top_n = int(overview["top_n_latest"] or settings.top_n)
    with positions_tab:
        _guarded(render_positions_tab, path, ver, overview, query, period, n, top_n)
    with snapshot_tab:
        _guarded(render_snapshot_tab, path, ver, overview, query)
    with runlog_tab:
        _guarded(render_runlog_tab, path, ver, run_log, latest)
    with about_tab:
        _guarded(render_about_tab, series.sheet_size(overview), top_n)


main()
