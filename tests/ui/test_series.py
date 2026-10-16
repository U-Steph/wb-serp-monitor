"""Pure header logic, run-log shaping and Russian formatters (fixed clocks, no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ui.dbbuilder import NM, Q1, scenario_mixed
from wbmon.ui import data, series, texts

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)
FIN = "2026-10-16T%02d:%02d:%02dZ"


def ago(**kwargs) -> str:
    return (NOW - timedelta(**kwargs)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------- formatters


def test_fmt_int_groups_with_narrow_no_break_space():
    assert texts.fmt_int(1234567) == "1 234 567"
    assert texts.fmt_int(0) == "0"


def test_fmt_price_unknown_is_a_dash_not_zero():
    assert texts.fmt_price(None) == "—"
    assert texts.fmt_price(float("nan")) == "—"
    assert texts.fmt_price(pd.NA) == "—"
    assert texts.fmt_price(1234.4) == "1 234 ₽"
    assert texts.fmt_price(0) == "0 ₽"


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (1, "запрос"),
        (3, "запроса"),
        (5, "запросов"),
        (21, "запрос"),
        (11, "запросов"),
        (0, "запросов"),
    ],
)
def test_plural_ru(n, expected):
    assert texts.plural_ru(n, ("запрос", "запроса", "запросов")) == expected


def test_queries_genitive():
    assert texts.queries_genitive(3) == "3 запросов"
    assert texts.queries_genitive(21) == "21 запроса"
    assert texts.queries_genitive(11) == "11 запросов"


def test_subtitle():
    assert texts.subtitle(70, 200) == "70 запросов · топ-200 · Москва · сбор каждый час"
    assert texts.subtitle(1, 50).startswith("1 запрос ·")


@pytest.mark.parametrize(
    ("seconds", "span", "age"),
    [
        (5, "5 с", "только что"),
        (59, "59 с", "только что"),
        (60, "1 мин", "1 мин назад"),
        (1800, "30 мин", "30 мин назад"),
        (7200, "2 ч 0 мин", "2 ч 0 мин назад"),
        (3 * 86400, "3 дн. 0 ч", "3 дн. 0 ч назад"),
    ],
)
def test_fmt_span_and_age(seconds, span, age):
    assert texts.fmt_span(seconds) == span
    assert texts.fmt_age(seconds) == age


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(42, "42 с"), (252, "4 мин 12 с"), (3720, "1 ч 2 мин"), (None, "—")],
)
def test_fmt_duration(seconds, expected):
    assert texts.fmt_duration(seconds) == expected


# ------------------------------------------------------------------------------ time zone


def test_to_msk_adds_three_hours_and_drops_the_zone():
    out = series.to_msk(pd.Series(["2026-10-16T09:00:00Z"]))
    assert out.iloc[0] == pd.Timestamp("2026-10-16 12:00:00")
    assert out.dt.tz is None


def test_to_msk_one():
    assert series.to_msk_one(None) is None
    assert series.to_msk_one("2026-10-16T21:30:00Z") == datetime(2026, 10, 17, 0, 30)


# ----------------------------------------------------------------------------- freshness


def test_freshness_without_a_successful_run_is_gray():
    assert series.freshness(None, NOW) == ("gray", "⚪ Успешных сборов пока нет")


def test_freshness_recent_run_is_green_with_age():
    colour, text = series.freshness(ago(minutes=30), NOW)
    assert colour == "green"
    assert text == "🟢 Последний успешный сбор: 16.10 14:30 МСК (30 мин назад)"


def test_freshness_seconds_old_is_just_now():
    assert series.freshness(ago(seconds=30), NOW)[1].endswith("(только что)")


def test_freshness_two_hours_is_yellow_and_five_hours_red():
    colour, text = series.freshness(ago(hours=2), NOW)
    assert colour == "yellow"
    assert text.startswith("🟡") and text.endswith("(2 ч 0 мин назад)")
    colour, text = series.freshness(ago(hours=5), NOW)
    assert colour == "red" and text.startswith("🔴")


def test_freshness_days_use_the_day_format():
    assert series.freshness(ago(days=3), NOW)[1].endswith("(3 дн. 0 ч назад)")


@pytest.mark.parametrize(
    ("minutes", "colour"),
    [(89, "green"), (90, "yellow"), (179, "yellow"), (180, "red")],
)
def test_freshness_boundaries(minutes, colour):
    assert series.freshness(ago(minutes=minutes), NOW)[0] == colour


# --------------------------------------------------------------------------- heartbeat


def test_heartbeat_absent():
    assert (
        series.heartbeat_line(None, NOW, None) == "⚪ Сборщик: нет данных (файл сигнала не найден)"
    )


def test_heartbeat_live():
    line = series.heartbeat_line(NOW - timedelta(seconds=12), NOW, None)
    assert line == "🟢 Сборщик: на связи, сигнал 12 с назад"


def test_heartbeat_stale():
    line = series.heartbeat_line(NOW - timedelta(hours=2), NOW, None)
    assert line == "🟠 Сборщик: нет сигнала уже 2 ч 0 мин"


def test_heartbeat_stale_threshold_is_a_parameter():
    hb = NOW - timedelta(seconds=100)
    assert series.heartbeat_line(hb, NOW, None, stale_s=600).startswith("🟢")
    assert series.heartbeat_line(hb, NOW, None, stale_s=60).startswith("🟠")


@pytest.mark.parametrize("hb", [None, NOW - timedelta(hours=2)])
def test_young_running_run_wins_over_a_missing_or_stale_heartbeat(hb):
    run = {"status": "running", "started_at": ago(minutes=10)}
    assert series.heartbeat_line(hb, NOW, run) == "🟢 Сборщик: идёт сбор (начат 14:50 МСК)"


def test_old_running_run_does_not_count_as_alive():
    run = {"status": "running", "started_at": ago(minutes=70)}
    assert series.heartbeat_line(None, NOW, run).startswith("⚪")


# ------------------------------------------------------------------------------- banner


def run_row(status, *, n=3, ok=0, blocked=0, error=0, started=None):
    return {
        "status": status,
        "started_at": started or ago(minutes=5),
        "n_queries": n,
        "n_ok": ok,
        "n_blocked": blocked,
        "n_error": error,
    }


def test_banner_without_runs():
    assert series.banner(None, 0, None, NOW) == (
        "info",
        "Сборов ещё не было. Первый сбор — в ближайшие :00 по МСК.",
    )


def test_banner_running():
    kind, text = series.banner(run_row("running", started=ago(minutes=10)), 4, None, NOW)
    assert kind == "info"
    assert text == "⏳ Идёт сбор (начат 14:50 МСК). Данные появятся после завершения."


def test_banner_ok_is_silent():
    assert series.banner(run_row("ok", ok=3), 5, None, NOW) is None


def test_banner_partial():
    kind, text = series.banner(run_row("partial", ok=2, blocked=1), 5, 403, NOW)
    assert kind == "warning"
    assert text.startswith(
        "🟡 Последний сбор завершён частично: успешно 2 из 3 запросов, заблокировано 1, "
        "с ошибкой 0."
    )
    assert text.endswith("Следующая попытка в 16:00 МСК.")


def test_banner_blocked_with_earlier_successes():
    kind, text = series.banner(run_row("blocked", blocked=3), 4, 403, NOW)
    assert kind == "warning"
    assert text.startswith(
        "⛔ Последний сбор заблокирован Wildberries (HTTP 403) по 3 из 3 запросов."
    )
    assert "Следующая попытка в 16:00 МСК." in text


def test_banner_blocked_without_a_known_code_omits_http():
    text = series.banner(run_row("blocked", blocked=3), 4, None, NOW)[1]
    assert "Wildberries по 3 из 3 запросов" in text
    assert "HTTP" not in text


def test_banner_all_blocked_ever_uses_the_locked_copy():
    kind, text = series.banner(run_row("blocked", blocked=3), 0, 403, NOW)
    assert kind == "warning"
    assert text.startswith("⛔ Wildberries отклоняет запросы сборщика (антибот, HTTP 403). ")
    assert "данные не подставляются." in text


def test_banner_all_blocked_substitutes_the_observed_code_or_drops_it():
    assert "(антибот, HTTP 498)" in series.banner(run_row("blocked"), 0, 498, NOW)[1]
    text = series.banner(run_row("blocked"), 0, None, NOW)[1]
    assert "(антибот)" in text and "HTTP" not in text


def test_banner_error_and_abandoned():
    assert series.banner(run_row("error"), 2, None, NOW) == (
        "error",
        "❌ Последний сбор завершился ошибкой. Подробности — на вкладке «Журнал сборов». "
        "Следующая попытка в 16:00 МСК.",
    )
    kind, text = series.banner(run_row("abandoned"), 2, None, NOW)
    assert kind == "warning"
    assert text.startswith("⏸ Последний сбор был прерван")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 16, 9, 20, tzinfo=UTC), "13:00"),
        (datetime(2026, 10, 16, 20, 20, tzinfo=UTC), "00:00"),
        (datetime(2026, 10, 16, 9, 0, tzinfo=UTC), "13:00"),
    ],
)
def test_next_attempt(now, expected):
    assert series.next_attempt(now) == expected


# ---------------------------------------------------------------------------- run table


def log_frame(rows):
    columns = [
        "run_id", "trigger", "started_at", "finished_at", "status", "query_source", "top_n",
        "n_queries", "n_ok", "n_partial", "n_blocked", "n_error", "n_items", "error",
    ]  # fmt: skip
    return pd.DataFrame(rows, columns=columns).astype(
        {c: "Int64" for c in ("n_ok", "n_partial", "n_blocked", "n_error", "n_items")}
    )


def test_run_log_frame_columns_and_values():
    frame = series.run_log_frame(
        log_frame(
            [
                (2, "manual", "2026-10-16T09:00:00Z", "2026-10-16T09:04:12Z", "partial",
                 "bundled", 200, 3, 2, 0, 1, 0, 400, None),
                (1, "scheduler", "2026-10-16T08:00:00Z", "2026-10-16T08:00:42Z", "blocked",
                 "bundled", 200, 3, 0, 0, 3, 0, 0, None),
                (0, "scheduler", "2026-10-16T07:00:00Z", "2026-10-16T08:02:00Z", "ok",
                 "bundled", 200, 3, 3, 0, 0, 0, 600, None),
            ]
        )
    )  # fmt: skip
    assert list(frame.columns) == [
        "Начало (МСК)", "Длительность", "Тип", "Статус", "Успешных", "Частичных", "Заблок.",
        "Ошибок", "Товаров сохранено",
    ]  # fmt: skip
    assert frame["Начало (МСК)"].dt.tz is None
    assert frame["Начало (МСК)"].iloc[0] == pd.Timestamp("2026-10-16 12:00:00")
    assert list(frame["Длительность"]) == ["4 мин 12 с", "42 с", "1 ч 2 мин"]
    assert list(frame["Тип"]) == ["Вручную", "По расписанию", "По расписанию"]
    assert list(frame["Статус"]) == ["🟡 Частично", "⛔ Заблокировано", "✅ Успешно"]
    assert list(frame["Заблок."]) == [1, 3, 0]


def test_run_log_frame_running_and_unfinished_rows():
    frame = series.run_log_frame(
        log_frame(
            [
                (2, "manual", "2026-10-16T09:00:00Z", None, "running", "bundled", 200, 3,
                 0, 0, 0, 0, 0, None),
                (1, "scheduler", "2026-10-16T08:00:00Z", None, "error", "bundled", 200, 3,
                 0, 0, 0, 3, 0, None),
            ]
        )
    )  # fmt: skip
    assert list(frame["Длительность"]) == ["идёт", "—"]
    for column in ("Успешных", "Частичных", "Заблок.", "Ошибок", "Товаров сохранено"):
        assert frame[column].iloc[0] is pd.NA
    assert frame["Ошибок"].iloc[1] == 3


def test_run_log_frame_of_nothing_keeps_the_columns():
    empty = series.run_log_frame(log_frame([]))
    assert len(empty) == 0
    assert list(empty.columns)[0] == "Начало (МСК)"


# --------------------------------------------------------------------------------- totals


def test_totals_lines_for_zero_history():
    lines = series.totals_lines(
        {"n_runs": 0, "n_ok_runs": 0, "first_started": None, "n_obs": 0, "n_products": 0}
    )
    assert lines == [
        "Всего сборов: **0**",
        "Успешных сборов: **0**",
        "Первый сбор: —",
        "Наблюдений в базе: **0**",
        "Уникальных товаров: **0**",
    ]


def test_totals_lines_use_separators_and_moscow_time():
    lines = series.totals_lines(
        {
            "n_runs": 1200,
            "n_ok_runs": 1100,
            "first_started": "2026-10-16T09:00:00Z",
            "n_obs": 2236958,
            "n_products": 14000,
        }
    )
    assert lines[0] == "Всего сборов: **1 200**"
    assert lines[2] == "Первый сбор: **16.10.2026 12:00** МСК"
    assert lines[3] == "Наблюдений в базе: **2 236 958**"


# ------------------------------------------------------------------ honest gaps (03-02)

A, B, C, D, E, F = (NM[k] for k in "ABCDEF")


@pytest.fixture
def mixed(db_path):
    """Grid, observations of A, B and F, and the built ids of the mixed scenario's first query."""
    built = scenario_mixed(db_path)
    path, q1 = str(db_path), built.query_ids[Q1]
    grid = data.load_run_grid_raw(path, q1, 1, "")
    obs = data.load_observations_raw(path, q1, (A, B, C, D, E, F), "")
    return built, grid, obs


