"""Demo mode: a separate synthetic temp database that can never be the working one."""

from __future__ import annotations

import contextlib
import json
import tempfile
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui.dbbuilder import scenario_all_blocked
from wbmon.clock import utc_now
from wbmon.queries import load_bundled
from wbmon.storage.db import connect_ro
from wbmon.ui import demo, texts

APP = str(Path(__file__).resolve().parents[2] / "src" / "wbmon" / "ui" / "app.py")


def run_app(section: str | None = None, *, mode: str | None = None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    if section is not None:
        at.session_state["section"] = section
    if mode is not None:
        at.session_state["mode"] = mode
    return at.run()


def markdown_values(at: AppTest) -> list[str]:
    return [m.value for m in at.markdown]


def chart_specs(at: AppTest) -> list[dict]:
    return [json.loads(el.proto.spec) for el in at.get("plotly_chart")]


def table_counts(path: Path) -> tuple[int, int, int]:
    with contextlib.closing(connect_ro(path)) as con:
        return tuple(  # type: ignore[return-value]
            con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("runs", "run_queries", "observations")
        )


# ------------------------------------------------------------------------------- path guards


def test_demo_path_is_inside_the_temp_directory_and_per_user():
    path = demo.demo_db_path()
    assert path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert path.name == "demo.sqlite3"
    assert path.parent.name.startswith("wbmon-demo-")


def test_the_real_database_path_is_refused(demo_tmp):
    with pytest.raises(ValueError, match="working database"):
        demo.check_demo_path(demo.demo_db_path(), demo.demo_db_path())


def test_a_path_outside_the_temp_directory_is_refused(demo_tmp):
    with pytest.raises(ValueError, match="temporary directory"):
        demo.check_demo_path(Path("/var/lib/wbmon/demo.sqlite3"), demo_tmp / "real.sqlite3")


def test_a_symlinked_demo_directory_is_refused(demo_tmp):
    target = demo_tmp / "elsewhere"
    target.mkdir()
    demo.demo_db_path().parent.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        demo.ensure_demo_db(demo_tmp / "real.sqlite3")
    assert not (target / "demo.sqlite3").exists()


# --------------------------------------------------------------------------------- content


def test_ensure_demo_db_builds_the_documented_history(demo_tmp):
    real = demo_tmp / "real.sqlite3"
    path = demo.ensure_demo_db(real)
    assert path != real and not real.exists()
    assert path.parent.stat().st_mode & 0o777 == 0o700
    with contextlib.closing(connect_ro(path)) as con:
        texts_ = [r[0] for r in con.execute("SELECT text FROM queries ORDER BY sheet_order")]
        assert texts_ == list(load_bundled()[:6])
        assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 48
        params = {r[0] for r in con.execute("SELECT params_json FROM runs")}
        assert {json.loads(p) == {"demo": True} for p in params} == {True}
        assert con.execute("SELECT MAX(position) FROM observations").fetchone()[0] <= 30
        assert con.execute("SELECT COUNT(*) FROM observations WHERE is_promoted = 1").fetchone()[0]
        assert con.execute("SELECT COUNT(*) FROM runs WHERE status <> 'ok'").fetchone()[0] >= 2
        assert (
            con.execute("SELECT COUNT(*) FROM observations WHERE name LIKE '%ТЕСТ%'").fetchone()[0]
            == 0
        )
        # a real gap: present in a complete run, absent in the next complete one, back later
        rows = con.execute(
            "SELECT rq.query_id, rq.run_id, o.nm_id FROM run_queries rq "
            "LEFT JOIN observations o ON o.run_id = rq.run_id AND o.query_id = rq.query_id "
            "WHERE rq.status = 'ok' AND rq.is_complete = 1 ORDER BY rq.query_id, rq.run_id"
        ).fetchall()
    seen: dict[tuple[int, int], list[int]] = {}
    runs_of: dict[int, list[int]] = {}
    for query_id, run_id, nm_id in rows:
        runs = runs_of.setdefault(query_id, [])
        if not runs or runs[-1] != run_id:
            runs.append(run_id)
        seen.setdefault((query_id, nm_id), []).append(run_id)
    gap = False
    for (query_id, nm_id), present in seen.items():
        if nm_id is None:
            continue
        index = {run: i for i, run in enumerate(runs_of[query_id])}
        steps = [index[r] for r in present]
        gap = gap or any(b - a > 1 for a, b in zip(steps, steps[1:], strict=False))
    assert gap


def test_ensure_demo_db_is_rebuilt_from_scratch(demo_tmp):
    real = demo_tmp / "real.sqlite3"
    first = demo.ensure_demo_db(real, utc_now())
    second = demo.ensure_demo_db(real, utc_now())
    assert first == second
    assert table_counts(second)[0] == 48


# --------------------------------------------------------------------------------- the app


def test_demo_mode_never_touches_the_real_database(db_path, demo_tmp):
    scenario_all_blocked(db_path)
    before_stat = db_path.stat()
    before = table_counts(db_path)
    for section in texts.NAV_OPTIONS:
        at = run_app(section, mode=texts.MODE_DEMO)
        assert not at.exception
        assert not at.error
    after_stat = db_path.stat()
    assert (after_stat.st_size, after_stat.st_mtime_ns) == (
        before_stat.st_size,
        before_stat.st_mtime_ns,
    )
    assert table_counts(db_path) == before
    assert demo.demo_db_path() != db_path
    assert demo.demo_db_path().is_file()


def test_demo_positions_show_all_four_labels(db_path, demo_tmp):
    scenario_all_blocked(db_path)
    at = run_app(mode=texts.MODE_DEMO)
    assert not at.exception
    assert at.segmented_control(key="mode").value == texts.MODE_DEMO
    shown = markdown_values(at)
    assert texts.PILL_DEMO in shown
    assert any("Демо-режим." in v for v in shown)
    assert at.button(key="demo_off").label == texts.DEMO_OFF
    (spec,) = chart_specs(at)
    assert [a["text"] for a in spec["layout"]["annotations"]] == ["ДЕМО"]
    assert at.multiselect[0].value  # the demo has products, unlike the blocked real database
    assert not at.get("tabs")


def test_real_mode_has_no_watermark_and_no_demo_card(db_path, demo_tmp):
    from ui.dbbuilder import scenario_mixed

    scenario_mixed(db_path)
    at = run_app()
    (spec,) = chart_specs(at)
    assert not spec["layout"].get("annotations")
    assert texts.PILL_DEMO not in markdown_values(at)
    assert "demo_off" not in [b.key for b in at.button]


def test_demo_snapshot_and_run_log_render_synthetic_data(db_path, demo_tmp):
    scenario_all_blocked(db_path)
    at = run_app(texts.TAB_SNAPSHOT, mode=texts.MODE_DEMO)
    assert not at.exception
    assert len(at.metric) == 4
    assert at.metric[0].value == "30"
    at = run_app(texts.TAB_RUNLOG, mode=texts.MODE_DEMO)
    assert not at.exception
    heat = next(s for s in chart_specs(at) if s["data"][0]["type"] == "heatmap")
    assert len(heat["data"][0]["x"]) == 48


def test_the_empty_state_button_switches_to_demo_and_back(db_path, demo_tmp):
    scenario_all_blocked(db_path)
    at = run_app()
    assert "Данных о товарах пока нет" in [h.value for h in at.subheader]
    at.button(key="demo_on").click().run()
    assert not at.exception
    assert at.segmented_control(key="mode").value == texts.MODE_DEMO
    (spec,) = chart_specs(at)
    assert [a["text"] for a in spec["layout"]["annotations"]] == ["ДЕМО"]
    at.button(key="demo_off").click().run()
    assert not at.exception
    assert at.segmented_control(key="mode").value == texts.MODE_REAL
    assert "Данных о товарах пока нет" in [h.value for h in at.subheader]
    assert not chart_specs(at)


def test_a_failed_demo_build_shows_the_fixed_copy_and_keeps_the_page(
    db_path, demo_tmp, monkeypatch
):
    scenario_all_blocked(db_path)

    def boom(*_args, **_kwargs):
        raise OSError("disk full: /secret/path")

    monkeypatch.setattr(demo, "ensure_demo_db", boom)
    at = run_app(mode=texts.MODE_DEMO)
    assert not at.exception
    assert [e.value for e in at.error] == [texts.DEMO_ERROR]
    assert not any("secret" in v for v in markdown_values(at))
