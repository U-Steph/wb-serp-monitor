# Pitfalls Research

**Domain:** Wildberries (WB) SERP position tracking: hourly collector of the public search JSON, SQLite history, Streamlit + Plotly UI
**Researched:** 2026-10-16
**Confidence:** MEDIUM overall. Response-shape, pagination, price, ad-slot and rating facts rest on real recorded WB payloads and on parsers dated 2026. Delivery-day semantics and the `dest` check are NOT documented anywhere public. No live WB request was made (it returns 403 from this machine), so everything below comes from public code, fixtures and write-ups.

**Phase labels used below** (the roadmap may remap them):
- **P1** Access and contract spike: fixtures, deployment-host probe, DB schema contract
- **P2** Collector core: request, parse, normalize, classify
- **P3** Storage and run orchestration: schema, scheduler, locking, run log
- **P4** Streamlit UI
- **P5** Deploy, ops and write-up

**Scope note:** anti-bot circumvention is deliberately absent from this file (hard project constraint). Where sources describe it, it was left out. The only anti-bot guidance is: detect, classify as `blocked`, back off, report honestly, never fabricate.

---

## Critical Pitfalls

### Pitfall 1: Treating a block (403 / 498 / HTML-at-200 / 429) as "no results" or as an empty list

**What goes wrong:** The collector records "0 products" or "product not found", and the chart shows the product dropping out of the top-200. Real history is silently corrupted and the Core Value ("never fabricated") is violated.

**Why it happens:** Parsers written as `data.get("products", [])` swallow every failure. The 2026 ru-marketplace-mcp issue #101 is exactly this: a 403 became `status: ok, 0 results`, then a stale fallback source returned unpriced items that looked like an answer. Other WB failures also come back with a 200 status:
- an HTML wall page at HTTP 200;
- a differently shaped JSON body with `data.products` instead of top-level `products` (reported on v4);
- `products: null` for a bad `dest`.

**How to avoid:** Classify every HTTP exchange BEFORE parsing, as a closed enum:

| Observation | Class |
|---|---|
| 200 + JSON object + products list (any known shape) | `ok` |
| 403 (esp. `server: wbaas`), 498 | `blocked` |
| 429 | `rate_limited` |
| 200 but body starts with `<html` (check `body[:200].lower()`) | `blocked` (edge wall) |
| 200 + JSON but no products list in any known location | `schema_drift` |
| 200 + products empty/null on page 1 of a query that normally returns thousands | `empty_suspect` (NOT "no results") |
| timeout / connection error / 5xx / invalid JSON | `error` |

Check `content-type` and the first bytes before calling `.json()`. Persist the class, HTTP status, the `server` header and the first ~200 bytes of body (truncated) on `query_runs`. Never fall back to another data source silently, and never reuse an older result.

Add a circuit breaker: after N consecutive `blocked` queries in one run (suggest 3), mark the remaining queries `skipped_blocked` and end the run. Do not hammer 70 queries × 2 pages. The next hourly run retries from scratch.

**Warning signs:**
- A run with 70 "ok" queries but 0 rows.
- `total` missing or 0 on a bedding query.
- Many identical empty runs.
- The UI shows a product vanishing from ALL queries at the same hour.

**Phase to address:** P2 (classifier and tests with recorded 403 / 498 / HTML / empty fixtures). P3 (persist classes). P4 (display blocked runs honestly).

---

### Pitfall 2: Not knowing whether WB serves this host at all, and discovering it on the last day

**What goes wrong:** The whole pipeline is built, then every request from the deployment host returns 403, so there is no "real data". The dev machine already gets 403 (German VPN IP). A Russian residential IP is also reported blocked for non-browser clients in some 2026 reports.

**Why it happens:** Reachability depends on the egress IP and on WB's edge decisions, which cannot be known in advance.

**How to avoid:** First task of the project: a 5-minute read-only probe from the actual deployment host with the real httpx client and the real params. Record status, `server` header and body prefix. If it is blocked there, STOP and report to the user as a decision point: change host, use an official/licensed data source, or ship with honest "blocked" runs. Do NOT escalate to evasion.

