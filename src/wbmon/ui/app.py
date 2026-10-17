"""Streamlit entrypoint of the read-only monitor: ``streamlit run src/wbmon/ui/app.py``.

Run it from the repository root so ``.streamlit/config.toml`` (read from the process cwd) applies.
Imports are absolute on purpose: Streamlit executes this file as a script, where relative imports
fail. The page only reads the database, through the loaders in ``wbmon.ui.data``.

Layout: a left menu with four sections (``st.radio``), a top bar with the collector status pill
and the real/demo switch, and white cards in the main area. The look lives in ``wbmon.ui.theme``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from wbmon import clock
from wbmon.config import ConfigError, Settings
from wbmon.ui import charts, data, demo, series, texts, theme

logger = logging.getLogger("wbmon.ui")

_COUNT_COLUMNS = ("Успешных", "Частичных", "Заблок.", "Ошибок", "Товаров сохранено")
_ALERT_ICONS = {"info": ":material/info:", "warning": ":material/priority_high:"}


def _guarded(render: Callable[..., None], *args: object) -> None:
    """Run one section body; a failure shows the generic copy and keeps the page alive."""
    try:
        render(*args)
    except Exception:
        logger.exception("section %s failed", getattr(render, "__name__", "?"))
        st.error(texts.ERROR_BLOCK)


@st.cache_resource(show_spinner=texts.DEMO_SPINNER)
def _demo_db(real_db_path: str) -> str:
    """Build the synthetic demo database once per web process; returns its (temp) path."""
    return str(demo.ensure_demo_db(real_db_path))


def _set_mode(value: str) -> None:
    """Button callback: switch the data mode (session-only, never persisted)."""
    st.session_state["mode"] = value


def render_pill(settings: Settings, latest: Mapping | None, demo_on: bool = False) -> None:
    """The status pill of the top bar: the scheduler heartbeat, or the demo label in demo mode."""
    if demo_on:
        state, text = "demo", texts.PILL_DEMO
    else:
        heartbeat = data.read_heartbeat(settings.db_path)
        state, text = series.heartbeat_pill(
            heartbeat, clock.utc_now(), latest, stale_s=data.HEARTBEAT_STALE_S
        )
    with st.container(key=f"pill_{state}", width="content"):
        st.markdown(text)


def render_header(
    section: str,
    overview: Mapping,
    latest: Mapping | None,
    blocked_http: int | None,
    settings: Settings,
    demo_on: bool,
) -> None:
    """Section title, subtitle and one status card: the banner, or the purple card in demo mode."""
    now = clock.utc_now()
    st.title(texts.SECTION_TITLES[section])
    st.caption(
        texts.subtitle(series.sheet_size(overview), overview["top_n_latest"] or settings.top_n)
    )
    if demo_on:
        with st.container(key="demo_card", horizontal=True, vertical_alignment="center"):
            st.markdown(texts.DEMO_CARD)
            st.button(texts.DEMO_OFF, key="demo_off", on_click=_set_mode, args=(texts.MODE_REAL,))
        return
    shown = series.banner(
        latest, overview["n_ok_runs"], blocked_http, now, n_runs=int(overview["n_runs"])
    )
    if shown is not None:
        kind, message = shown
        card = {"info": st.info, "warning": st.warning, "error": st.error}[kind]
        card(texts.no_lead_icon(message), icon=_ALERT_ICONS.get(kind, ":material/priority_high:"))


def render_query_select(queries: pd.DataFrame, ns: str) -> Mapping | None:
    """The query picker; the text is shown through ``format_func`` (plain text), never Markdown."""
    if queries.empty:
        return None
    labels = {
        int(q): texts.query_label(int(o), str(t))
        for q, o, t in zip(
            queries["query_id"], queries["sheet_order"], queries["text"], strict=True
        )
    }
    query_id = st.selectbox(
        texts.QUERY_LABEL,
        options=list(labels),
        format_func=labels.__getitem__,
        key=f"{ns}query",
        persist_state="page",
    )
    row = queries[queries["query_id"] == query_id].iloc[0]
    return {
        "query_id": int(row["query_id"]),
        "sheet_order": int(row["sheet_order"]),
        "text": str(row["text"]),
    }


def render_empty_state(kind: str, overview: Mapping, latest: Mapping | None, top_n: int) -> None:
    """The card shown while the database holds no product: real counts and the demo button."""
    positions = kind == "positions"
    title = texts.EMPTY_POSITIONS_TITLE if positions else texts.EMPTY_SNAPSHOT_TITLE
    text = (
        texts.EMPTY_POSITIONS_TEXT if positions else texts.EMPTY_SNAPSHOT_TEXT.format(top_n=top_n)
    )
    with st.container(key="card_empty"):
        with st.container(key="empty_orb", width="content"):
            st.markdown("🛡️")
        st.subheader(title)
        st.caption(text)
        with st.container(key="empty_chips", horizontal=True, horizontal_alignment="center"):
            for chip in series.empty_chips(overview, latest):
                st.markdown(chip, width="content")
        st.button(
            texts.DEMO_ON,
            type="primary",
            key="demo_on",
            on_click=_set_mode,
            args=(texts.MODE_DEMO,),
        )


def _link_column(label: str = texts.COL_ID) -> object:
    """Product link that shows only the number; the URL is built from the integer id alone."""
    return st.column_config.LinkColumn(label, display_text=r"catalog/(\d+)/detail")


def _table_config(*, characteristics: bool = False, history: bool = False) -> dict[str, object]:
    """Column config shared by the characteristics, history and snapshot tables."""
    cc = st.column_config
    config: dict[str, object] = {
        texts.COL_ID: _link_column(),
        # "%d", not "localized": the browser locale would print "1,073" in the Russian UI (VIS-2);
        # the unit is in the column title «Цена, ₽».
        texts.COL_PRICE: cc.NumberColumn(format="%d"),
        texts.COL_RATING: cc.NumberColumn(format="%.1f"),
        texts.COL_FEEDBACKS: cc.NumberColumn(format="%d"),
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


def render_characteristics(
    points: pd.DataFrame, selected: list[int], options: pd.DataFrame
) -> None:
    """Characteristics table with its captions.

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
    st.caption(texts.CSV_CAPTION)


