"""Static validation of the packaging artifacts (Dockerfile, .dockerignore, ...).

Docker is not installed on the development machine, so nothing here builds or starts an image and
nothing here runs a collection: the files are parsed and checked against the locked topology.
The real ``docker compose up`` happens on the deploy host in Phase 4.

Paths come from ``REPO_ROOT`` because the autouse ``_isolated`` fixture chdirs into ``tmp_path``.
"""

from __future__ import annotations

import json
import re
import tomllib

import pytest

from factories import REPO_ROOT

DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
PYPROJECT = REPO_ROOT / "pyproject.toml"


# ---------------------------------------------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------------------------------------------


def parse_dockerfile(text: str) -> list[tuple[str, str]]:
    """Return ``(INSTRUCTION, args)`` pairs: backslash continuations joined, comments dropped."""
    instructions: list[tuple[str, str]] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if pending and line.startswith("#"):
            continue  # a comment line inside a continuation is ignored by Docker
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        full = (pending + line).strip()
        pending = ""
        if full:
            head, _, rest = full.partition(" ")
            instructions.append((head.upper(), rest.strip()))
    assert not pending, "Dockerfile ends inside a line continuation"
    return instructions


@pytest.fixture(scope="module")
def dockerfile() -> list[tuple[str, str]]:
    return parse_dockerfile(DOCKERFILE.read_text(encoding="utf-8"))


def _args(dockerfile: list[tuple[str, str]], name: str) -> list[str]:
    return [args for head, args in dockerfile if head == name]


def test_dockerfile_parser_handles_continuations_and_comments():
    parsed = parse_dockerfile("# c\nRUN a \\\n  # inner\n && b\n\nUSER x\n")
    assert parsed == [("RUN", "a && b"), ("USER", "x")]


def test_dockerfile_starts_with_mirror_friendly_base_image(dockerfile):
    assert dockerfile[0] == ("ARG", "PYTHON_IMAGE=python:3.12-slim")
    assert _args(dockerfile, "FROM") == ["${PYTHON_IMAGE}"]


def test_dockerfile_environment(dockerfile):
    env = " ".join(_args(dockerfile, "ENV"))
    for pair in (
        "PYTHONUNBUFFERED=1",
        "PYTHONDONTWRITEBYTECODE=1",
        "TZ=Europe/Moscow",
        "WBMON_DB_PATH=/data/wbmon.sqlite3",
    ):
        assert re.search(rf"(^|\s){re.escape(pair)}(\s|$)", env), pair


def test_dockerfile_creates_the_same_non_root_user_and_owns_data(dockerfile):
    runs = _args(dockerfile, "RUN")
    user_run = next(run for run in runs if "useradd" in run)
    assert "groupadd --gid 10001 app" in user_run
    assert re.search(r"useradd --uid 10001 --gid 10001 .*--create-home", user_run)
    assert "mkdir -p /data" in user_run
    assert "chown app:app /data" in user_run


def test_dockerfile_workdir_copy_and_pip_index_arg_before_install(dockerfile):
    assert ("WORKDIR", "/app") in dockerfile
    assert ("COPY", ". /app") in dockerfile
    heads = [(head, args) for head, args in dockerfile]
    arg_index = heads.index(("ARG", "PIP_INDEX_URL=https://pypi.org/simple"))
    install_index = next(i for i, (head, args) in enumerate(heads) if "pip install" in args)
    assert arg_index < install_index


def test_dockerfile_install_run_smoke_checks_package_and_tz_data(dockerfile):
    install = next(args for head, args in dockerfile if head == "RUN" and "pip install" in args)
    assert 'pip install --no-cache-dir ".[web]"' in install
    assert "wbmon --help" in install
    assert "load_bundled()" in install and "== 70" in install
    assert 'ZoneInfo("Europe/Moscow")' in install.replace("'", '"')


def test_dockerfile_runs_as_app_last_and_exposes_streamlit_port(dockerfile):
    users = [i for i, (head, _) in enumerate(dockerfile) if head == "USER"]
    runs = [i for i, (head, _) in enumerate(dockerfile) if head == "RUN"]
    assert dockerfile[users[-1]] == ("USER", "app")
    assert users[-1] > max(runs)
    assert ("EXPOSE", "8501") in dockerfile


def test_dockerfile_cmd_is_exec_form_scheduler(dockerfile):
    cmd = _args(dockerfile, "CMD")
    assert len(cmd) == 1
    assert json.loads(cmd[0]) == ["wbmon", "scheduler"]


def test_dockerfile_has_no_forbidden_constructs(dockerfile):
    text = DOCKERFILE.read_text(encoding="utf-8")
    heads = {head for head, _ in dockerfile}
    assert "HEALTHCHECK" not in heads  # healthchecks live in compose.yaml
    assert "ADD" not in heads
    assert not re.search(r"^#\s*syntax\s*=", text, re.MULTILINE | re.IGNORECASE)
    assert not re.search(r"\b(curl|wget)\b", "\n".join(args for _, args in dockerfile))
    assert ":latest" not in text


# ---------------------------------------------------------------------------------------------
# .dockerignore and pyproject coupling
# ---------------------------------------------------------------------------------------------


def _ignore_rules() -> list[str]:
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def test_dockerignore_is_an_allowlist():
    rules = _ignore_rules()
    assert rules[0] == "*"
    assert [r for r in rules if r.startswith("!")] == ["!pyproject.toml", "!src", "!.streamlit"]
    for pattern in ("**/__pycache__", "**/*.pyc", "**/*.sqlite3*"):
        assert pattern in rules


def test_dockerignore_never_reincludes_secrets_or_junk():
    reincluded = {r[1:] for r in _ignore_rules() if r.startswith("!")}
    for name in (".venv", ".git", ".planning", "data", "tests", ".env"):
        assert name not in reincluded


def test_pyproject_has_no_file_the_allowlist_would_exclude():
    text = PYPROJECT.read_text(encoding="utf-8")
    project = tomllib.loads(text)["project"]
    assert "readme" not in project
    assert "license-files" not in project
    assert not re.search(r"license\s*=\s*\{\s*file", text)
