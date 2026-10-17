"""Documentation contract: README, SUBMISSION, the systemd units and CI are bound to the real code.

Nothing here talks to the network or starts a process. The documents are parsed and compared with
the real CLI parser (``wbmon.cli.build_parser``), ``wbmon.config.Settings``, ``Makefile``,
``compose.yaml`` and the scheduler constants, so a documented command, setting, default or service
name can never drift away from the code. Paths come from ``REPO_ROOT`` because the autouse
``_isolated`` fixture chdirs into ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import re
import shlex

import pytest
import yaml

from factories import REPO_ROOT

README = REPO_ROOT / "README.md"
SUBMISSION = REPO_ROOT / "SUBMISSION.md"
COMPOSE = REPO_ROOT / "compose.yaml"
MAKEFILE = REPO_ROOT / "Makefile"
CONFIG_PY = REPO_ROOT / "src" / "wbmon" / "config.py"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SCHEDULER_UNIT = REPO_ROOT / "deploy" / "systemd" / "wbmon-scheduler.service"
WEB_UNIT = REPO_ROOT / "deploy" / "systemd" / "wbmon-web.service"

README_H1 = "# WB SERP Monitor — мониторинг позиций в поиске Wildberries"
README_H2 = (
    "## Что это",
    "## Быстрый старт (Docker)",
    "## Запуск без Docker",
    "## Развёртывание на сервере",
    "## Конфигурация",
    "## Архитектура",
    "## Модель данных",
    "## Поля и допущения",
    "## Честность данных",
    "## Эксплуатация",
    "## Тесты и CI",
    "## Ограничения",
    "## Проверка доступности Wildberries",
    "## Структура проекта",
    "## Как сделано: AI-инструменты",
    "## Что дальше",
)
SUBMISSION_H2 = (
    "## Ссылки",
    "## Как запустить",
    "## Затраченное время",
    "## Сложности и решения",
    "## AI-инструменты",
    "## Что дальше",
)
DEV_PROBE_JSON = (
    '{"outcome": "blocked", "error_class": "http_403", "http_status": 403, '
    '"server": "wbaas", "attempts": 1}'
)
NO_CIRCUMVENTION = "Обход антибот-защиты Wildberries не выполняется — это осознанное решение."
NO_SYNTHETIC = "Синтетические или подставленные данные в рабочую базу не пишутся."
UNIT_APP = "/opt/wb-serp-monitor/src/wbmon/ui/app.py"
UNIT_DB = "/var/lib/wbmon/wbmon.sqlite3"
UNIT_BIN = "/opt/wb-serp-monitor/.venv/bin"
PATH_PREFIXES = ("src/", "tests/", "deploy/", "docs/", ".github/", ".streamlit/", ".planning/")
ROOT_FILES = frozenset(
    {
        "compose.yaml",
        "Dockerfile",
        "Makefile",
        "pyproject.toml",
        "README.md",
        "SUBMISSION.md",
        ".dockerignore",
        ".gitignore",
    }
)
_PUNCTUATION = set("();<>|&")

# Built from fragments so this file can never match a repository secret scan.
SECRET_PATTERNS = {
    "GitHub token": re.compile("gh" + "[pousr]_[A-Za-z0-9]{20,}"),
    "GitHub fine-grained token": re.compile("github" + "_pat_[A-Za-z0-9_]{20,}"),
    "private key": re.compile("-----BEGIN" + r" [A-Z ]*PRIVATE KEY"),
    "AWS access key": re.compile("AK" + "IA[0-9A-Z]{16}"),
    "Slack token": re.compile("xo" + "x[baprs]-[A-Za-z0-9-]+"),
    "password assignment": re.compile("pass" + r"word\s*=\s*\S", re.IGNORECASE),
    "e-mail address": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
}


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------


def _read(path) -> str:
    """Return the text of ``path`` or fail naming the missing document."""
    if not path.is_file():
        pytest.fail(f"{path.relative_to(REPO_ROOT)} is missing")
    return path.read_text(encoding="utf-8")


def _fenced_blocks(text: str) -> list[tuple[str, str]]:
    """Return ``(info string, body)`` for every fenced code block."""
    blocks: list[tuple[str, str]] = []
    info: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if info is None:
                info, body = line.lstrip()[3:].strip(), []
            else:
                blocks.append((info, "\n".join(body)))
                info = None
            continue
        if info is not None:
            body.append(line)
    return blocks


def _prose_lines(text: str) -> list[str]:
    """Return the lines that are outside fenced code blocks."""
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            lines.append(line)
    return lines


def _h2_sections(text: str) -> dict[str, str]:
    """Map every ``## `` heading (outside code blocks) to the text up to the next one."""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("## "):
            current = sections.setdefault(line.rstrip(), [])
            continue
        if current is not None:
            current.append(line)
    return {heading: "\n".join(body) for heading, body in sections.items()}