def render_csv_button(
    points: pd.DataFrame, selected: list[int], query: Mapping, now: datetime, ns: str
) -> None:
    """The ghost CSV export of the selected period (the filter row, last column)."""
    has_rows = bool(selected) and bool((points["state"] == series.FOUND).any())
    st.download_button(
        texts.CSV_LABEL,
        data=series.csv_bytes(points, str(query["text"])) if has_rows else b"",
        file_name=series.csv_filename(int(query["sheet_order"]), now),
        mime="text/csv",
        type="secondary",
        icon=":material/download:",
        on_click="ignore",
        disabled=not has_rows,
        help=None if has_rows else texts.CSV_DISABLED_HELP,
        key=f"{ns}csv_{query['query_id']}",
    )


def render_history(points: pd.DataFrame) -> None:
    """The collapsed history expander: every observation of the selection, newest first."""
    with st.expander(texts.HISTORY_EXPANDER):
        st.dataframe(
            series.history_frame(points),
            hide_index=True,
            width="stretch",
            height=400,
            placeholder="—",
            column_config=_table_config(history=True),
        )


def render_positions_section(
    path: str,
    ver: data.DataVersion,
    overview: Mapping,
    queries: pd.DataFrame,
    latest: Mapping | None,
    top_n: int,
    ns: str,
    demo_on: bool = False,
) -> None:
    """Filter row, product picker, the honest position chart, its tables and the CSV."""
    if not int(overview["n_obs"]):
        render_empty_state("positions", overview, latest, top_n)
        return
    with st.container(key="filters"):
        c_query, c_period, c_topn, c_csv = st.columns([4, 3, 2, 1.5], vertical_alignment="bottom")
        with c_query:
            query = render_query_select(queries, ns)
        with c_period:
            # A deselected segment returns None: it means "all time" (UI-SPEC refinement).
            period = str(
                st.segmented_control(
                    texts.PERIOD_LABEL,
                    texts.PERIOD_OPTIONS,
                    default=texts.PERIOD_ALL,
                    required=True,
                    key="period",
                    persist_state="page",
                )
                or texts.PERIOD_ALL
            )
        with c_topn:
            n = int(
                st.number_input(
                    texts.TOPN_LABEL,
                    min_value=1,
                    max_value=20,
                    value=5,
                    step=1,
                    help=texts.TOPN_HELP,
                    key="topn",
                    persist_state="page",
                )
            )
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
        top_ids = (
            () if latest_run is None else data.load_top_ids(path, ver, latest_run, query_id, n)
        )
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
            key=f"{ns}products_{query_id}_{n}",
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
                points,
                selected,
                bands,
                series.series_names(options),
                series.x_range(grid),
                top_n,
                texts.DEMO_WATERMARK if demo_on else None,
            )
        if ids:
            with c_csv:
                render_csv_button(points, selected, query, clock.utc_now(), ns)
    with st.container(key="card_chart"):
        st.subheader(texts.CHART_TITLE)
        st.plotly_chart(fig, width="stretch", config=charts.CHART_CONFIG)
        st.caption(texts.CHART_CAPTION.format(top_n=top_n))
        st.caption(texts.CHART_SUMMARY.format(k=len(selected), n=len(grid), u=len(bands)))
    if ids:
        with st.container(key="card_table"):
            render_characteristics(points, selected, options)
        render_history(points)


