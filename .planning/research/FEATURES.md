# Feature Research

**Domain:** Marketplace (Wildberries) SERP position tracker: hourly collector, append-only history, Streamlit UI
**Researched:** 2026-10-16
**Confidence:** MEDIUM. Table stakes come straight from the TZ (HIGH). Competitor feature claims (MPStats, SellerFox, MarketGuru) rest on general domain knowledge plus thin marketing articles, so they are MEDIUM/LOW. The web search found no documentation of how those tools mark ad slots or draw history charts. Differentiator and anti-feature choices are engineering judgment for a test assignment judged on three things: works with real data, stable, visual quality.

## Framing

An evaluator opens the public link and has about two minutes. The UI has to answer these questions without being asked:

1. Is this live data? When was the last collection?
2. Has it been running reliably? What happened to failed runs?
3. Does the chart tell the truth? Position 1 at the top, gaps where a product is absent, hover shows datetime, ID and position.
4. Can I find and compare products easily?

Commercial trackers (SellerFox, MPStats "Позиции") are built around "my SKUs x my keywords" matrices, daily position tables, notifications and Excel export. That is the wrong model here. This project tracks **all products on a fixed 70-query SERP**, so the right shape is "pick a query, then pick products". Copy only the useful parts: history chart, characteristics table, export, and an ad/organic distinction.

Two facts shape the feature list:

- **Blocking is a first-class state.** PROJECT.md says WB's wbaas layer returns 403/498 to non-browser clients. The UI must look correct and informative when there are zero successful runs, few runs, or only partial runs. If all runs are blocked, the run-log view is the whole product, so it has to be excellent.
- **"Absent" and "unknown" are different.** If a query-run was blocked or only page 1 came back, "product not in top-200" is unknown, not true. Do not draw it as a claim of absence. See the coverage rule below.

## Feature Landscape

### Table Stakes (evaluator marks down if missing)

#### Collector and storage (the evaluator sees these only through the UI and the repo)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Hourly scheduled collection of 70 queries x top-200, in SERP order, 1-based position across pages | TZ §2 | MEDIUM | An in-process scheduler (APScheduler) or a loop container aligned to the top of the hour. Also run once at container start so the evaluator never sees an empty DB. |
| Append-only observation history (no overwrite, no delete, no upsert on `(query, nm_id)`) | TZ §3, Core Value | LOW | One row per (run, query, position). Unique key `(run_id, query_id, position)`. No `UPDATE` or `DELETE` paths anywhere in code. |
| Required fields per observation: query, `collected_at`, position, `nm_id`, price, rating, review count, delivery days, seller | TZ §2 | MEDIUM | Missing fields are NULL, never defaulted to 0. Delivery days are derived (see PROJECT.md hypothesis). Store `collected_at` in UTC and show it in МСК. |
| Run log: `runs` table (start, finish, status) plus `query_runs` table (per query: status ok/partial/blocked/error, HTTP status, items collected, pages fetched, duration, error text) | Stability criterion, honest reporting | MEDIUM | **The most important stability feature.** It is also the source of truth for the "absent vs unknown" rule. |
| Per-query error isolation, with retry and backoff for 429/5xx (blocks recorded, not circumvented) | "Stable" criterion | MEDIUM | One failing query never aborts the run. A blocked page 2 gives status `partial`, with page-1 items still stored and `items_collected` recorded. |
| Idempotent and crash-safe runs (a crashed run leaves `status=running`, which a watchdog or the next start turns into `interrupted`; the next hour proceeds normally) | "No manual intervention" | LOW-MEDIUM | Prevents a ghost "running" row from permanently marking the system as unhealthy in the UI. |
| Bundled query list as offline fallback, with the Sheet loaded each run | TZ §2, PROJECT.md | LOW | The log notes which source was used. |
| Docker restart policy plus a healthcheck/heartbeat | Unattended operation | LOW | The collector writes a heartbeat. The UI can read it as a second freshness signal. |

