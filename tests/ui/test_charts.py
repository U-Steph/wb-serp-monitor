"""The pure Plotly position chart: axis, honest gaps, bands, ad diamonds, hover, escaping, speed."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ui.dbbuilder import (
    NM,
    Q1,
    Q2,
    Q3,
    RunSpec,
    build_db,
    failed_result,
    ok_result,
    scenario_all_blocked,
    scenario_mixed,
)
from wbmon.clock import iso_z, utc_now
from wbmon.ui import charts, data, series, texts

A, B, C, D, E, F = (NM[k] for k in "ABCDEF")
TOP5 = [A, B, D, C, E]
NAMES = {nm_id: f"ID {nm_id} · ТЕСТ-БРЕНД" for nm_id in NM.values()}


@pytest.fixture
def mixed(db_path):
    """Figure inputs of the mixed scenario's first query: run ids, grid, points for the top 5."""
    built = scenario_mixed(db_path)
    path, q1 = str(db_path), built.query_ids[Q1]
    grid = data.load_run_grid_raw(path, q1, 1, "")
    obs = data.load_observations_raw(path, q1, tuple(TOP5), "")
    points = series.build_points(grid, obs, TOP5)
    return built.run_ids, grid, points


def figure_of(grid, points, nm_ids=TOP5, names=NAMES, top_n=200):
    return charts.position_figure(
        points, nm_ids, series.band_times(grid), names, series.x_range(grid), top_n
    )


def trace_of(fig, nm_id):
    return next(t for t in fig.data if t.name == NAMES[nm_id])


def x_of(grid, run_id):
    """The figure x string of a run (Moscow wall clock)."""
    prepared = series.prepare_grid(grid)
    stamp = prepared.loc[prepared["run_id"] == run_id, "t_msk"].iloc[0]
    return stamp.strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------------------- Y axis


def test_y_axis_puts_position_one_on_top_with_headroom():
    spec = charts.y_axis_spec([1, 5], 200)
    assert spec["range"][0] > spec["range"][1]
    assert spec["range"][1] == -0.5
    assert spec["tickmode"] == "array"
    assert spec["tickvals"][0] == 1
    assert all(v >= 1 for v in spec["tickvals"])
    assert spec["title"]["text"] == "Позиция в выдаче"


def test_y_axis_far_from_the_top_keeps_a_positive_top_bound():
    spec = charts.y_axis_spec([50, 60], 200)
    bottom, top = spec["range"]
    assert top == 48
    assert bottom == 62
    assert spec["tickvals"]
    assert all(top <= v <= bottom for v in spec["tickvals"])


def test_y_axis_without_found_points_spans_the_whole_top_n():
    spec = charts.y_axis_spec([], 200)
    assert spec["range"] == [200.5, -0.5]
    assert spec["tickvals"][0] == 1
    assert all(1 <= v <= 200 for v in spec["tickvals"])


@pytest.mark.parametrize("positions", [[1], [1, 200], [3, 4], [199, 200], [1, 39], [2, 100]])
def test_y_axis_never_shows_a_tick_below_one(positions):
    spec = charts.y_axis_spec(positions, 200)
    assert min(spec["tickvals"]) >= 1
    assert spec["range"][1] <= min(positions)


# ------------------------------------------------------------------------------ traces


def test_legend_names_are_cut_to_fit_one_fixed_width_entry():
    short = "ID 900000101 · ТЕСТ-БРЕНД"
    assert charts.legend_name(short) == short
    long_name = "ID 900000101 · " + "Очень длинное название бренда " * 3
    cut = charts.legend_name(long_name)
    assert len(cut) == charts.LEGEND_NAME_CHARS and cut.endswith("…")
    assert long_name.startswith(cut[:-1])


