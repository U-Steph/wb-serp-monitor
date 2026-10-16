# Walking Skeleton — WB SERP Monitor

**Phase:** 1
**Generated:** 2026-10-16
**Adaptation:** D-A10 (01-CONTEXT.md addendum). Phase 1 is a CLI data pipeline: no UI, no deployment. The
"full stack" proven here is CLI -> config -> query source -> HTTP client -> normalizer -> append-only
SQLite -> read-back. The Streamlit UI (Phase 3) and Docker/hosting (Phases 2 and 4) are later slices.

## Capability Proven End-to-End

`wbmon init-db` followed by `wbmon collect --limit-queries 1 --top-n 5`, replayed offline through
`httpx.MockTransport` serving a WB-shaped page-1 payload, writes one real `runs` row, one `run_queries` row
and five `observations` rows into a temporary SQLite database and prints an `event=run_summary` line whose
values were read back from that database. Delivered by plan 01-01 and proven by
`tests/test_skeleton_e2e.py`.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language / runtime | Python `>=3.11` (dev 3.14 venv, deploy target 3.12, CI matrix 3.11 + 3.12) | Locked stack; Streamlit/pandas floors are 3.11 |
| Packaging | `pyproject.toml`, hatchling backend, src layout `src/wbmon/`, console script `wbmon` + `python -m wbmon`; optional groups `web` and `dev` declared up front | Phases 2 and 3 run in parallel and must never edit `pyproject.toml` concurrently |
| Runtime dependencies | exactly `httpx>=0.28.1,<1` and `tzdata` (enforced by `tests/test_guardrails.py`) | COL-05: the dependency list itself proves no circumvention tooling |
| CLI | stdlib `argparse`: `init-db`, `collect`, `probe`; exit 0 whenever a run completed (even all-blocked), 2 on config/usage error, 1 on crash | Same entry point for manual runs and for the Phase 2 scheduler subprocess |
| Configuration | frozen dataclass `wbmon.config.Settings.from_env()` over `WBMON_*` env vars, CLI flags override via `with_overrides()` | Orchestrator decision: no pydantic |
| HTTP | sync `httpx.Client`, honest UA `Mozilla/5.0 (compatible; wbmon/<version>; +https://github.com/U-Steph/wb-serp-monitor)`, `trust_env=False`, cookie policy that rejects everything, `follow_redirects=False` for WB, fixed polite delay, bounded retries | Hard constraint: no anti-bot circumvention of any kind |
| Data layer | stdlib `sqlite3`, WAL, `busy_timeout=30000`, `foreign_keys=ON`, `recursive_triggers=ON`; schema via `PRAGMA user_version` migrations (`storage/migrations.py`); explicit SQL `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK` | Zero-ops single writer + many readers; verified on 3.11/3.12/3.14 |
| History guarantee | append-only by DB triggers (no UPDATE/DELETE on `observations`, no DELETE on `runs`/`run_queries`/`queries`, `run_queries` written once, finished runs immutable, query text immutable); no `INSERT OR REPLACE` anywhere in `src/` | Core value: history is never lost or rewritten |
| Schema contract | migration 1 is frozen in plan 01-01 (incl. `subject_id`, `root_id`, ad and price-audit columns, per-query status/coverage, `runs.params_json`); `ObservationRow` field order == `observations` columns | Non-backfillable fields captured from the first real run; Phase 3 builds on this contract |
| Time | store UTC ISO-8601 `YYYY-MM-DDTHH:MM:SSZ`; compute delivery days and display in `Europe/Moscow` via `zoneinfo` + `tzdata` | One rule everywhere; MSK has no DST |
| Logging | one `key=value` line per event on line-buffered stdout (`wbmon.log`) | `docker compose logs` friendly (SCHED-04) |
| Testing | pytest, fully offline: `httpx.MockTransport` (`tests/factories.FakeWB`), `tmp_path` databases, autouse guard (tmp cwd, tmp `WBMON_DB_PATH`, DNS/socket blocked), fixtures marked `"_test_only": true`; `pytest` = fast suite, `pytest -m slow` = 2.4M-row perf test | Data-integrity hard constraint: no synthetic data can reach a real DB, no live WB call from tests |
| Auth | none (anonymous public WB search JSON; UI has no accounts, Phase 3) | Out of scope by REQUIREMENTS |
| Scheduling | not in the skeleton; Phase 2 `wbmon scheduler` loop calls `python -m wbmon collect --trigger scheduler --slot <ISO>` | Orchestrator decision (no supercronic/APScheduler) |
| Directory layout | `src/wbmon/{config,clock,models,log,queries,wb_client,classify,normalize,delivery,pager,collector,orchestrator,cli}.py`, `src/wbmon/storage/{db,migrations,writer,reader,lock}.py`, `src/wbmon/data/queries_bundled.txt`, `tests/` | Functional core (`clock`, `models`, `normalize`, `delivery`, `pager`, `classify`) is I/O-free; shell modules own httpx/sqlite/time |

## Stack Touched in Phase 1

- [x] Project scaffold (pyproject, hatchling build, ruff lint, pytest runner, `.gitignore` incl. `.venv/`)
- [x] Routing — CLI subcommands `init-db` and `collect` (plan 01-01); `probe` (plan 01-04)
- [x] Database — real write (run, run_query, observations) AND real read (run summary read back) in a temp DB
- [ ] UI — not applicable in Phase 1 (the CLI summary line is the interface); Streamlit UI is Phase 3
- [x] Deployment — documented local full-pipeline command: `.venv/bin/python -m pytest -q tests/test_skeleton_e2e.py` (offline). A live `wbmon collect` is deliberately NOT run in Phase 1 (dev machine egress is blocked by WB; live runs start on the deploy host in Phase 4)

## Out of Scope (Deferred to Later Slices)

- Hourly scheduler, heartbeat, Docker image, Compose, `.env.example` — Phase 2
- Streamlit UI, read-only UI connection helpers beyond `connect_ro`, charts, CSV export — Phase 3
- Hosting, public link, go/no-go probe on the deploy host, README, write-up, zip — Phase 4
- Organic-rank series, raw response archival, Postgres backend — v2 (CONTEXT Deferred Ideas)
- Any anti-bot circumvention — never (hard constraint, not a deferral)

## Subsequent Slice Plan

Inside Phase 1 (each plan widens what `wbmon collect` really does):

- 01-02 (wave 2): every observation carries real field values — buyer price, audit prices, rating, reviews, delivery days, seller, ad flags — plus the pure top-N pagination state machine
- 01-03 (wave 2): history is provably durable — append-only enforcement tests, atomic per-query writes with `db_write_failed` fallback, `abandon_stale_runs`, run lock, STOR-04 read paths with a 2.4M-row perf test
- 01-04 (wave 2): WB access is honest and polite — response classification (blocked / rate_limited / error), bounded retries, Retry-After, Google Sheet query source with bundled fallback, `wbmon probe`
- 01-05 (wave 3): one command performs the complete honest run — multi-page top-200, circuit breaker, run deadline, lock/slot/abandon semantics, full CLI contract, CI workflow

Later phases (do not alter the decisions above):

- Phase 2: the collector runs unattended every hour on the Moscow clock (scheduler + Docker Compose)
- Phase 3: the user sees honest position history in a Russian Streamlit UI built on this schema
- Phase 4: deploy to the user's host, real hourly history, README, write-up, archive
