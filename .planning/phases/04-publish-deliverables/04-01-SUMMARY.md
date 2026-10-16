---
phase: 04-publish-deliverables
plan: 01
subsystem: docs
tags: [readme, submission, systemd, ci, contract-tests, russian]

requires:
  - phase: 01-collection-core
    provides: CLI contract (init-db, collect, probe), Settings, dev-machine probe evidence (403 / wbaas)
  - phase: 02-unattended-operation-packaging
    provides: scheduler, healthcheck, Dockerfile, compose.yaml, Makefile
  - phase: 03-web-ui
    provides: Streamlit UI (src/wbmon/ui), .streamlit/config.toml
provides:
  - "README.md in Russian: 16 sections, configuration table + dotenv block, mermaid diagram, probe evidence"
  - "SUBMISSION.md: one-page Russian write-up (2771 characters)"
  - "deploy/systemd/wbmon-scheduler.service and wbmon-web.service: no-Docker production path"
  - "CI installs .[web,dev] so tests/ui runs on every push"
  - "tests/test_docs.py: 22 contract tests binding docs, units and CI to the real code"
affects: [04-02 publish and deploy, 04-03 final links and sign-off]

tech-stack:
  added: []
  patterns:
    - "docs are executable: every wbmon/make/docker compose command in README bash blocks is parsed by the real parser, Makefile and compose.yaml"
    - "deployment-dependent content carries the fixed marker text and is replaced by 04-03"

key-files:
  created:
    - README.md
    - SUBMISSION.md
    - deploy/systemd/wbmon-scheduler.service
    - deploy/systemd/wbmon-web.service
    - tests/test_docs.py
  modified:
    - .github/workflows/ci.yml

key-decisions:
  - "OPS-02 is satisfied by the README configuration table plus the dotenv block (no .env.example is created: the user's permission rules deny .env* writes); the REQUIREMENTS wording should be amended by the orchestrator"
  - "systemd units use KillMode=mixed and TimeoutStopSec=150, equal to the compose collector stop_grace_period; the test also requires it to exceed scheduler.STOP_GRACE_S + 5 ticks"
  - "the README documents make up-tls as the safe TLS path (it sets WEB_BIND=127.0.0.1); the plan listed only --profile tls, but the Makefile gained up-tls in Phase 2"
  - "the backup recipe writes to /tmp inside the collector container and uses the SQLite backup API; it was run against a temporary database to prove the snippet works"

patterns-established:
  - "Contract tests parse docs outside code fences, tokenise bash blocks with shlex (punctuation_chars) and fail with the offending line"
  - "Secret patterns in tests are built from fragments so the test file cannot match a repository secret scan"

requirements-completed: [DOC-01, DOC-02, OPS-02]

duration: 25min
completed: 2026-10-16
---

# Phase 4 Plan 01: README, write-up, systemd units and docs contract tests Summary

**Russian README (16 sections), one-page SUBMISSION.md, two sandboxed systemd units for the no-Docker path and CI that also runs the UI tests, all bound to the real CLI, Settings, Makefile and compose.yaml by 22 executable contract tests.**

## Performance

- **Duration:** about 25 min of execution
- **Completed:** 2026-10-16
- **Tasks:** 3 of 3
- **Files:** 5 created, 1 modified

## Accomplishments

