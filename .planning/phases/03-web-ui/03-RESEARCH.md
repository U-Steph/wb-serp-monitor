# Phase 3: Web UI - Research

**Researched:** 2026-10-16
**Domain:** Read-only Streamlit 1.66 + Plotly 7.1 + pandas 3.0 UI over the Phase 1 SQLite history (Russian UI, honest gaps)
**Confidence:** HIGH (every Streamlit/Plotly/pandas/SQL claim below was run in this session against the installed `.venv` and a scratch DB built with the real Phase 1 `init_db` + `Writer`; the few exceptions are tagged `[ASSUMED]`)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Phase boundary.** A Russian-language, read-only Streamlit app (`src/wbmon/ui/`) on top of the Phase 1 SQLite schema: data freshness header and honest blocked/partial banners, query + product selection, an interactive Plotly position chart (position 1 on top, honest gaps, failed-run bands, ad markers), characteristics and history tables with CSV export, a run log («Журнал сборов») with a query × run status heatmap, a SERP snapshot tab («Выдача») and a short methodology tab («О сервисе»). Out of scope: collection code (Phase 1), scheduler/Docker (Phase 2, runs in parallel — do not touch its files), deployment and README (Phase 4).

**Area 1 — Layout & navigation**

| # | Question | Accepted |
|---|----------|----------|
| 1 | Page structure | Single page, `st.set_page_config(page_title="WB SERP Monitor — позиции в поиске Wildberries", page_icon="📈", layout="wide")`; header (title, one-line caption «70 запросов · топ-200 · Москва · сбор каждый час», freshness badge, collector heartbeat, status banner); sidebar for global controls; 4 tabs: «📈 Позиции», «🗂 Выдача», «🩺 Журнал сборов», «ℹ️ О сервисе» |
| 2 | Sidebar controls | Query `st.selectbox` over all queries in sheet order, label `"{sheet_order:02d}. {text}"` (original wording); period `st.segmented_control` («24 ч» / «7 дней» / «Всё время», default «Всё время»); top-N default size `st.number_input` 1–20 default 5; totals block (runs total, successful runs, first collection МСК, observations, distinct products) |
| 3 | Visual style | `.streamlit/config.toml` light theme with a restrained purple accent `primaryColor="#7B2CBF"`, neutral greys, `font="sans serif"`; no WB logos/brand assets (no impersonation); Russian copy throughout; numbers formatted with thin spaces/₽ |
| 4 | Empty / error states | No DB file → info «База ещё не создана — запустите `wbmon init-db`»; zero runs → info «Сборов ещё не было. Первый сбор — в ближайшие :00 МСК»; all runs blocked → warning «Wildberries отклоняет запросы сборщика (антибот, HTTP 403). Запуски продолжаются каждый час и фиксируются в «Журнале сборов»; данные не подставляются»; query with no observations → info explaining why (blocked/never collected) |

**Area 2 — Position chart («📈 Позиции»)**

| # | Question | Accepted |
|---|----------|----------|
| 1 | Product picker | `st.multiselect` (fuzzy filter) over every `nm_id` ever seen for the query, label `"{nm_id} · {brand} · {name[:40]}"`, options ordered by latest position then best position; default = top-N (sidebar N) of the latest successful run for the query; widget key includes the query id so selection resets on query change; `max_selections=15` |
| 2 | Chart encoding | Plotly `go.Scatter` per product, `mode="lines+markers"`, x = run time converted UTC→Europe/Moscow and made tz-naive, y = position; `yaxis.autorange="reversed"` with `rangemode="tozero"`-like padding so position 1 is at the top; axis titles «Позиция в выдаче» / «Время сбора, МСК»; `hovermode="closest"`; hovertemplate «%{x|%d.%m.%Y %H:%M} МСК<br>ID %{customdata[0]}<br>Позиция %{y}<br>Цена %{customdata[1]}<extra>%{fullData.name}</extra>» (price shown as «—» when NULL); legend horizontal below; `st.plotly_chart(fig, width="stretch")`, toolbar `displaylogo=False` |
| 3 | Honest gaps & failures | Build the grid from ALL runs of the selected query (`runs` LEFT JOIN `run_queries`); point state per (run, product): FOUND (observation exists), ABSENT (query complete for that run and product not in it → y=None gap), UNKNOWN (query blocked/error/rate_limited/partial-before-reaching-depth or run abandoned → y=None gap + light-grey `add_vrect` band per such run with legend note «сбор не удался»); `connectgaps=False`; a caption under the chart explains: gap = товар не найден в топ-N; серая полоса = сбор по запросу не удался |
| 4 | Ad markers | Promoted observations drawn with `marker.symbol="diamond"` (others `circle`) via per-point symbol arrays; hover adds «реклама»; legend/caption note «◆ — рекламное размещение» |

**Area 3 — Tables & export**

| # | Question | Accepted |
|---|----------|----------|
| 1 | Characteristics table | One row per selected `nm_id` (latest observation in the period): ID (LinkColumn to `https://www.wildberries.ru/catalog/{nm_id}/detail.aspx`, display = ID), Название, Бренд, Продавец, Позиция (текущая), Δ к прошлому сбору (▲/▼, «новый»), Лучшая, Худшая, В выдаче (% complete runs where found), Цена, ₽, Рейтинг, Отзывов, Доставка, дн., Реклама (✓), Последний раз в выдаче (МСК); `st.dataframe` with `column_config`, hide index |
| 2 | History table | Expander «История наблюдений»: all observations of the selected products in the period (time МСК, ID, position, price, rating, reviews, delivery days, seller, ad) sorted by time desc |
| 3 | CSV | `st.download_button` «Скачать CSV»: `;` separator, decimal comma, `utf-8-sig` encoding (opens correctly in Russian Excel), filename `wb_serp_{query_slug}_{YYYYMMDD_HHMM}.csv` |
| 4 | «🗂 Выдача» tab | `st.select_slider` over the query's runs that have observations (labels МСК time, default latest) → full top-N table: Позиция, Реклама, ID (link), Название, Бренд, Цена, Рейтинг, Отзывов, Доставка, Продавец; metrics above: товаров собрано, из них рекламных, медианная цена, статус сбора |

**Area 4 — Run log, data access, performance**

| # | Question | Accepted |
|---|----------|----------|
| 1 | «🩺 Журнал сборов» | Table of last 200 runs: Начало (МСК), Длительность, Тип (по расписанию/вручную), Статус (emoji + Russian label: ✅ Успешно / 🟡 Частично / ⛔ Заблокировано / ❌ Ошибка / ⏸ Прерван / ⏳ Идёт), запросов ok/partial/blocked/error, товаров сохранено; above it a Plotly heatmap: Y = queries (sheet order, short labels), X = last 48 runs (МСК), categorical colours ok=green, partial=amber, blocked=red, rate_limited=orange, error=grey, missing=white, hover with query text + status + items; legend explanation of statuses in Russian |
| 2 | Freshness & heartbeat | Header badge: «Последний успешный сбор: DD.MM HH:MM МСК (N мин назад)» green < 90 min, amber < 3 h, red otherwise, or «успешных сборов пока нет»; second line «Сборщик: на связи N с назад / нет сигнала» from `<db_dir>/scheduler_heartbeat.json` if present (Phase 2 writes it; absent file → «нет данных о сборщике», not an error); banner for latest run status |
| 3 | Data access layer | `src/wbmon/ui/data.py`: short-lived read-only connections via Phase 1 `connect_ro` (`mode=ro` URI from `Path.as_uri()`, `PRAGMA query_only=ON`), never a cached connection object; loaders wrapped in `st.cache_data(ttl=60)` and keyed on `(db_path, latest_run_id)` so a new run invalidates caches; SQL only through parameterized queries; pure pandas transforms in `src/wbmon/ui/series.py` (grid building, point states, stats, Δ, presence) and Plotly figure builders in `src/wbmon/ui/charts.py`; `src/wbmon/ui/app.py` is the Streamlit entrypoint (`streamlit run src/wbmon/ui/app.py`) and uses `wbmon.config.Settings.from_env()` for `WBMON_DB_PATH` |
| 4 | Tests & performance | `tests/ui/test_series.py` (pure grid/state/stats vectors incl. absent vs unknown, isolated points, ads), `tests/ui/test_charts.py` (figure properties: reversed axis, connectgaps False, vrects, symbols), `tests/ui/test_app.py` with `streamlit.testing.v1.AppTest` against tmp DBs built through the Phase 1 writer: no DB, zero runs, all-blocked runs, normal multi-run history; UI-12 check: a generated week-sized tmp DB (≈168 runs × 70 × 200 via the fast generator from Phase 1 perf test) keeps each loader < 1 s; synthetic rows only ever in tmp DBs |

**«ℹ️ О сервисе» tab content (Russian).** Short methodology: what is collected and when (70 queries from the Google Sheet, top-200, hourly at :00 МСК, Moscow region `dest`), definitions (позиция = место в выдаче с учётом рекламных мест; цена = цена для покупателя без авторизации, минимальная по размерам; рейтинг/отзывы = по карточке товара; срок доставки = календарные дни от момента сбора по данным WB time1+time2, приблизительно), honesty rules (нет подстановки значений, пропуск на графике = товар не найден, серая полоса = сбор не удался), link to the repository (placeholder `https://github.com/U-Steph/wb-serp-monitor`), data limitations (WB anti-bot may block collection; blocked runs are shown as such).

**Code context.** Phase 3 must consume the Phase 1 schema/`connect_ro`/`reader`/`Settings`/`clock`, may ADD UI-specific read queries in `src/wbmon/ui/data.py`, and must not change the schema or Phase 1 modules except for additive, backwards-compatible reader helpers if unavoidable (prefer keeping UI SQL in `ui/data.py`).

