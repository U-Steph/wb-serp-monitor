"""Static validation of the packaging artifacts (Dockerfile, .dockerignore, ...).

Docker is not installed on the development machine, so nothing here builds or starts an image and
nothing here runs a collection: the files are parsed and checked against the locked topology.
The real ``docker compose up`` happens on the deploy host in Phase 4.

Paths come from ``REPO_ROOT`` because the autouse ``_isolated`` fixture chdirs into ``tmp_path``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import tomllib

import pytest

from factories import REPO_ROOT

DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
PYPROJECT = REPO_ROOT / "pyproject.toml"
COMPOSE = REPO_ROOT / "compose.yaml"
CADDYFILE = REPO_ROOT / "deploy" / "Caddyfile"

# Vendored official Compose Specification schema (tests/data/compose-spec.json). Never edit it by
# hand; to refresh, re-download it and update the pinned hash deliberately.
#   source:    https://raw.githubusercontent.com/compose-spec/compose-spec/main/schema/compose-spec.json
#   retrieved: 2026-10-16 (89,808 bytes, JSON Schema draft 2020-12)
#   license:   Apache-2.0 (compose-spec/compose-spec)
COMPOSE_SCHEMA = REPO_ROOT / "tests" / "data" / "compose-spec.json"
COMPOSE_SCHEMA_SHA256 = "d61cc3df8c6e6a727043e84f3405c69ffd63d341f629db8480f1d2ee5405b10c"


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


# ---------------------------------------------------------------------------------------------
# compose.yaml and deploy/Caddyfile
# ---------------------------------------------------------------------------------------------

ROLES = ("init-db", "collector", "web")
_DEFAULT_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")


def resolve(value):
    """Resolve ``${VAR:-default}`` to its default (compose interpolation without an environment)."""
    return _DEFAULT_RE.sub(r"\1", value) if isinstance(value, str) else value


def _seconds(duration: str) -> int:
    match = re.fullmatch(r"(\d+)s", duration)
    assert match, f"unsupported duration {duration!r}"
    return int(match.group(1))


@pytest.fixture(scope="module")
def compose() -> dict:
    import yaml  # dev dependency: a missing one must fail loudly, never skip

    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(COMPOSE_SCHEMA.read_text(encoding="utf-8"))


def test_vendored_schema_is_the_pinned_official_file():
    assert hashlib.sha256(COMPOSE_SCHEMA.read_bytes()).hexdigest() == COMPOSE_SCHEMA_SHA256


def test_compose_matches_the_official_schema(compose, schema):
    from jsonschema.validators import validator_for

    validator = validator_for(schema)(schema)
    assert [e.message for e in validator.iter_errors(compose)] == []


def test_schema_negative_control_rejects_a_typo(compose, schema):
    from jsonschema.validators import validator_for

    broken = copy.deepcopy(compose)
    service = broken["services"]["collector"]
    service["restart_polcy"] = service.pop("restart")
    errors = list(validator_for(schema)(schema).iter_errors(broken))
    assert errors, "the schema must reject an unknown service key"


def test_compose_top_level(compose):
    assert "version" not in compose
    assert compose["name"] == "wb-serp-monitor"
    assert set(compose["services"]) == {"init-db", "collector", "web", "caddy"}
    assert compose["volumes"]["wbdata"] == {"name": "wbmon_data"}
    assert "caddy_data" in compose["volumes"] and "caddy_config" in compose["volumes"]


@pytest.mark.parametrize("role", ROLES)
def test_role_shares_image_volume_environment_and_log_rotation(compose, role):
    service = compose["services"][role]
    assert service["build"]["context"] == "."
    assert service["image"] == "wb-serp-monitor:local"
    assert "wbdata:/data" in service["volumes"]  # read-write: WAL readers must create -shm
    assert not any(str(v).endswith(":ro") for v in service["volumes"])
    assert service["environment"]["WBMON_DB_PATH"] == "/data/wbmon.sqlite3"
    assert service["environment"]["TZ"] == "Europe/Moscow"
    assert service["env_file"] == [{"path": ".env", "required": False}]
    assert service["logging"]["driver"] == "json-file"
    assert service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}
    assert isinstance(service["command"], list)  # exec form: PID 1 receives SIGTERM


def test_build_args_default_to_the_pinned_image_and_pypi(compose):
    args = compose["services"]["collector"]["build"]["args"]
    assert resolve(args["PYTHON_IMAGE"]) == "python:3.12-slim"
    assert resolve(args["PIP_INDEX_URL"]) == "https://pypi.org/simple"


def test_init_db_is_a_one_shot(compose):
    service = compose["services"]["init-db"]
    assert service["command"] == ["wbmon", "init-db"]
    assert service["restart"] == "no"


def test_collector_runs_the_scheduler_with_healthcheck_and_grace_period(compose):
    service = compose["services"]["collector"]
    assert service["command"] == ["wbmon", "scheduler"]
    assert service["restart"] == "unless-stopped"
    assert _seconds(service["stop_grace_period"]) >= 75  # > the scheduler's 60 s child wait
    assert service["depends_on"] == {"init-db": {"condition": "service_completed_successfully"}}
    health = service["healthcheck"]
    assert health["test"] == ["CMD", "wbmon", "healthcheck", "--max-age", "180"]
    assert (health["interval"], health["timeout"], health["retries"], health["start_period"]) == (
        "60s",
        "10s",
        3,
        "30s",
    )


def test_web_runs_streamlit_from_the_app_workdir(compose):
    service = compose["services"]["web"]
    assert service["command"] == [
        "streamlit",
        "run",
        "/app/src/wbmon/ui/app.py",
        "--server.address=0.0.0.0",
        "--server.port=8501",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    assert service["working_dir"] == "/app"
    assert (
        posixpath.join(service["working_dir"], service["command"][2]) == "/app/src/wbmon/ui/app.py"
    )
    assert service["restart"] == "unless-stopped"
    assert service["depends_on"] == {"init-db": {"condition": "service_completed_successfully"}}
    assert service["ports"] == ["${WEB_BIND:-0.0.0.0}:${WEB_PORT:-8501}:8501"]


def test_web_healthcheck_probes_streamlit_on_loopback_without_running_it(compose):
    health = compose["services"]["web"]["healthcheck"]
    assert health["test"][:3] == ["CMD", "python", "-c"]
    code = health["test"][3]
    assert "127.0.0.1:8501/_stcore/health" in code
    compile(code, "hc", "exec")  # syntax check only; never executed


def test_streamlit_config_reaches_the_container_working_directory(compose, dockerfile):
    """Cross-phase contract (Phase 3): .streamlit/ is read from the web process's cwd."""
    workdir = compose["services"]["web"]["working_dir"]
    assert workdir == "/app"
    assert ("WORKDIR", workdir) in dockerfile
    assert ("COPY", ". /app") in dockerfile
    assert "!.streamlit" in _ignore_rules()


def test_caddy_is_an_optional_tls_front(compose):
    service = compose["services"]["caddy"]
    assert service["profiles"] == ["tls"]
    assert resolve(service["image"]) == "caddy:2.11-alpine"
    assert service["restart"] == "unless-stopped"
    assert "web" in service["depends_on"]
    assert resolve(service["environment"]["SITE_ADDRESS"]) == ":80"
    for mount in (
        "./deploy/Caddyfile:/etc/caddy/Caddyfile:ro",
        "caddy_data:/data",
        "caddy_config:/config",
    ):
        assert mount in service["volumes"]


def test_caddyfile_proxies_to_streamlit():
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "{$SITE_ADDRESS}" in text
    assert "reverse_proxy web:8501" in text


def test_compose_text_has_no_latest_tag_and_documents_the_compose_version():
    text = COMPOSE.read_text(encoding="utf-8")
    assert ":latest" not in text
    env_file_index = text.index("env_file:")
    assert "2.24" in text[max(0, env_file_index - 400) : env_file_index]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
def test_docker_compose_config_accepts_the_file():
    """Runs on CI runners that have Docker; ``config -q`` validates only, it builds nothing."""
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.yaml", "config", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