#### UI: freshness and stability (items 1 and 2 of the brief)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Freshness badge** in the header, e.g. «Последний успешный сбор: 12 мин назад (14:00 МСК)». Green under about 90 min, amber under about 3 h, red beyond. | Proves "real data, hourly" at a glance | LOW | Compute from the latest run with at least one `ok` query-run. Show absolute МСК time and relative age. Cache for about 30-60 s. |
| **Blocked-state banner** when the latest run was blocked or partial, e.g. «Последний сбор: WB вернул 403 по 70 из 70 запросов. Сбор повторится в 15:00». | The honest-failure story, central given wbaas | LOW | An `st.warning` driven by `query_runs`. Never hide it. |
| **Run history view** (tab «Журнал сборов»): table of the last N runs with start МСК, duration, status, queries ok/partial/blocked/error, products stored. Colour-code the status. | "Stable: repeated runs work, history not lost" | LOW-MEDIUM | `st.dataframe` with column_config. Status words in Russian: «Успешно / Частично / Заблокировано / Ошибка / Прерван». |
| **Data totals** in the sidebar: runs total, successful runs, first collection date, observations stored, distinct `nm_id` | Quick proof that history accumulates | LOW | Four `st.metric`s or one caption line. |
| Graceful empty states (no runs yet, no successful runs, query has no data, product has no observations in the selected range) | Never a traceback in front of the evaluator | LOW | Each case gets a Russian message that explains what happened. If everything is blocked, the run log still shows the truth. |

#### UI: query and product selection (item 5)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Query `selectbox` with all 70 queries (type-to-filter is built in) | TZ §4 | LOW | Show each option as the original wording. Caption under it: «N успешных сборов, последний: …». |
| Product `multiselect` over **all `nm_id` ever seen for that query** | TZ §4 | MEDIUM | Options sorted by latest position, then best position. Products not in the latest run go at the bottom, labelled «не в последней выдаче». Streamlit's multiselect filters by typed text, so the label must contain what the user searches: `"{nm_id} · {brand} · {name}"`. |
| **Sensible default selection**: current top-N of the latest successful run for that query (N=5 by default, small N control) | The first view must already be a good chart | LOW | Reset the default when the query changes (use a widget key containing the query). |
| Search by ID or name | Brief item 5 | LOW (given the label above) | **Requires storing `name` and `brand`**, which the search response contains. PROJECT.md's per-product field list omits them. See "Implications". |
| Time-range filter (24 ч / 7 дн / всё время) | A chart over a long history is unreadable without it | LOW | A radio or `segmented_control`. Default «Всё время» while history is under about 3 days, otherwise 7 дн. |
| Hard cap and warning for many lines (e.g. above 10 products: «Показаны первые 10 из N») | Readability criterion | LOW | A 200-line chart is unreadable. |

#### UI: position chart (item 3)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Plotly line+marker chart, **Y axis reversed (position 1 at top)** | TZ §4 | LOW | `yaxis.autorange="reversed"`, integer ticks, lower bound 1 (never 0). |
| **Gaps, not interpolation or fake positions** | TZ §4, Core Value | MEDIUM | Plotly draws a gap only at a `None` y value, so build the x-grid from **all complete-run timestamps for that query** and put NaN where the product is missing. Set `connectgaps=False`. Always use `mode="lines+markers"` so a product seen in one isolated run still appears. |
| Hover shows datetime (МСК), `nm_id`, position | TZ §4 | LOW | `hovertemplate` with `customdata`. Add name, price, and a «реклама» marker as free extras. Use `hovermode="closest"` (not "x unified", which gets cluttered at 5+ lines). |
| Legend labelled with `nm_id` and short name; click to toggle a line | Readability | LOW | Plotly default behaviour. |
| Time axis in МСК with sensible ticks, plus the data-range caption | "Visual quality" | LOW | Convert UTC to `Europe/Moscow` once, before plotting. |
| **Distinguish "absent" from "run failed"**: light-grey vertical bands (or a small marker row under the x axis) for runs where the query was blocked/partial/error, with a legend note | Honest gaps, the stability story shown in-chart | MEDIUM | Without this, a blocked hour looks like every product vanishing. This is the single most "senior" visual detail. If it is too costly, fall back to the caption «Пропуски = товара нет в топ-N или сбор не удался (см. журнал)». |
| Chart caption in Russian explaining the rule: «Позиция 1 — сверху. Пропуск на линии = товар не найден в выдаче в этот сбор; значения не достраиваются.» | Makes the honesty rule visible to the evaluator | LOW | One `st.caption`. |
| Fixed theme and sizes: `layout="wide"`, chart height about 480-560 px, consistent colours, no Plotly toolbar clutter beyond zoom/reset | "Neat and understandable UI" | LOW | A `.streamlit/config.toml` with a light theme. |