**Specifics.** Streamlit 1.66 / Plotly 7.1 / pandas 3.0 are installed in `.venv`; `use_container_width` is removed — use `width="stretch"`. Phase 2 owns Dockerfile/compose/scheduler/Makefile/.env.example; Phase 3 owns `src/wbmon/ui/**`, `.streamlit/**`, `tests/ui/**` only (plus the `web` optional-dependency group already declared in Phase 1's pyproject). The real database may contain only blocked runs during review (WB anti-bot); the UI must look complete and informative in that state.

### Claude's Discretion
- Exact helper names inside `series.py` / `charts.py` / `data.py`, CSS-free styling details, emoji choice, exact Russian microcopy wording (keep it concise and neutral).

### Deferred Ideas (OUT OF SCOPE)
- Organic-rank chart mode, price-history chart, alerts, multi-region comparison — v2.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STOR-05 | Read-only access path for the UI (`mode=ro` URI + `query_only`), short-lived connections | `connect_ro` already exists in Phase 1 `storage/db.py` (read in this session); verified: read works while the collector holds an open `BEGIN IMMEDIATE` (2 ms, sees only committed rows) and a write raises `attempt to write a readonly database`. Pattern: `contextlib.closing(connect_ro(path))` per loader call; AST guardrail test bans `connect_rw`/writer imports and DML in `ui/` |
| UI-01 | Russian Streamlit app, zero/one/many runs, all-blocked states | `db_status()` tri-state (missing / uninitialized / ready) + AppTest scenarios (see Test Strategy); verified AppTest runs under the repo's socket-blocking autouse fixture |
| UI-02 | Freshness header + banner + sidebar totals | Overview SQL (0.9 ms) + separate `COUNT(*)` (7.6 ms) and `COUNT(DISTINCT nm_id)` (60 ms) statements — never combined (670 ms); freshness/heartbeat rules below |
| UI-03 | Select one of the 70 queries, original wording, sheet order | `SELECT id, text, sheet_order FROM queries ORDER BY sheet_order, id` (0.5 ms); label via `format_func`, never markdown-rendered |
| UI-04 | Pick nm_ids, searchable labels, default top-5 of latest run, reset on query change | Window-function product-options SQL (65 ms on 450 ids/query), `LATEST_DATA_RUN` + `TOP_OF_RUN` SQL, widget-key pattern verified in AppTest (key = query id + N), clamp default to `max_selections` |
| UI-05 | Interactive Plotly position chart | Prototype rendered in a real headless Chromium with the same plotly.js v4 bundle: reversed axis with explicit ticks, per-point diamonds, hover verified by `Plotly.Fx.hover` |
| UI-06 | Honest gaps / failed-run marking | Grid = `runs LEFT JOIN run_queries`; FOUND/ABSENT/UNKNOWN rule (ABSENT only when `status='ok' AND is_complete=1`); bands via ONE `layout.shapes` list assignment (not `add_vrect` per run, which is O(n²): 892 ms for 103 bands, 2.2 s for 168) |
| UI-07 | Characteristics + history tables | Derived in pandas from one observations query (3 ms for 15 ids / 1 095 rows); LinkColumn regex, `%d ₽` format, `placeholder="—"` verified by rendering in a real browser |
| UI-08 | CSV export, Excel-friendly | `df.to_csv(None, sep=";", decimal=",")` returns **str without BOM**; must `.encode("utf-8-sig")` (verified); CSV-injection sanitiser required |
| UI-09 | Time-range filter | `since` ISO-Z parameter anchored on the latest run's start (deterministic, cache-friendly); `started_at >= ''` for all-time |
| UI-10 | Run log tab + heatmap | Run-log SQL 0.6 ms; heatmap SQL 2.6 ms for 48 runs × 70; discrete-colour categorical `go.Heatmap` rendered and hover-verified |
| UI-11 | «Выдача» snapshot | Snapshot SQL 0.6 ms (autoindex); `st.select_slider` with run-id options + `format_func` labels verified (0 options errors, 1 option works) |
| UI-12 | Cached reads keyed on latest run id; responsive on a week of data | `st.cache_data(ttl=60, max_entries=...)` keyed on a data-version tuple; every loader measured on a generated 2.24 M-row DB: slowest 65 ms |
</phase_requirements>

## Summary

Phase 3 needs **no new dependency**: Streamlit 1.66.0, Plotly 7.1.0, pandas 3.0.6, numpy 2.5.4 and pyarrow 26.0.0 are already installed and declared in the `web` extra [VERIFIED: `.venv/bin/pip list`]. The data layer is cheap: with the frozen Phase 1 indexes, every UI loader I prototyped runs in under 70 ms on a generated week-sized database (2.24 M observations, 800 MB); the only trap is combining `COUNT(*)` and `COUNT(DISTINCT nm_id)` in one statement (670 ms, full table scan) versus two statements (7.6 ms + 60 ms). The honest-gap logic is a pure pandas cross-join of the run grid with the selected `nm_id`s and a three-state rule; I prototyped it against a DB written by the real Phase 1 `Writer` (including blocked, partial and absent cases) and it behaves as specified.

Four findings change how plans must be written. (1) **A Phase 1 guardrail test will fail the moment `src/wbmon/ui` lands**: `tests/test_guardrails.py::test_third_party_imports_in_src_are_httpx_only` scans every `.py` under `src/wbmon` and asserts third-party imports ⊆ {httpx}; Streamlit/pandas/plotly under `ui/` violate it, so Phase 3 must make a minimal, additive amendment (allow `{streamlit, pandas, plotly}` under `ui/` only) as its very first task; the sibling test `test_src_never_contains_forbidden_text` also scans the UI source for the strings `factories`, `_test_only`, `proxy=`, `OR REPLACE` etc. (2) **Untrusted WB strings must never reach markdown-rendering Streamlit calls**: verified in a real browser that `st.multiselect` labels, `st.dataframe` and `st.text` render text literally, while `st.write`/`st.markdown`/`st.caption`/`st.info` render markdown (LaTeX and links from product names would be interpreted). (3) **A NULL price inside a Plotly `hovertemplate` number format renders as «0 ₽»** (verified), which would be a fabricated value; prices must be pre-formatted to strings in Python («—» for NULL). (4) `st.cache_data` is shared across `AppTest` instances in one pytest process, `multiselect(default=...)` raises `StreamlitSelectionCountExceedsMaxError` when the sidebar top-N (up to 20) exceeds `max_selections=15`, `segmented_control` can be deselected to `None`, and Streamlit script files cannot use relative imports (all verified).

**UI-SPEC.** `03-UI-SPEC.md` (status: approved, appeared while this research was running) is the visual/copy contract. This research verified its stack claims and found **two places where following it literally causes a defect** (see "UI-SPEC Alignment"): `add_vrect` per failed run costs 2.2 s per rerun for a 168-run all-blocked week (the likely review state), and the `lo = max(0.5, y_min - pad)` y-axis rule clips the markers at position 1. Both have drop-in fixes that keep the specified visuals.

**Primary recommendation:** Build the UI as three layers with frozen DataFrame contracts (`data.py` SQL loaders with undecorated `*_raw` twins → `series.py` pure pandas → `charts.py` pure Plotly builders → thin `app.py`), assign failed-run bands once as a `layout.shapes` list (never `add_vrect` in a loop) and `html.escape` every untrusted string put into Plotly, pre-format every displayed number/price/time as text in Python, key caches on `(db_path, latest_run_id, last_run_query_id, latest_status)`, and plan in two waves of three plans (data layer ‖ pure core, then the app + AppTest suite).

## UI-SPEC Alignment (03-UI-SPEC.md, approved)

Authority: CONTEXT.md > UI-SPEC > FEATURES.md. Planners should copy visual/copy details (labels, banners, table columns, sizes) from UI-SPEC; this research only changes *how* a few of them are implemented. Verified or corrected here:

| UI-SPEC item | Research finding | Action |
|--------------|------------------|--------|
| `.streamlit/config.toml` content | **Valid as written**: all 25 keys (incl. `baseRadius`, `chartCategoricalColors`, `toolbarMode`) are accepted by Streamlit 1.66 `config.get_option`, and `streamlit run` starts with no config warning. `font = "sans-serif"` is the value listed in 1.66's `config.py` (CONTEXT's `"sans serif"` is the legacy spelling) | Use the UI-SPEC file verbatim; optionally add `showErrorDetails = "none"` under `[client]` (public-app hardening, not contradicting UI-SPEC). The file is read from the process **cwd** |
| Failed-run bands via `fig.add_vrect(...)` per run | `add_vrect` is O(n²): 59 ms / 24 bands, 892 ms / 103, **2 240 ms / 168** (all-blocked week), minutes for a month. Same visual with one assignment of `layout.shapes=[dict(type="rect", xref="x", yref="paper", y0=0, y1=1, x0=t-30min, x1=t+30min, fillcolor="rgba(134,142,150,0.20)", line_width=0, layer="below"), ...]`: 16-40 ms for 168 bands, 61 ms for 720, 166 ms for 2 000 (linear) | **Deviation (implementation only)**: build the shapes list in one go; keep UI-SPEC's colour, ±30 min, `layer="below"` and its two legend-only dummy traces. Verified in Chromium: adjacent bands merge into one grey field, the empty all-blocked skeleton (axes + bands + legend) renders |
| Y axis `lo = max(0.5, y_min - pad)` and Plotly-chosen `dtick` | With `y_min = 1` the top bound is 0.5, i.e. ~4 px of headroom; markers at position 1 are **clipped by the plot edge** (seen in the first render). Explicit ticks avoid any "tick below 1" | **Deviation**: top bound `-0.5` whenever `y_min - pad <= 0`, `tickmode="array"` with `tickvals=[1, 10, 20, ...]` so no tick ≤ 0 is ever shown (the intent of the spec). Needs product-owner/planner acknowledgement (Open Question 6) |
| Series name `ID {nm_id} · {brand}` | Plotly renders a **subset of HTML in trace names/hover**: verified `<b>` is rendered bold and `<a href="https://evil.example">` becomes a clickable link in the legend (`<img>` stays text). Brand is marketplace-controlled | Wrap every untrusted string placed in `name`, `customdata`, tick labels with `html.escape(s, quote=False)` (verified: no anchor is created; `quote=True` would show literal `&quot;`) |
| Hover template third line `<br>%{customdata[2]}` "must not leave a blank line" | A conditional line cannot live in the template | Make `customdata[2]` the whole fragment: `"<br>реклама"` (our constant, trusted) or `""`, and use `...Цена %{customdata[1]}%{customdata[2]}<extra>…` |
| Heatmap `text` + `texttemplate` glyphs "white except partial" | Plotly's default `textfont.color` is automatic-contrast: verified white glyphs on dark cells and dark (#444) on the amber cell | Do **not** set `textfont.color`; only `size=14` |
| Heatmap hover shows `HTTP` and items | `run_queries.http_status` was missing from the draft heatmap SQL | Added to the SQL below (2.2 ms) |
| Banner «Последний сбор заблокирован (HTTP {code})» | needs the most frequent `http_status` among blocked attempts | New loader `load_latest_run_detail` (0.3 ms), SQL below |
| `render_query_attempts(query_id, limit=10)` table | needs a loader | New loader `load_query_attempts`, SQL below (0.3 ms) |
| "Успешных сборов" = `ok` or `partial` that stored ≥ 1 observation; same definition for the freshness badge | `finish_run` yields `partial` only when ≥ 1 query is `ok`/`partial` (items > 0) and `ok` only when all are `ok`, so `status IN ('ok','partial')` is exactly that set (abandoned runs excluded) | Resolves Open Question 5: use `status IN ('ok','partial')` for both the sidebar total and the badge (`MAX(finished_at)`) |
| `HEARTBEAT_STALE_S` constant (default 600 s) in `ui/data.py` | Phase 2 CONTEXT: heartbeat written at start, every tick ≤ 30 s and after each run; whether it ticks *during* a collect subprocess is unknown | Keep the constant at 600; additionally treat «latest run `running` and started < 60 min ago» as alive (A5) |
| Plotly series colours "15-colour palette + dash for index ≥ 9" | explicit hex values keep figures deterministic and unit-testable regardless of how `theme="streamlit"` recolours traces `[ASSUMED]` | Put UI-SPEC's `chartCategoricalColors` list into `charts.PALETTE` explicitly; `line.dash="dash"` for index ≥ 9 |

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Rendering charts, hover, zoom, legend | Browser (plotly.js v4 bundled in Streamlit) | — | Figure is a JSON spec; Python never draws pixels |
| Widget state, reruns, tab layout | Streamlit server (Python script rerun model) | Browser (widget UI) | Streamlit re-executes `app.py` on every interaction |
| SQL reads, period filtering, aggregation that must use indexes | Database / Storage (SQLite via `connect_ro`) | Streamlit server (pandas) | Indexed paths are < 70 ms; never load whole `observations` |
| Honest-gap state machine (FOUND/ABSENT/UNKNOWN), Δ, presence %, freshness colours | Streamlit server — pure functions in `series.py` | — | Pure + unit-testable without Streamlit or SQLite |
| Figure construction (axis, bands, symbols, heatmap) | Streamlit server — pure functions in `charts.py` | Browser renders | Testable by inspecting `fig.data` / `fig.layout` |
| Caching | Streamlit server (`st.cache_data`) | — | Keyed on data version; connections never cached |
| Collector liveness (heartbeat file) | Filesystem (`<db_dir>/scheduler_heartbeat.json`, written by Phase 2) | Streamlit server (read, uncached) | Absent file is a normal state |
| CSV export | Streamlit server (bytes) → Browser (download) | — | Built from the already-loaded history frame |
| Read-only guarantee | Database (`mode=ro` + `query_only`) | CI guardrail test on `ui/` sources | Defence in depth: DB refuses writes, AST test forbids DML/`connect_rw` |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| streamlit | 1.66.0 (installed; published 2026-10-14) | UI, `AppTest`, caching | Required by the assignment; verified APIs below [VERIFIED: `.venv`] |
| plotly | 7.1.0 (installed) | Position chart, heatmap | Bundles with plotly.js v4 in Streamlit 1.66 (project STACK.md); rendered in headless Chromium this session without console errors [VERIFIED] |
| pandas | 3.0.6 (installed) | Shaping loader results, CSV | Streamlit hard dependency; new `str` default dtype confirmed [VERIFIED] |
| stdlib `sqlite3` via `wbmon.storage.db.connect_ro` | SQLite 3.50.4 in the dev venv (Docker image: 3.46) | Read path | Window functions (`ROW_NUMBER() OVER`) are available in both [VERIFIED locally; CITED: SQLite ≥ 3.25] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | 9.1.1 (installed) | Test runner | `tests/ui/**`; default addopts `-m "not slow"`; UI-12 week-DB test is `@pytest.mark.slow` |
| tzdata / `zoneinfo` | 2026.5 (installed) | Europe/Moscow conversion | `pd.to_datetime(..., utc=True).dt.tz_convert("Europe/Moscow").dt.tz_localize(None)` [VERIFIED] |
| numpy | 2.5.4 (transitive via pandas) | — | **Do not import directly** in `src/wbmon/ui`; use pandas only, so the guardrail allow-list stays `{streamlit, pandas, plotly}` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| One assignment of a `layout.shapes` list (+ legend-only dummy trace) | `fig.add_vrect` per run (CONTEXT/UI-SPEC wording) | `add_vrect` is O(n²): 59 ms for 24 bands, 892 ms for 103, **2 240 ms for 168** (verified); shapes list: 16-40 ms for 168, 61 ms for 720. Identical visual |
| `layout.shapes` list | One `go.Bar` trace on a hidden `y2` overlay axis | Bar gives hover text («сбор не удался (заблокировано)») and a native legend entry at 1 ms, but needs a second axis; use only if hover on bands is wanted (UI-SPEC says no information may depend on hover alone) |
| `st.connection("sql")` / SQLAlchemy | stdlib + `connect_ro` | Rejected by CONTEXT |
| `Scatter` (SVG) | `Scattergl` | Only if > ~20 k points (6 months × 15 products); not needed for the assignment |

