# Phase 3: Web UI - Context

**Gathered:** 2026-10-16
**Status:** Ready for planning
**Mode:** Smart discuss (autonomous). The user asked for a fully autonomous solution, so the recommended
answer for every grey area was auto-accepted by the orchestrator; alternatives are recorded for traceability.

<domain>
## Phase Boundary

A Russian-language, read-only Streamlit app (`src/wbmon/ui/`) on top of the Phase 1 SQLite schema: data
freshness header and honest blocked/partial banners, query + product selection, an interactive Plotly
position chart (position 1 on top, honest gaps, failed-run bands, ad markers), characteristics and
history tables with CSV export, a run log («Журнал сборов») with a query × run status heatmap, a SERP
snapshot tab («Выдача») and a short methodology tab («О сервисе»). Out of scope: collection code
(Phase 1), scheduler/Docker (Phase 2, runs in parallel — do not touch its files), deployment and README
(Phase 4).

</domain>

<decisions>
## Implementation Decisions

### Area 1 — Layout & navigation
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Page structure | Single page, `st.set_page_config(page_title="WB SERP Monitor — позиции в поиске Wildberries", page_icon="📈", layout="wide")`; header (title, one-line caption «70 запросов · топ-200 · Москва · сбор каждый час», freshness badge, collector heartbeat, status banner); sidebar for global controls; 4 tabs: «📈 Позиции», «🗂 Выдача», «🩺 Журнал сборов», «ℹ️ О сервисе» | multipage `st.navigation` (rejected: more clicks for an evaluator) |
| 2 | Sidebar controls | Query `st.selectbox` over all queries in sheet order, label `"{sheet_order:02d}. {text}"` (original wording); period `st.segmented_control` («24 ч» / «7 дней» / «Всё время», default «Всё время»); top-N default size `st.number_input` 1–20 default 5; totals block (runs total, successful runs, first collection МСК, observations, distinct products) | date_input range (rejected: fiddly) |
| 3 | Visual style | `.streamlit/config.toml` light theme with a restrained purple accent `primaryColor="#7B2CBF"`, neutral greys, `font="sans serif"`; no WB logos/brand assets (no impersonation); Russian copy throughout; numbers formatted with thin spaces/₽ | dark theme |
| 4 | Empty / error states | No DB file → info «База ещё не создана — запустите `wbmon init-db`»; zero runs → info «Сборов ещё не было. Первый сбор — в ближайшие :00 МСК»; all runs blocked → warning «Wildberries отклоняет запросы сборщика (антибот, HTTP 403). Запуски продолжаются каждый час и фиксируются в «Журнале сборов»; данные не подставляются»; query with no observations → info explaining why (blocked/never collected) | — |

### Area 2 — Position chart («📈 Позиции»)
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Product picker | `st.multiselect` (fuzzy filter) over every `nm_id` ever seen for the query, label `"{nm_id} · {brand} · {name[:40]}"`, options ordered by latest position then best position; default = top-N (sidebar N) of the latest successful run for the query; widget key includes the query id so selection resets on query change; `max_selections=15` | text input of IDs |
| 2 | Chart encoding | Plotly `go.Scatter` per product, `mode="lines+markers"`, x = run time converted UTC→Europe/Moscow and made tz-naive, y = position; `yaxis.autorange="reversed"` with `rangemode="tozero"`-like padding so position 1 is at the top; axis titles «Позиция в выдаче» / «Время сбора, МСК»; `hovermode="closest"`; hovertemplate «%{x|%d.%m.%Y %H:%M} МСК<br>ID %{customdata[0]}<br>Позиция %{y}<br>Цена %{customdata[1]}<extra>%{fullData.name}</extra>» (price shown as «—» when NULL); legend horizontal below; `st.plotly_chart(fig, width="stretch")`, toolbar `displaylogo=False` | Altair (rejected) |
| 3 | Honest gaps & failures | Build the grid from ALL runs of the selected query (`runs` LEFT JOIN `run_queries`); point state per (run, product): FOUND (observation exists), ABSENT (query complete for that run and product not in it → y=None gap), UNKNOWN (query blocked/error/rate_limited/partial-before-reaching-depth or run abandoned → y=None gap + light-grey `add_vrect` band per such run with legend note «сбор не удался»); `connectgaps=False`; a caption under the chart explains: gap = товар не найден в топ-N; серая полоса = сбор по запросу не удался | interpolation (forbidden) |
| 4 | Ad markers | Promoted observations drawn with `marker.symbol="diamond"` (others `circle`) via per-point symbol arrays; hover adds «реклама»; legend/caption note «◆ — рекламное размещение» | separate organic series (deferred v2) |

### Area 3 — Tables & export
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Characteristics table | One row per selected `nm_id` (latest observation in the period): ID (LinkColumn to `https://www.wildberries.ru/catalog/{nm_id}/detail.aspx`, display = ID), Название, Бренд, Продавец, Позиция (текущая), Δ к прошлому сбору (▲/▼, «новый»), Лучшая, Худшая, В выдаче (% complete runs where found), Цена, ₽, Рейтинг, Отзывов, Доставка, дн., Реклама (✓), Последний раз в выдаче (МСК); `st.dataframe` with `column_config`, hide index | — |
| 2 | History table | Expander «История наблюдений»: all observations of the selected products in the period (time МСК, ID, position, price, rating, reviews, delivery days, seller, ad) sorted by time desc | — |
| 3 | CSV | `st.download_button` «Скачать CSV»: `;` separator, decimal comma, `utf-8-sig` encoding (opens correctly in Russian Excel), filename `wb_serp_{query_slug}_{YYYYMMDD_HHMM}.csv` | `,` separator |
| 4 | «🗂 Выдача» tab | `st.select_slider` over the query's runs that have observations (labels МСК time, default latest) → full top-N table: Позиция, Реклама, ID (link), Название, Бренд, Цена, Рейтинг, Отзывов, Доставка, Продавец; metrics above: товаров собрано, из них рекламных, медианная цена, статус сбора | — |

