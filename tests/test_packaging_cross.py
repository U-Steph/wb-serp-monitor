"""compose.yaml, Dockerfile and Makefile agree with the CLI and with the scheduler's timings.

Static plus in-process only: no Docker, no subprocess, no network. A command string that the CLI
does not know would crash-loop the container, so every ``wbmon ...`` command in the packaging
files is parsed with the real argparse parser, and the healthcheck is run in-process.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
from pathlib import PurePosixPath

import pytest

from factories import REPO_ROOT

COMPOSE = REPO_ROOT / "compose.yaml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
MAKEFILE = REPO_ROOT / "Makefile"

_WBMON_IN_RECIPE = re.compile(r"(?:^|[\s/])wbmon\s+(?P<rest>[a-z][a-z-]*(?:\s.*)?)$")


def subcommands() -> set[str]:
    from wbmon.cli import build_parser

    (action,) = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return set(action.choices)


def parse_cli(tokens: list[str]) -> argparse.Namespace:
    """Parse ``tokens`` (no leading ``wbmon``) with the real parser; a usage error fails."""
    from wbmon.cli import build_parser

    return build_parser().parse_args(tokens)


@pytest.fixture(scope="module")
def compose() -> dict:
    import yaml

    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dockerfile_lines() -> list[tuple[str, str]]:
    from test_packaging import parse_dockerfile

    return parse_dockerfile(DOCKERFILE.read_text(encoding="utf-8"))


def _seconds(duration: str) -> int:
    match = re.fullmatch(r"(\d+)s", duration)
    assert match, f"unsupported duration {duration!r}"
    return int(match.group(1))


def _healthcheck_args(compose: dict) -> list[str]:
    test = compose["services"]["collector"]["healthcheck"]["test"]
    assert test[0] == "CMD" and test[1] == "wbmon"
    return list(test[1:])


def _pinned_db_path(compose: dict) -> str:
    return compose["services"]["collector"]["environment"]["WBMON_DB_PATH"]


# -------------------------------------------------------------------- commands parse


def test_every_wbmon_service_command_parses_with_the_real_cli(compose):
    commands = {
        name: service["command"]
        for name, service in compose["services"].items()
        if isinstance(service.get("command"), list) and service["command"][0] == "wbmon"
    }
    assert set(commands) == {"init-db", "collector"}
    for name, command in commands.items():
        args = parse_cli(command[1:])
        assert args.command in subcommands(), name
    assert parse_cli(commands["collector"][1:]).command == "scheduler"
    assert parse_cli(commands["init-db"][1:]).command == "init-db"


def test_the_collector_healthcheck_command_parses(compose):
    args = parse_cli(_healthcheck_args(compose)[1:])
    assert args.command == "healthcheck"
    assert args.max_age == 180


def test_the_dockerfile_cmd_parses(dockerfile_lines):
    (cmd,) = [rest for head, rest in dockerfile_lines if head == "CMD"]
    argv = json.loads(cmd)
    assert argv[0] == "wbmon"
    assert parse_cli(argv[1:]).command == "scheduler"


def test_every_makefile_wbmon_command_is_a_registered_subcommand_that_parses():
    from test_packaging import parse_makefile

    known = subcommands()
    found: set[str] = set()
    for target, body in parse_makefile(MAKEFILE.read_text(encoding="utf-8")).items():
        for line in body["recipe"]:
            match = _WBMON_IN_RECIPE.search(line)
            if match is None:
                continue
            tokens = shlex.split(match.group("rest"))
            assert tokens[0] in known, f"{target}: unknown wbmon command {tokens[0]!r}"
            parse_cli(tokens)
            found.add(tokens[0])
    assert {"init-db", "collect", "scheduler"} <= found


def test_the_registered_subcommands_are_the_expected_ones():
    assert {"init-db", "collect", "scheduler", "healthcheck"} <= subcommands()


# --------------------------------------------------------------- healthcheck in-process


def test_the_compose_healthcheck_is_1_without_a_heartbeat_and_0_with_a_fresh_one(
    compose, monkeypatch, tmp_path, capsys
):
    from wbmon import cli, clock, health

    db_path = tmp_path / "data" / "wbmon.sqlite3"
    monkeypatch.setenv("WBMON_DB_PATH", str(db_path))
    argv = _healthcheck_args(compose)[1:]

    assert cli.main(argv) == 1  # no heartbeat file yet
    assert "unhealthy" in capsys.readouterr().out

    payload = {
        "ts": clock.iso_z(clock.utc_now()),
        "pid": 1,
        "next_slot": "2026-10-16T10:00:00Z",
        "last_slot": None,
        "last_exit": None,
        "version": "test",
    }
    assert tuple(sorted(payload)) == tuple(sorted(health.HEARTBEAT_KEYS))
    health.write_heartbeat(health.heartbeat_path(db_path), payload)

    assert cli.main(argv) == 0


@pytest.mark.parametrize(
    "bad", [["healthcheck", "--bogus"], ["healthcheck", "--max-age", "nope"], ["healthcheck", "x"]]
)
def test_the_healthcheck_never_returns_docker_reserved_code_2(bad, monkeypatch, tmp_path):
    from wbmon import cli

    monkeypatch.setenv("WBMON_DB_PATH", str(tmp_path / "wbmon.sqlite3"))

    assert cli.main(bad) == 1


# ----------------------------------------------------------------------------- timing


def test_the_collector_stop_grace_period_outlasts_the_scheduler_stop_wait(compose):
    from wbmon import scheduler

    grace = _seconds(compose["services"]["collector"]["stop_grace_period"])

    assert grace > scheduler.STOP_GRACE_S + 5 * scheduler.TICK_S


def test_the_healthcheck_window_covers_at_least_three_heartbeat_intervals(compose):
    from wbmon import health, scheduler

    max_age = parse_cli(_healthcheck_args(compose)[1:]).max_age

    assert max_age == health.DEFAULT_MAX_AGE_S
    assert max_age >= 3 * scheduler.HEARTBEAT_EVERY_S


def test_a_run_that_hits_its_deadline_ends_before_the_next_slot():
    from wbmon import scheduler
    from wbmon.config import Settings

    child_timeout = Settings.from_env({}).run_deadline + scheduler.CHILD_TIMEOUT_MARGIN_S

    assert child_timeout < 3600


# ------------------------------------------------------------------------ shared paths


def test_the_database_path_is_pinned_identically_in_the_image_and_compose(
    compose, dockerfile_lines
):
    from wbmon import health

    env = " ".join(rest for head, rest in dockerfile_lines if head == "ENV")
    match = re.search(r"(?:^|\s)WBMON_DB_PATH=(\S+)", env)
    assert match is not None
    pinned = _pinned_db_path(compose)

    assert match.group(1) == pinned == "/data/wbmon.sqlite3"
    assert str(health.heartbeat_path(pinned)) == "/data/scheduler_heartbeat.json"


def test_collector_and_web_share_the_data_volume_that_holds_the_heartbeat(compose):
    services = compose["services"]
    for role in ("collector", "web"):
        assert "wbdata:/data" in services[role]["volumes"], role
        assert services[role]["environment"]["WBMON_DB_PATH"] == "/data/wbmon.sqlite3", role
    assert compose["volumes"]["wbdata"]["name"] == "wbmon_data"


def test_the_web_entrypoint_resolves_inside_the_image_workdir(compose, dockerfile_lines):
    web = compose["services"]["web"]
    (workdir,) = [rest for head, rest in dockerfile_lines if head == "WORKDIR"]
    script = web["command"][web["command"].index("run") + 1]

    assert workdir == "/app" == web["working_dir"]
    resolved = PurePosixPath(web["working_dir"]) / script  # absolute script wins over the join
    assert str(resolved) == "/app/src/wbmon/ui/app.py"