- **tests/test_docs.py (22 tests).** README sections and order; every `WBMON_*` name read from `config.py` has a table row whose first cell is the name and which carries the default from `Settings.from_env({})`; every `${VAR:-default}` of `compose.yaml` plus `COMPOSE_PROFILES` has a row with its default; exactly one `dotenv` block lists all of them (no `$`); every `wbmon` command in bash blocks is accepted by `build_parser()`; every `make` target exists in the Makefile; every `docker compose` command uses real services, only `--profile` as a global option and never `down -v`; backticked repository paths exist; the mermaid flowchart sits in the Architecture section; the exact probe JSON line and the two anchor sentences are present; the units, SUBMISSION and CI are checked line by line. Two helper self-tests keep the extractors and the secret patterns from going vacuous.
- **README.md (529 lines).** Docker quick start in three commands, local no-Docker path, server deployment (Compose, TLS through `make up-tls`, registry mirrors, `docker save`/`load`, systemd), configuration table of 10 `WBMON_*` plus 7 compose variables with real defaults and the dotenv block, mermaid architecture and module map, data model, field semantics and assumptions, honesty rules with status tables, operations (logs, healthcheck, tested backup snippet), tests and CI, limitations including the ToS exposure, probe evidence, project tree, AI-process section, next steps.
- **systemd units.** `wbmon-scheduler.service` (`wbmon scheduler --db /var/lib/wbmon/wbmon.sqlite3`, `KillMode=mixed`, `TimeoutStopSec=150`) and `wbmon-web.service` (Streamlit with exactly the compose flags, `HOME=/var/lib/wbmon`), both as user `wbserp` with `Restart=always`, `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `StateDirectory=wbmon`.
- **SUBMISSION.md** (2771 characters, limit 5000) with links, how to run, time, difficulties and solutions, AI tools, next steps.
- **CI** installs `".[web,dev]"`; permissions, concurrency, matrix and lint/test steps unchanged (`tests/test_guardrails.py` green).

## Task Commits

1. **Task 1: failing docs contract tests** - `a881582` (test)
2. **Task 2: README and systemd units** - `57e2933` (docs)
3. **Task 3: SUBMISSION.md and CI web extra** - `86d887a` (docs)
4. **Time-estimate note in SUBMISSION.md** - `a548f4c` (docs)

## Preflight (Task 1 step 1, adapted to the worktree)

- Branch: `gsd/phase-04-docs` (orchestrator worktree), not `main`; the orchestrator overrode the check. Phase 3 is in history through merge commit `6df5216`; the branch name `gsd/phase-03-web-ui` does not resolve inside this worktree, so `git merge-base --is-ancestor` could not be run literally.
- Present: `02-03-SUMMARY.md`, `03-04-SUMMARY.md`, `src/wbmon/ui/app.py`, `.streamlit/config.toml`.
- Baseline suite before any change: **1209 passed, 1 skipped (docker not installed), 24 deselected** (Python 3.14.6).

## Verification

- Dev venv (3.14.6): `pytest -q` 1231 passed, 1 skipped, 24 deselected; `ruff check src tests` and `ruff format --check src tests` clean (73 files).
- Throwaway venvs under the session scratchpad with `pip install -q -e ".[web,dev]"` from PyPI (deleted afterwards): Python 3.11.15 1231 passed, 1 skipped; Python 3.12.13 1231 passed, 1 skipped. No dependency download failed.
- Commands checked against the real tools: `--help` of the CLI and of all five subcommands, `make help`, `make -n up`, `make -n down`, `make -n up-tls`, `yaml.safe_load(compose.yaml)` (services init-db, collector, web, caddy); in a mktemp directory `wbmon init-db` twice (exit 0, 0) and `wbmon healthcheck` (exit 1, no heartbeat, as expected); the README backup snippet was run against that temporary database and produced a valid copy (`user_version` 1). The directory was removed.
- No live Wildberries or Google request, no `collect`/`probe`/`scheduler`, no docker command, no `.env*` file.

## Publish readiness (for the 04-02 decision checkpoint)

- **(a) Tracked files:** `git ls-files | grep -nE '(^|/)\.env|\.sqlite3|(^|/)data/|^\.venv/|\.lock$'` prints only `src/wbmon/data/queries_bundled.txt` and `tests/data/compose-spec.json`. Nothing else.
- **(b) History secret scan:** `git log -p --all` against the GitHub token, fine-grained token, PEM private key, AWS key, Slack token and password-assignment patterns found nothing. An e-mail scan finds addresses only in commit headers (author lines and the Co-Authored-By trailer), none in any tracked file content.
- **(c) Public commit identity:** a single author and committer, name `U-Steph`, with a personal gmail address (the value is omitted here on purpose; run `git log --format='%an %ae' | sort -u`). It becomes public in every commit once the repository is published; the user decides whether to publish with it or rewrite the identity first.
- **(d) Repository existence:** `gh repo view U-Steph/wb-serp-monitor --json name,visibility` answered "Could not resolve to a Repository": the repository does **not** exist yet (so 04-02 creates it; REPO_URL already points there).
- **(e) Archive:** `git archive --format=zip --prefix=wb-serp-monitor/ HEAD` gives 862441 bytes, 173 files; it contains `README.md`, `SUBMISSION.md`, `deploy/systemd/` (both units), `src/wbmon/ui/app.py`, `compose.yaml`, `Dockerfile`; it contains no `.venv`, `*.sqlite3`, `.env` or `data/wbmon` entries. The temporary file was deleted. Note that `.planning/` and `CLAUDE.md` are part of the archive and the repository by design (record of the AI process).

## Decisions Made

See key-decisions in the frontmatter. In addition:

- **Time figure (provisional, the user must confirm in 04-03).** SUBMISSION.md says `≈ 4–5 ч работы с AI-агентами (Claude Code + GSD)`, as instructed by the orchestrator. Measured from 2026-10-16 15:05 МСК to the last commit of this plan (18:41 МСК) it is 3.6 h; the range anticipates the deployment work of 04-02 and 04-03. An HTML comment in SUBMISSION.md records both numbers. The test regex accepts a single figure or a range.
- **OPS-02 wording.** The requirement mentions `.env.example`; it is satisfied through the README table and dotenv block. The orchestrator should amend the REQUIREMENTS text.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Time regex accepts a range**
- **Found during:** Task 1
- **Issue:** the plan's regex `≈\s*\d+(?:[.,]\d+)?\s*ч` rejects the orchestrator's «≈ 4–5 ч» wording.
- **Fix:** the test regex allows an optional `–N` range; a single figure still passes.
- **Files modified:** `tests/test_docs.py`
- **Commit:** `a881582`

**2. [Rule 2 - Missing coverage] `make up-tls` documented and tested**
- **Found during:** Task 2
- **Issue:** the Makefile and compose.yaml (Phase 2) have `up-tls`, which sets `WEB_BIND=127.0.0.1` together with the `tls` profile; the plan only mentioned `--profile tls`, which alone leaves port 8501 public.
- **Fix:** README documents `make up-tls` first and explains why `--profile tls` alone is not enough; the Makefile-target test covers it.
- **Files modified:** `README.md`
- **Commit:** `57e2933`

**3. [Plan-vs-reality] Stricter table-row rule and extra tests**
- The config tests match the table row whose **first cell** is the name (so a mention in another row cannot satisfy the check), cross-check that every `Settings` field has a `WBMON_` name, and add two helper self-tests, which brings the file to 22 tests (plan minimum 21).

### Plan-vs-reality notes

- The Makefile `up*` targets copy `.env.example` only if it exists; since the file is intentionally not shipped, they are no-ops for it.
- The compose volume key is `wbdata` with the fixed name `wbmon_data`; the README uses `wbmon_data`.
- Docker is not installed and the machine is macOS, so `docker compose config` and `systemd-analyze verify` were not run; compose syntax is covered by the Phase 2 schema test and the units by `tests/test_docs.py`.
- The README states the default test count (1232) literally; adding tests later requires updating it (the count comes from `pytest --collect-only -q`).

## Known Stubs

Intentional, replaced by plan 04-03 (grep for `будет добавлено после развёртывания`):

- `README.md`: the line `**Работающий интерфейс:**` and the subsection «С сервера развёртывания».
- `SUBMISSION.md`: the line `**Работающий интерфейс:**`.

## Threat Flags

None. The new files add no network endpoint or auth path beyond the documented public Streamlit port (T-04-04, accepted); the units implement T-04-03 and T-04-07; secret and e-mail scans are part of the test suite (T-04-01).

## Next Phase Readiness

04-02 can decide on publishing with the facts above (repository absent, identity listed, history clean, archive verified). The README «Развёртывание на сервере» contains the exact go/no-go probe commands for both Compose and systemd hosts. 04-03 replaces the three marker occurrences and confirms the time figure.

## Self-Check: PASSED

- Files exist: `README.md`, `SUBMISSION.md`, `deploy/systemd/wbmon-scheduler.service`, `deploy/systemd/wbmon-web.service`, `tests/test_docs.py`, `.github/workflows/ci.yml`
- Commits `a881582`, `57e2933`, `86d887a`, `a548f4c` exist in `git log`
- `git status --porcelain` clean before this file; no `.env*` file in the working tree
