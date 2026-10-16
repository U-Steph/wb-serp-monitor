---
phase: 04-publish-deliverables
plan: 02
subsystem: infra
tags: [deploy, vps, docker-compose, caddy, sslip, go-no-go, probe, wbaas, restart-survival]

requires:
  - phase: 04-publish-deliverables
    provides: "04-01 README deployment commands, compose.yaml/Makefile/Caddyfile, docs contract tests"
provides:
  - "Stack running unattended on a SpaceWeb cloud VPS (Docker Compose, tls profile) with a valid Let's Encrypt certificate"
  - "Public URL https://194-226-98-141.sslip.io answering /_stcore/health = ok from outside"
  - "Go/no-go probe from the deployment host, recorded verbatim: blocked (403, server wbaas)"
  - "Honest blocked hourly history from the host (2 scheduler runs, 0 observations, 0 integrity violations)"
  - "Restart (down without -v / up) and host reboot evidence with unchanged counts"
  - "Untracked operator notes and a read-only report script in ../wbmon-deploy/"
affects: [04-03 evidence, live-URL and probe-JSON marker replacement]

tech-stack:
  added: []
  patterns:
    - "probe is the first WB request from a new host; a blocked outcome skips the manual run and the scheduler starts anyway"
    - "read-only host report piped through `docker compose exec -T collector python -` via wbmon.storage.db.connect_ro"

key-files:
  created: []
  modified: []

key-decisions:
  - "Docker Hub and PyPI were reachable from the host directly, so the approved SpaceWeb mirror and the systemd fallback were not needed"
  - "HTTPS on the sslip.io address worked at the first attempt (certificate obtained in 7 s), so the plain-HTTP fallback was not used"
  - "ufw is inactive on the host and was left inactive (enabling it risked an SSH lock-out); port 8501 is bound to 127.0.0.1, only 22, 80 and 443 listen publicly"
  - "Task 3 decision is pending-user: the probe was blocked, so continue was not auto-selected"

patterns-established:
  - "Repository files are shipped with `git archive HEAD` over SSH, so the deployed code equals the committed HEAD"

requirements-completed: []

duration: 45min
completed: 2026-10-17
---

# Phase 4 Plan 02: Deploy to a VPS, host probe and unattended collection Summary

**The stack runs unattended on a SpaceWeb VPS behind Caddy with a valid certificate at https://194-226-98-141.sslip.io; the first Wildberries request from the host (probe) was blocked with 403 `wbaas`, so no manual run was made, and the scheduler records an honest blocked history that survives a stack restart and a host reboot. The post-probe decision (Task 3) is pending the user.**

## Performance

- **Duration:** about 45 min
- **Started:** 2026-10-16T21:20Z (preflight 21:23:02Z)
- **Completed:** 2026-10-16T22:05Z (01:05 МСК, 2026-10-17)
- **Tasks:** Task 1 recorded from the user's answers, Task 2 done, Task 3 pending the user
- **Files modified in the repository:** 0 (only this SUMMARY and the state files)

## Task 1: user answers (recorded; SSH details are in the untracked notes only)

- publish: A, the repository is already public at https://github.com/U-Steph/wb-serp-monitor; REPO_URL unchanged, so no commit for step 1.
- host: a SpaceWeb cloud VPS in Saint Petersburg, free 3-day trial (10 Mbit/s channel, no SMTP), key-based login authorised by the user and verified by the orchestrator.
- method: Docker Compose; fallbacks (SpaceWeb Docker Hub mirror, then systemd) were allowed but not used.
- address: HTTPS through `194-226-98-141.sslip.io` with the Caddy tls profile and `WEB_BIND=127.0.0.1`.
- apt: yes. ufw: only if active (it was inactive). reboot: yes. mirror: `dockerhub.sweb.ru`, fallback only.

## Deployment record