def render_snapshot_section(
    path: str,
    ver: data.DataVersion,
    overview: Mapping,
    queries: pd.DataFrame,
    latest: Mapping | None,
    top_n: int,
    ns: str,
) -> None:
    """The stored top-N of the selected query at a chosen run, with four headline numbers."""
    if not int(overview["n_obs"]):
        render_empty_state("snapshot", overview, latest, top_n)
        return
    with st.container(key="filters"):
        c_query, c_slider = st.columns([4, 5], vertical_alignment="bottom")
        with c_query:
            query = render_query_select(queries, ns)
        if query is None:
            st.info(texts.EMPTY_QUERY_NO_DATA)
            return
        query_id = int(query["query_id"])
        runs = data.load_snapshot_runs(path, ver, query_id)
        run_id = 0
        if not runs.empty:
            oldest_first = runs.iloc[::-1]
            run_ids = [int(r) for r in oldest_first["run_id"]]
            labels = {
                int(r): texts.fmt_dt_short(series.to_msk_one(str(t)))  # type: ignore[arg-type]
                for r, t in zip(oldest_first["run_id"], oldest_first["started_at"], strict=True)
            }
            statuses = {
                int(r): str(x)
                for r, x in zip(oldest_first["run_id"], oldest_first["status"], strict=True)
            }
            with c_slider:
                run_id = st.select_slider(
                    texts.SNAPSHOT_SLIDER_LABEL,
                    options=run_ids,
                    value=run_ids[-1],
                    format_func=labels.__getitem__,
                    # The newest run id is part of the key: a run that lands while the page is open
                    # resets the slider to "latest" instead of pinning an hour-old snapshot.
                    key=f"{ns}snap_{query_id}_{run_ids[-1]}",
                )
    # The sheet text is untrusted: md_literal escapes every Markdown special, so st.caption (the
    # theme's body font, not st.text's monospace) shows it exactly as typed.
    query_caption = texts.md_literal(
        texts.query_label(int(query["sheet_order"]), str(query["text"]))
    )
    if runs.empty:
        st.caption(query_caption)
        st.info(texts.SNAPSHOT_EMPTY)
        render_query_attempts(path, ver, query_id)
        return
    snapshot = data.load_snapshot(path, ver, int(run_id), query_id)
    metrics = series.snapshot_metrics(snapshot, statuses[int(run_id)])
    for column, (label, value) in zip(st.columns(4, gap="small"), metrics.items(), strict=True):
        column.metric(label, value)
    with st.container(key="card_snapshot"):
        st.subheader(texts.SNAPSHOT_TITLE)
        st.caption(query_caption)
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


