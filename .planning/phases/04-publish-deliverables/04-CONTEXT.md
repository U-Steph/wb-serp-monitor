# Phase 4: Publish & Deliverables - Context

**Gathered:** 2026-10-16
**Status:** Ready for planning
**Mode:** Smart discuss (autonomous). Recommended answers auto-accepted by the orchestrator per the user's
request for a fully autonomous solution. Hosting and publishing remain explicit human checkpoints.

<domain>
## Phase Boundary

Turn the finished code (Phases 1–3) into what the employer receives: a Russian README that lets anyone run
the project, a Russian write-up for the employer (time spent, difficulties and solutions, AI tools), a
source archive, the public repository link and a link to the running UI on a host chosen by the user —
plus the go/no-go WB reachability probe recorded from that host. No new product features.

</domain>

<decisions>
## Implementation Decisions

### Area 1 — Documentation (DOC-01)
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | README language & audience | Russian, for the employer's reviewer: what the service does, a quick start in 3 commands (Docker) and a local no-Docker path, configuration table (`WBMON_*`, `WEB_PORT`, `WEB_BIND`, `SITE_ADDRESS`), architecture diagram (ASCII/mermaid: Google Sheet → collector → SQLite ← Streamlit; scheduler → collect subprocesses), data model (tables + key columns), field semantics & assumptions (position incl. ads, price = min `sizes[].price.product`/100, rating/reviews from merged card values, delivery days derived from `time1+time2` in МСК — approximate, Moscow `dest=-1257786`), honesty rules (NULL instead of invented values, gaps, blocked/partial statuses, append-only), operations (logs, healthcheck, backups note, upgrades), testing (`make test`, CI), limitations (WB anti-bot since ~late Sep 2026; no circumvention by design; ToS note), project structure, AI-tools section | English README (rejected — employer is Russian-speaking) |
| 2 | Evidence | Include the real `wbmon probe` JSON from the dev machine (blocked/http_403/server wbaas) and, once deployed, from the host; include UI screenshots only of REAL databases (never of synthetic test DBs) — or none | screenshots from test fixtures (rejected: would look like real data) |
| 3 | Docs location | `README.md` (root, main doc), `docs/ARCHITECTURE.md` optional if README gets too long, `.planning/` stays in the repo as evidence of the AI-driven process | — |

### Area 2 — Employer write-up (DOC-02) and archive (DOC-03)
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Format | `SUBMISSION.md` in the repo root (Russian, ≤ 1 page): links (repo, live UI), how to run (2–3 lines + pointer to README), time spent, difficulties & solutions (WB anti-bot 403/498 and the decision not to circumvent; byte-exact query wording; honest gaps; SQLite WAL sharing; unattended scheduler), AI tools (Claude Code with the GSD multi-agent workflow: research → plan → plan-check → execute → verify → code review; 700+ offline tests), what would be next | separate PDF only |
| 2 | Time spent | Measured from the session: work started 2026-10-16 ~15:05 МСК; stated as "≈ N часов работы с AI-агентами (Claude Code)"; the user reviews/edits the number before sending | — |
| 3 | Archive | `git archive --format=zip --prefix=wb-serp-monitor/ HEAD` → `../wb-serp-monitor.zip` (outside the repo, next to the TZ); excludes `.venv`, data, caches automatically | zip of the working dir (rejected: would include .venv) |
| 4 | Ready-to-send message | A short Russian message text (Telegram-ready) with the links, placed in `../ОТВЕТ_РАБОТОДАТЕЛЮ.md` (outside the repo) for the user to copy | — |

### Area 3 — Deployment & publishing (OPS-03) — HUMAN CHECKPOINTS
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Publishing the repo | Requires explicit user approval: public GitHub repo `U-Steph/wb-serp-monitor` (gh is authenticated); if not approved — zip only | private repo + collaborator invite |
| 2 | Host | Preferred: the user's Russian VPS via SSH (host/user provided by the user) running `docker compose up -d --build` (or the no-Docker systemd path if Docker/registries are unavailable); fallback: this Mac (venv + `wbmon scheduler` + Streamlit) with a temporary public tunnel only if the user approves; Streamlit Community Cloud is NOT suitable (no shared SQLite, sleeps) | — |
| 3 | Go/no-go probe | First action on the host: `wbmon probe`; record the JSON. If `ok` → run `wbmon collect` once, leave the scheduler running and wait for ≥ 2–3 hourly runs before sending. If `blocked` → keep the scheduler running anyway (honest blocked history), report to the user and let the user decide (another host / another data source / ship as is). NEVER circumvent | — |
| 4 | Registry access from RU | If Docker Hub is unreachable: `PYTHON_IMAGE`/`CADDY_IMAGE` build args pointing at a mirror, or the no-Docker path (python3.12 venv + systemd units in `deploy/systemd/`) | — |

### Claude's Discretion
- README structure/wording, diagram style, whether to add `deploy/systemd/*.service` units (recommended as the no-Docker production path), CI change to install `.[web,dev]` so UI tests run in CI.

</decisions>

<code_context>
## Existing Code Insights

Phases 1–3 deliver: `src/wbmon/` (collector, storage, scheduler, health, UI under `ui/`), `tests/` (offline, 700+), `Dockerfile`, `compose.yaml`, `deploy/Caddyfile`, `.env.example`, `Makefile`, `.github/workflows/ci.yml`. Read the final versions (after Phase 2 and the merged Phase 3 branch) before writing docs — every command in the README must be copy-paste correct.

</code_context>

<specifics>
## Specific Ideas

- The README must state plainly that from the developer machine WB returned HTTP 403 (`server: wbaas`) and that the service records such runs as `blocked` instead of inventing data.
- The UI link and repo URL replace the placeholder `https://github.com/U-Steph/wb-serp-monitor` everywhere once known (User-Agent string, «О сервисе» tab, README).

</specifics>

<deferred>
## Deferred Ideas

- Automated backups, alerts, multi-region — v2 (mention in README «Что дальше»).

</deferred>