#### UI: characteristics table (item 4)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| One row per selected product with latest values: Позиция сейчас, Цена, Рейтинг, Отзывы, Доставка (дни), Продавец, plus ID and name | TZ §4 | LOW | Source is the product's latest **observation** (not necessarily the latest run). |
| **Last-seen time** column («Последний раз в выдаче»), so a stale row is not mistaken for current | Honesty | LOW | |
| Correct formatting via `st.column_config`: price «1 234 ₽», rating with one decimal, int reviews, NULL shown as «—» | Visual quality | LOW | Never show NaN or None. |
| Delivery shown as days with a legend, «0 = сегодня, 1 = завтра» | Matches the TZ definition | LOW | Optionally add a text column: Сегодня / Завтра / через N дн. |

#### Export (item 6)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| `st.download_button` for CSV of the **observations for the current selection** (query, selected products, range): datetime, query, position, nm_id, price, rating, reviews, delivery days, seller, is_promoted | Brief item 6 | LOW | **Excel-for-Russia detail:** write `utf-8-sig` (BOM) so Cyrillic is not garbled. Consider `sep=";"` and decimal comma, because Russian-locale Excel misreads `4.8` as a date. Document the choice in the file name or a caption. (MEDIUM confidence, widely known pitfall.) |
| CSV of the characteristics table | Brief item 6 | LOW | Same helper. |

### Differentiators (cheap, visible quality wins)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Per-run x per-query status heatmap** (70 queries on Y, last 24-72 runs on X, colour = ok/partial/blocked/error), in the «Журнал сборов» tab | Shows stability across all 70 queries in one picture. Wow factor for a test task. | MEDIUM | One Plotly `Heatmap` over `query_runs`, a categorical colour scale. Worth it even if everything is green. |
| **Coverage rule surfaced in the UI**: partial runs (fewer than `top_n` items) are marked and do not count as proof of absence | Data-integrity credibility | LOW (once `items_collected` is stored) | Chart bands and a «Охват» column in the run log. |
| **Promoted/ad slot marker**: different marker shape (diamond) or hollow marker on chart points where `is_promoted` is true; «Реклама» column in tables; legend note | The TZ says promoted slots are included and flagged. Competitors separate ad from organic positions, so it reads as domain-aware. | LOW-MEDIUM | Needs `is_promoted` stored per observation (from the `log` object present on promoted slots). Do **not** compute a separate organic rank (scope). |
| **WB product links**: `LinkColumn` to `https://www.wildberries.ru/catalog/{nm_id}/detail.aspx` in tables | Lets the evaluator verify a product in one click. Very cheap. | LOW | `st.column_config.LinkColumn(display_text=r".*/catalog/(\d+)/.*")` shows only the ID. |
| **Best / current / worst position, and presence share** columns in the characteristics table: «Лучшая», «Текущая», «Худшая», «Замеров», «В выдаче, %» | Competitor-style summary; no extra data needed | LOW | A pandas groupby over the selected range. Presence = seen / complete runs. |
| **SERP snapshot tab** («Выдача»): the full top-200 table of one query at a chosen collection time (slider over runs) with position, ad flag, price, rating, seller and WB link | Shows the raw collected data, backs up "real data", and doubles as a debugging view | MEDIUM | `select_slider` over complete runs. Default is the latest. |
| **Position change vs previous run** («Δ за час», ▲/▼) in the table and snapshot | Intuitive at a glance | LOW | Skip it if the product was absent in the previous run (show «новый»). |
| **Collector health in the header**: heartbeat age («Сборщик жив: 2 мин назад») | A second independent freshness signal that works even when WB blocks everything | LOW | Heartbeat row or file mtime. |
| Y-axis scale toggle: «авто» (zoom to data) vs «1–200» (fixed). Default is auto with a few positions of padding. | Auto avoids a flat line on a stable product; fixed shows real scale | LOW | |
| Price (and rating) history for selected products, as an optional second chart or tab | Data is already stored per observation; adds analytical depth | LOW-MEDIUM | Same gap rule. **Only after all table stakes are done.** |
| Streamlit caching (`st.cache_data(ttl=60)`) plus a read-only SQLite connection with indexes `(query_id, nm_id, collected_at)` and `(run_id)` | A snappy UI after weeks of history (about 336k rows/day) | LOW-MEDIUM | This is a stability feature as much as a performance one, and it prevents UI/collector lock contention (WAL mode). |
| Short «О сервисе» expander: what is measured (Moscow, `dest`, top-200, hourly), the gap rule, delivery-day definition, data source | Explains semantics to an evaluator without a README | LOW | Also the place to put the position definition: «позиция включает рекламные слоты». |
| README screenshots and the "how stability is ensured" paragraph tied to the run log | The write-up is a deliverable | LOW | Outside the UI but part of the evaluation. |