Keep the endpoint host configurable (`search.wb.ru` vs `u-search.wb.ru` vs the site's `/__internal/u-search/` path). Gating differs per host in public reports, and the chosen host must be stored in the run row. Switching to an alternative PUBLIC host that is documented in config is fine; rotating identities or clients is not.

**Warning signs:** The first probe returns anything other than JSON with products. `server: wbaas`, `status-no-id`, HTTP 498.

**Phase to address:** P1 (deployment-host probe is the exit criterion). P5 (final verification on the host).

---

### Pitfall 3: Response-shape drift across v13...v18 (and across hosts)

**What goes wrong:** The parser handles one shape and silently yields 0 products or NULL prices on another.

**Observed shapes** (real payloads and parsers):

| Variant | Where | Products | Total | Metadata |
|---|---|---|---|---|
| Older / v13-ish | search.wb.ru | `data.products` | absent, or `data.total` | `metadata{name,catalog_type,catalog_value}` |
| v18 via `/__internal/u-search/...` and v9 | site and search host | top-level `products` | top-level `total` | top-level `metadata` |
| Mid versions | search.wb.ru | `data.products` | `data.total` | `metadata` has `normquery`, `title`, `rs` (=100), `rmi`, `qv` (opaque token), `search_result` |
| Legacy/degraded | search.wb.ru | `data.products` + `params{version,curr,spp,payloadVersion}` | none | none |

Differences in the legacy/degraded shape:
- `sizes[]` carry `priceU/salePriceU/logisticsCost` instead of `price{}`.
- Items have `log`, `logs` (opaque base64-like string) and `__sort`.

Other drift points:
- `nmReviewRating` and `nmFeedbacks` are absent in older shapes (null, not 0).
- `sizes[].time1/time2` exist in some versions and not in others.
- `price.total` is absent in some payloads.
- A `sizes: [null]` or number-as-string drift was observed by other maintainers.

**Why it happens:** WB runs several backend versions and A/B tests; the website, `search.wb.ru` and `u-search.wb.ru` don't serve the same shape.

**How to avoid:**
- Write one `extract_products(obj) -> list | None` that tries `obj["products"]` then `obj["data"]["products"]`, and returns None (→ `schema_drift`) if neither is a list.
- Extract `total` from `total`, then `data.total`, then `metadata.total`. Treat it as informational only.
- Keep a per-field tolerant mapper: missing is NULL, never 0, never guessed.
- Record `shape_variant` and the endpoint version per query_run. If prices come only from `priceU/salePriceU`, store NULL by default and count it as drift. Mixing semantic fields is an invention risk.
- Commit sanitized recorded fixtures for each variant to `tests/fixtures/` (test-only, never the production DB).
- Keep one tiny raw sample (first product JSON) per run, in a `run_samples` table or log, to debug drift after the fact.

**Warning signs:** `price_rub` NULL rate jumps; `shape_variant` ratio changes; `total` or `rs` disappears.

**Phase to address:** P1 (fixture contract), P2 (tolerant extractor + tests).

---

### Pitfall 4: Past-the-end pagination returns page 1 again (duplicate fill), and the page size is not guaranteed

**What goes wrong:** The collector requests page 2, receives a repeat of page 1 (or an overlapping page), and stores positions 101-200 as copies of 1-100. Positions are wrong and the hover shows impossible duplicates.

**Evidence:**
- dev.to write-up: past the last real page WB "starts serving page 1 again" with HTTP 200.
- ru-marketplace-mcp (verified live 2026-07-28): page 20 was byte-identical to page 1 (100 products, 200, no marker); `total` stayed far larger than the depth actually served, so `total` cannot find the last page.
- Page size is 100 (`metadata.rs=100`; `ceil(total/100)` is used by several parsers).
- The current website request has no `limit` param (older clients passed `limit`; its effect is unverified). Do not pass it.

**Why it happens:** Naive loops (`while page <= pages_from_total`) trust `total`, and naive dedupe hides the wrap.

**How to avoid:**
- Do not hardcode "2 pages × 100". Loop `page = 1, 2, ...` until `len(rows) >= top_n` OR a short or empty page OR a wrap is detected, with a hard safety cap of `ceil(top_n/len(page1)) + 1`.
- Wrap detection: fingerprint each page by its first 10 ids in order. If page k's fingerprint equals page 1's, treat as end-of-results.
- Check that page k's ids are not a subset of already-collected ids (>50% overlap = suspicious). Mark the query-run `short_result`, not `ok-complete`.
- Slice to `top_n` AFTER concatenation. Position = 1-based index in the concatenated list.
- Use `UNIQUE(query_run_id, position)` and also `UNIQUE(query_run_id, nm_id)`.
- Fetch page 1 and page 2 back to back (no long sleeps between them). Ranking is dynamic (ads re-auction per request), so the same nm can appear on both pages and some items can be skipped. On a duplicate nm_id keep the FIRST occurrence (best position) and count `duplicates_dropped` on the query_run.

**Warning signs:** `duplicates_dropped` > 0 in every query; `distinct nm_id` < 200 in nearly all runs; page 2 first id == page 1 first id.

**Phase to address:** P2 (pager + fingerprint test using a fixture where page 2 == page 1). P3 (uniqueness constraints).

---

### Pitfall 5: Defining "position" without handling ad (promoted) slots

**What goes wrong:** Position gets recomputed (e.g. sorted by `log.position`), or ads are filtered out, so stored positions disagree with what the site shows. Or ads are mixed in with no flag, and the analyst can't separate organic from paid rank.

**What the data shows** (three real search payloads):
- Ad items carry `log: {cpm, promotion:1, promoPosition, position, advertId, tp}`.
- `log.promoPosition` equals the item's 1-based index in `products[]` on that page in every observed case (90/90 items in one 100-item page; 3/3 in another). So the array order IS the displayed order with ads already placed in their slots.
- `log.position` is a different number: the item's organic rank (e.g. an ad at slot 1 with `position: 7`). A 2026 Sheets tracker treats `position` as organic and `promoPosition` as the boosted slot.
- `tp` was `'b'` in one payload and `'c'` in another; meaning undocumented. Store raw.
- One page had `log` on 90 of 100 items, another on 3 of 100, so the fraction is highly variable by query.
- Ads can be 2 nm_ids of the same advert (same `advertId`, same `position`).
- Whether `promoPosition` continues 101... on page 2 is UNVERIFIED (no page-2 fixture found).
- `cpm` is a competitor's bid, public in the payload. Store it, but treat it as an ad-intelligence extra, not a requirement.

**How to avoid:**
- Position = 1-based index across concatenated pages in array order. Never re-sort.
- Store flags and raw numbers: `is_ad` (= `log.promotion == 1` or `log` present with `promoPosition`), `ad_organic_position` (= `log.position`), `ad_slot` (= `log.promoPosition`), `ad_cpm`, `ad_tp`, `advert_id`. Everything NULL when no `log`.
- Add a consistency check: if `is_ad` and `ad_slot` is not NULL and `ad_slot != page_offset + index_in_page`, increment a `ad_slot_mismatch` counter on the run and log it. This is an early warning that the "array order == display order" assumption changed.
- In the UI add an "ad" marker on hover and an optional toggle "показывать органическую позицию" using `ad_organic_position`. Keep default = displayed position (per TZ).
- Document in README: a product's position can jump because its ad slot turned on/off, not because organic relevance changed.

**Warning signs:** `ad_slot_mismatch` > 0; position series with huge step changes that coincide with `is_ad` flips; ads share of items = 0 across the board (maybe `log` dropped from the payload: schema drift).

**Phase to address:** P1 (decision recorded), P2 (mapping + check), P3 (columns), P4 (hover flag).

---

### Pitfall 6: Wrong "buyer price" field, or the wrong size

**What goes wrong:** The price in the table does not match the site's price card, or flips between hours without a real price change.

**What the data shows:**
- Top-level `priceU/salePriceU` are null in current payloads (ru-marketplace-mcp, July 2026). Real prices live in `sizes[].price`, in kopecks: divide by 100.
- Recorded payloads: `{"basic":216500,"product":158000,"total":158000,"logistics":0,"return":0}`; and cross-border/WB-seller items: `{"basic":604400,"product":206300,"total":273700,"logistics":67400,"return":5000}` where `total == product + logistics` exactly (also 912100+3600=915700, 30800+14200=45000).
- `basic` = pre-discount price. `product` = the price the buyer pays for the item (dev.to write-up; ru-marketplace-mcp; 3 independent parsers use `product`).
- `total` includes the delivery charge `logistics`. Parsers disagree: ru-marketplace-mcp uses `product or total`, a price-bot treats price as `product + logistics`, and Nabralos prefers `total`. Treat the site card as `product` (MEDIUM, needs a spot-check).
- In multi-size products the size prices often differ. In one 100-item page, 37 products had multiple sizes and 11 of those had different `product` prices per size. In another sample only some products had `price` at all.
- `sizes: []`, `[null]`, `price: null`, or `product: 0` occur for unsellable items (`totalQuantity == 0`). A 0 sentinel would "rank" a dead listing as cheapest.

**How to avoid:**
- `price_rub` = `product / 100`, from the cheapest priced size (min over sizes with `product > 0`), because the site's list card shows a single price, apparently the lowest available. Store `price_basic_rub`, `price_total_rub`, `logistics_rub` and `sizes_priced_count` as audit columns.
- If there is no priced size → NULL, not 0. Skip nulls and non-dicts defensively.
- Coerce number-as-string.
- Before trusting the choice, do a one-time human spot-check: open 3 products in a browser with Moscow selected, compare with stored `product`/`total`, and record the result in the README.
- Never apply the personal discounts (WB Wallet, logged-in price). Out of scope per PROJECT.md. The page-price an anonymous buyer sees is what we track.

**Warning signs:** Price == 0 in the DB; `price_total != price_product` for many rows (logistics > 0 means cross-border); price differs from the browser by a constant factor (SPP/Wallet).

**Phase to address:** P1 (decision + spot-check), P2 (mapper), P3 (audit columns).

---

### Pitfall 7: Wrong rating/review fields (and 0 stored as a real rating)

**What goes wrong:** Rating or review counts are off versus the card, or products with no reviews show "0.0 ★".

**What the data shows:**
- `rating` is an old rounded integer (5, 4), not for display.
- `reviewRating` is the float shown on the card (4.9). `feedbacks` is the review count.
- `nmReviewRating` / `nmFeedbacks` are per-nm (this color/size variant only). In a recorded page, `feedbacks >= nmFeedbacks` always (19 of 100 differed, e.g. `feedbacks 26, nmFeedbacks 0, reviewRating 4.8, nmReviewRating 0`). So `feedbacks` is merged across variants of one imt/root.
- Therefore: for a product with only variant-level reviews, `nm*` = 0 while the card still shows merged stars.
- Absence of reviews shows as `reviewRating: 0` and `feedbacks: 0`. The nm* fields are absent (null) in older versions.
- Mapper used by other projects: `reviewRating ?? rating`, `feedbacks ?? nmFeedbacks`.

**How to avoid:**
- Store `rating = reviewRating`, `reviews_count = feedbacks`. Also store `nm_rating`, `nm_feedbacks` raw for audit. Do not fall back from `reviewRating` to `rating` silently (different precision).
- `feedbacks == 0` or `reviewRating == 0` → rating NULL ("нет отзывов"). Don't plot or average 0 as a rating.
- Spot-check against the site (same as pitfall 6). Confidence in "site shows merged" is MEDIUM.

**Warning signs:** Many 0.0 ratings with large review counts, or `feedbacks < nmFeedbacks` (would contradict the merged model: log it).

**Phase to address:** P2 (mapper), P3 (columns), P4 (NULL display).

---

### Pitfall 8: Delivery days derived with an unverified formula and without raw inputs

**What goes wrong:** `delivery_days` shown in the UI do not match the card's "Доставим завтра / 18 октября"; and because only the derived number was saved, it can't be recomputed after the formula is fixed.

**What is known:**
- No public documentation or front-end write-up of `time1/time2/dtype/wh` was found (Habr Q&A "what do time1/time2 mean" has 0 answers; articles only show the fields). Everything below is inference from recorded payloads.
- `time1`, `time2` are integers in hours. Typical `time1` is 2-7 (a few at 15-24, sometimes 101), `time2` is 26-32 (up to 56-218 for far warehouses). Same-warehouse items share `time2` but differ in `time1` (e.g. 3 vs 101 with `dist 91`): consistent with `time1` ≈ lead/processing time and `time2` ≈ transit time. Sum = total hours (hypothesis in PROJECT.md).
- Product-level `time1/time2/wh/dtype/dist` equals the FASTEST size: in 96 of 100 products in one page and 100 of 100 in another, product-level `time1+time2` equals the minimum over `sizes[]`. So use the product-level fields (and don't re-derive from sizes).
- `sizes[].stocks[]` (per-warehouse `wh, dtype, qty, priority, time1, time2, dist`) appears only in card/detail, not in search responses. Search has no per-stock data.
- `dtype` is a bit-flag-like value up to 6.6×10^12 (`6597069766657`) and also small ints (4). Store as INTEGER (SQLite 64-bit is fine), never in an INT32, and don't use it in the delivery math.
- `dist` is the distance (km) between warehouse and the `dest` point.

**How to avoid:**
- Store raw `time1_h`, `time2_h`, `wh`, `dtype`, `dist` per observation, and `delivery_hours = time1 + time2` only if both are non-null (else NULL).
- Compute `delivery_days` in a SQL view or a pure function, not baked at ingest, so a later formula fix needs no recollection. Hypothesis: `(date_msk(collected_at + delivery_hours) - date_msk(collected_at)).days`.
- Calibrate once by hand: for ~10 cards, compare the browser's delivery label (Moscow) to the formula at collection time. Check variants: floor vs ceil; a cutoff hour; whether `time1` is included. Write the finding into the README honestly ("derived, approximate") if it can't be matched exactly.
- Use zoneinfo `Europe/Moscow` (UTC+3, no DST since 2014). The slim Docker image has no tz database: add the `tzdata` pip package or `ZoneInfo` raises `ZoneInfoNotFoundError`.
- Do not compute on the browser's time: collected_at is the server-side wall clock of the collector.

**Warning signs:** `delivery_days` of 0 for items with `time1+time2 > 24`; negative or huge (>30) days; mismatch with the browser during the spot-check; values change with the hour of collection for the same product more than expected.

**Phase to address:** P1 (spot-check + decision), P2 (function + unit tests with fixed timestamps, e.g. 23:30 and 00:10 MSK), P3 (raw columns/view).

---

### Pitfall 9: Wrong or silently different `dest` (region) and unpinned request params

**What goes wrong:** Prices, stock and delivery times belong to a different region, or the SERP differs from what a Moscow buyer sees, with no error.

**What is known:**
- `dest` = a geo-entity id, not a city code. Moscow is commonly `-1257786` (dev.to: "Moscow default"; used by many parsers). Other Moscow-ish ids exist in the wild (`-445298` labelled "MSK" in one 2026 Sheets script, others for Krasnodar `12358063`, Ekaterinburg `-5817698`, Vladivostok `123587791`).
- Changing `dest` changes price and delivery: dev.to measured `time2` 18→27 and a price 1,719 RUB cheaper for `dest=123585487`.
- Bad ids don't error: `dest=0` → `products: null`; `-1` and `-446488` → empty results.
- Site flows get `dest` from the geo endpoint's `xinfo` string (`appType=1&curr=rub&dest=...&spp=30`). `spp=30` is a discount/price parameter, NOT the page size (a Habr article misdescribes it). Keep it constant.
- Extra params seen on the site (`hide_dtype`, `hide_vflags`, `ab_testid`, `ab_testing`, `appType=64`, `inheritFilters`, `autoselectFilters`) can change WHICH items appear or their ranking.

**How to avoid:**
- Pin ONE explicit param set in config and store the full request param dict (JSON) on each run. Never mix params copied from different repos. Start from the project's documented set (`appType=1&curr=rub&dest=-1257786&lang=ru&resultset=catalog&sort=popular&spp=30&suppressSpellcheck=false` plus `ab_testing=false`).
- One-time human verification of Moscow: in a browser with the Moscow address selected, read the search request's `dest` in DevTools (your own traffic) and compare to the config. Write it into the README.
- Soft sanity check per run: median `dist` and `time2` of the page are within a band seen in the first successful runs; warn (not fail) on a jump. This is a heuristic (LOW).
- Fail loudly on `products: null` (`schema_drift`/`empty_suspect`), never treat as "no products".

**Warning signs:** Sudden shift of all prices or delivery days across all queries at once; `products: null`.

**Phase to address:** P1 (decision + verification), P2 (config + params stored), P3 (`runs.params_json`).

---

### Pitfall 10: Query text altered on the way in, or double-encoded on the wire

**What goes wrong:** WB receives a different query than the sheet's wording, so the SERP differs. The typo query "пастельный комплект 2-спальный" gets "fixed", "160х200" (Cyrillic х) and "160x200" (Latin x) get merged, "ё" turns into "е", and `%`, `*`, `,` break the URL.

**Why it happens:**
- `.strip().lower()`, NFKC/NFD normalization or `str.casefold()` somewhere.
- CSV read with the wrong encoding or a BOM. httpx guesses a charset for `text/csv` with no `charset=`: decode `response.content` as `utf-8-sig` yourself.
- `csv.DictReader` on a file with no header row eats query #1 as the header.
- Google's `/export?format=csv` answers with a redirect, and httpx does NOT follow redirects by default (`follow_redirects=True` needed).
- URLs built by f-string with `quote()` and then passed into `params=` (double encoding: `%25D0...`), or `%` not escaped.
- A non-shared sheet returns an HTML login page with 200.

**How to avoid:**
- Parse with `csv.reader` (no header), keep each cell byte-for-byte (strip only the line terminator). Do not lower/strip/normalize.
- Keep original text in `queries.query_text` (UNIQUE) and use it for the request. Let httpx's `params=` encode (UTF-8 percent-encoding; space as `+`). Never pre-quote.
- Validate the sheet fetch: status 200, `content-type` not HTML, row count within sane bounds (e.g. 50-100 here). Otherwise use the bundled list and record `queries_source` (sheet or bundled) on the run. Sheet wins when it is valid.
- Test with a fixture list that includes ё, Cyrillic х vs Latin x, `%`, `*`, `,`, trailing/double spaces, and assert the exact outgoing URL query.
- Record the normalized query WB reports (`metadata.normquery` / `title` when present) in `query_runs.wb_normquery`. With `suppressSpellcheck=false` (the site's default) WB can silently correct a typo and return the SERP of the corrected query. That is what the site shows, so keep `false`, but store `normquery` so the UI can show "WB понял запрос как: ...".
- Keep duplicates out: if the sheet has identical rows, collect once, log it. Never delete queries that disappear from a later sheet; keep them as historical (inactive) so old history stays viewable.

**Warning signs:** Query count in DB != 70; two distinct sheet queries collapse into one; `normquery` differs from `query_text` often; `UnicodeDecodeError` or a `U+FEFF (BOM)` prefix on the first query.

**Phase to address:** P2 (loader + tests), P3 (queries table, versioning).

---

### Pitfall 11: Rate limiting handled by hope (429 loop, parallel fan-out, no pacing)

**What goes wrong:** Parallel requests cause 429s, retries pile up, the run overlaps the next hour, and continued attempts after a block look like abuse.

**Evidence:** Only `search.wb.ru` showed throttling in reports. The 429 body is HTML with no `Retry-After`. In a test, 50 parallel requests gave 30×200 and 20×429. Another parser uses 1.5 s between requests as "polite" pacing. A 2026 tracker says WB's offer forbids automated price collection.

**How to avoid:**
- Sequential, single connection, a fixed gap of ~1.5-3 s between requests: 70 queries × 2 pages ≈ 140 requests ≈ 4-7 minutes per run, which is comfortably inside the hour.
- On 429: exponential backoff with jitter (e.g. 30 s, 60 s, 120 s), at most 2-3 attempts per page, then mark the query `rate_limited` and move on (per-query isolation). Do not retry 403/498 within the same run beyond a single re-check.
- Run deadline (e.g. 40 min): stop starting new queries after it; mark the rest `skipped_deadline`.
- Randomize query order per run so late-queue queries are not always the ones starved.
- Reasonable explicit `httpx.Timeout` (connect 5 s, read 15 s). No retries on parse errors.
- State the ToS risk honestly in the write-up (public offer text restricts automated collection). The project mitigates with a low request rate and no evasion; the user decides on deployment.

**Warning signs:** Per-run duration creeping toward 60 min; 429 share rising; the same queries always `skipped_*`.

**Phase to address:** P2 (client + pacing + classifier), P3 (run deadline).

---

### Pitfall 12: Overlapping or crashed runs, non-idempotent re-runs, drifting schedule

**What goes wrong:** Two collectors write the same hour (double rows), a crash leaves a run "running" forever, a rerun after a crash duplicates or loses data, or `sleep(3600)` drift pushes runs off the hour so chart x-points are ragged.

**How to avoid:**
- One scheduler process (long-lived container with `restart: unless-stopped`) that aligns to the next hour boundary and computes `slot_start` (UTC hour). `runs.slot_start` UNIQUE: a second attempt for the same slot is a no-op or an explicit "retry slot" that appends a new `attempt` number, never overwrites.
- Single-run mutex: an OS file lock (`fcntl.flock`) on a lock file next to the DB, plus `BEGIN IMMEDIATE` when creating the run row. If locked → log and exit/skip.
- Each run inserts its row at start (`status=running`, `started_at`, `heartbeat_at`), then each query_run + its observation rows in ONE transaction (all-or-nothing per query), and sets the final status at the end (`ok | partial | blocked | failed`). On startup, any `running` row with a stale heartbeat becomes `abandoned`.
- The chart x-position is the run's `slot_start` (or `started_at`), not per-query timestamps, so all queries of an hour line up. Still store `collected_at` per observation.
- Per-query isolation: wrap each query in try/except; one query's exception becomes `error` for that query only.
- Never `INSERT OR REPLACE`/`UPDATE` observations. Append-only; corrections via a new row/flag.
- Crash visibility: log to stdout with JSON lines, docker log rotation, and a UI banner "последний успешный сбор: N минут назад" (red if > 2 h). Optional healthcheck: fail if the last `runs.finished_at` is too old.

**Warning signs:** Two runs with the same hour; `status=running` rows older than 2 h; gaps in the hourly cadence; duplicate (query, nm, hour) rows.

**Phase to address:** P3 (schema + lock + heartbeat), P5 (restart policy, healthcheck).

---

### Pitfall 13: Treating "absent from an incomplete query-run" as "not found"

**What goes wrong:** Page 2 failed (429), so only positions 1-100 were collected. A product at position 150 last hour now looks like it "fell out of the top-200", and the chart gap lies about reality.

**How to avoid:**
- Give each `query_run` an explicit `status` (`complete | short_result | partial | blocked | rate_limited | error | skipped_*`) and `rows_collected`, `pages_ok`, `wb_total`.
- Only `complete` and `short_result` (natural end, e.g. fewer than top_n results) count as proof of absence. In the UI, "found in X of Y runs" counts only those.
- Chart: a gap for any missing point (per requirement), plus an optional thin "нет данных" rug/marker on the x-axis for query_runs that were incomplete or blocked, so a viewer can tell "left the top-200" from "we didn't see".
- Store partial rows (positions 1-100) but label them with their query_run status; never invent the rest.

**Warning signs:** Position series with gaps that line up exactly with `partial/blocked` query_runs; a product "lost" across many queries in one hour.

**Phase to address:** P3 (status model), P4 (chart + counts).

---

## Moderate Pitfalls

### Pitfall 14: SQLite writer/reader contention, WAL misconfiguration, volume mounts

**What goes wrong:** `database is locked` in the UI or collector; the WAL file grows unbounded; the UI container can't open the DB read-only.

**Facts** (sqlite.org/wal.html): readers don't block the writer and vice versa, but there is exactly one writer. WAL needs shared memory, so all processes must be on the same host and not on a network filesystem. A checkpoint can't finish while a reader is always active, so the WAL can grow without bound if there's no reader gap. The default auto-checkpoint is at 1000 pages and doesn't truncate the file. `journal_mode=WAL` persists in the DB file. `synchronous=NORMAL` is safe from corruption in WAL, though the last commits may be lost on power failure.

**Prevention:**
- Collector connection: `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=30000; PRAGMA foreign_keys=ON;` and `sqlite3.connect(timeout=30)`.
- Keep write transactions short: one per query (≤ 200 rows, `executemany`), never one giant per-run transaction.
- The UI opens a fresh connection per query, read-only via URI `file:...?mode=ro` (NOT a `:ro` volume mount; WAL readers need to write the `-shm` file). Also `PRAGMA busy_timeout`. Both containers mount the same host directory read-write. Don't put the DB on a network share or on macOS bind mounts in dev if odd lock errors appear.
- Run `PRAGMA wal_checkpoint(TRUNCATE)` at the end of each run (the collector finishes between UI reads), and set `journal_size_limit`.
- Do not share a `sqlite3` connection across Streamlit threads (`ProgrammingError: SQLite objects created in a thread...`). Open per call, or use `check_same_thread=False` only with a read-only connection from `st.cache_resource`.
- Python 3.12+ deprecates the default datetime adapters: store ISO-8601 UTC strings (or integer epoch) explicitly. Python 3.14 locally vs 3.12 in Docker can hide warnings.

**Warning signs:** `OperationalError: database is locked`; the `-wal` file grows to hundreds of MB; "unable to open database file" in the UI container.

**Phase:** P3 (pragmas, tx size), P4 (UI connection), P5 (compose volumes).

### Pitfall 15: DB growth and slow "product picker" queries

**What goes wrong:** 70 queries × 200 = 14,000 rows/hour = 336k/day = ~10M/month. At ~250-350 B/row that's ~85-120 MB/day. A `SELECT DISTINCT nm_id` for a query scans millions of rows, and the picker is slow.

**Prevention:**
- Narrow observation row, no raw JSON per row. Keep product names/brand/seller in the observation only if needed for the table, or in a `products` dimension with `first_seen` and an append-only `product_snapshots` (name/seller/brand changes are history, not overwrite).
- Indexes: PK on `observations(id)`; `UNIQUE(query_run_id, position)`; `UNIQUE(query_run_id, nm_id)`; `INDEX(query_id, nm_id, collected_at)` for the series; `INDEX(nm_id)` only if needed. Cap the number of indexes: each one slows ingest.
- A small `query_products(query_id, nm_id, first_seen, last_seen, seen_count, best_position)` summary maintained at ingest (upsert is fine here, since it's derived, not history) powers the multiselect instantly.
- The product picker must be searchable and sorted (by `seen_count` desc, then `best_position`); Streamlit multiselect with thousands of options is sluggish. Default-select the top-3 most frequent.
- Retain all history (requirement). For a test assignment the volume over a few days is trivial; do not add pruning, but document `VACUUM`/disk planning.
- Run `ANALYZE` after the first runs.

**Warning signs:** The picker takes > 1 s; DB > several GB; `EXPLAIN QUERY PLAN` shows `SCAN observations`.

**Phase:** P3.

### Pitfall 16: Streamlit caching staleness and rerun cost

**What goes wrong:** New hourly data doesn't show up (stale `st.cache_data`), or the page recomputes huge frames on every widget click.

**Prevention:**
- Don't cache without a `ttl` (docs: without it, DB results may never refresh). Better: pass `latest_run_id`/`MAX(finished_at)` (a cheap query, ttl 30-60 s) as an argument to cached loaders so the cache invalidates exactly when data lands.
- `st.cache_data` for DataFrames (returns copies), `st.cache_resource` only for the connection/engine, which must be thread-safe.
- Query only the selected query and selected nm_ids; never load the whole table.
- Show a "данные на: <timestamp МСК>" caption so staleness is visible.
- `st.dataframe`/table: cast `nm_id` to string or `format="%d"` to avoid `1,234,567` formatting; render NULL as "-" or "нет данных", not `None`/`nan`.

**Phase:** P4.

### Pitfall 17: Plotly chart lies: no gaps, wrong axis, bad hover, timezone shift

**What goes wrong:**
- The line connects across runs where the product was absent, because rows for those runs simply don't exist.
- The Y axis is not reversed, or autorange hides the "1 at top" convention.
- A single-point series is invisible in `lines` mode.
- Hover shows epoch or UTC times.
- Times shift by 3 hours.

**Prevention:**
- Build the series on the FULL grid of that query's runs (from `query_runs`, not from observations): reindex each product onto all run slots, with `y=None` where absent. Then `connectgaps=False` (Plotly default for scatter; set explicitly) really produces gaps.
- `mode="lines+markers"`, so isolated points are visible.
- `fig.update_yaxes(autorange="reversed")`, and optionally fix the range (`range=[top_n + 5, 0.5]`) so 200 → 1 is stable between selections. Integer ticks only; do not use a category axis.
- `hovertemplate="%{x|%d.%m.%Y %H:%M}<br>ID %{customdata[0]}<br>Позиция %{y}<br>%{customdata[1]}<extra></extra>"`, with `customdata` carrying nm_id and the ad flag. Without `<extra></extra>` the trace name shows up in a secondary box.
- Timezone: Plotly/plotly.js have no real timezone support; tz-aware datetimes are reported to be rendered as UTC or shifted by the browser offset. Convert to Europe/Moscow and then DROP tzinfo (or pass formatted strings) before plotting, and label the axis "время, МСК". Store UTC in the DB.
- One trace per product with legend names `ID <nm_id>`; limit the default selection to ≤ 5 products for legibility.
- Test at least one chart-data function (not the pixels): given observations with a missing hour, the output y has None at that hour.

**Warning signs:** Straight line through a known-missing hour; hover time differs from the table time by 3 h; chart empty for a product seen once.

**Phase:** P4 (+ unit test of the series builder in P3/P4).

### Pitfall 18: Slim container timezone and clock assumptions

**What goes wrong:** `ZoneInfo("Europe/Moscow")` fails in `python:3.12-slim` (no tz database); cron or schedulers run in UTC while hour-aligned code assumes MSK; `datetime.utcnow()` deprecation noise.

**Prevention:** `pip install tzdata`; use timezone-aware `datetime.now(timezone.utc)`; compute MSK only for delivery days and display; set `TZ=Europe/Moscow` in compose only for log readability. Assert at startup that `ZoneInfo("Europe/Moscow")` loads.

**Phase:** P2 (delivery function), P5 (Dockerfile).

### Pitfall 19: Query-level A/B noise and "personalized" ranking misread as signal

**What goes wrong:** Position jitters ±N between consecutive runs and is interpreted as meaningful movement. Results can vary by A/B bucket (`ab_testing`, `ab_testid`), time, and ad auctions.

**Prevention:** Fix `ab_testing=false`; do not pass `ab_testid`. Keep one anonymous identity, with no cookies or tokens added by us. Document in the README that the series has natural noise; the UI may offer an optional "сглаживание" toggle, off by default (smoothing must never replace raw data). Never interpolate.

**Phase:** P2 (params), P4 (README text/toggle).

### Pitfall 20: Fallback sources and demo data that look like real data

**What goes wrong:** To "make the demo work", a fallback fabricates values. Real example found while researching: a published WB project falls back to fixed invented prices/ratings (price 1990, rating 4.8, feedback counts derived from the id) when its source returns nothing. That kind of behaviour is forbidden here (TZ §5, Core Value).

**Prevention:**
- Fixtures only in `tests/` or temporary DBs. The collector refuses to start if `DB_PATH` points at a path containing the test fixture marker, and tests use `tmp_path`.
- No synthetic seeding script in the repo that can target the production DB. If a demo mode is ever added, it writes to a separate file and the UI shows a visible "DEMO" banner. Prefer: no demo mode.
- Each row has `source_run_id`; the UI exposes the run log (blocked runs are visible).

**Phase:** P1 (rule), P3 (guards), P5 (final audit).

---

## Minor Pitfalls

### Pitfall 21: Treating `nm_id` or `dtype` as 32-bit or float
IDs are ~9 digits (fine in Python/SQLite) but `dtype` reaches 6.6e12. Parse with `int` (not float via pandas autoconvert with NaN, which can turn ints into floats). Use pandas nullable `Int64` for columns with NULLs.

### Pitfall 22: Name/brand/seller mojibake or odd whitespace
Some connectors observed mojibake and stray whitespace in WB text. Store as received (UTF-8 str); sanitize only for display. Use `supplier` for the seller name and also store `supplierId`, as the name can change.

### Pitfall 23: Product present twice with different `nm_id` of the same `root`/imt
Color variants are separate nm_ids of one root (`root` = imt). They are distinct observations; do not merge. Store `root_id` so the UI can group optionally.

### Pitfall 24: Timestamps recorded at the wrong moment
Record `collected_at` just after the response is received, per page (store per query_run). A per-run timestamp taken at the start is misleading for the last queries (minutes later).

### Pitfall 25: Python 3.14 locally vs 3.12 in Docker
Pin the dependency versions in a lockfile, test in the 3.12 container before the final deploy, and avoid 3.13+ only features.

### Pitfall 26: Streamlit exposed publicly with the DB writable
Public link means anyone can open it. The UI uses a read-only connection, no free-text SQL, and no file download of the DB. Put the DB outside any static directory.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Parse only `data.products` or only top-level `products` | 5 minutes saved | Silent empty runs after a WB version change | Never: accept both |
| Bake `delivery_days` at ingest without raw hours | Simpler schema | Cannot fix after a formula correction; needs recollection that isn't possible | Never: store raw `time1/time2` |
| `sizes[0].price.product` blindly | Simple | Wrong when size 0 is unpriced or pricier | Only if spot-check shows identical across sizes (unlikely) |
| One transaction per run | Faster inserts | A crash loses the whole run; long writer lock blocks the UI | Never: per-query transactions |
| `INSERT OR REPLACE` to "make reruns idempotent" | No unique errors | Overwrites history, violates the append-only rule | Never |
| `st.cache_data` without ttl | Snappy UI | Stale chart until restart | Never |
| Denormalized wide rows (name, brand, seller each row) | No joins | ~100 MB/day | Acceptable for this assignment's days-to-weeks horizon |
| Skip the run lock because "cron runs hourly" | Less code | Overlap when a run takes > 1 h or on a manual rerun | Never |
| `time.sleep(3600)` loop | Trivial | Schedule drift, ragged x-axis | Only if it re-aligns to the hour boundary each loop |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| WB search JSON | Trusting HTTP 200 as success | Classify by status + headers + body prefix + shape before parsing (Pitfall 1) |
| WB search pagination | Using `total` to decide page count | Loop until `top_n`, short page, or wrap (page-1 fingerprint) (Pitfall 4) |
| WB `log` object | Sorting by `log.position` or dropping ads | Keep array order, store `log` fields as flags (Pitfall 5) |
| WB prices | Using `priceU/salePriceU` or `basic` | `sizes[].price.product`/100, min over priced sizes; keep basic/total as audit (Pitfall 6) |
| WB `dest` | Copying one from a random repo | Pin and verify once in a browser; log it per run (Pitfall 9) |
| Google Sheet CSV | `DictReader`, no `follow_redirects`, guessed charset | `csv.reader`, `follow_redirects=True`, decode `utf-8-sig`, validate row count, fall back to bundled list (Pitfall 10) |
| httpx params | Pre-quoted query in the URL string | Pass raw text via `params=`; test the wire URL |
| SQLite from Streamlit | Module-level shared connection | Per-call read-only connection (`mode=ro`), busy_timeout (Pitfall 14) |
| Plotly dates | Passing tz-aware datetimes | Convert to MSK, drop tzinfo, label "МСК" (Pitfall 17) |
| Docker slim | No tz database | Install `tzdata` (Pitfall 18) |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Parallel fan-out of requests | 429 bursts, then blocks | Sequential, 1.5-3 s gap | Immediately at ~30+ concurrent (reported) |
| `SELECT DISTINCT nm_id WHERE query_id=?` on raw rows | Picker takes seconds | `query_products` summary + composite index | > ~2M rows (~6 days) |
| Loading all observations into pandas on each rerun | UI freezes, memory climbs | Query by (query_id, nm_ids), cache by latest run id | > 1M rows |
| WAL never checkpointed because the UI always reads | `-wal` grows | `wal_checkpoint(TRUNCATE)` after each run, short reader connections | Days of continuous load |
| Multiselect with thousands of ids | Slow widget | Searchable, sorted, limited list | > ~1-2k options |
| Many indexes | Slow inserts (14k rows/run) | 3-4 indexes only | Visible at > 10M rows |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Logging full response bodies / headers to public logs | Leaks nothing secret but bloats logs; may capture opaque tokens (`qv`, `payload`, `logs` strings) that aren't needed | Log only status, server header, ≤200-byte body prefix; do NOT persist opaque `payload`/`logs`/`qv` fields |
| Streamlit app exposes DB path or raw SQL | Data tamper/leak | Read-only URI, fixed queries only, DB outside static dirs |
| Google Sheet URL/ID and any env secrets committed | Low-risk leakage | Sheet ID is public per the assignment; keep any future keys in `.env` (gitignored) |
| Public UI with no rate limit | Resource exhaustion | Cached loaders, row limits, single small container |
| ToS exposure of automated collection | Platform-rule risk | Low request rate, no evasion, honest write-up of the risk (Pitfall 11) |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Gap not explained | Viewer thinks the chart is broken | Legend/caption: "разрыв = товара нет в топ-200 или данные не собраны"; rug marker for blocked/partial runs |
| Position axis not reversed | 1 looks like the worst | `autorange="reversed"`, label "Позиция (1 = верх)" |
| No "data as of" and no run health | Stale/blocked state is invisible | Header with last successful run time (МСК) and a red banner if blocked/old |
| Ad slots indistinguishable | Misleading rank story | Hover flag "реклама" and optional marker style |
| NULLs shown as `None`/`nan`/0 | Misread as real values | "нет данных" / "-" |
| Rating 0.0 for unreviewed items | False signal | NULL → "нет отзывов" |
| Times in UTC | 3 h confusion | Always МСК, labelled |
| Huge ID list | Unusable picker | Searchable, sorted by frequency, default top-3 |
| English labels | TZ requires a Russian UI | All UI strings in Russian |

## "Looks Done But Isn't" Checklist

- [ ] **Collector:** often handles only the happy path; verify with recorded 403, 498, 429, HTML-at-200, `products:null`, `data.products` vs top-level `products`, and page-2-equals-page-1 fixtures.
- [ ] **Query list:** often loses ё / Cyrillic х / typo; verify the outgoing URL for all 70 queries equals the sheet text exactly (decode and compare), and that count == 70.
- [ ] **Position:** verify it equals the array index across pages with ads kept in place, and that `ad_slot_mismatch` is tracked.
- [ ] **Price:** verify against 3 browser cards (Moscow); NULL, not 0, for unpriced.
- [ ] **Delivery:** verify with fixed-clock unit tests (23:30 MSK + 1 h = 1; 10:00 + 5 h = 0) and a browser spot-check; raw hours stored.
- [ ] **Idempotency:** verify a second run in the same hour slot appends nothing duplicate, and killing the process mid-run leaves a visible `abandoned` run and no half-written query.
- [ ] **Blocked state:** simulate a 403 deployment and confirm the UI shows blocked runs, not empty charts or fake data.
- [ ] **UI gaps:** verify with a product missing in a middle run: the line breaks, no interpolation, hover works, single points visible.
- [ ] **Timezone:** the hover time equals the table time equals the MSK wall clock.
- [ ] **Docker:** `ZoneInfo("Europe/Moscow")` loads in the image; both containers share the DB volume; UI reads while the collector writes with no `locked` errors.
- [ ] **No fabricated data:** grep the repo for any seed script that can write to the real DB path.
- [ ] **Restart:** `docker compose restart` / host reboot resumes the hourly cadence with no manual step.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Wrong delivery formula already stored | LOW | Raw `time1/time2` kept → fix the function/view, no recollection |
| Wrong price field stored | MEDIUM | If audit columns (`basic/total/logistics`) exist, recompute; otherwise history before the fix is unreliable: mark and document |
| Silent empty runs from drift | MEDIUM | Query `query_runs` for `schema_drift/empty_suspect`; fix the parser; the lost hours cannot be recovered (WB gives only the live SERP). Disclose gaps |
| Duplicate rows from overlapping runs | LOW | Delete the later `attempt` rows for that `slot_start` using the run id (the only permitted deletion, logged), add the lock |
| Corrupted/locked DB | LOW-MEDIUM | Stop containers, `sqlite3 .recover` or restore the last file copy (daily `.backup` to another volume) |
| Blocked host | HIGH | Not recoverable by code under project rules: change the deployment host or data source, or ship honest blocked history |
| Stale cache in UI | LOW | Clear cache / fix ttl and latest-run key |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| 1 Block treated as empty | P2 (+P3, P4) | Fixture tests per class; simulated 403 run shows `blocked` in UI |
| 2 Host reachability unknown | P1 | Probe result recorded; decision logged |
| 3 Shape drift | P1, P2 | Fixtures for each shape variant pass; `schema_drift` test |
| 4 Past-the-end pagination | P2, P3 | Page-2-equals-page-1 fixture ends at 100 rows, `short_result` |
| 5 Ads / position definition | P1, P2, P3, P4 | Position == array index test; `is_ad` stored; hover flag |
| 6 Price field/size | P1, P2, P3 | Mapper tests (multi-size, no-size, total≠product); browser spot-check |
| 7 Rating/review fields | P2, P3, P4 | Test `feedbacks=0` → NULL; merged vs nm fields stored |
| 8 Delivery days | P1, P2, P3 | Fixed-clock tests; spot-check noted in README |
| 9 `dest` and params | P1, P2, P3 | Params JSON stored per run; browser verification noted |
| 10 Query text/encoding | P2, P3 | Wire-URL test with ё, х/x, `%`, `*`, `,`; count == 70 |
| 11 Rate limits/pacing | P2, P3 | Run duration < 15 min typical; 429 test with backoff |
| 12 Overlap/crash/idempotency | P3, P5 | Kill-mid-run test; double-start test; UNIQUE slot |
| 13 Incomplete run ≠ not found | P3, P4 | Chart/table counts exclude incomplete runs |
| 14 SQLite contention | P3, P4, P5 | Concurrent write/read soak 10 min, no `locked`; WAL size bounded |
| 15 DB growth/indexes | P3 | `EXPLAIN QUERY PLAN` uses indexes; picker < 300 ms on 2M synthetic rows in a temp DB (test-only) |
| 16 Streamlit staleness | P4 | New run appears within ttl without restart |
| 17 Plotly gaps/axis/tz | P4 | Series-builder unit test; manual visual check |
| 18 tzdata | P2, P5 | Container starts; zoneinfo assertion |
| 19 A/B noise | P2, P4 | README note; params pinned |
| 20 Fabricated fallbacks | P1, P3, P5 | Repo grep; DB path guard |

## Verified field mapping

Confidence: HIGH = confirmed in several independent real payloads or parsers; MEDIUM = consistent evidence but needs a browser spot-check; LOW = inference.

| WB field | Our column | Confidence | Source |
|---|---|---|---|
| `products[]` / `data.products[]` order | `position` (1-based index across pages) | HIGH | `log.promoPosition == array index` in recorded payloads: https://github.com/mishannn/wb-goods-feed/blob/HEAD/internal/integrations/wildberries/products_response_fixture.json ; https://github.com/Unti1/T-skRAZRAB0T/blob/HEAD/products.json |
| `id` | `nm_id` | HIGH | same fixtures; https://github.com/Vladimir-Human/ru-marketplace-mcp/blob/HEAD/packages/wb-connector/src/wb_connector/server.py |
| `root` | `root_id` | HIGH | same fixtures |
| `name`, `brand`, `supplier`, `supplierId` | `name`, `brand`, `seller_name`, `seller_id` | HIGH | same fixtures; https://github.com/boeing666/tgmarket_bot/blob/HEAD/internal/parser/wildberries_types.go |
| `sizes[].price.product` / 100 (min over priced sizes) | `price_rub` | MEDIUM | https://dev.to/actorforge/the-wildberries-api-in-english-how-to-get-products-prices-and-reviews-as-json-4h1e ; ru-marketplace-mcp server.py (`_extract_price_rub`); https://github.com/maxexplorer/ParserProject/blob/HEAD/wb_products/main.py |
| `sizes[].price.basic` / 100 | `price_basic_rub` | HIGH | recorded payloads |
| `sizes[].price.total` / 100 (= product + logistics) | `price_total_rub` (audit only) | HIGH for the arithmetic, MEDIUM for site semantics | https://github.com/ibrohim-Fazliddinov/WB-Analyze/blob/HEAD/json.json ; https://github.com/Unti1/T-skRAZRAB0T/blob/HEAD/products.json ; https://github.com/lebrit/wb-price-bot/blob/HEAD/docs/WB_PROVIDER.md |
| `sizes[].price.logistics` / 100 | `logistics_rub` (audit) | HIGH | same |
| `priceU` / `salePriceU` (top-level, legacy `sizes[]`) | NOT used (NULL → drift counter) | HIGH (null in current payloads) | ru-marketplace-mcp server.py header comment; https://github.com/ShodanAI/Python-learning/blob/HEAD/ParserWB/js.json |
| `reviewRating` (float) | `rating` (NULL when 0 or `feedbacks == 0`) | MEDIUM | https://github.com/Unti1/T-skRAZRAB0T/blob/HEAD/products.json ; dev.to article |
| `feedbacks` | `reviews_count` | MEDIUM | same (always >= `nmFeedbacks`) |
| `nmReviewRating`, `nmFeedbacks` | `nm_rating`, `nm_feedbacks` (audit, NULL if absent) | MEDIUM | same |
| `rating` (int) | not used | HIGH | recorded payloads (`rating` 5 vs `reviewRating` 4.9) |
| product-level `time1`, `time2` (hours) | `time1_h`, `time2_h` | HIGH (equal to the fastest size in 196/200 products) | https://github.com/ibrohim-Fazliddinov/WB-Analyze/blob/HEAD/json.json ; Unti1 products.json |
| `time1 + time2` → calendar days in Europe/Moscow | `delivery_days` (derived view) | LOW (unverified vs site) | no public documentation found; inference from payloads |
| `wh`, `dtype`, `dist` | `wh`, `dtype`, `dist_km` | HIGH for presence, LOW for meaning | https://github.com/papyrussolution/OpenPapyrus/blob/HEAD/Src/PPLib/marketplace.cpp (sample card payload) |
| `log.promoPosition` | `ad_slot` | HIGH | mishannn fixture; https://github.com/Zaruber/Sheetswbparser/blob/HEAD/findwbpos.gs |
| `log.position` | `ad_organic_position` | MEDIUM | findwbpos.gs; fixtures |
| `log.cpm`, `log.tp`, `log.advertId`, `log.promotion` | `ad_cpm`, `ad_tp`, `advert_id`, `is_ad` | MEDIUM (`tp` meaning unknown) | fixtures |
| `totalQuantity` | `total_quantity` | HIGH | fixtures |
| `metadata.normquery` / `title` / `name` | `wb_normquery` | MEDIUM | https://github.com/Unti1/T-skRAZRAB0T/blob/HEAD/products.json |
| `total` / `data.total` / `metadata.total` | `wb_total` (informational) | MEDIUM | ru-marketplace-mcp server.py; https://github.com/maxexplorer/ParserProject/blob/HEAD/wb_products/main.py |
| `dest=-1257786` | `runs.dest` | MEDIUM | dev.to article; https://github.com/ZwickVitaly/wb_search_scan/blob/HEAD/searcher/parser/get_single_query_data.py |
| page size 100 (`metadata.rs`) | derived from `len(page1)`, not hardcoded | HIGH | dev.to article; Unti1 products.json |

## Sources

- Real WB search payload fixtures (shape, ads, prices, ratings, delivery): https://github.com/mishannn/wb-goods-feed/blob/HEAD/internal/integrations/wildberries/products_response_fixture.json , https://github.com/Unti1/T-skRAZRAB0T/blob/HEAD/products.json , https://github.com/ibrohim-Fazliddinov/WB-Analyze/blob/HEAD/json.json , https://github.com/ShodanAI/Python-learning/blob/HEAD/ParserWB/js.json (HIGH for shape facts; counts computed locally from these files)
- ru-marketplace-mcp (July-October 2026): server.py (pagination wrap verified live 2026-07-28, price location, edge-wall detection, "failure is not emptiness") and issue #101 (403 mis-reported as ok/0 results): https://github.com/Vladimir-Human/ru-marketplace-mcp (MEDIUM-HIGH; the parts about TLS impersonation in issue #93 were intentionally not used)
- dev.to "The Wildberries API in English" (2026): https://dev.to/actorforge/the-wildberries-api-in-english-how-to-get-products-prices-and-reviews-as-json-4h1e (MEDIUM, single blog; its client-fingerprint advice was intentionally ignored)
- 2026 position-tracker script handling `log.promoPosition`: https://github.com/Zaruber/Sheetswbparser/blob/HEAD/findwbpos.gs (MEDIUM)
- Pagination/total usage in current v18 parsers: https://github.com/maxexplorer/ParserProject/blob/HEAD/wb_products/main.py , https://github.com/Duff89/wb_parse_search_phrase/blob/HEAD/wb_catalog_parser.py (MEDIUM)
- Card/search sample with `stocks[]`, `dist`, `dtype`: https://github.com/papyrussolution/OpenPapyrus/blob/HEAD/Src/PPLib/marketplace.cpp (MEDIUM)
- `dest` and geo `xinfo`: https://github.com/Skifmail/WbPositionBot/blob/HEAD/scripts/fetch_dest.py , https://github.com/ZwickVitaly/wb_search_scan/blob/HEAD/searcher/parser/get_single_query_data.py (MEDIUM)
- Price semantic variants and ToS statement: https://github.com/lebrit/wb-price-bot/blob/HEAD/docs/WB_PROVIDER.md , https://github.com/SVorobiev-ru/Nabralos/blob/HEAD/src/capture/normalizers/wb.ts (MEDIUM)
- Habr articles/Q&A showing the fields without definitions (confirms the documentation gap for `time1/time2`): https://habr.com/ru/companies/amvera/articles/948988/ , https://qna.habr.com/q/1301608 (LOW as to semantics)
- SQLite WAL: https://www.sqlite.org/wal.html (HIGH)
- Streamlit caching: https://docs.streamlit.io/develop/concepts/architecture/caching (HIGH)
- Plotly hovertemplate/date formatting: https://plotly.com/python/reference/scatter/ (HIGH); Plotly time zone behaviour (community reports, MEDIUM-LOW): https://github.com/plotly/plotly.py/issues/209 , https://community.plotly.com/t/datetime-and-plotly-dash-charts-time-zone-issues/20605

---
*Pitfalls research for: Wildberries SERP position tracking (Python collector + SQLite + Streamlit)*
*Researched: 2026-10-16*
