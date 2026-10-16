---
phase: 02-unattended-operation-packaging
reviewed: 2026-10-16T00:00:00Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - src/wbmon/health.py
  - src/wbmon/schedule.py
  - src/wbmon/signals.py
  - src/wbmon/scheduler.py
  - src/wbmon/cli.py
  - src/wbmon/orchestrator.py
  - src/wbmon/models.py
  - Dockerfile
  - .dockerignore
  - compose.yaml
  - deploy/Caddyfile
  - Makefile
  - pyproject.toml
findings:
  critical: 0
  warning: 5
  info: 6
  total: 11
status: issues_found
---

# Phase 2: Code Review Report

**Reviewed:** 2026-10-16
**Depth:** standard
**Files Reviewed:** 13 (pyproject.toml included)
**Status:** issues_found

## Summary

The supervision core is sound. I traced these paths and found no bug in them:
- the Popen poll loop;
- the SIGTERM→SIGKILL escalation;
- the slot math (inclusive 45-minute grace, no back-fill, a clock step back never re-runs a slot);
- atomic heartbeat replace;
- healthcheck exit codes (0/1 only, including argparse errors);
- Python 3.11 compatibility (`datetime.UTC`, walrus and `missing_ok` are all available).

The offline test suite passes (785 passed, 1 skipped because Docker is not installed).

The real problems are in the interplay between "graceful stop" and the actual duration of an in-flight WB query, and in the packaging defaults:
- The 60 s stop grace can be exceeded by design.
- Streamlit is published on all interfaces even when the TLS profile is used.
- The Makefile executes `.env` as shell code.

Limitation: `.env.example` is on a deny list, so I could not read it. WR-03 is therefore stated as a risk to check against that file, not as a confirmed failure.

## Warnings

### WR-01: SIGTERM cannot interrupt a sleeping or in-flight query, so the 60 s stop grace can be exceeded and the run is SIGKILLed

**File:** `src/wbmon/cli.py:245-257` (with `src/wbmon/wb_client.py:26-27,129-149` and `src/wbmon/scheduler.py:48,169-171`)
**Issue:**
- The `collect` SIGTERM handler only sets a flag.
- The flag is checked once per query, at the top of the loop in `orchestrator._run_queries`.
- `collect` passes plain `time.sleep` as the sleep function. Since PEP 475 a sleep interrupted by a signal handler resumes for the remaining time.
- The worst-case duration of one in-flight query is far above `STOP_GRACE_S = 60`:
  - Each page can make up to 3 attempts, and each attempt is capped at `REQUEST_TOTAL_SECONDS = 60`.
  - Backoff adds 2 s + 5 s.
  - A 429 adds a wait of up to `RETRY_AFTER_CAP = 60` s.
  - A query has 2 pages and a 1.5 s pacing sleep before each request.
- A degraded WB, which is exactly when timeouts happen, makes this likely. A `docker stop` or redeploy then takes the SIGKILL path at 60 s. The compose `stop_grace_period: 90s` would also be too short.
- The result is a run left `running`. The next start turns it into `abandoned` through `abandon_stale_runs`, and the intended `terminated` bookkeeping never happens. Data integrity holds, because every query commits atomically. The documented D2-A1 guarantee ("a SIGTERM never leaves a run running") is broken.

**Fix:**
- Give `collect` a stop-aware sleep: `_sleep or interruptible_sleep(stop)`. It would sleep in chunks of at most 0.5 s and return early when `stop.requested`. This covers the pacing delay and the backoff/Retry-After waits.
- A request that is already running cannot be interrupted this way. Either lower `REQUEST_TOTAL_SECONDS` while a stop is pending, or size the grace honestly. For example `STOP_GRACE_S = 90` with `stop_grace_period: 120s`, or document the limit.

```python
def stop_aware_sleep(flag: StopFlag, base=time.sleep, step=0.5):
    def _sleep(seconds: float) -> None:
        end = time.monotonic() + seconds
        while not flag.requested and (left := end - time.monotonic()) > 0:
            base(min(step, left))
    return _sleep
```

### WR-02: The web port is public by default, and the `tls` profile does not close it unless `WEB_BIND` is set

