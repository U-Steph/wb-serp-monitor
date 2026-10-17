"""End-to-end AppTests of the read-only shell: header, banner, sidebar totals, run log."""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from streamlit import config as st_config
from streamlit.testing.v1 import AppTest

from ui.dbbuilder import (
    NM,
    Q1,
    Q2,
    Q2_IDS,
    Q3,
    Q3_IDS,
    REPO_ROOT,
    RunSpec,
    append_run,
    build_db,
    failed_result,
    ok_result,
    scenario_all_blocked,
    scenario_latest,
    scenario_mixed,
)
from wbmon import REPO_URL
from wbmon.clock import iso_z, utc_now
from wbmon.storage.db import connect_ro
from wbmon.storage.migrations import init_db
from wbmon.ui import series, texts

APP = str(Path(__file__).resolve().parents[2] / "src" / "wbmon" / "ui" / "app.py")
NO_SUCCESS = "Успешных сборов пока нет"
FINAL_TABS = ["📈 Позиции", "🗂 Выдача", "🩺 Журнал сборов", "ℹ️ О сервисе"]


def run_app(section: str | None = None, *, mode: str | None = None) -> AppTest:
    """Run the app, optionally on a given menu section and data mode (preset, no warnings)."""
    at = AppTest.from_file(APP, default_timeout=30)
    if section is not None:
        at.session_state["section"] = section
    if mode is not None:
        at.session_state["mode"] = mode
    return at.run()


def menu(at: AppTest):
    return at.radio(key="section")


def markdown_values(at: AppTest) -> list[str]:
    return [m.value for m in at.markdown]


def caption_values(at: AppTest) -> list[str]:
    return (
        [c.value for c in at.caption]
        + [c.value for c in at.sidebar.caption]
        + [m.value for m in at.markdown]
    )


def badge_values(at: AppTest) -> list[str]:
    return [v for v in markdown_values(at) if v.startswith(":") and "-badge[" in v]


def find_df(at: AppTest, column: str) -> pd.DataFrame:
    for element in at.dataframe:
        frame = element.value
        if column in frame.columns:
            return frame
    raise AssertionError(f"no dataframe with column {column!r}")


def write_heartbeat(db_path: Path, ts) -> None:
    (db_path.parent / "scheduler_heartbeat.json").write_text(
        json.dumps({"ts": iso_z(ts), "pid": 1}), encoding="utf-8"
    )


def counts(db_path: Path) -> tuple:
    with contextlib.closing(connect_ro(db_path)) as con:
        return (
            con.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM run_queries").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            con.execute("PRAGMA user_version").fetchone()[0],
        )


def parse_started(db_path: Path, run_id: int):
    from wbmon.clock import parse_iso_z

    with contextlib.closing(connect_ro(db_path)) as con:
        row = con.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,)).fetchone()
    return parse_iso_z(row[0])


def has(values, needle: str) -> bool:
    return any(needle in value for value in values)


# --------------------------------------------------------------------- unreadable / empty states


def test_no_database_shows_hint_and_creates_nothing(db_path):
    at = run_app()
    assert not at.exception
    assert has([i.value for i in at.info], "wbmon init-db")
    assert not db_path.exists()
    assert not badge_values(at)


def test_empty_file_is_reported_as_not_initialised(db_path):
    db_path.write_bytes(b"")
    at = run_app()
    assert not at.exception
    assert has([i.value for i in at.info], "wbmon init-db")
    assert db_path.stat().st_size == 0


def test_non_sqlite_file_shows_the_read_error_without_path(db_path):
    db_path.write_bytes(b"not a database")
    at = run_app()
    assert not at.exception
    errors = [e.value for e in at.error]
    assert has(errors, "Не удалось прочитать базу данных")
    assert not has(errors, str(db_path))
    assert not has(errors, "Traceback")


def test_zero_runs_render_the_empty_header(db_path):
    init_db(db_path)
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    assert has(badge_values(at), NO_SUCCESS)
    assert has([i.value for i in at.info], "Сборов ещё не было")
    captions = caption_values(at)
    assert has(captions, "Всего сборов: **0**")
    assert has(captions, "Первый сбор: —")
    assert has(captions, "Наблюдений в базе: **0**")


# --------------------------------------------------------------------------------- mixed history


def test_mixed_history_header_and_run_log_totals(db_path):
    scenario_mixed(db_path)
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    badges = badge_values(at)
    assert any(v.startswith(":green-badge[") for v in badges)
    assert has(badges, "Последний успешный сбор:")
    assert has(badges, "МСК")
    assert not at.warning
    assert not at.error
    captions = caption_values(at)
    for line in (
        "Всего сборов: **6**",
        "Успешных сборов: **6**",
        "Наблюдений в базе: **60**",
        "Уникальных товаров: **12**",
    ):
        assert has(captions, line), line
    assert has(captions, "Первый сбор: **")
    assert "🩺 Журнал сборов" in menu(at).options


def test_mixed_history_run_table_is_newest_first(db_path):
    scenario_mixed(db_path)
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    frame = find_df(at, "Тип")
    assert len(frame) == 6
    assert list(frame["Статус"]) == [
        "✅ Успешно",
        "✅ Успешно",
        "🟡 Частично",
        "✅ Успешно",
        "🟡 Частично",
        "✅ Успешно",
    ]
    assert set(frame["Тип"]) == {"По расписанию"}
    assert frame["Начало (МСК)"].is_monotonic_decreasing


def test_all_blocked_history_is_complete_and_informative(db_path):
    scenario_all_blocked(db_path, n_runs=3)
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    assert has(
        [w.value for w in at.warning],
        "Wildberries отклоняет запросы сборщика (антибот, HTTP 403)",
    )
    assert has([w.value for w in at.warning], "3 сбора без успешных")
    assert has(badge_values(at), NO_SUCCESS)
    captions = caption_values(at)
    assert has(captions, "Успешных сборов: **0**")
    assert has(captions, "Наблюдений в базе: **0**")
    frame = find_df(at, "Тип")
    assert len(frame) == 3
    assert set(frame["Статус"]) == {"⛔ Заблокировано"}