**Installation:** none. `pip install -e ".[web,dev]"` already satisfied; Phase 3 never edits `pyproject.toml`.

**Version verification:** `.venv/bin/pip list` → streamlit 1.66.0, plotly 7.1.0, pandas 3.0.6, numpy 2.5.4, pyarrow 26.0.0, tzdata 2026.5, pytest 9.1.1 [VERIFIED: local].

## Package Legitimacy Audit

No package is installed by this phase (the `web` extra was declared by Phase 1 and installed by the orchestrator). For the record, `gsd-tools query package-legitimacy check --ecosystem pypi streamlit plotly pandas` returned `SUS` for all three, for the same heuristic reasons as in Phase 1 research (`too-new` = latest release is days/weeks old, `unknown-downloads` = PyPI JSON exposes no download counts); none is `SLOP`, none has a postinstall script.

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| streamlit | PyPI | multi-year, actively maintained (latest 2026-10-14) | n/a from seam | streamlit.io / github.com/streamlit/streamlit | SUS (too-new, unknown-downloads) | Already installed, user-locked by TZ; no new install, no checkpoint needed |
| plotly | PyPI | multi-year (latest 2026-09-15) | n/a | plotly.com/python | SUS (unknown-downloads) | Already installed; no new install |
| pandas | PyPI | multi-year (latest 2026-09-17) | n/a | pandas.pydata.org | SUS (too-new) | Already installed; no new install |

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** heuristic only, all already installed and user-locked; no `checkpoint:human-verify` required because Phase 3 installs nothing.

## Architecture Patterns

### System Architecture Diagram

```
 Browser ── widgets / tab clicks ──► Streamlit server: app.py reruns top-to-bottom
    ▲                                   │
    │ plotly.js renders fig JSON        ├─ Settings.from_env() ──► WBMON_DB_PATH (ConfigError → st.error + stop)
    │ (dataframes, badges, CSV bytes)   │
    │                                   ├─ db_status(path) ── "missing"/"uninitialized" ──► info + st.stop()
    │                                   │
    │                                   ├─ data_version(path)  (uncached, ~2 ms: MAX(runs.id), MAX(run_queries.id), latest status)
    │                                   │        │
    │                                   │        ▼  ver = tuple; part of every cache key
    │                                   ├─ data.load_*(path, ver, ...)  ── st.cache_data(ttl=60) ──► *_raw():
    │                                   │        closing(connect_ro(path)) → parameterised SQL → pandas (numpy_nullable)
    │                                   │        └──► SQLite WAL file (written by collector; readers never blocked)
    │                                   │
    │                                   ├─ heartbeat file read (uncached, tiny JSON, absent = normal)
    │                                   │
    │                                   ├─ series.* (pure pandas)  grid × nm_ids → FOUND / ABSENT / UNKNOWN,
    │                                   │         stats, Δ, presence %, freshness level, banner, CSV bytes
    │                                   └─ charts.* (pure plotly)  position_figure / status_heatmap → go.Figure
    │                                            │
    └──────────── st.plotly_chart(width="stretch") / st.dataframe(column_config) / st.download_button ◄──┘
```

### Recommended Project Structure
```
src/wbmon/ui/
├── __init__.py        # docstring only
├── app.py             # Streamlit entrypoint; absolute imports ONLY (relative imports fail in a script)
├── data.py            # SQL constants, *_raw loaders (no streamlit decorator), cached twins, db_status, data_version, heartbeat
├── series.py          # pure pandas: to_msk, prepare_grid, build_points, product_stats, delta_text, history_frame, csv_bytes, freshness, banner
├── charts.py          # pure plotly: y_axis_spec, position_figure, status_heatmap
└── texts.py           # Russian strings, status/trigger maps, md_escape, price/time formatters (lines ≤ 100 chars: ruff E501 applies)
.streamlit/config.toml # theme + client + browser keys (cwd-relative!)
tests/ui/
├── __init__.py        # avoids basename collisions with other tests/ modules (prepend import mode)
├── conftest.py        # autouse st.cache_data.clear(); fixtures building tmp DBs
├── dbbuilder.py       # scenario → DB via real Writer (see Code Examples)
├── test_data.py, test_series.py, test_charts.py, test_texts.py, test_app.py, test_ui_guardrails.py
└── test_perf.py       # @pytest.mark.slow, week-sized DB, loaders < 1 s
```
Module names must not shadow stdlib/third-party names: Streamlit puts the script's directory (`src/wbmon/ui`) at `sys.path[0]` (verified), so a file called `json.py`, `logging.py`, `types.py`, `copy.py`, `statistics.py` there would break imports. The names above are safe; always `from wbmon.ui.data import ...`.

### Frame contracts (so the two wave-1 plans can run in parallel)

All loader frames use `pd.read_sql_query(..., dtype_backend="numpy_nullable")` → integer columns with NULL become `Int64`, reals `Float64`, text `string`. Timestamps stay raw UTC `YYYY-MM-DDTHH:MM:SSZ` strings; `series.to_msk` converts. `ver` is the `DataVersion` named tuple.

| Loader (cached twin; `_raw` is the undecorated function) | Columns |
|---|---|
| `data_version(path) -> DataVersion(latest_run_id, last_rq_id, latest_status)` | uncached |
| `load_overview(path, ver) -> dict` | `n_runs, n_ok_runs, first_started, last_ok_finished, last_partial_finished, n_obs, n_products, has_observations, top_n_latest, n_queries` |
| `load_queries(path, ver)` | `query_id, text, sheet_order` |
| `load_run_grid(path, ver, query_id, sheet_order, since)` | `run_id, started_at, run_status, trigger, q_status, error_class, http_status, items_collected, is_complete, pages_fetched` (`q_status` NULL = no `run_queries` row) |
| `load_product_options(path, ver, query_id)` | `nm_id, n_seen, best_position, worst_position, last_at, last_run_id, last_position, last_promoted, name, brand` (one row per nm_id) |
| `load_default_ids(path, ver, query_id, n) -> tuple[int, ...]` | top-n positions of the latest run with data for that query |
| `load_observations(path, ver, query_id, nm_ids: tuple, since)` | `run_id, collected_at, nm_id, position, is_promoted, price_rub, rating, feedbacks, delivery_days, seller_name, name, brand` |
| `load_snapshot_runs(path, ver, query_id)` / `load_snapshot(path, ver, run_id, query_id)` | `run_id, started_at, status, items_collected` / `position, is_promoted, nm_id, name, brand, price_rub, rating, feedbacks, delivery_days, seller_name` |
| `load_latest_run_detail(path, ver, run_id)` | `{"blocked_http": int \| None}` – most frequent `http_status` among `blocked`/`rate_limited` attempts of that run |
| `load_query_attempts(path, ver, query_id, limit=10)` | `started_at, status, http_status, pages_fetched, items_collected, error_class, duration_ms` (newest first; feeds `render_query_attempts`) |
| `load_run_log(path, ver, limit=200)` | `run_id, trigger, started_at, finished_at, status, query_source, n_queries, n_ok, n_partial, n_blocked, n_error, n_items, error` (`n_blocked` counts blocked **and** rate_limited) |
| `load_status_matrix(path, ver, n_runs=48)` | `(runs_df[run_id, started_at, status], matrix_df[run_id, query_id, status, items_collected, is_complete, http_status, error_class])` |
| `read_heartbeat(path) -> dict \| None` | uncached; keys `ts, pid, next_slot, last_slot, last_exit, version` (Phase 2 CONTEXT) |