def grid_frame(rows):
    """Hand-built run grid: (run_id, started_at, q_status, is_complete, items_collected)."""
    return pd.DataFrame(
        {
            "run_id": [r[0] for r in rows],
            "started_at": [r[1] for r in rows],
            "q_status": pd.array([r[2] for r in rows], dtype="string"),
            "is_complete": pd.array([r[3] for r in rows], dtype="Int64"),
            "items_collected": pd.array([r[4] for r in rows], dtype="Int64"),
        }
    )


def obs_frame(rows):
    """Hand-built observations: (run_id, nm_id, position)."""
    return pd.DataFrame(
        {
            "run_id": pd.array([r[0] for r in rows], dtype="Int64"),
            "nm_id": pd.array([r[1] for r in rows], dtype="Int64"),
            "position": pd.array([r[2] for r in rows], dtype="Int64"),
            "is_promoted": pd.array([0] * len(rows), dtype="Int64"),
            "price_rub": pd.array([1.0] * len(rows), dtype="Float64"),
        }
    )


def state_of(points, run_id, nm_id):
    row = points[(points["run_id"] == run_id) & (points["nm_id"] == nm_id)]
    assert len(row) == 1
    return row.iloc[0]["state"]


def test_period_since_is_anchored_on_the_latest_run():
    last = "2026-10-16T09:00:00Z"
    assert series.period_since(last, "24 ч") == "2026-10-15T09:00:00Z"
    assert series.period_since(last, "7 дней") == "2026-10-09T09:00:00Z"


