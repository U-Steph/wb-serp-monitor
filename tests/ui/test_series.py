"""Pure header logic, run-log shaping and Russian formatters (fixed clocks, no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from wbmon.ui import series, texts

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