**File:** `compose.yaml:79-81`
**Issue:**
- `"${WEB_BIND:-0.0.0.0}:${WEB_PORT:-8501}:8501"` publishes an unauthenticated Streamlit on every interface.
- Docker publishes ports through its own iptables chains, which bypass ufw/firewalld. A host firewall is therefore not a mitigation.
- A user who enables `--profile tls` (or `COMPOSE_PROFILES=tls`) but forgets `WEB_BIND=127.0.0.1` gets plaintext 8501 next to the TLS endpoint. The Caddy hostname then protects nothing.
- Public exposure is accepted by D2-A3, but the safe configuration depends entirely on a remembered second setting.

**Fix:**
- Make the coupling harder to get wrong:
  - Ship the Caddy exposure as a separate override file (`compose.tls.yaml`) that does `ports: !override ["127.0.0.1:8501:8501"]`. This needs Compose >= 2.24.
  - Alternatively, have `make up` print a warning when `COMPOSE_PROFILES` contains `tls` and `WEB_BIND` is unset.
- At minimum, bind the web service's default to loopback and let `.env.example` opt in to public.

### WR-03: The Makefile executes `.env` as shell code and continues silently on a partial load

**File:** `Makefile:14,33,36,39,42`, together with the `Makefile:45,49` auto-copy of `.env.example`
**Issue:**
- `.env` is a compose-format file. It is also consumed by `env_file` and by compose interpolation. `ENVLOAD` instead runs it with `. ./.env` inside `set -a`.
- Compose accepts values that bash treats as syntax or control characters. Examples:
  - `WBMON_USER_AGENT=Mozilla/5.0 (compatible; wbmon/0.1; +https://...)` (parentheses and `;`);
  - a CSV URL containing `&` (the shell backgrounds the command and truncates the value);
  - `$` in values.
- In non-interactive bash, a syntax error inside a sourced file does not abort the recipe. The `. ./.env` part fails, `set +a;` runs, and `wbmon scheduler` or `wbmon collect` starts with a truncated or partial environment. A real run could then go to the default DB path or the wrong sheet.
- `.env` is also arbitrary code execution as the invoking user (acceptable for a local file, but it should be noted).
- I could not read `.env.example` to confirm which variables are present. Verify that every value in it is quoted.

**Fix:**
- Fail loudly: `ENVLOAD := set -a; if [ -f .env ]; then . ./.env || exit 1; fi; set +a;`
- Or avoid sourcing: run `docker compose --env-file`-style parsing, or use Make's `-include .env` plus `export` (which has stricter value rules).
- Document in `.env.example` that all values must be single-quoted.

### WR-04: `run_slot` has no guard against spawning after a stop request or leaking the child on an unexpected exception

**File:** `src/wbmon/scheduler.py:139-175`, `src/wbmon/scheduler.py:201-218`
**Issue:**
- Stop can be requested after the `while not self.stop.requested` check and before `spawn`. A child is then started and immediately SIGTERMed.
  - In that startup window, before `collect` installs its handler, SIGTERM has the default action and kills it.
  - That is harmless but wasteful, and it logs a spurious `run_failed`.
- The poll loop has no `try/finally`. Any unexpected exception in the loop is caught by `run_forever` and logged as `run_failed reason=exception`. The loop then continues, while the child (own session, so a terminal Ctrl-C never reaches it) keeps running untracked. The next slot's child only exits through `lock_busy`.
  - The in-loop exceptions I could identify (`beat`, `monotonic`, `terminate`) are unlikely.
- After `child.kill()` the loop polls forever if the process never dies (an unkillable D-state process). The heartbeat stays fresh, so the healthcheck reports healthy while no slots are run. A bound or an `unhealthy` signal is missing.

**Fix:**
```python
if self.stop.requested:          # at the top of run_slot, before spawn
    return
...
try:
    while (rc := child.poll()) is None:
        ...
except BaseException:
    with contextlib.suppress(Exception):
        child.kill()
        child.wait(timeout=5)
    raise
```
Also stop beating or log `child_unkillable` when `killed` has been set for more than ~60 s.

### WR-05: Container hardening is missing, and the unauthenticated public UI holds a read-write handle to the whole history volume