def test_one_trace_per_product_and_two_legend_only_entries(mixed):
    _, grid, points = mixed
    fig = figure_of(grid, points)
    assert len(fig.data) == 7
    legend = fig.data[-2:]
    assert [t.name for t in legend] == [charts.LEGEND_AD, charts.LEGEND_BAND]
    assert all(tuple(t.x) == (None,) and tuple(t.y) == (None,) for t in legend)
    for trace in fig.data[:5]:
        assert trace.mode == "lines+markers"
        assert trace.connectgaps is False


def test_gaps_are_none_values_never_numbers(mixed):
    run_ids, grid, points = mixed
    r1, r2, r3, r4, r5, r6 = run_ids
    trace = trace_of(figure_of(grid, points), B)
    y = dict(zip(trace.x, trace.y, strict=True))
    assert y[x_of(grid, r2)] is None
    assert y[x_of(grid, r3)] is None
    assert y[x_of(grid, r4)] is None
    assert y[x_of(grid, r1)] == 2
    assert y[x_of(grid, r6)] == 2
    assert isinstance(trace.y, tuple)  # a plain list, not an ndarray with a base64 payload
    assert len(trace.x) == 6  # the full run grid, not only the runs where B was found


def test_found_points_inside_a_partial_run_are_drawn(mixed):
    run_ids, grid, points = mixed
    r4 = run_ids[3]
    fig = figure_of(grid, points)
    assert dict(zip(trace_of(fig, A).x, trace_of(fig, A).y, strict=True))[x_of(grid, r4)] == 2
    assert dict(zip(trace_of(fig, C).x, trace_of(fig, C).y, strict=True))[x_of(grid, r4)] == 1


def test_ads_are_diamonds_and_the_rest_circles(mixed):
    run_ids, grid, points = mixed
    trace = trace_of(figure_of(grid, points), A)
    symbols = dict(zip(trace.x, trace.marker.symbol, strict=True))
    sizes = dict(zip(trace.x, trace.marker.size, strict=True))
    r3 = x_of(grid, run_ids[2])
    assert symbols[r3] == "diamond"
    assert sizes[r3] == 10
    others = [x for x in trace.x if x != r3]
    assert all(symbols[x] == "circle" and sizes[x] == 7 for x in others)


def test_hover_data_has_id_price_and_ad_fragment(mixed):
    run_ids, grid, points = mixed
    fig = figure_of(grid, points)
    c = dict(zip(trace_of(fig, C).x, trace_of(fig, C).customdata, strict=True))
    assert list(c[x_of(grid, run_ids[4])]) == [C, "—", ""]  # unknown price is a dash, not 0
    e = dict(zip(trace_of(fig, E).x, trace_of(fig, E).customdata, strict=True))
    assert e[x_of(grid, run_ids[5])][2] == "<br>реклама"
    assert e[x_of(grid, run_ids[0])][2] == ""
    a = dict(zip(trace_of(fig, A).x, trace_of(fig, A).customdata, strict=True))
    assert a[x_of(grid, run_ids[0])][0] == A
    assert a[x_of(grid, run_ids[0])][1] == texts.fmt_price(1001.0) == "1\u202f001 ₽"


def test_hover_template_matches_the_contract(mixed):
    _, grid, points = mixed
    template = figure_of(grid, points).data[0].hovertemplate
    assert template.startswith("%{x|%d.%m.%Y %H:%M} МСК<br>ID %{customdata[0]}<br>Позиция %{y}")
    assert template.endswith("Цена %{customdata[1]}%{customdata[2]}<extra>%{fullData.name}</extra>")


def test_x_is_moscow_wall_clock_without_a_zone(mixed):
    run_ids, grid, points = mixed
    fig = figure_of(grid, points)
    r6 = run_ids[-1]
    utc = pd.to_datetime(grid.loc[grid["run_id"] == r6, "started_at"].iloc[0], utc=True)
    shown = pd.Timestamp(x_of(grid, r6))
    assert shown - utc.tz_localize(None) == pd.Timedelta(hours=3)
    text = fig.to_json()
    spec = json.loads(text)
    for trace in spec["data"]:
        for value in trace["x"]:
            assert value is None or ("+03:00" not in value and not value.endswith("Z"))