def _bash_lines(text: str) -> list[str]:
    """Return the command lines of the ``bash`` blocks: continuations joined, comments dropped."""
    lines: list[str] = []
    for info, body in _fenced_blocks(text):
        if info != "bash":
            continue
        pending = ""
        for raw in body.splitlines():
            line = raw.rstrip()
            if line.endswith("\\"):
                pending += line[:-1] + " "
                continue
            full = (pending + line).strip()
            pending = ""
            if full.startswith("$ "):
                full = full[2:]
            if full and not full.startswith("#"):
                lines.append(full)
    return lines


def _commands(line: str) -> list[list[str]]:
    """Split one shell line into commands (token lists) at ``&& || ; |`` and redirections."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        pytest.fail(f"README bash line cannot be tokenised ({exc}): {line}")
    commands: list[list[str]] = [[]]
    for token in tokens:
        if set(token) <= _PUNCTUATION:
            commands.append([])
        else:
            commands[-1].append(token)
    return [command for command in commands if command]


def _all_commands(text: str) -> list[tuple[str, list[str]]]:
    return [(line, command) for line in _bash_lines(text) for command in _commands(line)]


def _skip_prefix(command: list[str]) -> list[str]:
    """Drop a leading ``sudo`` and ``NAME=value`` assignments."""
    index = 0
    while index < len(command) and (
        command[index] == "sudo" or re.fullmatch(r"[A-Z_][A-Z0-9_]*=.*", command[index])
    ):
        index += 1
    return command[index:]


def _compose() -> dict:
    return yaml.safe_load(_read(COMPOSE))


def _compose_variables() -> dict[str, str | None]:
    """Return ``name -> default`` for every ``${NAME:-default}`` / ``${NAME}`` in compose.yaml."""
    found: dict[str, str | None] = {}
    for name, default in re.findall(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}", _read(COMPOSE)):
        found[name] = default if default != "" else found.get(name)
    found.setdefault("COMPOSE_PROFILES", None)
    return found


def _settings_names() -> list[str]:
    names = sorted(set(re.findall(r'"(WBMON_[A-Z_]+)"', _read(CONFIG_PY))))
    assert names, "no WBMON_* names found in src/wbmon/config.py"
    return names


def _table_row(text: str, name: str) -> str:
    """Return the table line whose first cell is ``name`` in backticks."""
    for line in text.splitlines():
        if re.match(rf"^\|\s*`{re.escape(name)}`\s*\|", line):
            return line
    pytest.fail(f"README.md has no configuration table row for {name}")


def _parse_unit(text: str) -> dict[str, list[tuple[str, str]]]:
    """Parse a systemd unit into ``section -> [(key, value)]``, repeated keys kept."""
    unit: dict[str, list[tuple[str, str]]] = {}
    section: str | None = None
    pending = ""
    for raw in text.splitlines():
        line = pending + raw.strip()
        pending = ""
        if line.endswith("\\"):
            pending = line[:-1] + " "
            continue
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            unit.setdefault(section, [])
            continue
        assert section is not None, f"key outside a section: {line}"
        key, _, value = line.partition("=")
        unit[section].append((key.strip(), value.strip()))
    return unit


def _values(unit: dict, section: str, key: str) -> list[str]:
    return [value for name, value in unit.get(section, []) if name == key]


def _one(unit: dict, section: str, key: str) -> str:
    values = _values(unit, section, key)
    assert len(values) == 1, f"[{section}] {key}: expected exactly one line, got {values}"
    return values[0]


def _environment(unit: dict) -> dict[str, str]:
    env: dict[str, str] = {}
    for value in _values(unit, "Service", "Environment"):
        for assignment in shlex.split(value):
            name, _, rest = assignment.partition("=")
            env[name] = rest
    return env


def _seconds(text: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|m|min)?", text.strip())
    assert match, f"cannot read a duration from {text!r}"
    value = float(match.group(1))
    return value * 60 if match.group(2) in ("m", "min") else value


def _units() -> dict[str, dict]:
    return {
        "wbmon-scheduler.service": _parse_unit(_read(SCHEDULER_UNIT)),
        "wbmon-web.service": _parse_unit(_read(WEB_UNIT)),
    }


def _assert_no_secrets(name: str, text: str) -> None:
    for label, pattern in SECRET_PATTERNS.items():
        match = pattern.search(text)
        assert match is None, f"{name} contains a {label}: {match.group(0)[:12]}..."


# ---------------------------------------------------------------------------------------------
# helper self-tests (so a broken extractor can never make the contract tests vacuous)
# ---------------------------------------------------------------------------------------------


def test_bash_extraction_joins_continuations_and_splits_commands():
    text = (
        "intro\n```bash\n# comment\n$ make install && make test  # trailing\n"
        "docker compose run --rm \\\n  collector wbmon probe\n```\n```json\n{}\n```\n"
    )
    assert [" ".join(line.split()) for line in _bash_lines(text)] == [
        "make install && make test # trailing",
        "docker compose run --rm collector wbmon probe",
    ]
    assert _commands("make install && make test  # trailing") == [
        ["make", "install"],
        ["make", "test"],
    ]
    assert _commands('python -c "import os; print(1)" | cat') == [
        ["python", "-c", "import os; print(1)"],
        ["cat"],
    ]


def test_secret_patterns_catch_the_shapes_they_claim_to():
    samples = {
        "GitHub token": "gh" + "p_" + "A" * 24,
        "GitHub fine-grained token": "github" + "_pat_" + "B" * 24,
        "private key": "-----BEGIN" + " RSA PRIVATE KEY-----",
        "AWS access key": "AK" + "IA" + "C" * 16,
        "Slack token": "xo" + "xb-" + "1234567890",
        "password assignment": "pass" + "word = hunter2",
        "e-mail address": "someone" + "@" + "example.org",
    }
    assert set(samples) == set(SECRET_PATTERNS)
    for label, sample in samples.items():
        assert SECRET_PATTERNS[label].search(sample), label
    assert not any(p.search("WBMON_TOP_N=200 and 2 spaces") for p in SECRET_PATTERNS.values())


# ---------------------------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------------------------


def test_readme_has_the_required_sections_in_order():
    text = _read(README)
    assert text.startswith(README_H1 + "\n"), "README.md must start with the project H1"
    headings = [line.rstrip() for line in _prose_lines(text) if line.startswith("## ")]
    assert headings == list(README_H2)


def test_readme_config_table_documents_every_wbmon_setting_with_its_default():
    from wbmon.config import Settings

    text = _read(README)
    names = _settings_names()
    fields = {"WBMON_" + field.name.upper() for field in dataclasses.fields(Settings)}
    assert fields <= set(names), f"Settings fields without a WBMON_ name: {fields - set(names)}"
    settings = Settings.from_env({})
    defaults = {
        "WBMON_DB_PATH": str(settings.db_path),
        "WBMON_TOP_N": f"{settings.top_n:g}",
        "WBMON_DEST": settings.dest,
        "WBMON_SEARCH_URL": settings.search_url,
        "WBMON_REQUEST_DELAY": f"{settings.request_delay:g}",
        "WBMON_RUN_DEADLINE": f"{settings.run_deadline:g}",
        "WBMON_TIMEZONE": settings.timezone,
        "WBMON_LOG_LEVEL": settings.log_level,
        "WBMON_SHEET_CSV_URL": "1K0m4229w_1LONgrNzWpx8lkkDcAlbdZH5ifHenZV3x8",
        "WBMON_USER_AGENT": "compatible; wbmon/",
    }
    assert set(defaults) == set(names)
    assert settings.sheet_csv_url.count(defaults["WBMON_SHEET_CSV_URL"]) == 1
    assert defaults["WBMON_USER_AGENT"] in settings.user_agent
    for name in names:
        row = _table_row(text, name)
        assert defaults[name] in row, f"README.md row for {name} lacks the default {defaults[name]}"


def test_readme_config_table_documents_every_compose_variable_with_its_default():
    text = _read(README)
    variables = _compose_variables()
    assert {"WEB_BIND", "WEB_PORT", "SITE_ADDRESS", "PYTHON_IMAGE", "CADDY_IMAGE"} <= set(variables)
    assert "PIP_INDEX_URL" in variables
    for name, default in variables.items():
        row = _table_row(text, name)
        if default:
            assert default in row, f"README.md row for {name} lacks the default {default}"


def test_readme_dotenv_block_lists_every_setting():
    text = _read(README)
    blocks = [body for info, body in _fenced_blocks(text) if info == "dotenv"]
    assert len(blocks) == 1, f"README.md needs exactly one dotenv block, found {len(blocks)}"
    block = blocks[0]
    for name in [*_settings_names(), *_compose_variables()]:
        assert re.search(rf"^#?\s?{re.escape(name)}=", block, re.MULTILINE), (
            f"the dotenv block has no line for {name}"
        )
    assert "$" not in block, "the dotenv block must not rely on shell or compose expansion"


def test_readme_wbmon_commands_parse_with_the_real_cli():
    from wbmon.cli import build_parser

    covered: set[str] = set()
    for line, command in _all_commands(_read(README)):
        indexes = [
            i for i, tok in enumerate(command) if tok == "wbmon" or tok.endswith("/bin/wbmon")
        ]
        if not indexes:
            continue
        args = command[indexes[0] + 1 :]
        assert not any("$" in arg for arg in args), f"shell expansion in a wbmon command: {line}"
        try:
            namespace = build_parser().parse_args(args)
        except SystemExit as exc:
            assert exc.code in (0, None), f"the real CLI rejects this README command: {line}"
            covered.add(args[0] if args and not args[0].startswith("-") else "help")
            continue
        covered.add(namespace.command)
    assert {"init-db", "collect", "probe", "scheduler", "healthcheck"} <= covered


def test_readme_make_targets_exist():
    targets = set(re.findall(r"^([A-Za-z][\w-]*):(?!=)", _read(MAKEFILE), re.MULTILINE))
    assert {"install", "init-db", "collect-once", "scheduler", "web", "up", "down"} <= targets
    seen = 0
    for line, command in _all_commands(_read(README)):
        command = _skip_prefix(command)
        if not command or command[0] != "make":
            continue
        rest = [tok for tok in command[1:] if not tok.startswith("-") and "=" not in tok]
        assert rest, f"make without a target: {line}"
        assert rest[0] in targets, f"README names a missing Makefile target {rest[0]!r}: {line}"
        seen += 1
    assert seen >= 5, "README should document the make targets"


def test_readme_docker_compose_commands_use_real_services_and_keep_the_volume():
    services = set(_compose()["services"])
    positional_checked = {"logs", "restart", "stop", "start", "ps", "up", "build", "pull"}
    seen = 0
    for line, raw in _all_commands(_read(README)):
        assert raw[:2] != ["docker", "volume"] or raw[2] not in ("rm", "prune"), line
        command = _skip_prefix(raw)
        if command[:2] != ["docker", "compose"]:
            continue
        seen += 1
        rest = command[2:]
        while rest and rest[0].startswith("-"):  # only `--profile VALUE` precedes the subcommand
            assert rest[0] == "--profile" and len(rest) > 1, f"unsupported global option: {line}"
            rest = rest[2:]
        assert rest, f"docker compose without a subcommand: {line}"
        sub, args = rest[0], rest[1:]
        if sub == "down":
            assert not any(
                arg == "--volumes" or (arg[:1] == "-" and arg[1:2] != "-" and "v" in arg)
                for arg in args
            ), f"`down` must keep the data volume: {line}"
        positionals = [arg for arg in args if not arg.startswith("-")]
        if sub in positional_checked:
            for service in positionals:
                assert service in services, f"unknown compose service {service!r}: {line}"
        elif sub in ("exec", "run"):
            assert positionals and positionals[0] in services, f"no compose service: {line}"
        elif sub == "cp":
            for arg in positionals:
                if re.match(r"^[A-Za-z][\w-]*:", arg):
                    assert arg.split(":", 1)[0] in services, f"unknown compose service: {line}"
    assert seen >= 6, "README should document the docker compose commands"


def test_readme_referenced_repo_paths_exist():
    text = _read(README)
    checked = 0
    for line in _prose_lines(text):
        for token in re.findall(r"`([^`\n]+)`", line):
            if re.search(r"[*{<>\s]", token) or token in (".env", ".env.example"):
                continue
            if token.startswith(PATH_PREFIXES) or token in ROOT_FILES:
                assert (REPO_ROOT / token).exists(), f"README.md references a missing path: {token}"
                checked += 1
    assert checked >= 15, "README should reference the project files it describes"


def test_readme_has_a_mermaid_architecture_diagram():
    section = _h2_sections(_read(README)).get("## Архитектура")
    assert section is not None, "README.md has no «Архитектура» section"
    diagrams = [body for info, body in _fenced_blocks(section) if info == "mermaid"]
    assert diagrams, "the «Архитектура» section needs a mermaid block"
    assert "flowchart" in diagrams[0]


def test_readme_states_the_block_and_the_honesty_rules():
    text = _read(README)
    assert DEV_PROBE_JSON in text
    assert "wbaas" in text and "blocked" in text
    assert NO_CIRCUMVENTION in text
    assert NO_SYNTHETIC in text


def test_readme_links_the_repository_and_the_live_ui_line():
    from wbmon import REPO_URL

    text = _read(README)
    assert REPO_URL in text
    assert any(line.startswith("**Работающий интерфейс:**") for line in text.splitlines())


def test_no_deployment_marker_left():
    marker = re.compile(r"будет добавлен[аоы]? после развёртывания")
    for path in (README, SUBMISSION):
        assert marker.search(_read(path)) is None, f"{path.name} still has a deployment placeholder"


def test_readme_and_submission_link_the_live_ui():
    for path in (README, SUBMISSION):
        lines = [
            line
            for line in _read(path).splitlines()
            if line.startswith("**Работающий интерфейс:**")
        ]
        assert lines, f"{path.name} has no «Работающий интерфейс» line"
        match = re.search(r"https?://([^/\s]+)", lines[0])
        assert match, f"{path.name}: the «Работающий интерфейс» line has no URL"
        assert match.group(1) != "github.com", f"{path.name}: the live UI link points to GitHub"


def test_readme_has_no_secrets_or_personal_emails():
    _assert_no_secrets("README.md", _read(README))


# ---------------------------------------------------------------------------------------------
# systemd units
# ---------------------------------------------------------------------------------------------


def test_unit_files_run_as_a_non_root_sandboxed_user():
    for name, unit in _units().items():
        for key, expected in (
            ("User", "wbserp"),
            ("Group", "wbserp"),
            ("Restart", "always"),
            ("WorkingDirectory", "/opt/wb-serp-monitor"),
            ("StateDirectory", "wbmon"),
            ("NoNewPrivileges", "true"),
            ("ProtectSystem", "strict"),
            ("ProtectHome", "true"),
            ("PrivateTmp", "true"),
        ):
            assert _one(unit, "Service", key) == expected, f"{name}: {key}"
        assert _values(unit, "Install", "WantedBy") == ["multi-user.target"], name
        env = _environment(unit)
        assert env.get("WBMON_DB_PATH") == UNIT_DB, name
        assert env.get("TZ") == "Europe/Moscow", name
        assert all(value != "root" for values in unit.values() for _, value in values), name


def test_unit_scheduler_runs_the_real_cli_with_a_pinned_db_and_a_long_enough_stop_window():
    from wbmon import scheduler
    from wbmon.cli import build_parser

    unit = _parse_unit(_read(SCHEDULER_UNIT))
    command = shlex.split(_one(unit, "Service", "ExecStart"))
    assert command[0] == f"{UNIT_BIN}/wbmon"
    args = build_parser().parse_args(command[1:])
    assert args.command == "scheduler" and args.db == UNIT_DB
    assert _one(unit, "Service", "KillMode") == "mixed"
    timeout = _seconds(_one(unit, "Service", "TimeoutStopSec"))
    grace = _seconds(str(_compose()["services"]["collector"]["stop_grace_period"]))
    assert timeout == grace
    assert timeout > scheduler.STOP_GRACE_S + 5 * scheduler.TICK_S
    assert _values(unit, "Service", "EnvironmentFile") == ["-/etc/default/wbmon"]
    for key in ("After", "Wants"):
        assert "network-online.target" in " ".join(_values(unit, "Unit", key)).split(), key


def test_unit_web_runs_streamlit_like_compose_from_the_repo_root():
    unit = _parse_unit(_read(WEB_UNIT))
    command = shlex.split(_one(unit, "Service", "ExecStart"))
    assert command[:3] == [f"{UNIT_BIN}/streamlit", "run", UNIT_APP]
    web = _compose()["services"]["web"]["command"]
    assert web[:2] == ["streamlit", "run"]
    assert web[2].replace("/app/", "/opt/wb-serp-monitor/", 1) == UNIT_APP
    assert command[3:] == [arg for arg in web[3:] if arg.startswith("--")]
    assert len(command[3:]) == len(web[3:]) >= 4
    assert _environment(unit).get("HOME") == "/var/lib/wbmon"
    assert _values(unit, "Service", "EnvironmentFile") == []


def test_unit_files_have_no_secrets():
    for path in (SCHEDULER_UNIT, WEB_UNIT):
        _assert_no_secrets(path.name, _read(path))


# ---------------------------------------------------------------------------------------------
# SUBMISSION and CI
# ---------------------------------------------------------------------------------------------


def test_submission_has_the_required_sections_and_fits_one_page():
    text = _read(SUBMISSION)
    first = next(line for line in text.splitlines() if line.strip())
    assert first.startswith("# Тестовое задание")
    headings = [line.rstrip() for line in _prose_lines(text) if line.startswith("## ")]
    assert headings == list(SUBMISSION_H2)
    assert len(text) <= 5000, f"SUBMISSION.md is {len(text)} characters; the limit is 5000"


def test_submission_links_repo_readme_and_states_time():
    from wbmon import REPO_URL

    text = _read(SUBMISSION)
    assert REPO_URL in text and "README.md" in text
    assert any(line.startswith("**Работающий интерфейс:**") for line in text.splitlines())
    number = r"\d+(?:[.,]\d+)?"
    assert re.search(rf"≈\s*{number}(?:\s*[–-]\s*{number})?\s*ч", text), "no «≈ N ч» time figure"


def test_submission_has_no_secrets_or_personal_emails():
    _assert_no_secrets("SUBMISSION.md", _read(SUBMISSION))


def test_ci_installs_the_web_extra_so_ui_tests_run():
    text = _read(CI_YML)
    assert 'python -m pip install -e ".[web,dev]"' in text
    assert not re.search(r'pip install\b[^\n]*"\.\[dev\]"', text), "CI still installs only .[dev]"
