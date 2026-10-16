"""The pure Plotly position chart: axis, honest gaps, bands, ad diamonds, hover, escaping, speed."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ui.dbbuilder import NM, Q1, scenario_mixed
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