# ----------------------------------------------------------------------------------- the banner


def test_latest_running_shows_info_and_collector_line(db_path):
    scenario_latest(db_path, "running")
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    assert has([i.value for i in at.info], "Идёт сбор (начат")
    assert has(caption_values(at), "идёт сбор")
    assert find_df(at, "Тип")["Статус"].iloc[0] == "⏳ Идёт"


@pytest.mark.parametrize(
    ("final", "kind", "needle"),
    [
        (
            "partial",
            "warning",
            "Последний сбор завершён частично: успешно 2 из 3 запросов, "
            "заблокировано 1, с ошибкой 0",
        ),
        (
            "blocked",
            "warning",
            "Последний сбор заблокирован Wildberries (HTTP 403) по 3 из 3 запросов",
        ),
        ("error", "error", "Последний сбор завершился ошибкой"),
        ("abandoned", "warning", "Последний сбор был прерван"),
    ],
)
def test_latest_run_banner(db_path, final, kind, needle):
    scenario_latest(db_path, final)
    at = run_app()
    assert not at.exception
    assert has([e.value for e in getattr(at, kind)], needle)


# --------------------------------------------------------------------------------- the heartbeat


def test_heartbeat_absent(db_path):
    scenario_mixed(db_path)
    at = run_app()
    assert not at.exception
    assert has(caption_values(at), "Сборщик: нет данных (файл сигнала не найден)")


def test_heartbeat_fresh(db_path):
    scenario_mixed(db_path)
    write_heartbeat(db_path, utc_now() - timedelta(seconds=12))
    at = run_app()
    assert not at.exception
    assert has(caption_values(at), "Сборщик на связи · следующий сбор в")


def test_heartbeat_stale(db_path):
    scenario_mixed(db_path)
    write_heartbeat(db_path, utc_now() - timedelta(hours=2))
    at = run_app()
    assert not at.exception
    assert has(caption_values(at), "нет сигнала уже 2 ч")


def test_heartbeat_malformed_json_degrades_to_no_data(db_path):
    scenario_mixed(db_path)
    (db_path.parent / "scheduler_heartbeat.json").write_text("{broken", encoding="utf-8")
    at = run_app()
    assert not at.exception
    assert has(caption_values(at), "Сборщик: нет данных")


@pytest.mark.parametrize("ts", ["0001-01-01T00:00:00+05:00", "2999-01-01T00:00:00Z"])
def test_heartbeat_odd_timestamps_do_not_crash_or_claim_liveness(db_path, ts):
    """WR-02: an overflowing or future timestamp reads as 'no data', never 'на связи'."""
    scenario_mixed(db_path)
    (db_path.parent / "scheduler_heartbeat.json").write_text(
        json.dumps({"ts": ts}), encoding="utf-8"
    )
    at = run_app()
    assert not at.exception
    assert has(caption_values(at), "Сборщик: нет данных")
    assert not has(caption_values(at), "Сборщик на связи")