### Anti-Features (deliberately NOT built)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Any anti-bot workaround (TLS impersonation, headless/stealth browser, token/cookie harvesting or replay, CAPTCHA solving, proxy rotation) | "Make it work when WB blocks" | **Excluded by project constraint** (hard boundary) | Record `blocked` honestly, back off, retry next hour; show the state in the UI. |
| Synthetic/demo/backfilled observations, "interpolated" positions, filling gaps with 201 or a flat line | A chart that looks fuller on day one | Violates TZ §5 and Core Value, and is detectable by a careful evaluator | Real, partial history plus honest empty states. A `tests/` fixture DB is allowed only in tests. |
| "Запустить сбор сейчас" button in the public UI | Convenient for demonstration | A public endpoint that triggers outbound WB requests: abuse risk, rate violations, concurrent runs, DB locking | A read-only UI. Collection is scheduled only. Use CLI `python -m ... collect --once` for the operator. |
| Editing queries / settings in the UI, auth, user accounts | "Admin panel" feel | Out of scope per PROJECT.md. Adds a security surface. | Config via env/CLI. The query list comes from the Sheet plus the bundled fallback. |
| Alerts (Telegram/email) on position drops | Standard in commercial trackers | Needs credentials, a delivery pipeline and thresholds. Not asked for by the TZ. | Mention in the write-up as a "next step". |
| Tracking "my products" lists / per-seller dashboards / competitor analytics / sales estimates | The commercial model of SellerFox/MPStats | A different product, and an unbounded scope | Selection is per query among observed products. |
| Per-product detail requests (stocks, description, all sizes) | Richer characteristics | Out of scope per PROJECT.md. Multiplies request volume and raises block risk. | Only fields from the search response. |
| Ad-auction analytics (CPM/bids, "bidder", ad budget advice) | `log.cpm` is in the response | Different domain, and easy to get wrong | Store only the `is_promoted` flag (optionally the raw ad-position number). No bid analysis. |
| Multi-region/city comparison, region switcher | Real sellers care | TZ requires one fixed city | State the Moscow `dest` clearly in the UI. |
| Cross-query heatmaps of positions, rank-change leaderboards for all 70 queries, trend forecasting/ML | "Impressive" | Large visual and logic surface; low marginal credit for the criteria | Per-query chart plus the snapshot tab. |
| Postgres + Grafana + REST API + Celery/Redis | "Production-grade" signals | Over-engineering for 336k rows/day on one node. More that can break, less time for polish. | SQLite (WAL) plus APScheduler or a loop container. Mention Postgres as optional in the write-up. |
| Auto-refreshing UI every few seconds / websocket live updates | "Live" feel | Data changes hourly. Needless load and flicker. | A manual «Обновить» button (clears cache) and `ttl=60`. |
| Heavy custom CSS / custom components / dark-mode theming | "Visual quality" | Time sink. Streamlit upgrades can break custom CSS. | The built-in theme, consistent spacing, column_config, and a careful Plotly layout. |
| Deleting/pruning old data via UI or cron "to save space" | Disk concerns | Breaks "history never lost". | Not needed at this scale. Mention retention as a future option. |

## Feature Dependencies

```
Run log (runs + query_runs tables, statuses, items_collected)
    ├──requires──> Collector per-query error isolation + status classification (ok/partial/blocked/error)
    ├──enables───> Freshness badge, Blocked-state banner, Run history table, Status heatmap
    └──enables───> "Absent vs unknown" rule ──enables──> Chart gap bands, honest presence-share %

Append-only observations (+ name, brand, is_promoted stored)
    ├──requires──> Collector parse of products (data.products | top-level products)
    ├──enables───> Product multiselect with search by name/ID
    ├──enables───> Ad-slot markers (needs is_promoted)
    ├──enables───> Characteristics table, WB links, best/current/worst, Δ vs previous
    └──enables───> CSV export, SERP snapshot tab

Query selector ──requires──> query list in DB (queries table with stable query_id)
Product multiselect ──requires──> query selector + "all nm_id ever seen for this query"
Default selection (current top-N) ──requires──> latest complete run for the query
Position chart ──requires──> product selection + complete-run timestamp grid (for NaN gaps)
Chart gap bands for failed runs ──enhances──> Position chart (needs the Run log)
Ad-slot markers ──enhances──> Position chart + tables
Price history chart ──enhances──> Characteristics table (low priority)
Read-only DB connection + indexes + st.cache_data ──supports──> everything in the UI (needed before history grows)
Collector heartbeat ──enhances──> Freshness badge

Public "run now" button ──conflicts──> read-only UI / rate-limit politeness / DB stability
Synthetic or backfilled data ──conflicts──> Core Value / TZ §5
```