@pytest.mark.parametrize("period", ["Всё время", None, "неизвестно"])
def test_period_since_all_time_is_empty(period):
    assert series.period_since("2026-10-16T09:00:00Z", period) == ""


def test_period_since_without_any_run_is_empty():
    assert series.period_since(None, "24 ч") == ""


def test_prepare_grid_marks_complete_only_for_ok_and_complete():
    grid = grid_frame(
        [
            (1, "2026-10-16T09:00:00Z", "ok", 1, 10),
            (2, "2026-10-16T10:00:00Z", "ok", 0, 10),
            (3, "2026-10-16T11:00:00Z", "partial", 0, 4),
            (4, "2026-10-16T12:00:00Z", "blocked", 0, 0),
            (5, "2026-10-16T13:00:00Z", None, None, None),
        ]
    )
    out = series.prepare_grid(grid)
    assert list(out["complete"]) == [True, False, False, False, False]
    assert out["complete"].dtype == bool
    assert list(out["q_status"]) == ["ok", "ok", "partial", "blocked", "missing"]
    assert out["t_msk"].iloc[0] == pd.Timestamp("2026-10-16 12:00:00")


def test_prepare_grid_does_not_change_its_input():
    grid = grid_frame([(1, "2026-10-16T09:00:00Z", None, None, None)])
    series.prepare_grid(grid)
    assert "t_msk" not in grid.columns
    assert pd.isna(grid["q_status"].iloc[0])


