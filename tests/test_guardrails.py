"""Hard-constraint guardrails: no circumvention tooling, no history rewrites, no network."""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import tomllib
from pathlib import Path

import pytest

from factories import FIXTURES_DIR, REPO_ROOT, SRC_DIR, module_imports

CIRCUMVENTION_DENYLIST = (
    "curl_cffi",
    "curl-cffi",
    "tls_client",
    "tls-client",
    "playwright",
    "selenium",
    "undetected-chromedriver",
    "undetected_chromedriver",
    "cloudscraper",
    "requests-html",
    "pyppeteer",
    "fake-useragent",
    "fake_useragent",
    "httpx-socks",
    "pysocks",
    "2captcha",
    "twocaptcha",
    "anticaptcha",
    "capsolver",
)

FORBIDDEN_SOURCE_TEXT = (
    "OR REPLACE",
    "REPLACE INTO",
    "x_wbaas",
    "verify=False",
    "proxy=",
    "proxies=",
    "http2=True",
    "_test_only",
    "factories",
)


UI_DIR = SRC_DIR / "ui"
UI_THIRD_PARTY = frozenset({"streamlit", "pandas", "plotly"})  # D3-A1: the read-only UI stack


def _pyproject() -> tuple[str, dict]:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return text, tomllib.loads(text)


def _src_files() -> list[Path]:
    return sorted(SRC_DIR.rglob("*.py"))


def test_runtime_dependencies_are_exactly_httpx_and_tzdata():
    _, data = _pyproject()
    names = {
        re.match(r"[A-Za-z0-9_.-]+", dep).group(0).lower()
        for dep in data["project"]["dependencies"]
    }
    assert names == {"httpx", "tzdata"}


def test_no_circumvention_package_named_in_pyproject():
    text, _ = _pyproject()
    lowered = text.lower()
    assert [name for name in CIRCUMVENTION_DENYLIST if name in lowered] == []


def _third_party(paths: list[Path]) -> set[str]:
    names: set[str] = set()
    for path in paths:
        names |= {
            name
            for name in module_imports(path)
            if name not in sys.stdlib_module_names and name not in {"wbmon", ""}
        }
    return names


def test_third_party_imports_in_src_are_httpx_only():
    core = [path for path in _src_files() if not path.is_relative_to(UI_DIR)]
    assert _third_party(core) <= {"httpx"}


def test_ui_third_party_imports_are_streamlit_pandas_plotly_only():
    ui = [path for path in _src_files() if path.is_relative_to(UI_DIR)]
    assert _third_party(ui) <= UI_THIRD_PARTY


@pytest.mark.parametrize("needle", FORBIDDEN_SOURCE_TEXT)
def test_src_never_contains_forbidden_text(needle):
    haystack = "\n".join(p.read_text(encoding="utf-8") for p in _src_files()).upper()
    assert needle.upper() not in haystack


def test_fixtures_are_marked_test_only():
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload.get("_test_only") is True, path.name


def test_network_is_blocked_inside_tests():
    with pytest.raises(RuntimeError, match="network access is forbidden"):
        socket.getaddrinfo("search.wb.ru", 443)


def test_cwd_and_db_path_are_temporary(tmp_path):
    assert Path.cwd().resolve() == tmp_path.resolve()
    db_path = Path(os.environ["WBMON_DB_PATH"]).resolve()
    assert db_path.is_relative_to(tmp_path.resolve())
    assert not (REPO_ROOT / "data").exists()


# ------------------------------------------------------------------------- CI hardening (IN-09)

CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_workflow_is_least_privilege_and_covers_supported_pythons():
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^permissions:\n  contents: read$", text, re.MULTILINE)
    assert re.search(r"^concurrency:\n  group: \S.*\n  cancel-in-progress: true$", text, re.M)
    matrix = re.search(r"python-version: \[(.*?)\]", text)
    assert matrix is not None
    assert [v.strip(' "') for v in matrix.group(1).split(",")] == ["3.11", "3.12", "3.13", "3.14"]
    assert "pull_request_target" not in text
