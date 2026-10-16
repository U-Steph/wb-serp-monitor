"""End-to-end AppTests of the read-only shell: header, banner, sidebar totals, run log."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import tomllib
from datetime import timedelta
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
    ok_result,
    scenario_all_blocked,
    scenario_latest,
    scenario_mixed,
)
from wbmon.clock import iso_z, utc_now
from wbmon.storage.db import connect_ro

APP = str(Path(__file__).resolve().parents[2] / "src" / "wbmon" / "ui" / "app.py")
NO_SUCCESS = "Успешных сборов пока нет"


def run_app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=30).run()


def markdown_values(at: AppTest) -> list[str]:
    return [m.value for m in at.markdown]


def caption_values(at: AppTest) -> list[str]:
    return [c.value for c in at.caption] + [c.value for c in at.sidebar.caption]


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
    from wbmon.storage.migrations import init_db

    init_db(db_path)
    at = run_app()
    assert not at.exception
    assert has(badge_values(at), NO_SUCCESS)
    assert has([i.value for i in at.info], "Сборов ещё не было")
    captions = caption_values(at)
    assert has(captions, "Всего сборов: **0**")
    assert has(captions, "Первый сбор: —")
    assert has(captions, "Наблюдений в базе: **0**")


# --------------------------------------------------------------------------------- mixed history


def test_mixed_history_header_and_sidebar_totals(db_path):
    scenario_mixed(db_path)
    at = run_app()
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
    assert "🩺 Журнал сборов" in [tab.label for tab in at.tabs]


def test_mixed_history_run_table_is_newest_first(db_path):
    scenario_mixed(db_path)
    at = run_app()
    assert not at.exception
    frame = find_df(at, "Статус")
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
    at = run_app()
    assert not at.exception
    assert has(
        [w.value for w in at.warning],
        "Wildberries отклоняет запросы сборщика (антибот, HTTP 403)",
    )
    assert has(badge_values(at), NO_SUCCESS)
    captions = caption_values(at)
    assert has(captions, "Успешных сборов: **0**")
    assert has(captions, "Наблюдений в базе: **0**")
    frame = find_df(at, "Статус")
    assert len(frame) == 3
    assert set(frame["Статус"]) == {"⛔ Заблокировано"}


# ----------------------------------------------------------------------------------- the banner


def test_latest_running_shows_info_and_collector_line(db_path):
    scenario_latest(db_path, "running")
    at = run_app()
    assert not at.exception
    assert has([i.value for i in at.info], "⏳ Идёт сбор (начат")
    assert has(caption_values(at), "идёт сбор")
    assert find_df(at, "Статус")["Статус"].iloc[0] == "⏳ Идёт"


@pytest.mark.parametrize(
    ("final", "kind", "needle"),
    [
        (
            "partial",
            "warning",
            "🟡 Последний сбор завершён частично: успешно 2 из 3 запросов, "
            "заблокировано 1, с ошибкой 0",
        ),
        (
            "blocked",
            "warning",
            "⛔ Последний сбор заблокирован Wildberries (HTTP 403) по 3 из 3 запросов",
        ),
        ("error", "error", "❌ Последний сбор завершился ошибкой"),
        ("abandoned", "warning", "⏸ Последний сбор был прерван"),
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
    assert has(caption_values(at), "на связи, сигнал")


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


# ---------------------------------------------------------------- cache invalidation, read-only


def test_new_run_appears_without_restart(db_path):
    scenario_mixed(db_path)
    at = run_app()
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
    assert len(find_df(at, "Статус")) == 7


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


def test_streamlit_reads_the_theme_file_from_the_repo_root():
    script = (
        "from streamlit import config; "
        "print(config.get_option('client.showErrorDetails'), "
        "config.get_option('theme.primaryColor'))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["none", "#7B2CBF"]