### Pattern 1: Raw loader + cached twin, connection per call
**What:** SQL lives in undecorated functions that take a path and return DataFrames; the `st.cache_data` twin wraps them. Tests and the perf test call `*_raw` directly (no Streamlit runtime, no "No runtime found" noise); the app calls the cached twins.
**When to use:** every loader.
**Example:**
```python
# Source: verified in this session (streamlit 1.66.0, pandas 3.0.6, Python 3.14)
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import NamedTuple

import pandas as pd
import streamlit as st

from wbmon.storage.db import connect_ro


class DataVersion(NamedTuple):
    latest_run_id: int | None
    last_rq_id: int | None
    latest_status: str | None


def _df(con, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=params, dtype_backend="numpy_nullable")


def load_run_grid_raw(path: str, query_id: int, sheet_order: int, since: str) -> pd.DataFrame:
    with contextlib.closing(connect_ro(path)) as con:     # 3.13+ ResourceWarning: `with con:` does not close
        return _df(con, RUN_GRID_SQL, {"q": query_id, "since": since, "sheet_order": sheet_order})


@st.cache_data(ttl=60, max_entries=256, show_spinner=False)
def load_run_grid(path: str, ver: DataVersion, query_id: int, sheet_order: int, since: str) -> pd.DataFrame:
    del ver                                               # only part of the cache key
    return load_run_grid_raw(path, query_id, sheet_order, since)
```
Verified cache behaviour: tuple/str/int/None/Path/datetime args hash fine; a list and the equal tuple hash differently (pass tuples); an argument prefixed `_` is excluded from the key; the returned DataFrame is a copy (mutations do not leak); `ttl` and `fn.clear()` work; `max_entries` bounds memory (query × selection × period combinations).

### Pattern 2: SQL (verified against the real Phase 1 schema and a 2.24 M-row DB)

All bound by name (`:q`) or `?`; the only dynamic SQL is the `?`-placeholder list for `IN (...)` (≤ 15 ids from `max_selections`).

```sql
-- DATA_VERSION (uncached every rerun, ~2 ms)
SELECT (SELECT MAX(id) FROM runs) AS latest_run_id,
       (SELECT MAX(id) FROM run_queries) AS last_rq_id,
       (SELECT status FROM runs ORDER BY id DESC LIMIT 1) AS latest_status;

-- OVERVIEW (0.9 ms) -- run these as SEPARATE statements:
SELECT COUNT(*) AS n_runs, COALESCE(SUM(status IN ('ok','partial')), 0) AS n_ok_runs, MIN(started_at) AS first_started FROM runs;
SELECT MAX(finished_at) FROM runs WHERE status IN ('ok','partial'); -- freshness badge (UI-SPEC: same definition as the total)
SELECT COUNT(*) FROM observations;                                  -- 7.6 ms (covering scan)
SELECT COUNT(DISTINCT nm_id) FROM observations;                     -- 60 ms (covering ix_obs_query_nm_time)
SELECT EXISTS(SELECT 1 FROM observations);                          -- O(1): "has any data"
-- NEVER: SELECT COUNT(*), COUNT(DISTINCT nm_id) FROM observations  -> 668 ms (table scan)

-- RUN_GRID  (0.9 ms; plan: SEARCH r USING ix_runs_started, SEARCH rq USING ix_rq_query_run LEFT-JOIN)
SELECT r.id AS run_id, r.started_at, r.status AS run_status, r.trigger,
       rq.status AS q_status, rq.error_class, rq.http_status, rq.items_collected, rq.is_complete, rq.pages_fetched
FROM runs r LEFT JOIN run_queries rq ON rq.run_id = r.id AND rq.query_id = :q
WHERE r.started_at >= :since
  AND (rq.id IS NOT NULL OR (r.status <> 'running' AND :sheet_order <= r.n_queries))
ORDER BY r.started_at, r.id;
```
The last `WHERE` line is deliberate: a run that never attempted this query (`--limit-queries 2` manual runs, or a `running` run that has not reached it yet) must NOT paint a grey band; an abandoned/crashed run that planned the query (`sheet_order <= n_queries`) but has no `run_queries` row DOES (UNKNOWN). `wbmon probe` writes nothing to the DB (Phase 1 plan 01-04), so no probe runs exist. [VERIFIED: ran on the real schema; `sheet_order <= n_queries` is an approximation if the sheet order changes later — `[ASSUMED]` acceptable]

```sql
-- PRODUCT_OPTIONS (65 ms for 450 ids; one row per nm_id even if (run, nm_id) is duplicated)
WITH ranked AS (
  SELECT nm_id, run_id, position, is_promoted, name, brand, collected_at,
         ROW_NUMBER() OVER (PARTITION BY nm_id ORDER BY collected_at DESC, position ASC) AS rn,
         COUNT(*)      OVER (PARTITION BY nm_id) AS n_seen,
         MIN(position) OVER (PARTITION BY nm_id) AS best_position,
         MAX(position) OVER (PARTITION BY nm_id) AS worst_position
  FROM observations WHERE query_id = :q)
SELECT nm_id, n_seen, best_position, worst_position, collected_at AS last_at,
       run_id AS last_run_id, position AS last_position, is_promoted AS last_promoted, name, brand
FROM ranked WHERE rn = 1;

-- LATEST_DATA_RUN (0.3 ms, ix_rq_query_run) and TOP_OF_RUN (0.2 ms, sqlite_autoindex_observations_1)
SELECT MAX(run_id) FROM run_queries WHERE query_id = ? AND items_collected > 0;   -- ok OR partial
SELECT nm_id FROM observations WHERE run_id = :r AND query_id = :q ORDER BY position LIMIT :n;

-- OBSERVATIONS for selected ids (3.1 ms for 15 ids / 1 095 rows; ix_obs_query_nm_time, small temp b-tree for ORDER BY)
SELECT run_id, collected_at, nm_id, position, is_promoted, price_rub, rating, feedbacks, delivery_days,
       seller_name, name, brand
FROM observations WHERE query_id = ? AND nm_id IN (?,?,...) AND collected_at >= ? ORDER BY collected_at, nm_id;

-- SNAPSHOT_RUNS (0.6 ms) / SNAPSHOT (0.6 ms)
SELECT r.id AS run_id, r.started_at, rq.status, rq.items_collected FROM run_queries rq JOIN runs r ON r.id = rq.run_id
WHERE rq.query_id = ? AND rq.items_collected > 0 ORDER BY r.started_at DESC, r.id DESC LIMIT 500;
SELECT position, is_promoted, nm_id, name, brand, price_rub, rating, feedbacks, delivery_days, seller_name
FROM observations WHERE run_id = ? AND query_id = ? ORDER BY position;

-- RUN_LOG (0.6 ms, ix_runs_started) and HEATMAP (2.6 ms for 48 runs x 70; uses sqlite_autoindex_run_queries_1)
SELECT id AS run_id, trigger, started_at, finished_at, status, query_source, n_queries, n_ok, n_partial,
       n_blocked, n_error, n_items, error FROM runs ORDER BY started_at DESC, id DESC LIMIT :limit;
SELECT id AS run_id, started_at, status FROM runs ORDER BY started_at DESC, id DESC LIMIT :n;
SELECT rq.run_id, rq.query_id, rq.status, rq.items_collected, rq.is_complete, rq.http_status, rq.error_class
FROM run_queries rq WHERE rq.run_id IN (SELECT id FROM runs ORDER BY started_at DESC, id DESC LIMIT :n);

-- LATEST_RUN_DETAIL (0.3 ms) and QUERY_ATTEMPTS (0.3 ms, ix_rq_query_run)
SELECT http_status, COUNT(*) AS n FROM run_queries WHERE run_id = ? AND status IN ('blocked','rate_limited')
GROUP BY http_status ORDER BY n DESC LIMIT 1;
SELECT r.started_at, rq.status, rq.http_status, rq.pages_fetched, rq.items_collected, rq.error_class, rq.duration_ms
FROM run_queries rq JOIN runs r ON r.id = rq.run_id WHERE rq.query_id = ? ORDER BY rq.run_id DESC LIMIT ?;
```