### Area 4 — Run log, data access, performance
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | «🩺 Журнал сборов» | Table of last 200 runs: Начало (МСК), Длительность, Тип (по расписанию/вручную), Статус (emoji + Russian label: ✅ Успешно / 🟡 Частично / ⛔ Заблокировано / ❌ Ошибка / ⏸ Прерван / ⏳ Идёт), запросов ok/partial/blocked/error, товаров сохранено; above it a Plotly heatmap: Y = queries (sheet order, short labels), X = last 48 runs (МСК), categorical colours ok=green, partial=amber, blocked=red, rate_limited=orange, error=grey, missing=white, hover with query text + status + items; legend explanation of statuses in Russian | — |
| 2 | Freshness & heartbeat | Header badge: «Последний успешный сбор: DD.MM HH:MM МСК (N мин назад)» green < 90 min, amber < 3 h, red otherwise, or «успешных сборов пока нет»; second line «Сборщик: на связи N с назад / нет сигнала» from `<db_dir>/scheduler_heartbeat.json` if present (Phase 2 writes it; absent file → «нет данных о сборщике», not an error); banner for latest run status | — |
| 3 | Data access layer | `src/wbmon/ui/data.py`: short-lived read-only connections via Phase 1 `connect_ro` (`mode=ro` URI from `Path.as_uri()`, `PRAGMA query_only=ON`), never a cached connection object; loaders wrapped in `st.cache_data(ttl=60)` and keyed on `(db_path, latest_run_id)` so a new run invalidates caches; SQL only through parameterized queries; pure pandas transforms in `src/wbmon/ui/series.py` (grid building, point states, stats, Δ, presence) and Plotly figure builders in `src/wbmon/ui/charts.py`; `src/wbmon/ui/app.py` is the Streamlit entrypoint (`streamlit run src/wbmon/ui/app.py`) and uses `wbmon.config.Settings.from_env()` for `WBMON_DB_PATH` | SQLAlchemy / st.connection (rejected) |
| 4 | Tests & performance | `tests/ui/test_series.py` (pure grid/state/stats vectors incl. absent vs unknown, isolated points, ads), `tests/ui/test_charts.py` (figure properties: reversed axis, connectgaps False, vrects, symbols), `tests/ui/test_app.py` with `streamlit.testing.v1.AppTest` against tmp DBs built through the Phase 1 writer: no DB, zero runs, all-blocked runs, normal multi-run history; UI-12 check: a generated week-sized tmp DB (≈168 runs × 70 × 200 via the fast generator from Phase 1 perf test) keeps each loader < 1 s; synthetic rows only ever in tmp DBs | — |

### «ℹ️ О сервисе» tab content (Russian)
Short methodology: what is collected and when (70 queries from the Google Sheet, top-200, hourly at :00 МСК, Moscow region `dest`), definitions (позиция = место в выдаче с учётом рекламных мест; цена = цена для покупателя без авторизации, минимальная по размерам; рейтинг/отзывы = по карточке товара; срок доставки = календарные дни от момента сбора по данным WB time1+time2, приблизительно), honesty rules (нет подстановки значений, пропуск на графике = товар не найден, серая полоса = сбор не удался), link to the repository (placeholder `https://github.com/U-Steph/wb-serp-monitor`), data limitations (WB anti-bot may block collection; blocked runs are shown as such).

### Claude's Discretion
- Exact helper names inside `series.py` / `charts.py` / `data.py`, CSS-free styling details, emoji choice, exact Russian microcopy wording (keep it concise and neutral).

</decisions>

<code_context>
## Existing Code Insights

Phase 1 (executing now) defines the schema (tables `queries`, `runs`, `run_queries`, `observations`, see `.planning/phases/01-collection-core/01-01-PLAN.md` migration 1 and `01-03-PLAN.md` reader functions) and `wbmon.storage.db.connect_ro`, `wbmon.storage.reader`, `wbmon.config.Settings`, `wbmon.clock`. Phase 3 must consume those (read the Phase 1 plans/summaries and code), may ADD UI-specific read queries in `src/wbmon/ui/data.py`, and must not change the schema or Phase 1 modules except for additive, backwards-compatible reader helpers if unavoidable (prefer keeping UI SQL in `ui/data.py`).

</code_context>

<specifics>
## Specific Ideas

- Streamlit 1.66 / Plotly 7.1 / pandas 3.0 are installed in `.venv`; `use_container_width` is removed — use `width="stretch"`.
- Phase 2 runs in parallel and owns Dockerfile/compose/scheduler/Makefile/.env.example; Phase 3 owns `src/wbmon/ui/**`, `.streamlit/**`, `tests/ui/**` only (plus the `web` optional-dependency group already declared in Phase 1's pyproject).
- The real database may contain only blocked runs during review (WB anti-bot); the UI must look complete and informative in that state.

</specifics>

<deferred>
## Deferred Ideas

- Organic-rank chart mode, price-history chart, alerts, multi-region comparison — v2.

</deferred>