def test_a_header_failure_does_not_hide_the_menu(db_path, monkeypatch):
    """WR-02: the header is guarded like the section bodies."""
    scenario_mixed(db_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("header exploded")

    monkeypatch.setattr(series, "banner", boom)
    at = run_app()
    assert not at.exception
    assert has([e.value for e in at.error], "Не удалось построить этот блок")
    assert menu(at).options == FINAL_TABS
    assert at.selectbox(key="query")


# ------------------------------------------------------------------------------------ the shell


def test_shell_has_the_menu_topbar_and_filters(db_path):
    """Four sections in the left menu only, the mode switch in the top bar, filters in the main."""
    scenario_mixed(db_path)
    at = run_app()
    assert not at.exception
    assert menu(at).options == FINAL_TABS
    assert not at.sidebar.selectbox
    assert not at.sidebar.segmented_control
    assert not at.sidebar.number_input
    assert at.segmented_control(key="mode").value == texts.MODE_REAL
    assert at.selectbox(key="query")
    assert at.segmented_control(key="period")
    assert at.number_input(key="topn")
    assert at.get("html")
    assert at.title[0].value == "Позиции в поиске"
    assert not at.get("tabs")


# ---------------------------------------------------------------- cache invalidation, read-only


def test_new_run_appears_without_restart(db_path):
    scenario_mixed(db_path)
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    assert has(caption_values(at), "Всего сборов: **6**")
    started = iso_z(utc_now() - timedelta(minutes=1))
    spec = RunSpec(
        started_at=started,
        results={
            Q1: ok_result(started, (NM["A"],)),
            Q2: ok_result(started, Q2_IDS),
            Q3: ok_result(started, Q3_IDS),
        },
        trigger="manual",
        n_queries=3,
        duration_s=30,
    )
    append_run(db_path, spec)
    at.run()
    assert not at.exception
    assert has(caption_values(at), "Всего сборов: **7**")
    assert len(find_df(at, "Тип")) == 7


def test_app_never_writes_to_the_database(db_path):
    scenario_mixed(db_path)
    before = counts(db_path)
    at = run_app()
    assert not at.exception
    assert counts(db_path) == before
    assert before[0] == 6
    assert before[3] >= 1


# ------------------------------------------------------------------------------------ the theme


def test_streamlit_config_keys_resolve_and_hide_error_details():
    path = REPO_ROOT / ".streamlit" / "config.toml"
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    assert config
    for section, values in config.items():
        for key in values:
            st_config.get_option(f"{section}.{key}")  # raises on an unknown key
    assert config["client"]["showErrorDetails"] == "none"
    # WR-03: the built-in dataframe export would bypass the CSV formula-injection guard
    assert config["client"]["disableDataExport"] is True


def test_streamlit_reads_the_theme_file_from_the_repo_root():
    script = (
        "from streamlit import config; "
        "print(config.get_option('client.showErrorDetails'), "
        "config.get_option('theme.primaryColor'), "
        "config.get_option('client.disableDataExport'))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["none", "#7B10F5", "True"]


# ----------------------------------------------------------------------------- «Позиции» tab

A, B, C, D, E, F = (NM[k] for k in "ABCDEF")
NOT_IN_LATEST = " · не в последней выдаче"


def all_chart_specs(at: AppTest) -> list[dict]:
    return [json.loads(el.proto.spec) for el in at.get("plotly_chart")]


def chart_specs(at: AppTest) -> list[dict]:
    """The position charts only: the run-log heatmap lives in another section of the page."""
    return [spec for spec in all_chart_specs(at) if spec["data"][0]["type"] != "heatmap"]


def heatmap_specs(at: AppTest) -> list[dict]:
    return [spec for spec in all_chart_specs(at) if spec["data"][0]["type"] == "heatmap"]


def chart_spec(at: AppTest) -> dict:
    specs = chart_specs(at)
    assert len(specs) == 1
    return specs[0]


def trace_named(spec: dict, nm_id: int) -> dict:
    return next(t for t in spec["data"] if t["name"].startswith(f"ID {nm_id} "))


def msk_x(db_path: Path, run_id: int) -> str:
    """The figure x string of a run, computed independently of the chart code."""
    with contextlib.closing(connect_ro(db_path)) as con:
        started = con.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
    return series.to_msk(pd.Series([started])).iloc[0].strftime("%Y-%m-%d %H:%M:%S")


def info_values(at: AppTest) -> list[str]:
    return [i.value for i in at.info]


def covers(shape: dict, x: str) -> bool:
    return shape["x0"] <= x <= shape["x1"]


def test_filter_row_offers_every_query_in_sheet_order_with_original_wording(db_path):
    scenario_mixed(db_path)
    at = run_app()
    assert not at.exception
    query = at.selectbox(key="query")
    assert query.options == [f"01. {Q1}", f"02. {Q2}", f"03. {Q3}"]
    assert Q2 in query.options[1]  # Cyrillic "х" kept
    assert query.options[2].endswith("50x70")  # Latin "x" kept
    assert menu(at).options[:3] == ["📈 Позиции", "🗂 Выдача", "🩺 Журнал сборов"]
    assert at.segmented_control(key="period").value == "Всё время"
    assert at.number_input(key="topn").value == 5


def test_positions_tab_draws_the_honest_chart_of_the_default_top_five(db_path):
    built = scenario_mixed(db_path)
    r1, r2, r3, r4, r5, r6 = built.run_ids
    at = run_app()
    assert not at.exception
    assert at.multiselect[0].value == [A, B, D, C, E]
    options = at.multiselect[0].options
    assert len(options) == 6
    assert options[0] == f"{A} · ТЕСТ-БРЕНД · ТЕСТ товар {A}"
    assert options[-1].endswith(NOT_IN_LATEST)
    assert f"{F} · " in options[-1]

    spec = chart_spec(at)
    y_range = spec["layout"]["yaxis"]["range"]
    assert y_range[0] > y_range[1] and y_range[1] == -0.5
    shapes = spec["layout"]["shapes"]
    assert len(shapes) == 2
    x3 = msk_x(db_path, r3)
    assert not any(covers(shape, x3) for shape in shapes)
    assert sum(covers(shape, msk_x(db_path, r2)) for shape in shapes) == 1

    b = trace_named(spec, B)
    by_x = dict(zip(b["x"], b["y"], strict=True))
    assert by_x[x3] is None
    assert by_x[msk_x(db_path, r2)] is None
    assert by_x[msk_x(db_path, r1)] == 2
    a = trace_named(spec, A)
    assert dict(zip(a["x"], a["marker"]["symbol"], strict=True))[x3] == "diamond"
    assert dict(zip(a["x"], a["y"], strict=True))[msk_x(db_path, r4)] == 2
    c = trace_named(spec, C)
    prices = dict(zip(c["x"], [d[1] for d in c["customdata"]], strict=True))
    assert prices[msk_x(db_path, r5)] == "—"
    assert [t["name"] for t in spec["data"][-2:]] == ["◆ рекламное размещение", "сбор не удался"]

    captions = caption_values(at)
    assert has(captions, "Разрыв линии")
    assert has(
        captions,
        "Показано товаров: 5; сборов в периоде: 6; без данных (сбор не удался): 2.",
    )
    assert not at.info


def test_positions_show_four_kpi_tiles_of_the_latest_run(db_path):
    scenario_mixed(db_path)
    at = run_app()
    assert not at.exception
    tiles = {m.label: m.value for m in at.metric}
    assert list(tiles) == [
        "Товаров в выдаче",
        "Средняя цена, топ-10",
        "Средний рейтинг, топ-10",
        "Доставка в Москву",
    ]
    assert tiles["Товаров в выдаче"] == "5"
    assert tiles["Доставка в Москву"] == "2 дн."


def test_changing_the_query_resets_the_selection_to_its_top(db_path):
    built = scenario_mixed(db_path)
    at = run_app()
    at.selectbox(key="query").select(built.query_ids[Q2]).run()
    assert not at.exception
    assert at.multiselect[0].value == list(Q2_IDS)
    spec = chart_spec(at)
    assert spec["layout"].get("shapes", []) == []
    assert has(caption_values(at), "Показано товаров: 3; сборов в периоде: 6; без данных")


def test_changing_the_default_count_resets_the_selection(db_path):
    scenario_mixed(db_path)
    at = run_app()
    at.number_input(key="topn").set_value(2).run()
    assert not at.exception
    assert at.multiselect[0].value == [A, B]
    at.number_input(key="topn").set_value(20).run()
    assert not at.exception
    assert at.multiselect[0].value == [A, B, D, C, E]
    assert has(caption_values(at), "Показано не более 15 товаров")


def test_more_than_fifteen_products_are_capped_at_the_default(db_path):
    started = iso_z(utc_now() - timedelta(minutes=10))
    ids = tuple(range(910000001, 910000019))
    build_db(db_path, [RunSpec(started, {Q1: ok_result(started, ids)}, n_queries=3)])
    at = run_app()
    at.number_input(key="topn").set_value(20).run()
    assert not at.exception
    assert at.multiselect[0].value == list(ids[:15])
    assert len(at.multiselect[0].options) == 18


def test_an_empty_selection_shows_the_hint_and_still_draws_the_skeleton(db_path):
    scenario_mixed(db_path)
    at = run_app()
    at.multiselect[0].set_value([]).run()
    assert not at.exception
    assert has(info_values(at), "Выберите хотя бы один товар")
    spec = chart_spec(at)
    assert len(spec["layout"]["shapes"]) == 2
    assert [t["name"] for t in spec["data"]] == ["◆ рекламное размещение", "сбор не удался"]
    assert has(caption_values(at), "Показано товаров: 0; сборов в периоде: 6")


def test_period_narrows_the_grid_but_not_the_picker(db_path):
    built = scenario_mixed(db_path)
    started = iso_z(parse_started(db_path, built.run_ids[-1]) - timedelta(hours=30))
    append_run(db_path, RunSpec(started, {Q1: ok_result(started, (A,))}, "scheduler", n_queries=1))
    at = run_app()
    assert not at.exception
    assert has(caption_values(at), "сборов в периоде: 7")
    at.segmented_control(key="period").set_value("24 ч").run()
    assert not at.exception
    assert has(caption_values(at), "сборов в периоде: 6")
    assert len(at.multiselect[0].options) == 6


def test_a_period_without_runs_for_the_query_says_so(db_path):
    now = utc_now()
    old = iso_z(now - timedelta(hours=40))
    new = iso_z(now - timedelta(minutes=10))
    built = build_db(
        db_path,
        [
            RunSpec(old, {Q2: ok_result(old, Q2_IDS)}, n_queries=3),
            RunSpec(new, {Q1: ok_result(new, (A,))}, trigger="manual", n_queries=1),
        ],
    )
    at = run_app()
    at.selectbox(key="query").select(built.query_ids[Q2]).run()
    assert not at.exception
    assert has(caption_values(at), "сборов в периоде: 1")
    at.segmented_control(key="period").set_value("24 ч").run()
    assert not at.exception
    assert has(info_values(at), "За выбранный период нет сборов по этому запросу")
    assert not chart_specs(at)


def test_all_blocked_real_database_shows_the_empty_state_with_real_counts(db_path):
    scenario_all_blocked(db_path)
    at = run_app()
    assert not at.exception
    assert "Данных о товарах пока нет" in [h.value for h in at.subheader]
    assert has(markdown_values(at), "3 сбора")
    assert has(markdown_values(at), "3 из 3 запросов заблокировано")
    assert has(markdown_values(at), "последний — ")
    assert at.button(key="demo_on").label == texts.DEMO_ON
    assert not at.get("plotly_chart")
    assert not at.multiselect
    assert not at.dataframe
    at = run_app(texts.TAB_SNAPSHOT)
    assert not at.exception
    assert "Выдача пока пустая" in [h.value for h in at.subheader]
    assert at.button(key="demo_on")
    assert not at.select_slider
    assert not at.metric


def test_a_run_in_flight_adds_neither_a_column_nor_a_band_to_unreached_queries(db_path):
    built = scenario_latest(db_path, "running")
    at = run_app()
    at.selectbox(key="query").select(built.query_ids[Q2]).run()
    assert not at.exception
    assert chart_spec(at)["layout"].get("shapes", []) == []
    assert has(caption_values(at), "сборов в периоде: 6")
    at.selectbox(key="query").select(built.query_ids[Q1]).run()
    assert has(caption_values(at), "сборов в периоде: 7")


def test_zero_runs_show_only_the_info_in_the_positions_tab(db_path):
    init_db(db_path)
    at = run_app()
    assert not at.exception
    assert not chart_specs(at)
    assert not at.multiselect


def test_marketplace_text_never_becomes_markdown_or_markup(db_path):
    started = iso_z(utc_now() - timedelta(minutes=10))
    brand = '<a href="https://evil.example">x</a>'
    name = "$x^2$ [ссылка](http://e.x) **b**"
    plain = ok_result(started, (A, B))
    rows = tuple(replace(row, brand=brand, name=name) for row in plain.observations)
    build_db(db_path, [RunSpec(started, {Q1: replace(plain, observations=rows)}, n_queries=3)])
    at = run_app()
    assert not at.exception
    labels = at.multiselect[0].options
    assert labels[0] == f"{A} · {brand} · {name}"
    text = json.dumps(chart_spec(at), ensure_ascii=False)
    assert "<a href" not in text
    assert 'evil.example">' not in text
    shown = markdown_values(at) + caption_values(at) + info_values(at)
    assert not has(shown, "evil.example")
    assert not has(shown, "http://e.x")


# ------------------------------------------------- characteristics, history, CSV, attempts


def hostile_db(db_path: Path) -> str:
    started = iso_z(utc_now() - timedelta(minutes=10))
    name = "$x^2$ [ссылка](http://e.x) **b**"
    plain = ok_result(started, (A, B))
    rows = tuple(replace(row, brand="<b>x</b>", name=name) for row in plain.observations)
    build_db(db_path, [RunSpec(started, {Q1: replace(plain, observations=rows)}, n_queries=3)])
    return name


def test_characteristics_table_matches_the_scenario_facts(db_path):
    scenario_mixed(db_path)
    at = run_app()
    assert not at.exception
    frame = find_df(at, "Δ к прошлому сбору")
    assert list(frame["Позиция (текущая)"]) == [1, 2, 3, 4, 5]
    assert list(frame["Δ к прошлому сбору"]) == ["▲ 1", "▼ 1", "▲ 1", "▼ 1", "= 0"]
    assert list(frame["Лучшая"]) == [1, 1, 3, 1, 4]
    assert list(frame["Худшая"]) == [2, 2, 4, 4, 5]
    assert list(frame["В выдаче"]) == [100, 75, 100, 100, 100]
    assert list(frame["Цена, ₽"]) == [1001, 1002, 1003, 1004, 1005]
    assert list(frame["Реклама"]) == ["", "", "", "", "✓"]
    assert [u.rsplit("/", 2)[1] for u in frame["ID"]] == [str(n) for n in (A, B, D, C, E)]
    assert "Характеристики" in [h.value for h in at.subheader]
    assert has(caption_values(at), "Прочерк «—» — данных нет")


def test_characteristics_column_config_links_and_hides_the_index(db_path):
    scenario_mixed(db_path)
    at = run_app()
    element = next(e for e in at.dataframe if "Δ к прошлому сбору" in e.value.columns)
    config = json.loads(element.proto.columns)
    assert config["ID"]["type_config"]["type"] == "link"
    assert config["ID"]["type_config"]["display_text"] == r"catalog/(\d+)/detail"
    assert config["В выдаче"]["type_config"]["format"] == "%.0f%%"
    assert config["_index"]["hidden"] is True
    assert has([config["Доставка, дн."]["help"]], "0 — сегодня, 1 — завтра")


def test_no_table_column_uses_the_browser_locale_for_numbers(db_path):
    """VIS-2: 'localized' prints '1,073' in an en-US browser; every number format is explicit."""
    scenario_mixed(db_path)
    at = run_app()
    assert not at.exception
    seen: dict[str, set[str]] = {}
    for element in at.dataframe:
        for name, config in json.loads(element.proto.columns).items():
            kind = config.get("type_config", {})
            if kind.get("type") == "number":
                seen.setdefault(name, set()).add(kind.get("format"))
    assert "localized" not in {f for formats in seen.values() for f in formats}
    assert seen["Цена, ₽"] == {"%d"}
    assert seen["Отзывов"] == {"%d"}


def test_history_expander_lists_every_observation_of_the_selection(db_path):
    scenario_mixed(db_path)
    at = run_app()
    assert not at.exception
    frame = find_df(at, "Время (МСК)")
    assert len(frame) == 21
    assert frame["Время (МСК)"].is_monotonic_decreasing
    assert "История наблюдений" in [e.label for e in at.expander]


def test_period_narrows_the_tables_and_the_csv(db_path):
    built = scenario_mixed(db_path)
    started = iso_z(parse_started(db_path, built.run_ids[-1]) - timedelta(hours=30))
    append_run(db_path, RunSpec(started, {Q1: ok_result(started, (A,))}, "scheduler", n_queries=1))
    at = run_app()
    # the appended run has the highest id, so the default selection is its single product A
    assert at.multiselect[0].value == [A]
    assert len(find_df(at, "Время (МСК)")) == 6
    at.segmented_control(key="period").set_value("24 ч").run()
    assert not at.exception
    assert len(find_df(at, "Время (МСК)")) == 5


def test_csv_button_is_enabled_and_disabled_with_the_selection(db_path):
    scenario_mixed(db_path)
    at = run_app()
    assert not at.exception
    button = at.get("download_button")[0].proto
    assert button.label == "Скачать CSV"
    assert not button.disabled
    assert has(caption_values(at), "UTF-8 с BOM")
    assert has(caption_values(at), "экранированы апострофом")
    at.multiselect[0].set_value([]).run()
    assert not at.exception
    button = at.get("download_button")[0].proto
    assert button.disabled
    assert button.help == "Нет данных для выгрузки"
    assert len(find_df(at, "Δ к прошлому сбору")) == 0


def test_marketplace_text_stays_inside_the_table_cells(db_path):
    name = hostile_db(db_path)
    at = run_app()
    assert not at.exception
    assert name in list(find_df(at, "Δ к прошлому сбору")["Название"])
    shown = markdown_values(at) + caption_values(at) + info_values(at)
    assert not has(shown, "e.x)")
    assert not has(shown, "<b>x</b>")


# ------------------------------------------------------------------------ the «Выдача» tab

SNAPSHOT_FRAME_COLUMNS = list(series.SNAPSHOT_COLUMNS)


def find_exact_df(at: AppTest, columns: list[str]) -> pd.DataFrame:
    for element in at.dataframe:
        if list(element.value.columns) == columns:
            return element.value
    raise AssertionError(f"no dataframe with columns {columns!r}")


def started_label(db_path: Path, run_id: int) -> str:
    return texts.fmt_dt_short(series.to_msk_one(iso_z(parse_started(db_path, run_id))))


def test_menu_is_positions_snapshot_run_log_about(db_path):
    scenario_mixed(db_path)
    at = run_app()
    assert menu(at).options == FINAL_TABS


def test_snapshot_slider_defaults_to_the_newest_run_with_data(db_path):
    built = scenario_mixed(db_path)
    r1, _, r3, r4, r5, r6 = built.run_ids
    at = run_app(texts.TAB_SNAPSHOT)
    assert not at.exception
    slider = at.select_slider[0]
    assert slider.label == "Момент сбора (МСК)"
    assert slider.value == r6
    assert slider.options == [started_label(db_path, r) for r in (r1, r3, r4, r5, r6)]


def test_snapshot_shows_four_metrics_and_the_full_top_in_order(db_path):
    scenario_mixed(db_path)
    at = run_app(texts.TAB_SNAPSHOT)
    assert not at.exception
    assert {m.label: m.value for m in at.metric} == {
        "Товаров собрано": "5",
        "Из них рекламных": "1",
        "Медианная цена, ₽": "1\u202f003",
        "Статус сбора": "✅ Успешно",
    }
    frame = find_exact_df(at, SNAPSHOT_FRAME_COLUMNS)
    assert [u.rsplit("/", 2)[1] for u in frame["ID"]] == [str(n) for n in (A, B, D, C, E)]
    assert list(frame["Позиция"]) == [1, 2, 3, 4, 5]
    assert list(frame["Реклама"]) == ["", "", "", "", "✓"]
    assert "Выдача по запросу" in [h.value for h in at.subheader]


def test_snapshot_shows_product_cards_and_keeps_the_table_in_an_expander(db_path):
    scenario_mixed(db_path)
    at = run_app(texts.TAB_SNAPSHOT)
    assert not at.exception
    markdown = markdown_values(at)
    assert "**#1**" in markdown and "**#5**" in markdown
    assert not has(markdown, "#6")
    assert "Вся выдача таблицей" in [e.label for e in at.expander]
    ads = [v for v in badge_values(at) if "Реклама" in v]
    assert len(ads) == 1  # only product E is an ad slot in the newest mixed run


def test_hostile_names_reach_the_cards_only_escaped(db_path):
    name = hostile_db(db_path)
    at = run_app(texts.TAB_SNAPSHOT)
    assert not at.exception
    assert texts.md_literal(name) in caption_values(at)
    assert not has(markdown_values(at) + caption_values(at), "http://e.x)")


def test_choosing_a_partial_run_shows_its_two_rows_and_status(db_path):
    built = scenario_mixed(db_path)
    at = run_app(texts.TAB_SNAPSHOT)
    at.select_slider[0].set_value(built.run_ids[3]).run()
    assert not at.exception
    frame = find_exact_df(at, SNAPSHOT_FRAME_COLUMNS)
    assert [u.rsplit("/", 2)[1] for u in frame["ID"]] == [str(C), str(A)]
    assert {m.label: m.value for m in at.metric}["Статус сбора"] == "🟡 Частично"
    assert {m.label: m.value for m in at.metric}["Товаров собрано"] == "2"


def test_changing_the_query_resets_the_slider_to_its_newest_run(db_path):
    built = scenario_mixed(db_path)
    at = run_app(texts.TAB_SNAPSHOT)
    at.select_slider[0].set_value(built.run_ids[3]).run()
    assert at.select_slider[0].value == built.run_ids[3]
    at.selectbox(key="query").select(built.query_ids[Q3]).run()
    assert not at.exception
    assert at.select_slider[0].value == built.run_ids[-1]
    assert len(at.select_slider[0].options) == 6
    frame = find_exact_df(at, SNAPSHOT_FRAME_COLUMNS)
    assert len(frame) == len(Q3_IDS)


def test_snapshot_slider_follows_a_new_run_that_lands_while_the_page_is_open(db_path):
    """WR-04: the key-pinned slider used to stay on the previous latest run."""
    built = scenario_mixed(db_path)
    at = run_app(texts.TAB_SNAPSHOT)
    assert at.select_slider[0].value == built.run_ids[-1]
    started = iso_z(utc_now() - timedelta(minutes=1))
    spec = RunSpec(
        started_at=started,
        results={text: ok_result(started, Q2_IDS) for text in (Q1, Q2, Q3)},
        trigger="manual",
        n_queries=3,
    )
    new_run = append_run(db_path, spec)
    at.run()
    assert not at.exception
    assert at.select_slider[0].value == new_run
    assert len(at.select_slider[0].options) == 6  # five runs with data, plus the new one


def test_a_single_run_gives_a_slider_with_one_option(db_path):
    started = iso_z(utc_now() - timedelta(minutes=10))
    build_db(db_path, [RunSpec(started, {Q1: ok_result(started, (A, B))}, n_queries=3)])
    at = run_app(texts.TAB_SNAPSHOT)
    assert not at.exception
    assert len(at.select_slider[0].options) == 1
    assert len(find_exact_df(at, SNAPSHOT_FRAME_COLUMNS)) == 2


def test_snapshot_query_label_is_an_escaped_caption_not_monospace_text(db_path):
    scenario_mixed(db_path)
    at = run_app(texts.TAB_SNAPSHOT)
    assert not at.text  # st.text would use the monospace face (UI-2)
    assert texts.md_literal(f"01. {Q1}") in caption_values(at)
    assert not has(caption_values(at), f"01. {Q1}")  # the raw wording never reaches markdown


HOSTILE = (
    "*bold* _it_ __b__ ~~s~~ [click](https://evil.example/x) ![i](https://evil.example/p.png) "
    "`code` <b>x</b> <script>1</script> # h :red[c] :material/home: $e=mc^2$ > q - i & &amp; \\ "
    "1. n | t |"
)


def test_hostile_query_text_reaches_the_snapshot_caption_only_escaped(db_path):
    started = iso_z(utc_now() - timedelta(minutes=10))
    build_db(
        db_path, [RunSpec(started, {HOSTILE: ok_result(started, (A, B))}, n_queries=1)], (HOSTILE,)
    )
    at = run_app(texts.TAB_SNAPSHOT)
    assert not at.exception
    escaped = texts.md_literal(f"01. {HOSTILE}")
    assert escaped in caption_values(at)
    # no unescaped copy of any dangerous token in any markdown-rendered element
    rendered = markdown_values(at) + caption_values(at) + [i.value for i in at.info]
    for token in ("*bold*", "[click](", "![i]", "<b>", "<script>", "`code`", ":red[", "~~s~~"):
        assert not has(rendered, token), token


def test_md_literal_escapes_every_ascii_punctuation_and_keeps_the_wording():
    import string

    escaped = texts.md_literal(string.punctuation)
    assert escaped == "".join("\\" + ch for ch in string.punctuation)
    # CommonMark: a backslash before ASCII punctuation is dropped, so the text round-trips
    assert re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", texts.md_literal(HOSTILE)) == HOSTILE
    assert texts.md_literal("подушка 50x70, ёлка") == "подушка 50x70\\, ёлка"


def test_md_literal_flattens_line_breaks_so_text_cannot_open_a_block():
    assert texts.md_literal("a\n\n# h\n    code\tx") == "a \\# h     code x"


def test_zero_runs_snapshot_tab_shows_only_the_info(db_path):
    init_db(db_path)
    at = run_app(texts.TAB_SNAPSHOT)
    assert not at.exception
    assert not at.select_slider
    assert not at.metric


def test_snapshot_keeps_marketplace_text_inside_cells(db_path):
    name = hostile_db(db_path)
    at = run_app(texts.TAB_SNAPSHOT)
    assert not at.exception
    frame = find_exact_df(at, SNAPSHOT_FRAME_COLUMNS)
    assert name in list(frame["Название"])
    assert not has(markdown_values(at) + caption_values(at) + info_values(at), "e.x)")


# ------------------------------------------------------- run-log heatmap and «О сервисе» tab


def heatmap_z(at: AppTest) -> list[list[int]]:
    (spec,) = heatmap_specs(at)
    return [list(row) for row in spec["data"][0]["z"]]


def text_values(at: AppTest) -> list[str]:
    return markdown_values(at) + caption_values(at) + [i.value for i in at.info]


def test_mixed_run_log_leads_with_badges_and_the_heatmap(db_path):
    scenario_mixed(db_path)
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    assert heatmap_z(at)[0] == [1, 3, 1, 2, 1, 1]
    assert heatmap_z(at)[1:] == [[1] * 6, [1] * 6]
    (spec,) = heatmap_specs(at)
    overlays = spec["data"][1:]
    assert overlays and all(t["type"] == "scatter" and t["mode"] == "text" for t in overlays)
    assert {t["textfont"]["color"] for t in overlays} == {"#FFFFFF", "#1A1D21"}
    assert spec["layout"]["height"] == 156
    markdown = markdown_values(at)
    summary = next(v for v in markdown if v.startswith("Последние 6 сборов"))
    assert ":green-badge[✅ Успешно: 4]" in summary
    assert ":yellow-badge[🟡 Частично: 2]" in summary
    assert "**Состояние запросов по сборам**" in markdown
    assert texts.HEATMAP_LEGEND in markdown
    assert markdown.index(summary) < markdown.index(texts.HEATMAP_LEGEND)


def test_zero_runs_show_the_info_and_no_heatmap(db_path):
    init_db(db_path)
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    assert has(info_values(at), "Данных о сборах пока нет")
    assert not heatmap_specs(at)
    assert has(info_values(at), "Сборов ещё не было")
    assert not at.dataframe


def test_one_ok_run_gives_a_one_column_heatmap_and_a_sixty_minute_chart(db_path):
    started = iso_z(utc_now() - timedelta(minutes=10))
    build_db(db_path, [RunSpec(started, {Q1: ok_result(started, (A, B))}, n_queries=3)])
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    (heat,) = heatmap_specs(at)
    assert len(heat["data"][0]["x"]) == 1
    at = run_app(texts.TAB_SNAPSHOT)
    assert len(at.select_slider[0].options) == 1
    at = run_app()
    lo, hi = chart_spec(at)["layout"]["xaxis"]["range"]
    fmt = "%Y-%m-%d %H:%M:%S"
    assert datetime.strptime(hi, fmt) - datetime.strptime(lo, fmt) == timedelta(minutes=60)


def test_all_blocked_history_is_a_full_red_field_with_explanations(db_path):
    scenario_all_blocked(db_path)
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    z = heatmap_z(at)
    assert len(z) == 3 and all(len(row) == 3 and set(row) == {3} for row in z)
    (spec,) = heatmap_specs(at)
    assert {g for t in spec["data"][1:] for g in t["text"]} == {"✕"}
    assert has([w.value for w in at.warning], "Wildberries отклоняет запросы сборщика")
    runs = find_df(at, "Тип")
    assert list(runs["Статус"]) == ["⛔ Заблокировано"] * 3
    assert has(markdown_values(at), ":red-badge[⛔ Заблокировано: 3]")
    assert not has(markdown_values(at), ":green-badge[✅")


@pytest.mark.parametrize(
    ("final", "badge"),
    [("running", ":blue-badge[⏳ Идёт: 1]"), ("abandoned", ":gray-badge[⏸ Прерван: 1]")],
)
def test_unfinished_runs_are_in_the_table_and_badges_but_not_in_the_heatmap(db_path, final, badge):
    scenario_latest(db_path, final)
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    assert len(heatmap_z(at)[0]) == 6
    assert has(markdown_values(at), badge)
    assert len(find_df(at, "Тип")) == 7


def test_latest_error_run_shows_the_error_banner_and_a_grey_column(db_path):
    scenario_latest(db_path, "error")
    at = run_app(texts.TAB_RUNLOG)
    assert not at.exception
    assert has([e.value for e in at.error], "Последний сбор завершился ошибкой")
    assert [row[-1] for row in heatmap_z(at)] == [5, 5, 5]


def test_about_section_has_the_methodology_and_the_repository_link(db_path):
    scenario_mixed(db_path)
    at = run_app(texts.TAB_ABOUT)
    assert not at.exception
    assert at.title[0].value == "О сервисе"
    subheaders = [h.value for h in at.subheader]
    for title in (
        "Что собирается",
        "Как читать данные",
        "Честность данных",
        "Ограничения",
        "Исходный код",
    ):
        assert title in subheaders
    markdown = markdown_values(at)
    assert has(markdown, REPO_URL)
    assert has(markdown, "Сервис не обходит защиту")
    assert has(markdown, "может не соответствовать правилам площадки")


def subtitle_caption(at: AppTest) -> str:
    return next(c for c in caption_values(at) if "· каждый час ·" in c)


def test_header_and_about_use_the_real_query_count(db_path):
    """IN-03: three queries in the sheet read '3 запроса', not a literal 70."""
    scenario_mixed(db_path)
    at = run_app()
    assert not at.exception
    assert subtitle_caption(at) == "3 запроса из таблицы · топ-200 · каждый час · доставка в Москву"
    menu(at).set_value(texts.TAB_ABOUT).run()
    assert not at.exception
    assert has(markdown_values(at), "(сейчас их 3)")
    assert has(markdown_values(at), "(сейчас 3;")
    assert has(markdown_values(at), "топ-200 по сортировке")
    assert not has(markdown_values(at), "70")


def test_header_ignores_queries_that_left_the_sheet_and_small_manual_runs(db_path):
    """IN-03: the queries table keeps removed queries; the header follows the planned sheet."""
    old, recent = (iso_z(utc_now() - timedelta(hours=h)) for h in (3, 2))
    full = RunSpec(old, {text: ok_result(old, Q2_IDS) for text in (Q1, Q2, Q3)}, n_queries=3)
    shrunk = RunSpec(
        recent, {Q1: ok_result(recent, (A,)), Q2: ok_result(recent, Q2_IDS)}, n_queries=2
    )
    build_db(db_path, [full, shrunk])  # Q3 is still stored, but the latest regular run skipped it
    at = run_app()
    assert subtitle_caption(at).startswith("2 запроса из таблицы ·")
    manual = iso_z(utc_now() - timedelta(seconds=30))
    small = RunSpec(manual, {Q1: ok_result(manual, (A,))}, trigger="manual", n_queries=1)
    append_run(db_path, small)
    at = run_app()
    assert subtitle_caption(at).startswith(
        "2 запроса из таблицы ·"
    )  # a one-query manual run is ignored


def test_empty_database_header_falls_back_to_the_named_default(db_path):
    init_db(db_path)
    at = run_app()
    assert subtitle_caption(at).startswith(f"{texts.DEFAULT_N_QUERIES} запросов из таблицы ·")


def _db_without_runs_table(path: Path) -> None:
    import sqlite3

    with contextlib.closing(sqlite3.connect(path)) as con:
        con.execute("PRAGMA user_version = 1")
        con.execute("CREATE TABLE unrelated(x)")
        con.commit()


def _one_query_with_data(path: Path) -> None:
    started = iso_z(utc_now() - timedelta(minutes=10))
    build_db(
        path,
        [
            RunSpec(
                started,
                {
                    Q1: ok_result(started, (A, B)),
                    Q2: failed_result(started),
                    Q3: failed_result(started),
                },
                n_queries=3,
            )
        ],
    )


def _one_ok_run(path: Path) -> None:
    started = iso_z(utc_now() - timedelta(minutes=10))
    build_db(path, [RunSpec(started, {Q1: ok_result(started, (A,))}, n_queries=3)])


STATE_MATRIX = {
    "no_db": (lambda p: None, False),
    "uninitialised": (lambda p: p.write_bytes(b""), False),
    "unreadable": (lambda p: p.write_bytes(b"not a database"), False),
    "no_runs_table": (_db_without_runs_table, False),
    "zero_runs": (init_db, True),
    "one_ok_run": (_one_ok_run, True),
    "mixed": (scenario_mixed, True),
    "all_blocked": (scenario_all_blocked, True),
    "latest_partial": (lambda p: scenario_latest(p, "partial"), True),
    "latest_running": (lambda p: scenario_latest(p, "running"), True),
    "latest_abandoned": (lambda p: scenario_latest(p, "abandoned"), True),
    "latest_blocked": (lambda p: scenario_latest(p, "blocked"), True),
    "latest_error": (lambda p: scenario_latest(p, "error"), True),
    "query_without_data": (_one_query_with_data, True),
}


@pytest.mark.parametrize("state", list(STATE_MATRIX))
def test_state_matrix_renders_without_an_exception(db_path, state):
    prepare, reaches_tabs = STATE_MATRIX[state]
    prepare(db_path)
    at = run_app()
    assert not at.exception
    assert menu(at).options == FINAL_TABS
    if reaches_tabs:
        assert not has([e.value for e in at.error], "Не удалось построить этот блок")
    else:
        shown = [e.value for e in at.error] + info_values(at)
        assert has(shown, "Не удалось прочитать") or has(shown, "wbmon init-db")


def test_a_query_without_data_gets_the_info_and_attempts_while_others_have_a_chart(db_path):
    _one_query_with_data(db_path)
    at = run_app()
    assert not at.exception
    assert chart_spec(at)["data"]
    at.selectbox(key="query").select(2).run()  # the second query was blocked in its only run
    assert not at.exception
    assert has(info_values(at), "По этому запросу пока нет успешных сборов")
    frame = find_df(at, "Причина")
    assert len(frame) == 1
    assert set(frame["Статус"]) == {"⛔ Заблокировано"}
    assert set(frame["HTTP"]) == {403}
    assert set(frame["Причина"]) == {"http_403"}
    assert set(frame["Товаров"]) == {0}
    assert not any("Δ к прошлому сбору" in e.value.columns for e in at.dataframe)
    assert not at.get("download_button")
    at = run_app(texts.TAB_RUNLOG)
    assert [row[0] for row in heatmap_z(at)] == [1, 3, 3]