Measured on a generated 168 runs × 70 queries × 200 positions DB (2 236 958 observations, 11 760 `run_queries`, 818 MB, ANALYZE'd, fresh `connect_ro` per call, macOS, warm cache): data_version 1.8 ms, overview 0.9 ms, queries 0.5 ms, run grid 0.9 ms, product options 24-65 ms, observations 1.8-3.1 ms, snapshot 0.6 ms, run log 0.6 ms, heatmap 2.6 ms, distinct-products 60 ms. All ≤ 70 ms, i.e. > 14× margin to UI-12's 1 s. A cold VPS disk adds I/O but every path is index-bounded. [VERIFIED: local benchmark]

### Pattern 3: The honest-gap state machine (`series.build_points`)

Rule (per run × product), prototyped and run against a DB written by the real `Writer`:
- **FOUND** – an observation row exists for `(run_id, nm_id)`.
- **ABSENT** – no row **and** the query attempt is *complete*: `q_status == 'ok' AND is_complete == 1`. (Phase 1 can store `status='ok', is_complete=0` when the page cap is hit; that is NOT proof of absence — PITFALLS #13.)
- **UNKNOWN** – no row and the attempt is not complete: `blocked`, `rate_limited`, `error`, `partial`, `ok` with `is_complete=0`, or no `run_queries` row (abandoned run).
- A `partial` run still contributes FOUND points (positions < the depth reached are real) but never ABSENT.
- Duplicate `(run_id, nm_id)` rows are not forbidden by the schema (UNIQUE is `(run_id, query_id, position)`): sort by `position` and `drop_duplicates(["run_id","nm_id"])` — **do not** use `merge(validate="one_to_one")` (my first prototype crashed on a duplicate).

```python
# Source: prototype verified against Phase 1 init_db + Writer output (scratch, not project code)
import numpy as np
import pandas as pd

FOUND, ABSENT, UNKNOWN = "found", "absent", "unknown"
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def to_msk(s: pd.Series) -> pd.Series:
    """UTC 'Z' strings -> tz-naive Europe/Moscow wall clock (plotly.js ignores offsets)."""
    return pd.to_datetime(s, format=TS_FORMAT, utc=True).dt.tz_convert("Europe/Moscow").dt.tz_localize(None)


def prepare_grid(grid: pd.DataFrame) -> pd.DataFrame:
    g = grid.copy()
    g["t_msk"] = to_msk(g["started_at"])
    g["q_status"] = g["q_status"].astype("object").where(g["q_status"].notna(), "missing")
    g["complete"] = ((g["q_status"] == "ok") & (g["is_complete"].fillna(0) == 1)).fillna(False).astype(bool)
    return g


def build_points(grid: pd.DataFrame, obs: pd.DataFrame, nm_ids: list[int]) -> pd.DataFrame:
    g = prepare_grid(grid)
    idx = pd.MultiIndex.from_product(
        [g["run_id"].astype("int64"), pd.Index(nm_ids, dtype="int64")], names=["run_id", "nm_id"])
    cross = idx.to_frame(index=False).merge(
        g[["run_id", "t_msk", "q_status", "complete", "items_collected"]], on="run_id", how="left")
    o = (obs.drop(columns=["collected_at"], errors="ignore").sort_values("position")
            .drop_duplicates(["run_id", "nm_id"]).copy())
    o["run_id"] = o["run_id"].astype("int64"); o["nm_id"] = o["nm_id"].astype("int64")
    out = cross.merge(o, on=["run_id", "nm_id"], how="left")
    found = out["position"].notna()
    out["state"] = np.where(found, FOUND, np.where(out["complete"], ABSENT, UNKNOWN))   # np in prototype; use Series.mask in src (no numpy import)
    return out.sort_values(["nm_id", "t_msk", "run_id"], ignore_index=True)
```
(In `src/` replace the two `np.where` calls with `pd.Series(UNKNOWN, index=out.index).mask(out["complete"], ABSENT).mask(found, FOUND)` so `numpy` is not imported directly.)

Derived rules for the characteristics table (recommendations, `[ASSUMED]` where noted):
- **Current position** = position in the latest run whose state for that product is known (FOUND or ABSENT); ABSENT → «—» plus note «вне топ-N».
- **Δ к прошлому сбору** = previous known-state run's position − current position: positive → «▲ n», negative → «▼ n», zero → «=», previous ABSENT and current FOUND → «новый», no previous known run → «—» `[ASSUMED]`.
- **В выдаче, %** = FOUND-in-complete-runs ÷ complete runs in the period (denominator excludes UNKNOWN runs; a partial run's FOUND points do not enter the numerator, so the value can never exceed 100 %).
- **Лучшая/Худшая** = min/max position over FOUND points in the period.
- **Период** («24 ч»/«7 дней») anchored on the latest run's `started_at` (not wall-clock now): deterministic for tests and cache keys; the header's freshness badge already tells the reader how stale the data is `[ASSUMED]`. `since = iso_z(latest_started - delta)`; all-time = `""`.

### Pattern 4: Chart builders (verified by rendering in headless Chromium, plotly.js from the same plotly 7.1 wheel)

Layout numbers (height 520, margins `l=64,r=16,t=16,b=88`, 14 px fonts, legend `y=-0.18`, `modeBarButtonsToRemove`) come from UI-SPEC "Chart contract"; the code shows the parts that need care.

```python
# Source: prototype rendered + hover-checked with Plotly.Fx.hover; fig.data inspected in pytest-style asserts
import html
import pandas as pd
import plotly.graph_objects as go

BAND_FILL = "rgba(134,142,150,0.20)"
HALF_RUN = pd.Timedelta(minutes=30)


def esc(text: object) -> str:
    """Plotly renders a subset of HTML (<b>, <a href>) in names/hover/ticks: neutralise untrusted text."""
    return html.escape("" if text is None or pd.isna(text) else str(text), quote=False)


def y_axis_spec(positions: list[int]) -> dict:
    """Reversed axis (range given high -> low), position 1 on top WITH headroom, explicit integer ticks."""
    lo, hi = (min(positions), max(positions)) if positions else (1, 20)
    pad = max(2.0, 0.06 * (hi - lo))
    top = lo - pad if lo - pad > 0 else -0.5          # UI-SPEC would give 0.5 -> markers at 1 are clipped
    bottom = hi + pad
    step = 5 if bottom <= 40 else 10 if bottom <= 120 else 20 if bottom <= 260 else 50
    ticks = [1] + [v for v in range(step, int(bottom) + 1, step) if v - 1 >= step / 2]
    return dict(title="Позиция в выдаче", range=[bottom, top], tickmode="array",
                tickvals=[v for v in ticks if top <= v <= bottom], tickformat="d", zeroline=False)


def price_text(v) -> str:                              # NEVER leave this to hovertemplate number formats (None -> "0")
    return "—" if pd.isna(v) else f"{v:,.0f}".replace(",", "\u202f") + " ₽"


def band_shapes(times: pd.Series) -> list[dict]:       # ONE list, ONE assignment (add_vrect loop is O(n^2))
    return [dict(type="rect", xref="x", yref="paper", y0=0, y1=1, x0=t - HALF_RUN, x1=t + HALF_RUN,
                 fillcolor=BAND_FILL, line_width=0, layer="below") for t in times]


def position_figure(points: pd.DataFrame, band_times: pd.Series, labels: dict[int, str],
                    x_range: tuple[pd.Timestamp, pd.Timestamp], top_n: int) -> go.Figure:
    fig = go.Figure()
    shown: list[int] = []
    for i, (nm_id, d) in enumerate(points.groupby("nm_id", sort=False)):
        d = d.sort_values("t_msk")
        y = [None if pd.isna(v) else int(v) for v in d["position"]]       # list, not ndarray (ndarray -> base64 'bdata')
        shown += [v for v in y if v is not None]
        promo = d["is_promoted"].fillna(0).astype(int).eq(1).tolist()
        color = PALETTE[i % len(PALETTE)]                                  # UI-SPEC chartCategoricalColors, explicit
        fig.add_trace(go.Scatter(
            x=d["t_msk"], y=y, mode="lines+markers", connectgaps=False, name=esc(labels[int(nm_id)]),
            line=dict(color=color, width=2, dash="dash" if i >= 9 else "solid"),
            marker=dict(color=color, size=[10 if p else 7 for p in promo],
                        symbol=["diamond" if p else "circle" for p in promo]),
            customdata=[[int(nm_id), price_text(p), "<br>реклама" if a else ""] for p, a in zip(d["price_rub"], promo)],
            hovertemplate=("%{x|%d.%m.%Y %H:%M} МСК<br>ID %{customdata[0]}<br>Позиция %{y}"
                           "<br>Цена %{customdata[1]}%{customdata[2]}<extra>%{fullData.name}</extra>")))
    # legend-only dummies (UI-SPEC): ad diamond + failed-run square; they carry no data
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name="◆ рекламное размещение",
                             marker=dict(symbol="diamond", size=10, color="#868E96")))
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name="сбор не удался",
                             marker=dict(symbol="square", size=12, color="rgba(134,142,150,0.5)")))
    fig.update_layout(
        template="plotly_white", height=520, hovermode="closest", shapes=band_shapes(band_times),
        xaxis=dict(title="Время сбора, МСК", type="date", range=list(x_range), tickformat="%d.%m<br>%H:%M"),
        yaxis=y_axis_spec(shown) if shown else {**y_axis_spec([1, top_n]), "range": [top_n + 0.5, -0.5]},
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0),
        margin=dict(l=64, r=16, t=16, b=88), font=dict(size=14, color="#1A1D21"))
    return fig
```
`x_range = (min(grid t_msk) - 30 min, max(grid t_msk) + 30 min)` keeps the axis non-degenerate for a single run and makes the *empty skeleton* (all-blocked or nothing selected) render: verified in Chromium — grey field of merged bands, reversed 1…200 axis, two legend entries, no data traces. For the `go.Scatter` trace count, the 15 product traces + 2 dummies stay far below any SVG limit (720 runs × 15 products = 10.8 k points rendered fine).

Rendered result (checked visually): gaps break the line, isolated points stay visible, diamonds mark ads, grey bands under the markers, position 1 labelled and not clipped. `st.plotly_chart(fig, width="stretch", config={"displaylogo": False, "modeBarButtonsToRemove": [...]}, key=...)`; AppTest exposes it as `at.get("plotly_chart")[0].proto.spec` (JSON with `data`/`layout`; `layout.shapes` holds the bands) and `.proto.config`.

Heatmap (also rendered and hover-checked): 6 status codes, `zmin=-0.5, zmax=n-0.5`, a stepped `colorscale` built as `[[i/n, c], [(i+1)/n, c]]`, `showscale=False` (UI-SPEC; the legend is a badge row, so the colorbar-ticktext trick is only needed if the colorbar is kept), `xgap=ygap=1` on grey `plot_bgcolor` so white «нет данных» cells stay visible, **category** x axis (a date axis would stretch cells across a missed hour) with `tickangle=-90`, `side="top"`; category y axis `autorange="reversed"` and `tickmode="array"` with one tick per query (`dtick=1` also works), `automargin=True`; `text` matrix + `texttemplate="%{text}"` with `textfont=dict(size=14)` and **no** colour (auto contrast gives white on dark cells and dark on amber, verified); `customdata` = per-cell strings; with a 2-D `customdata` of strings `%{customdata}` prints the cell string (verified: «08. запрос … / 15.10 07:00 МСК / Успешно»). Escape the query text in `customdata`/ticks with `esc()`.

### Pattern 5: Widgets (all verified in AppTest)
```python
period = st.segmented_control("Период", ["24 ч", "7 дней", "Всё время"], default="Всё время",
                              required=True, key="period") or "Всё время"        # None if deselected without required=True
n_default = st.number_input("Топ-N по умолчанию", 1, 20, 5, key="topn")
chosen = st.multiselect(                                                          # key includes query id AND N
    "Товары", options=ids, default=default_ids[: min(n_default, 15)], max_selections=15,
    format_func=labels.__getitem__, key=f"products_{query_id}_{n_default}", select_all=False)
run_id = st.select_slider("Сбор", options=run_ids, value=run_ids[-1], format_func=run_label,
                          key=f"snap_{query_id}")                                  # guard len(run_ids) == 0 first
st.download_button("Скачать CSV", data=csv_bytes, file_name=fname, mime="text/csv", on_click="ignore", key="dl")
```
- Key without N: changing N keeps the user's old selection (default ignored); key with N: N change resets to the new default (desired «top-N» semantics); both reset on query change.
- `default` longer than `max_selections` → `StreamlitSelectionCountExceedsMaxError` (also when session state is manipulated) → clamp `min(N, 15)`.
- `select_slider(options=[])` → `StreamlitMissingRequiredParameterError`; one option works; a stale stored value not in new options silently resets (still put the query id in the key).
- `st.badge` renders as markdown `:green-badge[...]` (AppTest: found in `at.markdown`).

### Pattern 6: Tables
`st.dataframe(df, hide_index=True, width="stretch", placeholder="—", column_order=[...], column_config={...})` with `LinkColumn("ID", display_text=r"catalog/(\d+)/detail")`, `NumberColumn("Цена, ₽", format="%d ₽")`, `NumberColumn(format="%d")`, `CheckboxColumn`, `DatetimeColumn(format="DD.MM.YYYY HH:mm")` on tz-naive MSK datetimes, `None` to hide a helper column. Rendered in headless Chromium: link text shows the bare ID, «1234 ₽», missing cells «—». Nullable `Int64`/`Float64`/`boolean`/`string` and `datetime64[us]` columns are accepted by Streamlit (no exception). `"%,d"` gives comma (not thin-space) thousands, so keep `"%d ₽"` (bedding prices < 10 000 ₽). AppTest assertions: `json.loads(at.dataframe[0].proto.columns)` contains `type_config` (link/number format/display_text) and `"_index": {"hidden": true}`; `at.dataframe[0].value` is the pandas frame.

### Pattern 7: Header, freshness, heartbeat (per UI-SPEC, plus verified details)
- **Successful run** = `runs.status IN ('ok','partial')` (UI-SPEC refinement; verified equivalent to "stored ≥ 1 observation" via `finish_run`). The freshness badge uses `MAX(finished_at)` of those runs (fallback `started_at`); colours: `:green-badge` < 90 min, `:yellow-badge` < 3 h, `:red-badge` otherwise, `:gray-badge` «Успешных сборов пока нет». The text is built only from our numbers (no untrusted strings), so `st.markdown(":green-badge[...]")` is safe. Age is computed on every rerun from `clock.utc_now()` — never inside a cached function.
- **Heartbeat**: `Path(settings.db_path).parent / "scheduler_heartbeat.json"`; parse `ts` with `datetime.fromisoformat(ts.replace("Z", "+00:00"))`; live if age ≤ `HEARTBEAT_STALE_S` (600 s, named constant in `data.py`); missing/invalid file → «нет данных», never an exception (catch `OSError`, `json.JSONDecodeError`, `KeyError`, `ValueError`). Guard `[ASSUMED]`: if the latest run is `running` and started < 60 min ago, treat the collector as alive («идёт сбор») even when the heartbeat is stale.
- **Banner** rules and copy: UI-SPEC "Status banner rules" (first match wins). Data needed: latest run row (from `load_run_log`), `has_observations`, `load_latest_run_detail(...)["blocked_http"]`.
- Header caption numbers: UI-SPEC hard-codes «70 запросов · топ-200»; prefer `COUNT(queries)` and the latest `runs.top_n` (honest if `WBMON_TOP_N` differs).

### Anti-Patterns to Avoid
- **Untrusted text in markdown calls.** Product `name/brand/seller_name` (WB) and query text (Google Sheet) must never go through `st.write/markdown/caption/info/warning/metric(label)` unescaped (verified: `$x^2$` becomes LaTeX, `[link](...)` becomes a link). Use `st.dataframe`, `st.text`, option labels, or `texts.md_escape()` (escape `\ ` * _ [ ] ( ) $ < > # ~` |`). Never `unsafe_allow_html`.
- **`add_vrect`/`add_shape` loops** (quadratic: 2.2 s for 168 bands). Assign `layout.shapes` once.
- **Unescaped marketplace text in Plotly names/hover/ticks**: `<a href=...>` in a brand becomes a live link in the legend (verified). `html.escape(s, quote=False)`.
- **Passing numpy arrays to `go.Scatter(y=...)`**: `to_json` emits `{"dtype":"f8","bdata":"..."}`; unit tests then cannot read values easily. Use Python lists with `None`.
- **Formatting prices in `hovertemplate` (`%{customdata[1]:.0f}`)**: `None` renders as `0`. Pre-format strings.
- **tz-aware datetimes to Plotly**: always `.dt.tz_localize(None)` after converting to MSK.
- **Caching a connection**, or letting a cached function read `utc_now()`.
- **Relative imports** in `app.py`/`ui` modules loaded by `streamlit run` (fails with `attempted relative import with no known parent package`, verified).
- **`merge(validate="one_to_one")`** on observations (duplicates possible).
- **`COUNT(*)` and `COUNT(DISTINCT nm_id)` in one statement.**

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| CSV with Russian Excel conventions | manual string joins | `DataFrame.to_csv(None, sep=";", decimal=",", index=False, lineterminator="\r\n")` + `.encode("utf-8-sig")` | Quoting of `;`, quotes and newlines inside names verified round-trip |
| Tz conversion | manual `+3h` arithmetic | `pd.to_datetime(utc=True).dt.tz_convert("Europe/Moscow").dt.tz_localize(None)` | tzdata installed; MSK has no DST but the code stays correct |
| Discrete categorical colours | custom shapes/annotations | `go.Heatmap` + stepped colorscale + custom colorbar ticks | Verified rendering and hover |
| Fuzzy product search | custom text filter | `st.multiselect(filter_mode="fuzzy")` (default) | Built in; labels render as plain text |
| Cache invalidation | timers/globals | `st.cache_data(ttl, max_entries)` keyed on `DataVersion` | Verified hashing/ttl/clear |
| Link display of ID | custom HTML | `LinkColumn(display_text=r"catalog/(\d+)/detail")` | Regex capture group verified in browser |
| Read-only enforcement | trusting conventions | `connect_ro` (already in Phase 1) + AST guardrail test | Defence in depth |
| UI testing | selenium/playwright (also banned-adjacent) | `streamlit.testing.v1.AppTest` | Ships with Streamlit; no browser/network |

**Key insight:** every "honesty" feature (gaps, bands, absent-vs-unknown, NULL-as-dash) is cheap when computed once in pure pandas and asserted on figure/frame properties; it becomes fragile when left to Plotly/Streamlit defaults (NaN→0 in templates, `None` rendered as the string "None", interpolation across missing x).

## Common Pitfalls

### Pitfall 1: Phase 1 guardrail tests fail as soon as `ui/` exists
**What goes wrong:** `tests/test_guardrails.py::test_third_party_imports_in_src_are_httpx_only` globs `src/wbmon/**/*.py` and requires non-stdlib imports ⊆ {httpx}; `test_src_never_contains_forbidden_text` greps all of `src/` for `factories`, `_test_only`, `proxy=`, `proxies=`, `OR REPLACE`, `REPLACE INTO`, `x_wbaas`, `verify=False`, `http2=True` (case-insensitive, whole-file upper-cased).
**Why it happens:** Phase 1 wrote the allowlist for a collector-only package [VERIFIED: file read this session].
**How to avoid:** Plan 03-01 Task 1 amends that one test minimally (UI files may import `{streamlit, pandas, plotly}` only; everything else unchanged), and wave-1 plans verify with *targeted* pytest paths (not the whole suite) until the amendment lands; the full-suite gate sits in the wave-2 plan. UI source must avoid the forbidden substrings (e.g. don't write "factories" in a docstring). **This is a Phase-1-owned file: the orchestrator must approve the exception (see "Open Questions" #1).**
**Warning signs:** red `test_guardrails.py` right after the first `import streamlit` in `src/`.

### Pitfall 2: `multiselect` default larger than `max_selections`
**What goes wrong:** sidebar N allows 1–20 but `max_selections=15` → `StreamlitSelectionCountExceedsMaxError` (app crash for every user who sets N > 15) [VERIFIED].
**How to avoid:** `default=ids[:min(n, 15)]`; AppTest case `N=20` must show no exception.

### Pitfall 3: NULL price shown as «0 ₽» (or «None»)
**What goes wrong:** Plotly `%{customdata[1]:.0f}` renders `null` as 0 [VERIFIED in Chromium]; `st.dataframe` shows `None` unless `placeholder` is set.
**How to avoid:** pre-formatted strings in `customdata`; `placeholder="—"` on every `st.dataframe`; unit test: a NULL-price point's `customdata[1] == "—"`.

### Pitfall 4: `to_csv` returns `str` without BOM
**What goes wrong:** `df.to_csv(sep=";", decimal=",", encoding="utf-8-sig")` with no path returns **str**; `encoding` is ignored → Excel shows mojibake [VERIFIED].
**How to avoid:** `df.to_csv(None, ...).encode("utf-8-sig")`; test asserts `data[:3] == b"\xef\xbb\xbf"` and `pd.read_csv(io.BytesIO(data), sep=";", decimal=",", encoding="utf-8-sig")` round-trips (including a name with `;`, `"` and a newline).

### Pitfall 5: CSV/formula injection from marketplace data
**What goes wrong:** a seller can name a product `=HYPERLINK("http://evil","click")`; Excel executes cells starting with `= + - @ \t \r` when the user opens the exported CSV (CWE-1236).
**How to avoid:** in the export only, prefix any text cell starting with those characters with `'` (document it in the UI caption); keep raw values in the DB and on screen. Unit-test vector `=1+1`, `+7999…`, `-cmd`, `@SUM(A1)`.

### Pitfall 6: pandas 3 nullable dtypes
**What goes wrong:** `pd.read_sql_query` turns an INTEGER column with NULLs into `float64` (positions shown as `3.0`) and text into `str` with `NaN` [VERIFIED]; `np.where` on `BooleanDtype` with `<NA>` raises.
**How to avoid:** `dtype_backend="numpy_nullable"` (→ `Int64`, `Float64`, `string`), `.fillna(False).astype(bool)` before boolean logic, convert to plain Python lists with `None` for Plotly (`[None if pd.isna(v) else int(v) ...]`). Use `contextlib.closing` for connections (3.13+ `ResourceWarning`).

### Pitfall 7: `st.cache_data` leaks between AppTest runs
**What goes wrong:** the cache is process-global; a second `AppTest` instance reuses the first one's cached frames (verified: new instance → no reload) → tests pass/fail depending on order. Also, `st.cache_data` functions called outside a runtime log "No runtime found, using MemoryCacheStorageManager" (harmless).
**How to avoid:** autouse fixture in `tests/ui/conftest.py`: `st.cache_data.clear()` before and after each test; per-test tmp DB path is also part of every key.

### Pitfall 8: Script-style entrypoint constraints
**What goes wrong:** relative imports fail; `sys.path[0]` is the `ui/` directory so sibling module names can shadow stdlib/third-party modules [VERIFIED]; `st.set_page_config` must be the first Streamlit call; Streamlit reads `.streamlit/config.toml` from the **current working directory**, not the script directory.
**How to avoid:** absolute imports `from wbmon.ui import data, series, charts, texts`; never name a `ui/` module like a stdlib module; Phase 2's Dockerfile must run `streamlit run` with `WORKDIR` containing `.streamlit/` and must not exclude `.streamlit` in `.dockerignore` (cross-phase note for the orchestrator).

### Pitfall 9: Tz and time semantics
**What goes wrong:** plotly.js ignores UTC offsets (shows wall clock); `DatetimeColumn` without `timezone` shows naive values as-is (good); the x axis mixing `collected_at` (per query, minutes after start) and run start produces ragged times.
**How to avoid:** x = `runs.started_at` converted to naive MSK for every chart/table/«Выдача» label (one rule: «Сбор (МСК)» = run start); `collected_at` kept only for the `since` filter (always ≥ run start, so `collected_at >= since_run_start` includes every observation of the period's runs). Axis title «Время сбора, МСК».

### Pitfall 10: Quadratic / heavy Plotly shapes
**What goes wrong:** `add_vrect` ×103 = 0.9 s, ×168 = 2.2 s of pure Python per rerun [VERIFIED]; the all-blocked review state is exactly the worst case, and the app reruns on every widget click. **How to avoid:** single `layout.shapes=[dict(...), ...]` assignment (16-40 ms for 168, 61 ms for 720); unit test: building the figure for 720 failed runs completes in < 250 ms.

### Pitfall 11: A run in progress / abandoned run confuses the grid
**What goes wrong:** the current `running` run has `run_queries` rows only for queries reached so far; without the grid's `WHERE` clause every other query would show a grey «failed» band for the in-flight hour; manual `--limit-queries` runs likewise. **How to avoid:** the `rq.id IS NOT NULL OR (status <> 'running' AND sheet_order <= n_queries)` predicate; AppTest/series vectors for running, abandoned and limited runs.

### Pitfall 12: Public-app error leakage and DB path disclosure
**What goes wrong:** an uncaught exception shows a traceback with the DB path to every visitor. **How to avoid:** `.streamlit/config.toml` `[client] showErrorDetails = "none"` and `toolbarMode = "viewer"` (both validated by `streamlit.config` in this session); catch `sqlite3.OperationalError` (locked/uninitialised) in `db_status`/loaders and show a generic Russian message; never print `str(exc)` or the path in the UI.

### Pitfall 13: `segmented_control` can be deselected; `select_slider` needs ≥ 1 option
Covered in Pattern 5 (`required=True` plus `or "Всё время"` fallback; guard empty run lists with `st.info`).

### Pitfall 14: Hidden-state surprises in tests
`at.selectbox[0].options` returns **strings** (`['1','2','3']`) even for int options, `.value` the raw value; `multiselect.set_value` bypasses the browser's `max_selections` guard (exceeding it raises in the script, shown in `at.exception`); `at.get("plotly_chart")` returns an `UnknownElement` (use `.proto.spec`); `st.badge` is only visible via `at.markdown`.

## Code Examples

### Test DB builder through the real Phase 1 writer (synthetic rows only in tmp DBs)
```python
# tests/ui/dbbuilder.py — verified pattern (built a 6-run, 3-query DB incl. blocked and partial attempts)
from datetime import datetime, timedelta, timezone
from wbmon.models import ObservationRow, QueryResult
from wbmon.storage.db import connect_rw
from wbmon.storage.migrations import init_db
from wbmon.storage.writer import Writer


def obs(position, nm_id, at, **kw):                      # every field explicit; unknown = None, never 0
    base = dict(collected_at=at, position=position, nm_id=nm_id, name=f"ТЕСТ товар {nm_id}", brand="ТЕСТ-БРЕНД",
                subject_id=None, root_id=None, price_rub=1000.0 + position, price_basic_rub=None, price_total_rub=None,
                logistics_rub=None, rating=4.7, feedbacks=10, nm_rating=None, nm_feedbacks=None, time1_h=None,
                time2_h=None, delivery_hours=None, delivery_days=2, seller_name="ТЕСТ-Продавец", seller_id=1,
                is_promoted=0, ad_promo_position=None, ad_organic_position=None, ad_tp=None, ad_cpm=None,
                total_quantity=None)
    base.update(kw)
    return ObservationRow(**base)


def build_db(path, scenario):                            # scenario: list of runs -> {query_text: QueryResult builder}
    init_db(path)
    con = connect_rw(path)
    w = Writer(con)
    ...                                                  # upsert_queries -> start_run(started_at=..., slot_utc=...) -> record_query -> finish_run
    con.close()
```
`finish_run` aggregates the status itself (all ok → `ok`; any blocked only → `blocked`; mixed → `partial`); a `running`/`abandoned` run is produced by simply not calling `finish_run` (leave `running`) or by inserting through `Writer.abandon_stale_runs` if Phase 1 ships it (check the Phase 1 summaries at execution time).

### AppTest scenario template
```python
# tests/ui/test_app.py
import os
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[2] / "src" / "wbmon" / "ui" / "app.py")

@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    st.cache_data.clear(); yield; st.cache_data.clear()

def run_app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=30).run()      # env WBMON_DB_PATH already set per test by tests/conftest.py

def test_no_db_shows_info():
    at = run_app()
    assert not at.exception
    assert any("wbmon init-db" in i.value for i in at.info)

def test_history(tmp_path):
    build_db(os.environ["WBMON_DB_PATH"], SCENARIO_NORMAL)
    at = run_app()
    assert not at.exception
    spec = json.loads(at.get("plotly_chart")[0].proto.spec)
    assert spec["layout"]["yaxis"]["range"][0] > spec["layout"]["yaxis"]["range"][1]      # reversed
    at.selectbox[0].select(2).run(); assert not at.exception                               # query change
    at.sidebar.number_input[0].set_value(20).run(); assert not at.exception                 # N=20 > max_selections
```
Verified API facts: `AppTest.from_file(path, default_timeout=...)` (default 3 s is too short for a big app), `.run()`, `at.exception`, `at.info/warning/error`, `at.markdown`, `at.metric`, `at.tabs`, `at.expander`, `at.dataframe`, `at.download_button`, `at.multiselect/select_slider/selectbox/number_input`, `at.sidebar.*`, `at.session_state[...]`, `at.segmented_control` (alias of `button_group`), `.set_value()`. It runs fine under the repo's autouse fixture (socket/DNS blocked, tmp cwd, tmp `WBMON_DB_PATH`) [VERIFIED with a copy of that fixture]. Uncaught exceptions print a large traceback to stderr but are also in `at.exception`.

### `.streamlit/config.toml`
Use the file in UI-SPEC "Theme file" **verbatim** — verified in this session: every key is accepted by Streamlit 1.66 and `streamlit run` starts with no warning. Suggested single addition for a public app:
```toml
[client]
toolbarMode = "viewer"
showErrorDetails = "none"      # no tracebacks / DB path in the browser (valid values: full, stacktrace, type, none)
```
Leave `[server]` flags (headless, port, address) to Phase 2's command line. The file is found via the process **cwd**; `AppTest` does not read it (theme is not testable there — human-verify step).

### Guardrail amendment (minimal, additive)
```python
UI_THIRD_PARTY = {"streamlit", "pandas", "plotly"}

def test_third_party_imports_in_src_are_httpx_only_and_ui_is_read_only_stack():
    core: set[str] = set(); ui: set[str] = set()
    for path in _src_files():
        names = {n for n in module_imports(path) if n not in sys.stdlib_module_names and n not in {"wbmon", ""}}
        (ui if path.relative_to(SRC_DIR).parts[0] == "ui" else core).update(names)
    assert core <= {"httpx"}
    assert ui <= UI_THIRD_PARTY            # the UI never imports httpx, numpy, sqlite writers...
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `st.plotly_chart(use_container_width=True)` | `width="stretch"` (arg still in the signature, deprecated) | Streamlit 1.5x | Use `width="stretch"` |
| `st.cache_data(ttl=..)` only | + `max_entries`, `.clear()` per function | long-standing | Bound memory for selection combinations |
| `st.radio(horizontal=True)` for periods | `st.segmented_control` (needs `required=True` to forbid deselect) | 1.40+ | Compact period filter |
| `st.multiselect` search | `filter_mode="fuzzy"` default, `select_all` param | 1.6x | Set `select_all=False` with `max_selections` |
| `add_vrect` per failed run | one `layout.shapes` assignment | plotly 6/7 | O(n) build instead of O(n²) |
| pandas object strings, float64 for NULL ints | `str` dtype default, `dtype_backend="numpy_nullable"` | pandas 3.0 | Convert explicitly for Plotly |
| `st.dataframe` shows `None` | `placeholder=` argument | 1.6x | `placeholder="—"` |

**Deprecated/outdated:** `use_container_width` (removed/deprecated), Tornado-era config advice, `add_vrect` loops for many bands, `datetime.UTC` is fine on 3.11+.

## Runtime State Inventory

Not applicable: greenfield UI phase, no rename/migration. None — verified: no stored data, service config, OS registration, secret or build artifact carries a name that Phase 3 changes (it adds new files only and touches no schema).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Exceptions to file ownership are acceptable: Phase 3 may amend Phase 1's `tests/test_guardrails.py` third-party allow-list for `src/wbmon/ui/` | Pitfall 1, Open Q1 | If refused, the full suite stays red; alternative is orchestrator amending it as part of Phase 1 wrap-up |
| A2 | Δ к прошлому сбору semantics (previous *known-state* run; «новый» when returning from ABSENT) | Pattern 3 | Cosmetic; user may expect "previous run" literally |
| A3 | Period filters anchored on the latest run's start, not wall clock | Pattern 3 | If the user wants "last 24 h from now", an outage shows an empty chart instead of the pre-outage window (header still shows staleness) |
| A4 | `sheet_order <= runs.n_queries` is an adequate "this run planned this query" test | Pattern 2 | If the sheet is reordered between runs, a limited manual run could paint a stray band or miss one |
| A5 | Heartbeat may not tick while a long `collect` subprocess runs (Phase 2 not yet implemented) → treat a young `running` run as «идёт сбор» even when the heartbeat is older than `HEARTBEAT_STALE_S` (600 s) | Pattern 7 | Without the guard, a false «нет сигнала» during each ~4-40 min run |
| A6 | Public WB product URL pattern `https://www.wildberries.ru/catalog/{nm_id}/detail.aspx` is valid (CONTEXT-locked; not fetched: no live WB requests allowed) | Pattern 6 | Dead link in table |
| A7 | Chart/heatmap look right inside the actual Streamlit page with `theme="streamlit"` (verified in standalone plotly HTML with the same plotly.js, and the dataframe in a real Streamlit page) | Pattern 4 | Minor visual polish; covered by end-of-phase human verify |
| A8 | The y-axis top bound `-0.5` with explicit ticks (instead of UI-SPEC's `max(0.5, …)`) is acceptable: same intent (no tick below 1), fixes clipping of markers at position 1 | UI-SPEC Alignment | If the spec owner insists on 0.5, markers at position 1 are half clipped |
| A9 | ASCII filename slug `q07` (sheet order) instead of transliterated query text | CSV | Cosmetic; CONTEXT says `{query_slug}` |
| A10 | Phase 1 reader/writer signatures read this session (`Writer.start_run`, `record_query`, `finish_run`, `connect_ro`, `Settings`) are final | Code Examples | Executors must re-read Phase 1 summaries; tests/ui/dbbuilder.py is the only consumer of the writer |

## Open Questions

1. **Who amends `tests/test_guardrails.py`?**
   - What we know: it fails for any `streamlit/pandas/plotly` import under `src/wbmon/` (read in this session); Phase 3 owns only `src/wbmon/ui/**`, `.streamlit/**`, `tests/ui/**`.
   - What's unclear: whether the orchestrator accepts a minimal additive edit by Plan 03-01.
   - Recommendation: approve it (snippet above); keeps the COL-05 core rule (`httpx` only for everything outside `ui/`) and adds `ui ⊆ {streamlit,pandas,plotly}`.
2. **`.streamlit/config.toml` location at runtime.** Streamlit reads it from the process cwd. Phase 2's compose must run `streamlit run` with cwd containing `.streamlit/` and not `.dockerignore` it. Recommendation: orchestrator relays this to Phase 2/4 (cross-phase note; Phase 3 cannot edit their files).
3. **Does Phase 2's scheduler heartbeat tick during a collect run?** Unknown until Phase 2 ships; the UI guard in Pattern 7 makes the behaviour correct either way.
4. **`ui_phase` / `ui_safety_gate`.** `03-UI-SPEC.md` now exists (approved); plans must cite it for copy, sizes and component choices, and cite this file for implementation mechanics. Where they differ (Open Question 6) the plan should state the chosen implementation explicitly.
5. ~~Freshness wording for partial runs~~ — resolved by UI-SPEC: successful = `ok` or `partial`.
6. **Two deliberate deviations from UI-SPEC implementation details** (bands as one `shapes` list instead of `add_vrect` calls; y-axis top bound `-0.5` + explicit ticks). Visual result is the specified one (or better); the planner should record them in the plan so the checker does not flag them.
7. **Heatmap/«Выдача» for > 500 runs**: `select_slider` is capped at the latest 500 runs; heatmap at 48 runs by decision. Fine for the assignment horizon.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python venv with streamlit/plotly/pandas | everything | ✓ | 3.14.6 (code must stay 3.11-compatible) | — |
| `streamlit.testing.v1.AppTest` | `tests/ui/test_app.py` | ✓ | 1.66.0 | — |
| Headless Chromium via gstack `browse` | optional visual check of chart/heatmap (not part of the automated suite) | ✓ | — | skip; end-of-phase human verify (`human_verify_mode: end-of-phase`) |
| Docker | not needed for Phase 3 | ✗ | — | Phase 4 |
| Network / live WB | forbidden | ✗ by design | — | tests are offline (socket guard) |

**Missing dependencies with no fallback:** none. **Missing with fallback:** none relevant.

## Plan Split (3 plans, 2 waves, disjoint `files_modified`)

**Wave 1 (parallel)** — verification commands must be *targeted* (`pytest tests/ui/test_x.py`, `ruff check <own files>`), not the full suite, until 03-01's guardrail amendment has landed.

| Plan | Wave | Requirements | `files_modified` | Content |
|------|------|--------------|------------------|---------|
| **03-01 Data layer + test infrastructure** | 1 | STOR-05, UI-12 (+ data side of UI-02/03/04/09/10/11) | `tests/test_guardrails.py` (Task 1, minimal amendment), `src/wbmon/ui/__init__.py`, `src/wbmon/ui/data.py`, `tests/ui/__init__.py`, `tests/ui/conftest.py`, `tests/ui/dbbuilder.py`, `tests/ui/test_data.py`, `tests/ui/test_perf.py` | Task 1 guardrail amendment; Task 2 `data.py` (SQL constants, `*_raw`, cached twins, `db_status`, `data_version`, heartbeat reader) + `dbbuilder` + `test_data.py` (query-plan assertions: no `SCAN observations` except covering-index scans; blocked/partial/running/abandoned grid vectors; read-only: INSERT via the UI connection raises); Task 3 slow perf test: generate week-sized tmp DB (recursive-CTE generator, 168×70×200 ≈ 8 s, 0.8 GB, deleted in a finalizer; own copy, do not import from `tests/test_storage_perf.py`) and assert each `*_raw` loader < 1 s |
| **03-02 Pure core: series, charts, texts, theme** (inputs: UI-SPEC copy/size contracts) | 1 | UI-05, UI-06, UI-07 (calc), UI-08, UI-09 (period math), UI-10/11 (builders), UI-02 (freshness/banner logic) | `src/wbmon/ui/texts.py`, `src/wbmon/ui/series.py`, `src/wbmon/ui/charts.py`, `.streamlit/config.toml` (UI-SPEC file verbatim + `showErrorDetails`), `tests/ui/test_series.py`, `tests/ui/test_charts.py`, `tests/ui/test_texts.py` | Pure functions only, built against the frame contracts above (no DB): grid/state vectors (absent vs unknown vs partial, isolated point, duplicates, ads, NULL price), stats/Δ/presence, freshness colour thresholds, banner selection, `csv_bytes` (BOM, `;`, decimal comma, injection prefix, round-trip), `md_escape`, figure properties (reversed range, `connectgaps False`, `None` y, symbols, band traces/legend names, `customdata` price «—», no tz-aware x, ≤ 15 palette), heatmap construction (colorscale steps, glyph text matrix, y order, height `20*n+96`, escaped hover text) |

**Wave 2**

| Plan | Wave | Requirements | `files_modified` | Content |
|------|------|--------------|------------------|---------|
| **03-03 App assembly + AppTest suite + visual check** | 2 (depends on 03-01, 03-02) | UI-01..UI-04 (app side), UI-05..UI-12 end-to-end | `src/wbmon/ui/app.py`, `tests/ui/test_app.py`, `tests/ui/test_ui_guardrails.py` | Tasks: (1) `app.py` skeleton: `set_page_config`, `Settings.from_env()` (catch `ConfigError`), `db_status` empty states, header (freshness badge, heartbeat, banner), sidebar; (2) Позиции tab (picker with clamped default and keys, figure, captions, characteristics table, history expander, CSV button); (3) Выдача, Журнал сборов (heatmap + table), О сервисе tabs; (4) AppTest scenarios: no DB, uninitialised DB file, zero runs, all-blocked, normal history (gap vs band assertions via `plotly_chart.proto.spec`), latest run `running`, query change, N=20, period filter, cache invalidation after appending a run through the Writer, markdown-injection vectors (`$x$`, `[l](u)`, `**b**`) appearing literally in dataframe/labels; `test_ui_guardrails.py` AST/text checks (no `connect_rw`/`wbmon.storage.writer`/`httpx`/`socket`/`numpy` imports in `ui/`, no `unsafe_allow_html`, no `use_container_width`, no INSERT/UPDATE/DELETE/DROP/ALTER/PRAGMA-write keywords in SQL strings). Whether untrusted text reaches a markdown call cannot be proven by AST; rely on the AppTest injection vectors plus `md_escape`/`esc` unit tests; (5) full-suite + ruff gate (`.venv/bin/python -m pytest -q` and `-m slow tests/ui/test_perf.py`, `.venv/bin/ruff check src tests`), then a `checkpoint:human-verify` (end-of-phase) opening `streamlit run src/wbmon/ui/app.py` against a **tmp** DB built by `dbbuilder` (never the real `data/`), checking the tabs visually |

Constraints for all plans: Python 3.11-compatible syntax; ruff `E,F,W,I,B,UP`, line length 100 (Russian copy must be wrapped); never run `wbmon collect`; never create `data/`; synthetic rows only in `tmp_path`; UI-12 measurements use `connect_ro`.

## Security Domain

`security_enforcement` is enabled (ASVS level 1).

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no (public read-only link by requirement; no accounts) | — |
| V3 Session Management | no (Streamlit session is anonymous, no secrets in state) | — |
| V4 Access Control | yes (read-only) | `connect_ro` + `query_only`; AST guardrail forbids writer imports/DML in `ui/`; no "run now" button; DB path never displayed |
| V5 Input Validation | yes | All user input is widget-constrained (selectbox over DB ids, number_input 1–20, segmented labels, ≤ 15 ids); SQL only via bound parameters, the sole dynamic fragment is `?` placeholders sized by `len(nm_ids)` (≤ 500 cap) |
| V6 Cryptography | no | TLS is Phase 2/4 (Caddy) |
| V7 Error handling & logging | yes | `showErrorDetails = "none"`; generic Russian messages; no exception text/paths in UI |
| V8/V12 Data & files | yes | CSV export sanitises formula-leading characters; no file upload; DB file not served |
| V14 Config | yes | Streamlit XSRF/CORS defaults untouched; toolbar `viewer` |

### Known Threat Patterns for Streamlit + SQLite + marketplace data

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Markdown/LaTeX/link injection via product or seller names (verified: `st.write` renders `$…$` and `[..](..)`) | Tampering / Spoofing | Render untrusted text only via `st.dataframe`/`st.text`/option labels; `md_escape` elsewhere; never `unsafe_allow_html` |
| CSV formula injection in exported history | Tampering | Prefix `'` on cells beginning `= + - @ \t \r` in the export only |
| SQL injection via IN-list or period | Tampering | Bound parameters; ids coerced with `int()`; `since` produced by `clock.iso_z` |
| Write via UI path | Tampering | `mode=ro`, `query_only=ON`, guardrail test |
| DoS via expensive selections / reruns | DoS | `max_selections=15`, cached loaders with `ttl`, `max_entries`, bounded `LIMIT`s (500 snapshot runs, 200 log rows), all loaders < 70 ms |
| Information disclosure (tracebacks, DB path) | Info disclosure | `showErrorDetails="none"`, no `str(exc)` in UI |
| Plotly legend/hover HTML (`<a href>`, `<b>`) from brand/name/query text (verified live link in legend) | Spoofing / Tampering | `html.escape(s, quote=False)` on every untrusted string in trace `name`, `customdata`, tick labels; unit test with `<a href="https://evil.example">x</a>` asserts no raw `<a` in the figure JSON |

## Sources

### Primary (HIGH confidence)
- Local execution against `.venv` (Python 3.14.6, streamlit 1.66.0, plotly 7.1.0, pandas 3.0.6, SQLite 3.50.4): AppTest behaviour (env, widgets, cache sharing, exceptions, relative imports, `sys.path[0]`, plotly proto spec, dataframe column proto, default > max_selections, select_slider 0/1 options, segmented deselect), `st.cache_data` hashing/ttl/max_entries/clear, widget-key semantics, `streamlit.config` validation, `inspect.signature`/docstrings of `multiselect`, `segmented_control`, `select_slider`, `badge`, `dataframe`, `download_button`, `column_config.*`.
- Rendering in headless Chromium (gstack `browse`, local `file://` HTML and a loopback Streamlit server only; no external network): position chart (reversed axis, gaps, diamonds, bands), categorical heatmap + colorbar + hover, `None` → «0 ₽» in hover, dataframe `LinkColumn`/`%d ₽`/placeholder rendering, markdown vs literal rendering of untrusted labels.
- Phase 1 sources read this session: `src/wbmon/storage/{db,writer,reader,migrations}.py`, `config.py`, `clock.py`, `models.py`, `tests/{conftest,factories,test_guardrails}.py`, plans 01-01/01-03/01-04/01-05, `SKELETON.md`, `01-RESEARCH.md` (DDL, perf table).
- SQL/perf: generated 2 236 958-row DB (scratch, deleted) and a DB written by the real `init_db` + `Writer`.

- `.planning/phases/03-web-ui/03-UI-SPEC.md` (approved; its theme file validated against Streamlit 1.66 `config`; its chart/heatmap contract exercised in Chromium).

### Secondary (MEDIUM confidence)
- `.planning/research/{STACK,PITFALLS,FEATURES}.md` (project-level; consistent with the verification above; STACK's `ttl=300` is superseded by CONTEXT's `ttl=60`).

### Tertiary (LOW confidence)
- Phase 2 heartbeat semantics (read from `02-CONTEXT.md` only; code not yet written) → A5.
- WB product URL pattern (CONTEXT-locked, not fetched) → A6.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — nothing new to install; versions read from the venv.
- Architecture: HIGH — prototypes ran against the real Phase 1 schema/writer and the installed libraries.
- Pitfalls: HIGH — each pitfall was reproduced (exceptions, wrong renderings, timings).
- Cross-phase items (guardrail amendment, `.streamlit` cwd, heartbeat cadence): MEDIUM — depend on other phases' final code.

**Research date:** 2026-10-16
**Valid until:** 2026-11-15 (Streamlit/Plotly release monthly; re-run `pip list` and the AppTest smoke before planning if versions change)