def test_build_points_on_the_mixed_history(mixed):
    built, grid, obs = mixed
    r1, r2, r3, r4, r5, r6 = built.run_ids
    points = series.build_points(grid, obs[obs["nm_id"].isin([A, B, F])], [A, B, F])
    assert len(points) == 18
    assert set(points["nm_id"]) == {A, B, F}
    assert state_of(points, r3, B) == series.ABSENT
    assert state_of(points, r6, F) == series.ABSENT
    for nm_id in (A, B, F):
        assert state_of(points, r2, nm_id) == series.UNKNOWN
    assert state_of(points, r4, B) == series.UNKNOWN
    assert state_of(points, r4, A) == series.FOUND
    assert points[(points["run_id"] == r4) & (points["nm_id"] == A)].iloc[0]["position"] == 2
    a3 = points[(points["run_id"] == r3) & (points["nm_id"] == A)].iloc[0]
    assert a3["is_promoted"] == 1


def test_build_points_only_has_positions_for_found_points(mixed):
    _, grid, obs = mixed
    points = series.build_points(grid, obs, [A, B, C, D, E, F])
    found = points["state"] == series.FOUND
    assert points.loc[found, "position"].notna().all()
    assert points.loc[~found, "position"].isna().all()


def test_build_points_is_sorted_by_product_then_time(mixed):
    _, grid, obs = mixed
    points = series.build_points(grid, obs, [B, A])
    keys = list(zip(points["nm_id"], points["t_msk"], strict=True))
    assert keys == sorted(keys)


