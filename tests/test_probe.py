"""`wbmon probe`: one request, one JSON line, nothing written, blocked is a normal result."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from factories import FakeWB, RecordingSleep, html_response, json_response, make_page, make_settings


def run_probe(capsys, fake: FakeWB, argv=("probe",)):
    from wbmon import cli

    sleep = RecordingSleep()
    code = cli.main(list(argv), _transport=httpx.MockTransport(fake), _sleep=sleep)
    out = capsys.readouterr().out
    return code, out, sleep


def first_query() -> str:
    from wbmon.queries import load_bundled

    return load_bundled()[0]


def assert_nothing_written() -> None:
    assert not Path(os.environ["WBMON_DB_PATH"]).exists()
    assert not Path("data").exists()
    assert list(Path(".").iterdir()) == []


def test_probe_blocked_is_reported_with_exit_zero(capsys):
    q1 = first_query()
    fake = FakeWB({(q1, 1): [html_response(403, headers={"server": "wbaas"})]})

    code, out, sleep = run_probe(capsys, fake)

    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 1
    summary = json.loads(lines[0])
    settings = make_settings()
    assert summary["outcome"] == "blocked"
    assert summary["error_class"] == "http_403"
    assert summary["http_status"] == 403
    assert summary["server"] == "wbaas"
    assert summary["products"] is None
    assert summary["endpoint"] == settings.search_url
    assert summary["params"]["query"] == q1
    assert summary["params"] == settings.search_params(q1, 1)
    assert summary["user_agent"] == settings.user_agent
    assert "server=wbaas" in summary["message"]
    assert summary["attempts"] == 1
    assert len(fake.wb_requests()) == 1
    assert fake.sheet_requests() == []
    assert sleep.calls == []
    assert_nothing_written()


def test_probe_5xx_is_a_single_request_without_retry(capsys):
    q1 = first_query()
    fake = FakeWB({(q1, 1): [httpx.Response(502, text="bad gateway")]})

    code, out, sleep = run_probe(capsys, fake)

    summary = json.loads(out)
    assert code == 0
    assert (summary["outcome"], summary["error_class"]) == ("error", "http_5xx")
    assert len(fake.requests) == 1
    assert sleep.calls == []
    assert_nothing_written()


def test_probe_custom_query_ok_page(capsys):
    query = "постельное бельё"
    fake = FakeWB({(query, 1): [json_response(make_page(900000001, 3))]})

    code, out, _ = run_probe(capsys, fake, ("probe", "--query", query))

    summary = json.loads(out)
    assert code == 0
    assert summary["outcome"] == "ok"
    assert summary["error_class"] is None
    assert summary["products"] == 3
    assert summary["normquery"] == "тест запрос"
    assert summary["shape"] == "data.products"
    assert summary["params"]["query"] == query
    assert summary["message"] is None
    assert_nothing_written()


def test_probe_output_is_utf8_json_not_ascii_escaped(capsys):
    query = "бельё"
    fake = FakeWB({(query, 1): [json_response(make_page(900000001, 1))]})

    _, out, _ = run_probe(capsys, fake, ("probe", "--query", query))

    assert "бельё" in out


def test_probe_bad_configuration_exits_two(capsys, monkeypatch):
    from wbmon import cli

    monkeypatch.setenv("WBMON_TOP_N", "zero")
    assert cli.main(["probe"]) == 2
    assert "configuration error" in capsys.readouterr().err
    assert_nothing_written()


def test_probe_is_listed_in_help(capsys):
    from wbmon import cli

    assert cli.main(["--help"]) == 0
    assert "probe" in capsys.readouterr().out


def test_probe_uses_single_request_without_retry_flag():
    from factories import SRC_DIR

    assert "retry=False" in (SRC_DIR / "cli.py").read_text(encoding="utf-8")