| Item | Value |
|------|-------|
| Deployed commit | `56a277a80e268d5b6784018195ecd867032a3a88` (shipped with `git archive --format=tar HEAD`, working tree clean) |
| Host OS | Ubuntu 24.04.5 LTS, kernel 6.8.0-139-generic, 2 vCPU, 1996 MB RAM, 20 GB disk (17 GB free at start) |
| Before | no Docker, Python 3.12.3, ufw inactive, only port 22 listening |
| Installed (apt, distro repositories) | `docker.io` and `docker-compose-v2` |
| Docker / Compose | Docker 29.1.3 (build 29.1.3-0ubuntu3~24.04.2), Docker Compose 2.40.3 (>= 2.24 required), docker service enabled |
| Image build | `docker compose build` exit 0 on the first try from Docker Hub and PyPI (no mirror), image `wb-serp-monitor:local` 871 MB; Caddy `caddy:2.11-alpine` pulled |
| Start command | `SITE_ADDRESS=194-226-98-141.sslip.io WEB_BIND=127.0.0.1 docker compose --profile tls up -d` run in `~/wb-serp-monitor` (also in the untracked notes) |
| Services | init-db `Exited (0)`, collector `healthy`, web `healthy`, caddy up; named volume `wbmon_data` |
| Public URL | https://194-226-98-141.sslip.io |
| No `.env*` file | none locally or on the host; variables passed on the command line |

## Go/no-go probe (first Wildberries request from the host)

Run at 2026-10-16T21:33:18Z (**00:33 МСК, 2026-10-17**) with `docker compose run --rm --no-deps collector wbmon probe`; repeated never. JSON line verbatim:

```json
{"outcome": "blocked", "error_class": "http_403", "http_status": 403, "server": "wbaas", "content_type": "text/plain", "products": null, "normquery": null, "shape": null, "elapsed_ms": 145, "attempts": 1, "endpoint": "https://search.wb.ru/exactmatch/ru/common/v18/search", "params": {"ab_testing": "false", "appType": "1", "curr": "rub", "lang": "ru", "locale": "ru", "resultset": "catalog", "sort": "popular", "spp": "30", "suppressSpellcheck": "false", "dest": "-1257786", "page": "1", "query": "постельное белье 2 спальное"}, "user_agent": "Mozilla/5.0 (compatible; wbmon/0.1.0; +https://github.com/U-Steph/wb-serp-monitor)", "message": "http=403 server=wbaas ctype=text/plain attempts=1 transport=- body="}
```

The same `wbaas` 403 as on the dev machine: Wildberries does not serve a plain, honestly identified HTTP client from this VPS either. No circumvention was attempted (no proxy, VPN change, browser, other user agent, cookie or token).

## First run summary

No manual collection: the probe was not `ok`, so by the plan the manual run was skipped. The scheduler's own start-up catch-up for the current slot ran at start (this is the designed behavior of `wbmon scheduler`, not a manual run):

```
event=run_summary run_id=1 status=blocked trigger=scheduler query_source=sheet started_at=2026-10-16T21:33:58Z finished_at=2026-10-16T21:34:06Z n_queries=70 n_ok=0 n_partial=0 n_blocked=70 n_error=0 n_items=0
```

Three real requests went out, then the circuit breaker (3 consecutive blocked) recorded the other 67 queries as `blocked`/`circuit_open` without requests. The next slot ran on schedule after the reboot:

```
event=run_summary run_id=2 status=blocked trigger=scheduler query_source=sheet started_at=2026-10-16T22:00:01Z finished_at=2026-10-16T22:00:09Z n_queries=70 n_ok=0 n_partial=0 n_blocked=70 n_error=0 n_items=0
```

## Public link

From this Mac, over the Internet:

- `curl -fsS --max-time 15 https://194-226-98-141.sslip.io/_stcore/health` prints `ok` (before the restart, after the restart, after the reboot and at 01:02 МСК).
- `curl -fsS -o /dev/null -w '%{http_code}' https://194-226-98-141.sslip.io/` prints `200`, certificate verification result 0 (valid Let's Encrypt certificate, obtained by Caddy 7 s after the start).
- The direct port `http://194.226.98.141:8501` does not answer from outside (`WEB_BIND=127.0.0.1`), as intended.

## Restart and reboot evidence

Report counters (read-only, through `connect_ro`; script in the untracked `../wbmon-deploy/run_report.py`):

| Moment | runs_total | observations | scheduler_streak | integrity counters |
|--------|-----------|--------------|------------------|--------------------|
| Before the restart (21:35Z) | 1 | 0 | 1 | all 0 |
| After `docker compose --profile tls down` (no `-v`) and the same start command (21:37Z) | 1 | 0 | 1 | all 0 |
| After a host reboot (21:38Z) | 1 | 0 | 1 | all 0 |
| After the next scheduled slot (22:02Z, 01:02 МСК) | 2 | 0 | 2 | all 0 |

- The volume `wbmon_data` existed after the `down` and after the reboot; the collector's start log after the reboot shows `slot_exists slot=2026-10-16T21:00:00Z` (no repeated run) and `slot_wait next_slot=2026-10-16T22:00:00Z`.
- The reboot happened at 00:36 МСК; docker is `enabled`, all containers returned with `restart: unless-stopped` and became healthy within about a minute.
- Final report integrity counters (22:02Z): observations without an ok/partial run_queries row 0, observations in runs that are not manual/scheduler 0, runs started before the deployment start 0, scheduler runs off their slot window 0. Database `journal_mode=wal`, `user_version=1`.
- Listening sockets after the reboot: 22, 80, 443 on all interfaces; 8501 only on 127.0.0.1. ufw stayed inactive (not enabled, on purpose).

## Decision (Task 3)

`decision: pending-user`. The probe was `blocked`, so `continue` was not auto-selected. The user chooses between: `continue` (keep the stack running; hourly attempts continue, which gives an honest blocked history and costs about 3 real requests per hour), `ship-blocked` (ship the honest blocked history now; the UI, run log and README show plainly that WB blocks plain requests from this host), `other-host` (another host, new honest probe) and `other-source` (new scope). Never circumvention. The stack keeps running in the meantime.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] ssh option with a space in the path**
- **Found during:** preflight
- **Issue:** `ssh -o UserKnownHostsFile=<path with a space>` splits the value on whitespace and reported "No ED25519 host key is known" although the pinned file is correct.
- **Fix:** the same pinned `known_hosts` file is referenced through a symlink in the session scratchpad (host-key checking stays `yes`, nothing disabled).
- **Files modified:** none in the repository.

**2. [Plan-vs-reality] Task 3 not auto-selected**
- The probe outcome was `blocked`, so the plan's auto mode rule applies: `decision: pending-user`, checkpoint returned to the orchestrator.

**3. [Observation, no change] plain `docker compose down` and the tls profile**
- `docker compose --profile tls down` was used so that Caddy is stopped together with the rest; README `make down` runs plain `docker compose down`, which does not remove the profile-gated caddy service. Not changed here; the operations note can mention `--profile tls` in 04-03 if wanted.

## Known Stubs

None created by this plan. The documentation markers «будет добавлено после развёртывания» (README x2, SUBMISSION x1) remain for 04-03.

## Threat Flags

None beyond the plan's threat model. The public surface is exactly 80 and 443 (Caddy); the Streamlit port is bound to loopback.

## Next Phase Readiness

04-03 needs the user's Task 3 decision first. With `continue`, the stack is already running and has 2 consecutive scheduler runs, but all of them are `blocked`, so the evidence of at least 3 runs with real data cannot be produced unless Wildberries starts serving the host. With `ship-blocked`, 04-03 can replace the markers with the live URL and the probe JSON above and describe the honest blocked history.

## Self-Check: PASSED

- Public URL answered `ok` at the end of the run; commit `56a277a` is the deployed commit (`git rev-parse HEAD`).
- `git grep -nF` for the SSH target and the key file name in tracked files prints nothing; `git status --porcelain` is clean; no `.env*` file locally.
- `../wbmon-deploy/notes.md` (with `public_url:` and the start command) and `../wbmon-deploy/run_report.py` exist outside the repository.