def test_build_points_does_not_invent_rows_for_unrequested_products(mixed):
    _, grid, obs = mixed
    points = series.build_points(grid, obs[obs["nm_id"] == A], [A, B])
    assert C not in set(points["nm_id"])
    assert len(points) == 12


def test_build_points_keeps_the_smaller_position_of_a_duplicate(mixed):
    built, grid, _ = mixed
    r1 = built.run_ids[0]
    obs = obs_frame([(r1, A, 7), (r1, A, 3)])
    points = series.build_points(grid, obs, [A])
    assert points[points["run_id"] == r1].iloc[0]["position"] == 3
    assert len(points) == len(grid)


def test_ok_but_incomplete_attempt_is_unknown_not_absent():
    grid = grid_frame([(1, "2026-10-16T09:00:00Z", "ok", 0, 10)])
    points = series.build_points(grid, obs_frame([]), [A])
    assert points.iloc[0]["state"] == series.UNKNOWN


def test_abandoned_attempt_without_a_row_is_unknown():
    grid = grid_frame([(1, "2026-10-16T09:00:00Z", None, None, None)])
    points = series.build_points(grid, obs_frame([]), [A])
    assert points.iloc[0]["state"] == series.UNKNOWN
    assert points.iloc[0]["q_status"] == "missing"


@pytest.mark.parametrize("status", ["blocked", "rate_limited", "error", "partial"])
def test_failed_attempts_are_unknown(status):
    grid = grid_frame([(1, "2026-10-16T09:00:00Z", status, 0, 0)])
    points = series.build_points(grid, obs_frame([]), [A])
    assert points.iloc[0]["state"] == series.UNKNOWN


def test_a_lone_found_point_between_absent_runs_stays_found():
    grid = grid_frame(
        [
            (1, "2026-10-16T09:00:00Z", "ok", 1, 10),
            (2, "2026-10-16T10:00:00Z", "ok", 1, 10),
            (3, "2026-10-16T11:00:00Z", "ok", 1, 10),
        ]
    )
    points = series.build_points(grid, obs_frame([(2, A, 4)]), [A])
    assert list(points["state"]) == [series.ABSENT, series.FOUND, series.ABSENT]


def test_build_points_with_no_observations_and_no_products():
    grid = grid_frame([(1, "2026-10-16T09:00:00Z", "ok", 1, 10)])
    assert series.build_points(grid, obs_frame([]), []).empty


def test_band_times_are_the_incomplete_runs(mixed):
    built, grid, _ = mixed
    prepared = series.prepare_grid(grid)
    expected = [
        prepared.loc[prepared["run_id"] == built.run_ids[1], "t_msk"].iloc[0],
        prepared.loc[prepared["run_id"] == built.run_ids[3], "t_msk"].iloc[0],
    ]
    assert list(series.band_times(grid)) == expected
    assert list(series.band_times(prepared)) == expected