def test_colours_follow_the_palette_in_selection_order_and_late_series_are_dashed():
    assert len(charts.SERIES_COLORS) == 15
    assert charts.SERIES_COLORS[0] == "#1F77B4" and charts.SERIES_COLORS[14] == "#862E9C"
    ids = list(range(1000, 1012))
    grid = pd.DataFrame(
        {
            "run_id": [1],
            "started_at": ["2026-10-16T09:00:00Z"],
            "q_status": pd.array(["ok"], dtype="string"),
            "is_complete": pd.array([1], dtype="Int64"),
            "items_collected": pd.array([12], dtype="Int64"),
        }
    )
    obs = pd.DataFrame(
        {
            "run_id": [1] * 12,
            "nm_id": ids,
            "position": list(range(1, 13)),
            "is_promoted": [0] * 12,
            "price_rub": [10.0] * 12,
        }
    )
    points = series.build_points(grid, obs, ids)
    fig = charts.position_figure(points, ids, series.band_times(grid), {}, series.x_range(grid), 50)
    for i in range(12):
        assert fig.data[i].line.color == charts.SERIES_COLORS[i]
        assert fig.data[i].line.dash == ("dash" if i >= 9 else "solid")
    assert fig.data[0].name == f"ID {ids[0]}"


def test_trace_names_come_from_the_names_mapping(mixed):
    _, grid, points = mixed
    fig = figure_of(grid, points)
    assert [t.name for t in fig.data[:5]] == [NAMES[i] for i in TOP5]


# ------------------------------------------------------------------------------ bands


def test_bands_are_one_shapes_list_with_the_contract_properties(mixed):
    run_ids, grid, points = mixed
    fig = figure_of(grid, points)
    shapes = fig.layout.shapes
    assert len(shapes) == 2
    r2, r4 = run_ids[1], run_ids[3]
    for shape, run_id in zip(shapes, (r2, r4), strict=True):
        middle = pd.Timestamp(x_of(grid, run_id))
        assert shape.type == "rect"
        assert (shape.xref, shape.yref) == ("x", "paper")
        assert (shape.y0, shape.y1) == (0, 1)
        assert shape.fillcolor == charts.BAND_FILL
        assert shape.line.width == 0
        assert shape.layer == "below"
        assert pd.Timestamp(shape.x0) == middle - pd.Timedelta(minutes=30)
        assert pd.Timestamp(shape.x1) == middle + pd.Timedelta(minutes=30)


def test_a_complete_run_where_a_product_is_absent_has_no_band(mixed):
    run_ids, grid, points = mixed
    r3 = pd.Timestamp(x_of(grid, run_ids[2]))
    for shape in figure_of(grid, points).layout.shapes:
        assert not (pd.Timestamp(shape.x0) <= r3 <= pd.Timestamp(shape.x1))


# ------------------------------------------------------------------------------ layout


def test_layout_follows_the_contract(mixed):
    _, grid, points = mixed
    layout = figure_of(grid, points).layout
    assert layout.height == 520
    assert layout.hovermode == "closest"
    assert layout.legend.orientation == "h"
    assert layout.legend.y == -0.18
    # VIS-1: a fixed entry width, or Plotly stacks the entries before the web font is measured
    assert layout.legend.entrywidthmode == "pixels"
    assert layout.legend.entrywidth == charts.LEGEND_ENTRY_PX == 250
    assert (layout.margin.l, layout.margin.r, layout.margin.t, layout.margin.b) == (64, 16, 16, 88)
    assert layout.font.size == 14
    assert layout.font.color == "#1A1D21"
    assert layout.xaxis.title.text == "Время сбора, МСК"
    assert layout.xaxis.tickformat == "%d.%m<br>%H:%M"
    assert layout.yaxis.range[0] > layout.yaxis.range[1]
    assert layout.yaxis.range[1] == -0.5
    assert layout.title.text is None


