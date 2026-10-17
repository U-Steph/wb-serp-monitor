"""Source-level guard of the UI: it can only read, and it cannot become a Markdown/HTML sink.

The checks are plain functions over source text so that each one is also proven on a deliberately
bad snippet (a guard that never fires would be worth nothing). They run over every file of
``src/wbmon/ui``. Naming the forbidden strings here is fine: this file lives in ``tests/``.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
import tomllib
from pathlib import Path

import pytest

from factories import PURE_FORBIDDEN_IMPORTS, SRC_DIR, module_imports
from ui.dbbuilder import REPO_ROOT

UI_DIR = SRC_DIR / "ui"
UI_FILES = sorted(UI_DIR.glob("*.py"))
PURE_FILES = [UI_DIR / "series.py", UI_DIR / "charts.py", UI_DIR / "texts.py"]

THIRD_PARTY_ALLOWED = {"streamlit", "pandas", "plotly"}
NEVER_IMPORTED = {"numpy", "httpx", "socket", "subprocess", "requests", "urllib"}
# module -> the names that may be imported from it (None: any name)
WBMON_ALLOWED: dict[str, set[str] | None] = {
    "wbmon": {"REPO_URL", "clock"},
    "wbmon.clock": None,
    "wbmon.config": None,
    "wbmon.storage.db": {"connect_ro"},
    "wbmon.storage.reader": {"MAX_IN_IDS"},
    "wbmon.ui": None,
}
# demo.py is the only UI module that builds a database (a synthetic one in the temp directory)
DEMO_EXTRA: dict[str, set[str] | None] = {
    "wbmon.storage.db": {"connect_ro", "connect_rw"},
    "wbmon.storage.migrations": {"init_db"},
    "wbmon.storage.writer": {"Writer"},
    "wbmon.models": {"ObservationRow", "QueryResult"},
    "wbmon.queries": {"load_bundled"},
}
DEMO_ALLOWED: dict[str, set[str] | None] = {**WBMON_ALLOWED, **DEMO_EXTRA}
FORBIDDEN_CALLS = (
    "unsafe_allow_html",
    "use_container_width",
    "connect_rw",
    "st.write(",
    "st.html(",
    "components.html",
    "add_vrect",
    "add_shape",
)
WRITE_WORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "ATTACH",
    "VACUUM",
    "REPLACE",
)
WRITE_WORD_RE = re.compile(r"\b(" + "|".join(WRITE_WORDS) + r")\b")
PRAGMA_ASSIGN_RE = re.compile(r"PRAGMA\s+\w+\s*=")


def code_text(source: str) -> str:
    """The source with every comment blanked out (docstrings and strings stay)."""
    lines = source.splitlines()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, col = token.start[0] - 1, token.start[1]
            lines[row] = lines[row][:col]
    return "\n".join(lines)


def imports_of(source: str) -> list[tuple[str, set[str]]]:
    """Every import as (full module name, imported names); ``import a.b`` gives ('a.b', set())."""
    found: list[tuple[str, set[str]]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [(alias.name, set()) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.module, {alias.name for alias in node.names}))
    return found


def import_violations(
    source: str, allowed: dict[str, set[str] | None] = WBMON_ALLOWED
) -> list[str]:
    problems: list[str] = []
    for module, names in imports_of(source):
        top = module.split(".")[0]
        if top in NEVER_IMPORTED:
            problems.append(f"network/process/numpy module: {module}")
        elif top == "wbmon":
            key = module if module in allowed else None
            if key is None and module.startswith("wbmon.ui."):
                key = "wbmon.ui"
            if key is None:
                problems.append(f"wbmon module outside the allow-list: {module}")
            elif allowed[key] is not None and not names <= allowed[key]:
                problems.append(f"{module}: {sorted(names - allowed[key])}")
        elif top not in sys.stdlib_module_names and top not in THIRD_PARTY_ALLOWED:
            problems.append(f"third-party module outside streamlit/pandas/plotly: {module}")
    return problems


def forbidden_call_violations(source: str) -> list[str]:
    text = code_text(source)
    return [needle for needle in FORBIDDEN_CALLS if needle in text]


def sql_violations(source: str) -> list[str]:
    """Writes, by SQL constant (must start with SELECT/WITH) and by raw uppercase keywords."""
    problems: list[str] = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.endswith(("_SQL", "_SQL_TEMPLATE")):
                if not node.value.value.strip().upper().startswith(("SELECT", "WITH")):
                    problems.append(f"{target.id} does not start with SELECT/WITH")
    text = code_text(source)
    problems += [f"write keyword {m.group(1)}" for m in WRITE_WORD_RE.finditer(text)]
    problems += [f"pragma assignment {m.group(0)!r}" for m in PRAGMA_ASSIGN_RE.finditer(text)]
    return problems


def _is_st_call(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
    )


def widget_violations(source: str) -> list[str]:
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords}
        if "unsafe_allow_html" in keywords:
            problems.append(f"line {node.lineno}: unsafe_allow_html")
        if _is_st_call(node, "dataframe"):
            hidden = keywords.get("hide_index")
            if not (isinstance(hidden, ast.Constant) and hidden.value is True):
                problems.append(f"line {node.lineno}: st.dataframe without hide_index=True")
            if "placeholder" not in keywords:
                problems.append(f"line {node.lineno}: st.dataframe without placeholder")
        if _is_st_call(node, "plotly_chart"):
            width = keywords.get("width")
            if not (isinstance(width, ast.Constant) and width.value == "stretch"):
                problems.append(f"line {node.lineno}: st.plotly_chart without width='stretch'")
    return problems


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- the real tree


def test_the_ui_tree_exists():
    assert {p.name for p in UI_FILES} >= {"app.py", "charts.py", "data.py", "series.py", "texts.py"}


def test_imports_are_the_read_only_stack_and_the_wbmon_allow_list():
    for path in UI_FILES:
        if path.name != "demo.py":  # the demo builder has its own, wider list (next test)
            assert import_violations(read(path)) == [], path.name


def test_demo_module_is_the_only_writer():
    demo = read(UI_DIR / "demo.py")
    assert import_violations(demo, DEMO_ALLOWED) == []
    assert import_violations(demo) != []  # the read-only list would reject it
    assert "streamlit" not in {module.split(".")[0] for module, _ in imports_of(demo)}
    for path in UI_FILES:
        if path.name == "demo.py":
            continue
        text = code_text(read(path))
        for needle in ("connect_rw", "Writer", "init_db"):
            assert needle not in text, (path.name, needle)


def test_pure_modules_import_no_io_and_no_streamlit():
    forbidden = PURE_FORBIDDEN_IMPORTS | {"streamlit", "numpy"}
    for path in PURE_FILES:
        assert module_imports(path) & forbidden == set(), path.name


def test_no_writer_or_html_sinks_anywhere_in_the_ui():
    for path in UI_FILES:
        found = forbidden_call_violations(read(path))
        if path.name == "theme.py":  # the one HTML sink, checked by its own test below
            found = [needle for needle in found if needle != "st.html("]
        if path.name == "demo.py":  # the one module that builds the synthetic temp database
            found = [needle for needle in found if needle != "connect_rw"]
        assert found == [], path.name


def test_theme_is_the_only_html_sink_and_injects_a_constant():
    tree = ast.parse(read(UI_DIR / "theme.py"))
    sinks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "html"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
    ]
    assert len(sinks) == 1
    assert len(sinks[0].args) == 1 and not sinks[0].keywords
    assert isinstance(sinks[0].args[0], ast.Name) and sinks[0].args[0].id == "STYLE"
    assigned = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "STYLE" for t in node.targets)
    ]
    assert len(assigned) == 1
    assert isinstance(assigned[0].value, ast.Constant) and isinstance(assigned[0].value.value, str)
    style = assigned[0].value.value
    assert style.startswith("<style>") and style.endswith("</style>")
    for needle in ("<script", "javascript:", "@import"):
        assert needle not in style
    for path in UI_FILES:
        if path.name != "theme.py":
            assert "st.html(" not in code_text(read(path)), path.name


def test_sql_is_select_only_and_no_write_keyword_or_pragma_assignment_appears():
    for path in UI_FILES:
        assert sql_violations(read(path)) == [], path.name


def test_data_py_declares_its_sql_as_constants():
    tree = ast.parse(read(UI_DIR / "data.py"))
    names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.endswith(("_SQL", "_SQL_TEMPLATE"))
    }
    assert {"RUN_LOG_SQL", "STATUS_MATRIX_SQL", "OBSERVATIONS_SQL_TEMPLATE"} <= names
    assert len(names) >= 15


def test_every_dataframe_and_chart_call_in_the_app_is_safe():
    assert widget_violations(read(UI_DIR / "app.py")) == []
    for path in UI_FILES:
        assert widget_violations(read(path)) == [], path.name


def test_errors_are_never_shown_to_the_visitor():
    config = tomllib.loads(read(REPO_ROOT / ".streamlit" / "config.toml"))
    assert config["client"]["showErrorDetails"] == "none"


# --------------------------------------------------------------- the guard fires on bad code


@pytest.mark.parametrize(
    "bad",
    [
        "import numpy",
        "import httpx",
        "from socket import socket",
        "import requests",
        "from urllib.request import urlopen",
        "import yaml",
        "from wbmon.storage.writer import Writer",
        "from wbmon.storage.migrations import init_db",
        "from wbmon.storage.lock import RunLock",
        "from wbmon.collector import collect",
        "from wbmon.wb_client import WbClient",
        "from wbmon.storage.db import connect_rw",
        "from wbmon.storage.reader import list_queries",
    ],
)
def test_import_guard_fires(bad):
    assert import_violations(bad) != []


@pytest.mark.parametrize(
    "good",
    [
        "import pandas as pd",
        "import streamlit as st",
        "import plotly.graph_objects as go",
        "from wbmon.storage.db import connect_ro",
        "from wbmon import REPO_URL, clock",
        "from wbmon.ui import charts, data",
    ],
)
def test_import_guard_accepts_the_allowed_stack(good):
    assert import_violations(good) == []


@pytest.mark.parametrize(
    "bad",
    [
        "st.markdown(x, unsafe_allow_html=True)",
        "st.plotly_chart(fig, use_container_width=True)",
        "st.write(x)",
        "st.html(x)",
        "components.html(x)",
        "con = connect_rw(path)",
        "fig.add_vrect(x0=1, x1=2)",
        "fig.add_shape(type='rect')",
    ],
)
def test_call_guard_fires(bad):
    assert forbidden_call_violations(bad) != []


def test_call_guard_ignores_comments():
    assert forbidden_call_violations("x = 1  # st.write(x) and unsafe_allow_html\n") == []


@pytest.mark.parametrize(
    "bad",
    [
        'FOO_SQL = "DELETE FROM runs"',
        'FOO_SQL = "  insert into runs values (1)"',
        'FOO_SQL_TEMPLATE = "UPDATE runs SET status = 1"',
        'x = "DROP TABLE runs"',
        'x = "CREATE INDEX i ON runs(id)"',
        'x = "ATTACH DATABASE y AS z"',
        'x = "VACUUM"',
        'x = "REPLACE INTO runs VALUES (1)"',
        'x = "PRAGMA journal_mode = WAL"',
    ],
)
def test_sql_guard_fires(bad):
    assert sql_violations(bad) != []


def test_sql_guard_accepts_selects_and_pragma_reads():
    source = 'A_SQL = "SELECT 1"\nB_SQL = """\nWITH x AS (SELECT 1) SELECT * FROM x"""\n'
    source += 'y = "PRAGMA user_version"\n'
    assert sql_violations(source) == []


@pytest.mark.parametrize(
    "bad",
    [
        "st.dataframe(df)",
        "st.dataframe(df, hide_index=True)",
        "st.dataframe(df, placeholder='-')",
        "st.dataframe(df, hide_index=False, placeholder='-')",
        "st.plotly_chart(fig)",
        "st.plotly_chart(fig, width='content')",
        "st.caption(x, unsafe_allow_html=True)",
    ],
)
def test_widget_guard_fires(bad):
    assert widget_violations(bad) != []


def test_widget_guard_accepts_the_contract_calls():
    good = (
        "st.dataframe(df, hide_index=True, placeholder='-', width='stretch')\n"
        "st.plotly_chart(fig, width='stretch', config={})\n"
    )
    assert widget_violations(good) == []
