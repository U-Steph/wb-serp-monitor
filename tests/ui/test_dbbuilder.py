"""The scenario builder reproduces the documented facts and refuses non-temporary paths."""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path

import pytest

from ui import dbbuilder
from ui.dbbuilder import (
    NM,
    Q1,
    Q2,
    REPO_ROOT,
    RunSpec,
    append_run,
    assert_temporary,
    build_db,
    failed_result,
    ok_result,
    scenario_all_blocked,
    scenario_latest,
    scenario_mixed,
)
from wbmon.storage.db import connect_ro


def rows(path, sql, params=()):
    with contextlib.closing(connect_ro(path)) as con:
        return con.execute(sql, params).fetchall()


def test_mixed_run_statuses_and_totals(db_path):
    built = scenario_mixed(db_path)
    assert len(built.run_ids) == 6
    statuses = [r[0] for r in rows(db_path, "SELECT status FROM runs ORDER BY id")]
    assert statuses == ["ok", "partial", "ok", "partial", "ok", "ok"]
    assert rows(db_path, "SELECT COUNT(*) FROM observations")[0][0] == 60
    assert rows(db_path, "SELECT COUNT(DISTINCT nm_id) FROM observations")[0][0] == 12
    names = rows(db_path, "SELECT DISTINCT name FROM observations")
    assert all(name.startswith("ТЕСТ") for (name,) in names)


def test_mixed_q1_attempts_match_the_scenario_table(db_path):
    built = scenario_mixed(db_path)
    q1 = built.query_ids[Q1]
    r2, r4 = built.run_ids[1], built.run_ids[3]
    blocked = rows(
        db_path,
        "SELECT status, http_status, error_class, items_collected FROM run_queries"
        " WHERE run_id = ? AND query_id = ?",
        (r2, q1),
    )
    assert blocked == [("blocked", 403, "http_403", 0)]
    partial = rows(
        db_path,
        "SELECT status, is_complete, http_status, error_class, items_collected FROM run_queries"
        " WHERE run_id = ? AND query_id = ?",
        (r4, q1),
    )
    assert partial == [("partial", 0, 503, "http_5xx", 2)]
    r3_order = rows(
        db_path,
        "SELECT nm_id, is_promoted FROM observations WHERE run_id = ? AND query_id = ?"
        " ORDER BY position",
        (built.run_ids[2], q1),
    )
    assert [n for n, _ in r3_order] == [NM[k] for k in "ACDEF"]
    assert [p for _, p in r3_order] == [1, 0, 0, 0, 0]


def test_all_blocked_has_no_observations(db_path):
    scenario_all_blocked(db_path, n_runs=3)
    assert [r[0] for r in rows(db_path, "SELECT status FROM runs")] == ["blocked"] * 3
    assert rows(db_path, "SELECT COUNT(*) FROM observations")[0][0] == 0


def test_latest_abandoned_and_running(db_path, tmp_path):
    scenario_latest(db_path, "abandoned")
    assert rows(db_path, "SELECT status FROM runs ORDER BY id DESC LIMIT 1") == [("abandoned",)]
    other = tmp_path / "running.sqlite3"
    built = scenario_latest(other, "running")
    assert rows(other, "SELECT status FROM runs ORDER BY id DESC LIMIT 1") == [("running",)]
    count = rows(other, "SELECT COUNT(*) FROM run_queries WHERE run_id = ?", (built.run_ids[-1],))
    assert count == [(1,)]


@pytest.mark.parametrize("final", ["partial", "blocked", "error"])
def test_latest_finished_variants(db_path, final):
    scenario_latest(db_path, final)
    assert rows(db_path, "SELECT status FROM runs ORDER BY id DESC LIMIT 1") == [(final,)]


def test_unknown_latest_status_is_rejected(db_path):
    with pytest.raises(ValueError, match="unknown final status"):
        scenario_latest(db_path, "nonsense")


def test_append_run_adds_one_run(db_path):
    scenario_mixed(db_path)
    spec = RunSpec(
        started_at="2099-01-01T00:00:00Z",
        results={Q2: ok_result("2099-01-01T00:00:00Z", (NM["A"],))},
        trigger="manual",
        n_queries=1,
    )
    run_id = append_run(db_path, spec)
    assert rows(db_path, "SELECT COUNT(*) FROM runs")[0][0] == 7
    assert rows(db_path, "SELECT trigger, slot_utc FROM runs WHERE id = ?", (run_id,)) == [
        ("manual", None)
    ]


def test_partial_result_requires_incomplete_and_error_class():
    with pytest.raises(ValueError, match="partial"):
        ok_result("2026-01-01T00:00:00Z", (1,), status="partial")
    assert failed_result("2026-01-01T00:00:00Z").status == "blocked"


def test_refuses_paths_inside_the_repository():
    target = REPO_ROOT / "data" / "x.sqlite3"
    with pytest.raises(ValueError, match="outside the temp directory|inside the repository"):
        build_db(target, [])
    assert not target.exists()
    assert not (REPO_ROOT / "data").exists()


def test_refuses_paths_outside_the_temp_directory():
    target = Path.home() / "wbmon-synthetic-must-not-exist.sqlite3"
    with pytest.raises(ValueError, match="outside the temp directory"):
        build_db(target, [])
    assert not target.exists()
    with pytest.raises(ValueError, match="outside the temp directory"):
        assert_temporary("/var/lib/wbmon.sqlite3")


def test_cli_refuses_repository_path_and_creates_nothing(tmp_path):
    target = REPO_ROOT / "data" / "x.sqlite3"
    script = Path(dbbuilder.__file__)
    proc = subprocess.run(
        [sys.executable, str(script), "--scenario", "mixed", str(target)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert proc.returncode != 0
    assert not (REPO_ROOT / "data").exists()