def test_band_times_without_failures_is_empty():
    grid = grid_frame([(1, "2026-10-16T09:00:00Z", "ok", 1, 10)])
    assert series.band_times(grid).empty


def test_x_range_of_a_single_run_is_one_hour_wide():
    grid = grid_frame([(1, "2026-10-16T09:00:00Z", "ok", 1, 10)])
    lo, hi = series.x_range(grid)
    assert hi - lo == pd.Timedelta(minutes=60)
    assert lo == pd.Timestamp("2026-10-16 11:30:00")


def test_x_range_of_an_empty_grid_is_refused():
    with pytest.raises(ValueError):
        series.x_range(grid_frame([]))


def options_frame(rows):
    """(nm_id, last_run_id, last_position, best_position, brand, name)."""
    return pd.DataFrame(
        {
            "nm_id": pd.array([r[0] for r in rows], dtype="Int64"),
            "last_run_id": pd.array([r[1] for r in rows], dtype="Int64"),
            "last_position": pd.array([r[2] for r in rows], dtype="Int64"),
            "best_position": pd.array([r[3] for r in rows], dtype="Int64"),
            "brand": pd.array([r[4] for r in rows], dtype="string"),
            "name": pd.array([r[5] for r in rows], dtype="string"),
        }
    )


def test_product_options_frame_orders_by_current_then_best_position(mixed, db_path):
    built, _, _ = mixed
    options = data.load_product_options_raw(str(db_path), built.query_ids[Q1])
    frame = series.product_options_frame(options, built.run_ids[-1])
    assert list(frame["nm_id"]) == [A, B, D, C, E, F]
    assert list(frame["in_latest"]) == [True] * 5 + [False]
    assert frame["label"][0] == f"{A} · ТЕСТ-БРЕНД · ТЕСТ товар {A}"
    assert frame["label"][5].endswith(" · не в последней выдаче")
    assert not frame["label"][0].endswith("выдаче")


def test_product_options_frame_labels_cover_missing_brand_and_long_names():
    options = options_frame(
        [
            (1, 9, 1, 1, None, "x" * 60),
            (2, 9, 2, 2, "Бренд", None),
        ]
    )
    frame = series.product_options_frame(options, 9)
    assert frame["label"][0] == "1 · — · " + "x" * 40
    assert frame["label"][1] == "2 · Бренд · —"


def test_product_options_frame_without_a_latest_run_marks_everything_stale():
    options = options_frame([(1, 9, 1, 1, "b", "n"), (2, 9, 2, 2, "b", "n")])
    frame = series.product_options_frame(options, None)
    assert not frame["in_latest"].any()
    assert all(label.endswith("не в последней выдаче") for label in frame["label"])


def test_product_options_frame_puts_stale_products_last_by_best_position():
    options = options_frame(
        [
            (1, 5, 1, 1, "b", "n"),
            (2, 9, 4, 4, "b", "n"),
            (3, 5, 2, 2, "b", "n"),
            (4, 9, 3, 3, "b", "n"),
        ]
    )
    frame = series.product_options_frame(options, 9)
    assert list(frame["nm_id"]) == [4, 2, 1, 3]


def test_product_options_frame_of_nothing_is_empty():
    frame = series.product_options_frame(options_frame([]), 9)
    assert frame.empty
    assert list(frame.columns) == ["nm_id", "label", "in_latest"]


def test_default_selection_is_the_top_of_the_latest_run():
    top = (A, B, D, C, E)
    assert series.default_selection(top, [A, B, C, D, E, F], 5) == list(top)
    assert series.default_selection(top, [A, B, C, D, E, F], 2) == [A, B]


def test_default_selection_never_exceeds_the_picker_cap():
    ids = list(range(1, 19))
    assert series.default_selection(ids, ids, 20) == ids[:15]


def test_default_selection_drops_ids_the_picker_does_not_offer():
    assert series.default_selection((1, 2, 3), [1, 3], 3) == [1, 3]


def test_default_selection_of_an_empty_top_is_empty():
    assert series.default_selection((), [1, 2], 5) == []


def test_chart_modules_stay_pure():
    from pathlib import Path

    for module in (series, texts):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("import streamlit", "import sqlite3", "import numpy", "validate="):
            assert forbidden not in source