def render_runlog_section(
    path: str,
    ver: data.DataVersion,
    overview: Mapping,
    run_log: pd.DataFrame,
    latest: Mapping | None,
) -> None:
    """Totals, summary badges, the status heatmap and the run table (latest 200, newest first)."""
    with st.container(key="card_summary"):
        colour, text = series.freshness(overview["last_ok_finished"], clock.utc_now())
        st.badge(text, color=colour)
        for line in series.totals_lines(overview):
            st.caption(line)
    if run_log.empty or latest is None:
        st.info(texts.NO_RUNS_RUNLOG)
        return
    with st.container(key="card_heatmap"):
        st.markdown(texts.summary_badges(series.status_counts(run_log), min(24, len(run_log))))
        render_heatmap_block(path, ver)
    with st.container(key="card_runs"):
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


def render_about_section(n_queries: int, top_n: int) -> None:
    """The methodology page: our own copy plus two integers from the data, no database text."""
    with st.container(key="card_about"):
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
    """Build the page: left menu, top bar, then the header and the chosen section."""
    st.set_page_config(
        page_title=texts.PAGE_TITLE,
        page_icon=texts.PAGE_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    theme.inject()
    st.session_state.setdefault("mode", texts.MODE_REAL)

    with st.sidebar.container(key="nav_label"):
        st.caption(texts.NAV_LABEL)
    section = st.sidebar.radio(
        texts.NAV_LABEL, texts.NAV_OPTIONS, key="section", label_visibility="collapsed"
    )

    with st.container(key="topbar"):
        left, right = st.columns([3, 1.3], vertical_alignment="center")
        with right, st.container(horizontal=True, horizontal_alignment="right"):
            mode = st.segmented_control(
                texts.MODE_LABEL,
                texts.MODE_OPTIONS,
                key="mode",
                required=True,
                label_visibility="collapsed",
            )
        with left:
            pill_box = st.container(horizontal=True, vertical_alignment="center")
            with pill_box:
                st.markdown(f"**{texts.TITLE}**", width="content")

    try:
        settings = Settings.from_env()
    except ConfigError:
        logger.exception("invalid configuration")
        st.error(texts.ERROR_DB_READ)
        st.stop()
    demo_on = mode == texts.MODE_DEMO
    ns = "demo_" if demo_on else ""
    path = str(settings.db_path)
    if demo_on:
        try:
            path = _demo_db(path)
            if not Path(path).is_file():  # the temp file was cleaned up behind the cache
                _demo_db.clear()
                path = _demo_db(str(settings.db_path))
        except (ValueError, OSError, *data.READ_ERRORS):
            logger.exception("demo database failed")
            with pill_box:
                _guarded(render_pill, settings, None, True)
            st.error(texts.DEMO_ERROR)
            st.stop()

    status = data.db_status(path)
    if status in ("missing", "uninitialized"):
        with pill_box:
            _guarded(render_pill, settings, None, demo_on)
        st.title(texts.SECTION_TITLES[section])
        st.caption(texts.subtitle(texts.DEFAULT_N_QUERIES, settings.top_n))
        st.info(texts.EMPTY_NO_DB)
        st.stop()
    if status == "error":
        with pill_box:
            _guarded(render_pill, settings, None, demo_on)
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
        with pill_box:
            _guarded(render_pill, settings, None, demo_on)
        st.error(texts.ERROR_DB_READ)
        st.stop()

    with pill_box:
        _guarded(render_pill, settings, latest, demo_on)
    top_n = int(overview["top_n_latest"] or settings.top_n)
    _guarded(render_header, section, overview, latest, blocked_http, settings, demo_on)
    if section == texts.TAB_POSITIONS:
        _guarded(render_positions_section, path, ver, overview, queries, latest, top_n, ns, demo_on)
    elif section == texts.TAB_SNAPSHOT:
        _guarded(render_snapshot_section, path, ver, overview, queries, latest, top_n, ns)
    elif section == texts.TAB_RUNLOG:
        _guarded(render_runlog_section, path, ver, overview, run_log, latest)
    else:
        _guarded(render_about_section, series.sheet_size(overview), top_n)


main()