### Dependency Notes

- **Product search by name requires storing `name` and `brand`.** PROJECT.md's field list names only `nm_id`, price, rating, reviews, delivery days and seller. Add `name`, `brand`, `is_promoted` and `items_collected` (per query-run) to the schema now. Adding columns after weeks of collection means older rows lack them, which costs a phase of real history.
- **The gap rule requires the denominator.** To plot NaN only for "complete" runs and flag the rest, `query_runs` must say whether each (run, query) was complete. If it is not recorded at collection time, it cannot be reconstructed.
- **Freshness, the banner and the heatmap all read the same `query_runs` table.** Build the run log first and all three come almost free.
- **Chart quality depends on one pandas pivot step** (observations -> run-timestamp grid x product, NaN for absent). Put it in a pure function with unit tests: reversed axis, gap NaNs, isolated points, and failed-run bands all rely on it.

## MVP Definition

### Launch With (v1), the full table-stakes set

- [ ] Collector: hourly run, 70 x top-200, append-only storage, run log with per-query status and `items_collected`, per-query isolation, run-on-start
- [ ] Schema includes `name`, `brand`, `is_promoted`, NULL for unknown fields
- [ ] UI header: freshness badge, blocked/partial banner, sidebar totals, graceful empty states
- [ ] Query selector, product multiselect (all ever seen, searchable, default current top-5), time-range filter
- [ ] Plotly chart: reversed Y, gaps (no interpolation), hover with МСК datetime + nm_id + position, line+markers, line cap
- [ ] Characteristics table with last-seen, correct formatting, WB links
- [ ] CSV export (observations + table), Excel-safe encoding
- [ ] «Журнал сборов» tab: run history table
- [ ] Russian UI copy throughout, with a short caption explaining the gap rule

### Add After Core Works (v1.x), the cheap wins, in this order

- [ ] Failed-run bands on the chart (the "absent vs unknown" visual)
- [ ] Ad-slot markers on chart and table
- [ ] Best/current/worst and presence-share columns, Δ vs previous run
- [ ] Per-run x per-query status heatmap
- [ ] SERP snapshot tab
- [ ] Collector heartbeat in the header
- [ ] Y-axis scale toggle, «О сервисе» expander

### Future Consideration (v2+), mention in the write-up only

- [ ] Price/rating history charts
- [ ] Alerts, multi-region, Postgres, REST API, retention policy

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Run log + statuses + `items_collected` | HIGH | MEDIUM | P1 |
| Append-only schema with name/brand/is_promoted | HIGH | LOW | P1 |
| Freshness badge + blocked banner | HIGH | LOW | P1 |
| Position chart (reversed, gaps, hover) | HIGH | MEDIUM | P1 |
| Query selector + product multiselect with defaults | HIGH | MEDIUM | P1 |
| Characteristics table + WB links | HIGH | LOW | P1 |
| CSV export (Excel-safe) | MEDIUM | LOW | P1 |
| Run history table | HIGH | LOW | P1 |
| Empty states / Russian copy | HIGH | LOW | P1 |
| Failed-run bands on chart | HIGH | MEDIUM | P2 |
| Ad-slot markers | MEDIUM | LOW | P2 |
| Best/worst/presence columns, Δ vs previous | MEDIUM | LOW | P2 |
| Status heatmap | MEDIUM | MEDIUM | P2 |
| SERP snapshot tab | MEDIUM | MEDIUM | P2 |
| Caching + read-only connection + indexes | MEDIUM | LOW | P2 (do early in practice) |
| Collector heartbeat | MEDIUM | LOW | P2 |
| Axis scale toggle, «О сервисе» expander | LOW | LOW | P3 |
| Price/rating history chart | LOW | MEDIUM | P3 |
| Alerts, run-now button, auth, multi-region | n/a | HIGH | Do not build |