**File:** `compose.yaml:36-38,63-91`, `Dockerfile:31`
**Issue:**
- `web` mounts `wbdata` read-write. That is required for the WAL `-shm` file, and it is documented.
- Streamlit is public and unauthenticated (see WR-02). Any code-execution bug in the UI or its dependencies gives write access to the append-only history, which is the core value of the product.
- None of the services set `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, or `read_only: true` with `tmpfs` for `/tmp` and `/home/app`. Compose needs only `/data` and `~/.streamlit` to be writable.

**Fix:** Add to the shared `x-wbmon` block:
```yaml
security_opt: ["no-new-privileges:true"]
cap_drop: ["ALL"]
```
For `web` (and `collector`), add `read_only: true` plus `tmpfs: ["/tmp", "/home/app"]`. Check Streamlit starts with `read_only` in Phase 4.

## Info

### IN-01: Three services each carry the same `build:` block and image tag

**File:** `compose.yaml:20-26,42-49,63-64`
**Issue:** `init-db`, `collector` and `web` all inherit `build:` and `image: wb-serp-monitor:local` from the anchor. Depending on the Compose version, `up --build` may build the image once per service, or race on the tag.
**Fix:** Keep `build:` only on `init-db`. The other services reference `image: wb-serp-monitor:local` and `depends_on: init-db`, which they already do.

### IN-02: `.dockerignore` can ship secrets, and the `PIP_INDEX_URL` build argument is recorded in image history

**File:** `.dockerignore:6`, `Dockerfile:24`
**Issue:**
- `!.streamlit` re-includes everything under that directory, including a future `secrets.toml`.
- A mirror URL with credentials passed as `PIP_INDEX_URL` appears in `docker history`.
**Fix:**
- Add `.streamlit/secrets.toml` to the ignore list.
- Recommend a credential-free mirror, or use a BuildKit secret.

### IN-03: Caddy serves plain HTTP by default and enables no compression

**File:** `deploy/Caddyfile:7-9`, `compose.yaml:100`
**Issue:** The default is `:80`, so plain HTTP. There is no `encode zstd gzip`. CLAUDE.md lists lack of gzip as a drawback of the direct port, and the proxy does not provide it either.
**Fix:** Add `encode zstd gzip` inside the site block.

### IN-04: Only a plain SIGTERM or Ctrl-C works for the scheduler, and early SIGTERM is ignored in the container

**File:** `src/wbmon/signals.py:42-46`, `src/wbmon/scheduler.py:257-262`
**Issue:**
- The scheduler is PID 1 and has no `init: true`. Until the handler is installed (interpreter start plus `init_db`), the kernel ignores SIGTERM for PID 1, so `docker stop` waits the full 90 s.
- A second Ctrl-C locally only sets the flag again. The operator cannot force an exit short of `kill -9`, for up to 60 s.
**Fix:**
- Add `init: true` to the collector, or install handlers before `init_db`.
- Optionally let a second signal re-raise `KeyboardInterrupt`.

### IN-05: `should_catch_up(..., has_run, ...)` has a dead parameter

**File:** `src/wbmon/schedule.py:34-42`, `src/wbmon/scheduler.py:228-230`
**Issue:** The scheduler checks `has_run` itself and always passes `False`. The parameter is only used by tests, and the branch is duplicated.
**Fix:** Either use it (`should_catch_up(now, slot, self.has_run(slot), ...)`, taking the `slot_exists` log from the result), or drop it.

### IN-06: Smaller packaging and Makefile nits

**File:** `compose.yaml:66,87`, `Makefile:22-24`
**Issue:**
- `working_dir: /app` repeats the Dockerfile `WORKDIR`.
- The web healthcheck's `urllib.request.urlopen` honours `HTTP_PROXY` set through the `.env` file. A proxy value without `NO_PROXY` would route the 127.0.0.1 probe through the proxy and mark the container unhealthy.
- `make install` calls `python3` without a version check. The project needs >= 3.11, and macOS ships older interpreters, so the failure message is confusing.
- The healthcheck also opens the SQLite database for its informational `last_run`. That adds up to 5 s of busy-wait to a 10 s timeout.
**Fix:**
- Drop the duplicate `working_dir`.
- Use `urllib.request.build_opener(urllib.request.ProxyHandler({}))` in the probe.
- Let `make install` check `python3 -c 'import sys; assert sys.version_info >= (3, 11)'` first.
- Skip or cap the DB read when it is slow.

---

_Reviewed: 2026-10-16_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