def test_chart_config_hides_the_selection_tools():
    assert charts.CHART_CONFIG["displaylogo"] is False
    assert charts.CHART_CONFIG["modeBarButtonsToRemove"] == ["select2d", "lasso2d", "autoScale2d"]


# ------------------------------------------------------------------------------ escaping


def test_esc_neutralises_markup_and_unknowns():
    assert charts.esc('<a href="x">&</a>') == '&lt;a href="x"&gt;&amp;&lt;/a&gt;'
    assert charts.esc(None) == ""
    assert charts.esc(pd.NA) == ""
    assert charts.esc(float("nan")) == ""
    assert charts.esc(5) == "5"


def test_hostile_marketplace_text_cannot_become_markup(mixed):
    _, grid, points = mixed
    points = points.copy()
    points["name"] = "<b>bold</b>"
    points["brand"] = '<a href="https://evil.example">x</a>'
    names = {i: f'ID {i} · <a href="https://evil.example">x</a>' for i in TOP5}
    fig = figure_of(grid, points, names=names)
    text = fig.to_json()
    assert "<a " not in text
    assert "<b>" not in text
    assert "&lt;a href=" in text


# ------------------------------------------------------------------------------ skeleton


def test_empty_selection_still_draws_bands_and_legend(mixed):
    _, grid, points = mixed
    fig = charts.position_figure(
        points.iloc[0:0], [], series.band_times(grid), {}, series.x_range(grid), 200
    )
    assert [t.name for t in fig.data] == [charts.LEGEND_AD, charts.LEGEND_BAND]
    assert len(fig.layout.shapes) == 2
    assert list(fig.layout.yaxis.range) == [200.5, -0.5]


def test_selection_with_only_unknown_points_is_an_empty_skeleton(mixed):
    run_ids, grid, points = mixed
    only_failed = points[points["state"] == series.UNKNOWN]
    fig = figure_of(grid, only_failed, nm_ids=[B])
    assert all(v is None for v in fig.data[0].y)
    assert list(fig.layout.yaxis.range) == [200.5, -0.5]
    assert len(fig.layout.shapes) == 2


# ------------------------------------------------------------------------------ speed