**Priority key:** P1 = must have for launch; P2 = add once P1 works, in listed order; P3 = nice to have.

## Competitor Feature Analysis

Confidence is MEDIUM-LOW. These tools are mostly paywalled and the search turned up only marketing overviews, so details come from general domain knowledge, not documentation.

| Feature | SellerFox / MPStats-style trackers | Our Approach |
|---------|------------------------------------|--------------|
| Unit tracked | User's own SKUs x chosen keywords | All products on 70 fixed queries, chosen after the fact among everything observed |
| History | Position per day or per few hours, matrix or chart | Hourly, full append-only history, honest gaps |
| Ad vs organic | Typically distinguished (MPStats' bidder works with ad position ranges) | Single SERP position with an `is_promoted` flag and marker. No separate organic rank. |
| Product info | Often a separate product card page | A compact characteristics table from the search response |
| Export | Excel | CSV (Excel-safe) |
| Alerts | Position-change notifications | Not built (anti-feature for this scope) |
| Check frequency | Marketing claims of roughly every 2-4 h or daily | Hourly as the TZ requires, shown by the run log |
| Data honesty | Not visible to the user | Visible run log, blocked states, coverage rule. This is the angle where a test assignment can beat the commercial tools. |

## Suggested Russian UI copy (starting set)

- Title: «Мониторинг позиций в поиске Wildberries»
- Freshness: «Последний успешный сбор: {N} мин назад ({HH:MM} МСК)»
- Blocked: «WB заблокировал последний сбор (HTTP {code}) по {k} из {n} запросов. Следующая попытка в {HH:00} МСК.»
- Gap caption: «Позиция 1 — сверху. Разрыв линии означает, что товара не было в выдаче в этот сбор; значения не достраиваются.»
- Tabs: «Позиции», «Выдача», «Журнал сборов»
- Selectors: «Поисковый запрос», «Товары (ID или название)», «Период»
- Empty: «По этому запросу пока нет успешных сборов. Статус попыток — на вкладке «Журнал сборов».»
- Table columns: «ID», «Название», «Бренд», «Продавец», «Позиция», «Цена, ₽», «Рейтинг», «Отзывы», «Доставка, дн.», «Реклама», «Последний раз в выдаче», «Ссылка»
- Export: «Скачать CSV»

## Implications for requirements and roadmap

1. **Schema decision is front-loaded.** Fix `name`, `brand`, `is_promoted`, `items_collected` and per-query-run status in the first phase. They cannot be backfilled, and the clock of real history starts at the first deploy.
2. **Order of work:** collector plus run log first (history accrues while the UI is built), then UI shell with freshness and run history, then selection, chart and table, then export, then P2 polish. The chart pivot function needs its own unit tests.
3. **Deploy the collector early** so there is real data for the evaluator. An early deploy also shows whether plain httpx works from the target host, which PROJECT.md lists as unknown. If it is blocked, the run-log UI is the deliverable that proves honest stability.
4. **Needs verification on real data (not research):** the delivery-days formula, the `is_promoted` detection via `log`, and whether page 2 is reachable. These feed table columns and the coverage rule.

## Sources

- `/Users/fvst/Desktop/Тестовое задание./wb-serp-monitor/.planning/PROJECT.md`: requirements, constraints, WB response shape (HIGH, project-authoritative)
- [Uniseller: how to track WB positions](https://uniseller.io/blog/kak-otslezhivat-pozicii-tovarov-wildberries): general tracker expectations, check frequency (LOW, marketing blog)
- [Uniseller: MPSTATS June 2025 update](https://uniseller.io/blog/mpstats-obnovleniya-iyun-2025): ad-position ranges in the MPStats bidder (LOW-MEDIUM)
- [toolfox: SellerFox alternatives](https://toolfox.ru/services/s/wb-stream/alternatives): SellerFox as a positions/SEO-focused tool (LOW)
- Domain knowledge of Streamlit/Plotly behaviour (`autorange="reversed"`, `connectgaps=False`, `LinkColumn`, `download_button`, `column_config`) (MEDIUM; not re-verified against current docs this session)
- Competitor documentation of ad/organic marking and history charts was not found; treat the comparison table as indicative only.

---
*Feature research for: Wildberries SERP position tracker (test assignment)*
*Researched: 2026-10-16*