def test_a_month_of_failed_runs_builds_fast():
    n = 720
    base = datetime(2026, 9, 16, 0, 0, 0)
    starts = [(base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(n)]
    ok = [i % 2 == 0 for i in range(n)]
    grid = pd.DataFrame(
        {
            "run_id": list(range(1, n + 1)),
            "started_at": starts,
            "q_status": pd.array(["ok" if o else "blocked" for o in ok], dtype="string"),
            "is_complete": pd.array([1 if o else 0 for o in ok], dtype="Int64"),
            "items_collected": pd.array([15 if o else 0 for o in ok], dtype="Int64"),
        }
    )
    ids = list(range(5000, 5015))
    rows = [(r, nm, k + 1) for r in range(1, n + 1, 2) for k, nm in enumerate(ids)]
    obs = pd.DataFrame(
        {
            "run_id": [r[0] for r in rows],
            "nm_id": [r[1] for r in rows],
            "position": [r[2] for r in rows],
            "is_promoted": [0] * len(rows),
            "price_rub": [99.0] * len(rows),
        }
    )
    points = series.build_points(grid, obs, ids)
    times = series.band_times(grid)
    assert len(times) == n // 2

    started = time.perf_counter()
    shapes = charts.band_shapes(pd.concat([times] * 2, ignore_index=True))
    assert time.perf_counter() - started < 0.25
    assert len(shapes) == n

    started = time.perf_counter()
    fig = charts.position_figure(points, ids, times, {}, series.x_range(grid), 200)
    assert time.perf_counter() - started < 1.0
    assert len(fig.data) == 17
    assert len(fig.layout.shapes) == n // 2


# ---------------------------------------------------------------------- status heatmap


def heatmap_of(builder, *args, **kwargs):
    built = builder(*args, **kwargs)
    path = str(args[0])
    runs, matrix = data.load_status_matrix_raw(path)
    queries = data.load_queries_raw(path)
    return built, charts.status_heatmap(runs, matrix, queries), runs, matrix, queries


@pytest.fixture
def mixed_heatmap(db_path):
    return heatmap_of(scenario_mixed, db_path)


def glyph_cells(fig) -> dict[tuple[int, int], tuple[str, str]]:
    """``{(x, y): (glyph, colour)}`` read from the text overlays of a status heatmap."""
    cells: dict[tuple[int, int], tuple[str, str]] = {}
    for trace in fig.data[1:]:
        assert trace.type == "scatter" and trace.mode == "text"
        assert trace.hoverinfo == "skip" and trace.showlegend is False
        assert trace.textfont.size == 14
        for x, y, glyph in zip(trace.x, trace.y, trace.text, strict=True):
            assert (x, y) not in cells
            cells[(x, y)] = (glyph, trace.textfont.color)
    return cells


def test_heatmap_cells_are_codes_and_glyphs_oldest_to_newest(mixed_heatmap):
    _built, fig, *_ = mixed_heatmap
    trace = fig.data[0]
    assert trace.type == "heatmap"
    assert [list(row) for row in trace.z] == [
        [1, 3, 1, 2, 1, 1],
        [1, 1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1, 1],
    ]
    cells = glyph_cells(fig)
    assert [cells[(x, 0)][0] for x in range(6)] == ["✓", "✕", "✓", "~", "✓", "✓"]
    assert len(cells) == 18
    assert trace.text is None and trace.texttemplate is None  # the overlays carry the glyphs
    assert trace.showscale is False
    assert (trace.zmin, trace.zmax) == (-0.5, 5.5)
    assert (trace.xgap, trace.ygap) == (1, 1)


def test_heatmap_glyph_colours_follow_the_status_table(db_path):
    """UI-4: white glyphs on the dark fills, ink on amber; set explicitly, never auto-contrast."""
    started = "2026-10-16T09:00:00Z"
    results = {
        Q1: ok_result(started, (A,)),
        Q2: ok_result(started, (A,), complete=False, status="partial", error_class="http_5xx"),
        Q3: failed_result(started),
    }
    build_db(db_path, [RunSpec(started, results, n_queries=3)])
    path = str(db_path)
    runs, matrix = data.load_status_matrix_raw(path)
    fig = charts.status_heatmap(runs, matrix, data.load_queries_raw(path))
    cells = glyph_cells(fig)
    assert cells[(0, 0)] == ("✓", "#FFFFFF")
    assert cells[(0, 1)] == ("~", "#1A1D21")
    assert cells[(0, 2)] == ("✕", "#FFFFFF")


def test_every_status_style_has_an_explicit_glyph_colour():
    assert set(charts.GLYPH_COLORS) == set(charts.STATUS_STYLE)
    assert charts.GLYPH_COLORS["partial"] == "#1A1D21"
    assert {v for k, v in charts.GLYPH_COLORS.items() if k != "partial"} == {"#FFFFFF"}


def test_heatmap_colorscale_is_stepped_and_matches_the_status_table():
    scale = charts.heatmap_colorscale()
    assert len(scale) == 12
    assert [c for _, c in scale[::2]] == [
        "#FFFFFF",
        "#2B8A3E",
        "#F2C037",
        "#C92A2A",
        "#D9480F",
        "#6C757D",
    ]
    for i in range(6):
        assert scale[2 * i][0] == pytest.approx(i / 6)
        assert scale[2 * i + 1][0] == pytest.approx((i + 1) / 6)
        assert scale[2 * i][1] == scale[2 * i + 1][1]
    assert charts.MISSING_STYLE == (0, "#FFFFFF", "")
    assert {k: v[2] for k, v in charts.STATUS_STYLE.items()} == {
        "ok": "✓",
        "partial": "~",
        "blocked": "✕",
        "rate_limited": "!",
        "error": "E",
    }


def test_heatmap_figure_colorscale_is_the_helper(mixed_heatmap):
    _built, fig, *_ = mixed_heatmap
    assert [list(stop) for stop in fig.data[0].colorscale] == charts.heatmap_colorscale()


def test_heatmap_layout_follows_the_contract(mixed_heatmap):
    _built, fig, *_ = mixed_heatmap
    layout = fig.layout
    assert layout.height == 20 * 3 + 96 == 156
    assert (layout.margin.l, layout.margin.r, layout.margin.t, layout.margin.b) == (248, 16, 92, 4)
    assert layout.plot_bgcolor == "#E9ECEF"
    assert layout.xaxis.side == "top"
    assert layout.xaxis.tickangle == -90
    assert layout.xaxis.tickmode == "array"
    assert layout.yaxis.autorange == "reversed"
    assert layout.yaxis.tickmode == "array"
    assert layout.title.text is None


def test_heatmap_ticks_are_msk_run_labels_and_truncated_queries(mixed_heatmap):
    _built, fig, runs, *_ = mixed_heatmap
    expected = [
        texts.fmt_dt_short(series.to_msk_one(str(t))) for t in list(runs["started_at"])[::-1]
    ]
    assert list(fig.layout.xaxis.ticktext) == expected
    ticks = list(fig.layout.yaxis.ticktext)
    assert ticks[0] == "01. постельное бельё 2-спаль…"
    assert ticks[2] == "03. подушка 50x70"


def test_heatmap_hover_names_the_attempt(mixed_heatmap):
    _built, fig, runs, *_ = mixed_heatmap
    trace = fig.data[0]
    assert trace.hovertemplate == "%{customdata}<extra></extra>"
    blocked = trace.customdata[0][1]
    assert Q1 in blocked
    assert "Сбор " in blocked and " МСК" in blocked
    assert "Статус: ⛔ Заблокировано" in blocked
    assert "Товаров: 0" in blocked
    assert "HTTP: 403" in blocked
    ok = trace.customdata[0][0]
    assert "Статус: ✅ Успешно" in ok
    assert "HTTP: —" in ok
    stamp = texts.fmt_dt(series.to_msk_one(str(list(runs["started_at"])[-1])))
    assert f"Сбор {stamp} МСК" in ok


def test_heatmap_cell_without_an_attempt_is_empty(db_path):
    started = iso_z(utc_now() - timedelta(minutes=20))
    limited = RunSpec(started, {Q1: ok_result(started, (A,))}, trigger="manual", n_queries=1)
    build_db(db_path, [limited])
    path = str(db_path)
    queries = data.load_queries_raw(path)
    runs, matrix = data.load_status_matrix_raw(path)
    assert len(matrix) == 1  # only Q1 was attempted
    trace = charts.status_heatmap(runs, matrix, queries).data[0]
    assert [list(row) for row in trace.z] == [[1], [0], [0]]
    assert glyph_cells(charts.status_heatmap(runs, matrix, queries)) == {(0, 0): ("✓", "#FFFFFF")}
    assert f"{Q2}<br>" in trace.customdata[1][0]
    assert "Статус: пусто — запрос не выполнялся" in trace.customdata[1][0]


def test_heatmap_escapes_a_hostile_query_text(db_path):
    hostile = '<a href="https://evil.example">x</a>'
    started = "2026-10-16T09:00:00Z"
    build_db(
        db_path,
        [RunSpec(started, {hostile: ok_result(started, (A,))})],
        queries=(hostile,),
    )
    path = str(db_path)
    runs, matrix = data.load_status_matrix_raw(path)
    fig = charts.status_heatmap(runs, matrix, data.load_queries_raw(path))
    raw = fig.to_json()
    assert "<a " not in raw
    assert "&lt;a href" in raw


def test_heatmap_of_an_all_blocked_history_is_a_full_red_field(db_path):
    _built, fig, *_ = heatmap_of(scenario_all_blocked, db_path)
    trace = fig.data[0]
    assert all(code == 3 for row in trace.z for code in row)
    cells = glyph_cells(fig)
    assert len(cells) == 9 and set(cells.values()) == {("✕", "#FFFFFF")}
    assert len(trace.z) == 3 and len(trace.z[0]) == 3


def test_heatmap_with_fewer_runs_is_not_padded(db_path):
    started = "2026-10-16T09:00:00Z"
    build_db(db_path, [RunSpec(started, {Q1: ok_result(started, (A,))}, n_queries=3)])
    path = str(db_path)
    runs, matrix = data.load_status_matrix_raw(path)
    fig = charts.status_heatmap(runs, matrix, data.load_queries_raw(path))
    assert len(fig.data[0].x) == 1


def test_heatmap_axes_keep_duplicate_labels_apart():
    runs = pd.DataFrame(
        {
            "run_id": [2, 1],
            "started_at": ["2026-10-16T09:00:20Z", "2026-10-16T09:00:05Z"],
            "status": ["ok", "ok"],
        }
    )
    queries = pd.DataFrame({"query_id": [1, 2], "text": ["один", "один"], "sheet_order": [1, 1]})
    matrix = pd.DataFrame(
        {
            "run_id": [1, 1, 2, 2],
            "query_id": [1, 2, 1, 2],
            "status": ["ok", "blocked", "partial", "error"],
            "items_collected": [200, 0, 15, 0],
            "is_complete": [1, 0, 0, 0],
            "http_status": [None, 403, 503, None],
            "error_class": [None, "http_403", "http_5xx", "timeout"],
        }
    )
    fig = charts.status_heatmap(runs, matrix, queries)
    assert [list(row) for row in fig.data[0].z] == [[1, 2], [3, 5]]
    assert list(fig.data[0].x) == [0, 1]
    assert list(fig.data[0].y) == [0, 1]


def test_heatmap_of_48_runs_by_70_queries_builds_under_a_second():
    n_runs, n_queries = 48, 70
    runs = pd.DataFrame(
        {
            "run_id": range(n_runs, 0, -1),
            "started_at": [
                f"2026-10-{14 + i // 24:02d}T{i % 24:02d}:00:00Z" for i in range(n_runs)
            ],
            "status": "ok",
        }
    )
    queries = pd.DataFrame(
        {
            "query_id": range(1, n_queries + 1),
            "text": [f"запрос {i}" for i in range(1, n_queries + 1)],
            "sheet_order": range(1, n_queries + 1),
        }
    )
    matrix = pd.DataFrame(
        [
            {
                "run_id": r,
                "query_id": q,
                "status": "blocked" if (r + q) % 7 == 0 else "ok",
                "items_collected": 200,
                "is_complete": 1,
                "http_status": None,
                "error_class": None,
            }
            for r in range(1, n_runs + 1)
            for q in range(1, n_queries + 1)
        ]
    )
    started = time.perf_counter()
    fig = charts.status_heatmap(runs, matrix, queries)
    assert time.perf_counter() - started < 1.0
    assert fig.layout.height == 20 * 70 + 96
    assert len(fig.data[0].z) == 70 and len(fig.data[0].z[0]) == 48


# ------------------------------------------------------------------------------ purity


def test_the_chart_module_stays_pure():
    source = Path(charts.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import streamlit",
        "import numpy",
        "import sqlite3",
        "add_vrect",
        "add_shape",
    ):
        assert forbidden not in source
    assert "shapes=band_shapes(" in source
