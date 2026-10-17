---
phase: quick-261017-niw
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - .streamlit/config.toml
  - src/wbmon/ui/theme.py
  - src/wbmon/ui/app.py
  - src/wbmon/ui/texts.py
  - src/wbmon/ui/series.py
  - src/wbmon/ui/charts.py
  - src/wbmon/ui/data.py
  - src/wbmon/ui/demo.py
  - tests/ui/conftest.py
  - tests/ui/test_app.py
  - tests/ui/test_series.py
  - tests/ui/test_charts.py
  - tests/ui/test_data.py
  - tests/ui/test_demo.py
  - tests/ui/test_ui_guardrails.py
  - docs/DATA_ACCESS.md
  - README.md
  - SUBMISSION.md
autonomous: true
requirements: [D-01, D-02, D-03, D-04, D-05, D-06, D-07, D-08, D-09, D-10, D-11, D-12, D-13, D-14, D-15]
must_haves:
  truths:
    - "The page is white and uses Montserrat. The sidebar holds only «РАЗДЕЛЫ» and 4 sections (📈 Позиции, 🗂 Выдача, 🩺 Журнал сборов, ℹ️ О сервисе). The selected section has white liquid glass and a jelly bounce (D-01, D-02, D-03, D-05)"
    - "The top bar shows the bold title, a heartbeat-driven status pill and the «Реальные данные | ✨ Демо» switch. Streamlit's header toolbar, main menu and footer are hidden (D-04)"
    - "In real mode with no stored observations, Позиции and Выдача show the empty-state card with real counts and a «✨ Посмотреть на демо-данных» button that switches to demo (D-07)"
    - "Demo mode reads a separate synthetic SQLite file under tempfile.gettempdir()/wbmon-demo-<uid>/ and shows the purple card, the «ДЕМО» chart watermark and the demo pill. The real DB file keeps its size, mtime and row counts (D-12)"
    - "The Позиции chart keeps reversed Y, NaN gaps, failed-run bands and ad diamonds, and adds hollow circles at line endpoints next to an absent run. Four KPI tiles sit above it (D-08)"
    - "Выдача shows top-40 product cards, 4 per row, with the full table in an expander. Журнал сборов uses the new status colours inside white cards, and О сервисе sits in a white card (D-09, D-10, D-11)"
    - "docs/DATA_ACCESS.md matches the approved text byte for byte. README has «Демо-режим» and two links to it, and SUBMISSION has one link line (D-13)"
    - "pytest, ruff check and ruff format --check are all green. Nothing outside src/wbmon/ui changes in src/wbmon (D-14, D-15)"
  artifacts:
    - path: "src/wbmon/ui/theme.py"
      provides: "The one constant <style> sheet (STYLE) and inject(), the only st.html sink in the UI"
      contains: "def inject"
    - path: "src/wbmon/ui/demo.py"
      provides: "demo_db_path, check_demo_path, build_demo_db, ensure_demo_db: synthetic temp DB written through init_db plus Writer"
      contains: "def ensure_demo_db"
    - path: "tests/ui/test_demo.py"
      provides: "Demo guardrail tests: temp path, never the real path, real DB untouched, watermark, empty-state switch"
    - path: "docs/DATA_ACCESS.md"
      provides: "Approved data-access note"
      contains: "# Доступ к данным Wildberries"
  key_links:
    - from: "src/wbmon/ui/app.py"
      to: "wbmon.ui.demo.ensure_demo_db"
      via: "@st.cache_resource wrapper _demo_db(real_db_path), used only when st.session_state['mode'] == texts.MODE_DEMO"
      pattern: "demo\\.ensure_demo_db\\("
    - from: "src/wbmon/ui/app.py"
      to: "wbmon.ui.theme.inject"
      via: "called once right after st.set_page_config"
      pattern: "theme\\.inject\\(\\)"
    - from: "src/wbmon/ui/charts.py"
      to: "wbmon.ui.series.edge_caps"
      via: "per-point marker arrays in _product_trace"
      pattern: "series\\.edge_caps\\("
---

<objective>
Redesign the Streamlit UI to match the approved white "WB Seller" mockup (body.glass-white: Montserrat, Apple-style white liquid glass, a left menu with 4 sections and the real/demo switch in the top bar). Add a demo mode backed by a separate synthetic SQLite database and labelled «ДЕМО» everywhere. Write docs/DATA_ACCESS.md with the approved text.

Decision IDs D-01..D-15 are the numbered sections 1..15 of 261017-niw-CONTEXT.md, which are LOCKED. D-01 is the look, D-02 the font, D-03 the liquid glass, D-04 the top bar, D-05 the left menu, D-06 the main area, D-07 the empty states, D-08 Позиции, D-09 Выдача, D-10 Журнал сборов, D-11 О сервисе, D-12 the demo mode, D-13 the docs, D-14 the tests and D-15 the constraints.

Purpose: the live site has 0 products because Wildberries' wbaas anti-bot answers 403. The reviewer needs to see the UI working on clearly labelled synthetic data, and needs an honest note on data access.

Output: 3 atomic commits. Task 1 is the shell, Task 2 the demo mode plus the chart, and Task 3 is docs, the Выдача cards, the KPI tiles, the recolour and the full gate.
</objective>

<time_box>
HARD TIME BOX: about 75 minutes in total. The deadline for committed and tested code is about 18:30 МСК.
- Budget: Task 1 ≤ 30 min, Task 2 ≤ 25 min, Task 3 ≤ 20 min.
- Do not explore: every selector, key, signature and test mapping you need is in this plan. Verified facts (Streamlit 1.66.0 in .venv) are marked [verified].
- While iterating, run only the touched test files. Run the full suite once at the end of each task.
- Never skip: docs/DATA_ACCESS.md, the demo guardrail tests, the green full gate.
- Skip first if late: product-card hover polish, then the pimg gradient block, then the ALL_BLOCKED banner tail.
- The components-v2 sliding pill (D-05 stretch goal) is OPTIONAL. Skip it if it would take over 20 minutes. Default: SKIP.
</time_box>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md
@.planning/quick/261017-niw-ui-redesign-demo-mode/261017-niw-CONTEXT.md
@.planning/quick/261017-niw-ui-redesign-demo-mode/approved-mockup.html (CSS in lines 10-160 and the screen builders in lines 264-345; use the body.glass-white variables)
@src/wbmon/ui/app.py
@src/wbmon/ui/texts.py
@src/wbmon/ui/series.py
@src/wbmon/ui/charts.py
@tests/ui/dbbuilder.py (copy the patterns of scenario_visual_check, _visual_order and failed_result into demo.py)
@tests/ui/test_app.py
@tests/ui/test_ui_guardrails.py
@.streamlit/config.toml

Screenshots of the approved state, viewable with Read:
- /private/tmp/claude-501/-Users-fvst-Desktop------------------/7e0f8dee-1196-4375-bcff-e89e74b3bf87/scratchpad/mockup/F2-белое-демо.png (demo)
- .../F4-белое-реальные.png (real mode, all blocked)

<verified_facts>
These were verified in this repo's .venv (Streamlit 1.66.0) by a spike AppTest and by grepping streamlit/static/static/js.
- Keyed widgets AND keyed st.container get the CSS class `st-key-<key>`. Characters outside [a-zA-Z0-9_-] become "-". Use this for all targeting.
- Segmented-control buttons are `button[kind="segmented_control"]` and the selected one is `button[kind="segmented_controlActive"]`. The wrapper has data-testid="stButtonGroup".
- Alerts: the wrapper is `[data-testid="stAlertContainer"]` and the inner content is `[data-testid="stAlertContentWarning|Info|Error"]`. The icon is `[data-testid="stAlertDynamicIcon"]`.
- Other test ids that exist: stSidebar, stSidebarContent, stExpandSidebarButton (the mobile "open menu" button lives in the header), stHeader, stToolbar, stMainMenu, stAppDeployButton, stMainBlockContainer, stMetric, stMetricLabel, stDownloadButton, stMarkdownContainer, stCaptionContainer, stRadio, stRadioGroup, stPlotlyChart. NOT present: stDecoration, stVerticalBlockBorderWrapper.
- theme.font accepts "Name:URL" plus a comma fallback list (see streamlit/config.py theme.font). Nested tables ([theme.sidebar], [[theme.fontFaces]]) would break test_streamlit_config_keys_resolve_and_hide_error_details, which calls get_option on every key. DO NOT add them.
- `persist_state="page"` exists on selectbox, number_input, multiselect, select_slider, radio and segmented_control. It keeps a widget's value while the widget is not rendered (spike: the query survived a switch to another section and back).
- `st.session_state.setdefault("mode", ...)` with a segmented_control(key="mode", required=True) that has NO default works. An on_click callback that sets st.session_state["mode"] works and shows no warning.
- AppTest: `at = AppTest.from_file(APP, default_timeout=30); at.session_state["section"] = X; at.session_state["mode"] = Y; at.run()` presets both values without warnings. `at.radio(key="section")`, `at.selectbox(key="query")` and `at.button(key="demo_on").click().run()` all work from the root, and the root includes the sidebar. st.html shows up in `at.get("html")`.
- GOTCHA: Streamlit "magic" renders bare expression statements. A line like `st.selectbox(...) if cond else None` printed a stray markdown element. Never write bare expressions in app.py.
- Guard facts (tests/ui/test_ui_guardrails.py):
  - FORBIDDEN_CALLS forbid "unsafe_allow_html", "st.html(", "connect_rw", "st.write(", "add_shape" and "add_vrect" in every src/wbmon/ui/*.py.
  - The import allow-list (WBMON_ALLOWED) rejects wbmon.storage.writer, wbmon.storage.migrations, wbmon.models and wbmon.queries.
  - WRITE_WORD_RE rejects the uppercase words INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH, VACUUM and REPLACE ANYWHERE in UI source, including docstrings and CSS.
  - Every st.dataframe needs hide_index=True and placeholder, and st.plotly_chart needs width="stretch".
</verified_facts>

<interfaces>
New or changed contracts. Implement these exactly; the tests depend on them.

texts.py (Task 1):
- NAV_LABEL = "Разделы"
- NAV_OPTIONS = (TAB_POSITIONS, TAB_SNAPSHOT, TAB_RUNLOG, TAB_ABOUT). Reuse the existing TAB_* labels.
- SECTION_TITLES = {TAB_POSITIONS: "Позиции в поиске", TAB_SNAPSHOT: "Выдача на момент сбора", TAB_RUNLOG: "Журнал сборов", TAB_ABOUT: "О сервисе"}
- MODE_LABEL = "Режим данных"
- MODE_REAL = "Реальные данные"
- MODE_DEMO = "✨ Демо"
- MODE_OPTIONS = (MODE_REAL, MODE_DEMO)
- PILL_LIVE = "Сборщик на связи · следующий сбор в {next_attempt} МСК"
- PILL_RUNNING = "Сборщик: идёт сбор (начат {hm} МСК)"
- PILL_STALE = "Сборщик: нет сигнала уже {age}"
- PILL_ABSENT = "Сборщик: нет данных (файл сигнала не найден)"
- PILL_DEMO = "Демо-данные · синтетика для показа интерфейса"
- DEMO_CARD = "✨ **Демо-режим.** Данные сгенерированы, чтобы показать интерфейс. Реальный сбор сейчас блокирует Wildberries."
- DEMO_OFF = "Выключить демо"
- DEMO_ON = "✨ Посмотреть на демо-данных"
- DEMO_SPINNER = "Готовим демо-данные…"
- DEMO_ERROR = "Не удалось подготовить демо-данные. Реальные данные доступны в режиме «Реальные данные»."
- EMPTY_POSITIONS_TITLE = "Данных о товарах пока нет"
- EMPTY_POSITIONS_TEXT = "Как только Wildberries начнёт отвечать, здесь появятся график позиций и характеристики товаров."
- EMPTY_SNAPSHOT_TITLE = "Выдача пока пустая"
- EMPTY_SNAPSHOT_TEXT = "Здесь будет весь топ-{top_n} на момент выбранного сбора: позиция, цена, рейтинг, продавец и реклама."
- CHIP_RUNS = "{n} {noun}"
- CHIP_BLOCKED = "{blocked} из {n} заблокировано"
- CHIP_LAST = "последний — {when} МСК"
- CHIP_LAST_NONE = "сборов ещё не было"
- BANNER_ALL_BLOCKED_TAIL = " Сервис пробует каждый час: {n} {noun} без успешных, последний — {when} МСК."
- subtitle(n, top_n) now returns f"{n} {noun} из таблицы · топ-{top_n} · каждый час · доставка в Москву"
- no_lead_icon(text) -> str: drops the first space-separated token when that token contains no alphanumeric character (the leading emoji), and otherwise returns the text unchanged.

texts.py (Task 2):
- CHART_TITLE = "Динамика позиций"
- DEMO_WATERMARK = "ДЕМО"
- CHART_CAPTION = "Позиция 1 — сверху. Разрыв линии — не ошибка: в этот час товара не было в топ-{top_n}, и по ТЗ выдумывать позицию нельзя. Кружки — где товар вышел из выдачи и где вернулся. ◆ — реклама." (locked D-08 copy with {top_n}). CHART_SUMMARY is unchanged.

texts.py (Task 3):
- KPI_ITEMS = "Товаров в выдаче"
- KPI_PRICE = "Средняя цена, топ-10"
- KPI_RATING = "Средний рейтинг, топ-10"
- KPI_DELIVERY = "Доставка в Москву"
- AD_BADGE = "Реклама"
- SNAPSHOT_TABLE_EXPANDER = "Вся выдача таблицей"
- fmt_rating(v) -> "4,8" (one decimal, comma) or MISSING

series.py:
- (T1) heartbeat_pill(hb_ts, now, latest_run, *, stale_s=600) -> tuple[str, str]. It returns (state, text) with state in {"live", "stale", "none"}:
  - a young running run gives ("live", PILL_RUNNING);
  - hb None gives ("none", PILL_ABSENT);
  - age ≤ stale_s gives ("live", PILL_LIVE with next_attempt(now));
  - otherwise ("stale", PILL_STALE with fmt_span(age)).
  Reuse _young_running. Keep heartbeat_line unchanged; its tests stay.
- (T1) empty_chips(overview, latest_run) -> list[str]. It returns 3 strings:
  - CHIP_RUNS with fmt_int(n_runs) and plural_ru(n_runs, ("сбор", "сбора", "сборов"));
  - CHIP_BLOCKED with latest n_blocked and queries_genitive(latest n_queries) (0 / DEFAULT_N_QUERIES when latest is None);
  - CHIP_LAST with fmt_dt_short of the latest started_at in МСК, or CHIP_LAST_NONE.
- (T1) banner(latest_run, n_ok_runs, blocked_http, now, *, n_runs=None). Only when the ALL_BLOCKED branch fires AND n_runs is not None, it appends BANNER_ALL_BLOCKED_TAIL. Everything else is unchanged, so the existing test_series banner tests stay green.
- (T2) edge_caps(states: Sequence[str]) -> list[bool]. Element i is True when states[i] == FOUND and a neighbour (i-1 or i+1) is ABSENT. Edges of the sequence have no neighbour on that side, and an UNKNOWN neighbour (a failed run) is NOT a cap.
- (T3) kpi_tiles(snapshot) -> dict[str, str]. The keys are KPI_ITEMS, KPI_PRICE, KPI_RATING and KPI_DELIVERY:
  - items: fmt_int(len) of the snapshot;
  - price: fmt_price of the mean price_rub over position ≤ 10 with NaN dropped, or MISSING;
  - rating: fmt_rating of the mean rating over position ≤ 10, or MISSING;
  - delivery: "a–b дн." from the min and max delivery_days of the whole snapshot, "a дн." when they are equal, or MISSING.
- (T3) product_cards(snapshot, limit=40) -> list[dict]. Rows are in position order and each has these keys:
  - position "#N";
  - price fmt_price(price_rub);
  - basic: fmt_price(price_basic_rub) only when price_basic_rub > price_rub, else None;
  - name and seller: the raw text, or MISSING;
  - rating: "★ 4,7 · 2 056", using MISSING parts when unknown;
  - promoted bool.

charts.py:
- (T2) position_figure(..., top_n, watermark: str | None = None). A watermark adds one layout annotation {text, xref/yref "paper", x 0.5, y 0.5, showarrow False, textangle -10, font {size 88, color "rgba(123,16,245,0.07)"}}.
- (T2) SERIES_COLORS = ("#7B10F5", "#F0368F", "#13A89E", "#F59E0B", "#2563EB", "#E5484D", "#0EA5E9", "#65A30D", "#9333EA", "#EA580C", "#0F766E", "#DB2777", "#4F46E5", "#CA8A04", "#475569")
- (T2) GRID_COLOR = "#EEEEF3", AXIS_COLOR = "#E7E7EE", TEXT_COLOR = "#1F1F29", FONT_FAMILY = "Montserrat, sans-serif". Use FONT_FAMILY in position_figure and HEATMAP_FONT.
- (T3) STATUS_STYLE fills: ok "#2BBF7E", partial "#F5B04A", blocked "#EF6B6F", rate_limited "#F08A24", error "#9A9AA8". The glyphs and codes stay.

demo.py (T2), with NO streamlit import:
- demo_db_path() -> Path: Path(tempfile.gettempdir()) / f"wbmon-demo-{uid}" / "demo.sqlite3", where uid = getattr(os, "getuid", lambda: 0)().
- check_demo_path(demo, real) -> Path. It raises ValueError when demo.resolve() == Path(real).resolve(), when it is not inside Path(tempfile.gettempdir()).resolve(), or when demo.parent is a symlink. Otherwise it returns the resolved demo path.
- build_demo_db(path, now) -> None.
- ensure_demo_db(real_db_path, now=None) -> Path. It checks the path, creates the parent with mkdir(mode=0o700, parents=True, exist_ok=True), unlinks path, path-wal and path-shm (missing_ok), then builds and returns the path.

app.py session keys: "section" (radio), "mode" (segmented control), "period", "topn", f"{ns}query", f"{ns}products_{query_id}_{n}", f"{ns}snap_{query_id}_{newest_run}" and f"{ns}csv_{query_id}". ns is "" in real mode and "demo_" in demo mode, so the real-mode keys stay exactly as today.
</interfaces>

<css_contract>
theme.STYLE is ONE constant string: "<style>" + CSS + "</style>", with no f-strings and no runtime data. Copy the values from the mockup body.glass-white and CONTEXT D-01..D-03. The selectors were verified (see verified_facts).

| # | Target | Selector | Declarations |
|---|---|---|---|
| 1 | tokens | :root | --ink #1F1F29; --muted #6E6E80; --line #E7E7EE; --surface #F6F6F9; --primary #7B10F5; --primary-dark #4A0E8F; --primary-soft #F1E6FF; --warn-bg #FFF2D9; --warn-ink #7A4E00; --spring cubic-bezier(.34,1.56,.64,1); --glass-bg linear-gradient(180deg,#fff,#F7F7FA); --glass-shadow 0 0 0 1px rgba(31,31,41,.06), 0 12px 26px -12px rgba(31,31,41,.45), 0 3px 8px rgba(31,31,41,.08), inset 0 1px 1px #fff, inset 0 -5px 10px -6px rgba(31,31,41,.12); --rim linear-gradient(110deg,#fff 0%,#D9D9E3 25%,#fff 50%,#D9D9E3 75%,#fff 100%) |
| 2 | keyframes | @keyframes wbmon-sheen / wbmon-jelly / wbmon-pulse | sheen: to{background-position:150% 0}. jelly: 0%{transform:scale(1,1)} 30%{scale(1.12,.88)} 60%{scale(.96,1.04)} 100%{scale(1,1)}. pulse: copy the mockup @keyframes pulse with the #1FA971 rgba |
| 3 | GLASS recipe (rows 7, 10, 15) | element, ::before, ::after | element: position:relative; background:var(--glass-bg); box-shadow:var(--glass-shadow). ::before: content:""; position:absolute; inset:0; border-radius:inherit; padding:1.5px; pointer-events:none; background:var(--rim); background-size:300% 100%; -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0); -webkit-mask-composite:xor; mask-composite:exclude; animation:wbmon-sheen 6s linear infinite. ::after (top highlight): content:""; position:absolute; left:14%; right:14%; top:3px; height:40%; border-radius:999px; pointer-events:none; background:linear-gradient(180deg,rgba(255,255,255,.95),rgba(255,255,255,0)). Selection bounce: animation:wbmon-jelly .6s var(--spring) |
| 4 | chrome | [data-testid="stToolbar"], [data-testid="stMainMenu"], [data-testid="stAppDeployButton"], footer | display:none !important |
| 4b | header | header[data-testid="stHeader"] / [data-testid="stExpandSidebarButton"] | header: background:transparent; box-shadow:none; pointer-events:none. Expand button: pointer-events:auto (keeps the menu reachable on mobile) |
| 5 | page | [data-testid="stMainBlockContainer"] / [data-testid="stSidebar"] | main: padding-top:1.25rem; max-width:1300px. Sidebar: background:#fff; border-right:1px solid var(--line) |
| 6 | menu label | .st-key-nav_label p | font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:var(--muted) |
| 7 | menu items | .st-key-section [role="radiogroup"] > label | position:relative; width:100%; margin:0; padding:12px 16px; border-radius:14px; color:#3B3B4A. Children: `> label > div:first-child` display:none (the radio dot); `label p` font-size:14px, font-weight:600; `label:hover p` color:var(--primary); `label:has(input:checked)` GLASS + jelly; `label:has(input:checked) p` color:var(--primary-dark), font-weight:700 |
| 8 | top bar | .st-key-topbar / .st-key-topbar strong | bar: border-bottom:1px solid var(--line); padding-bottom:10px; margin-bottom:6px. Title: font-size:16px; font-weight:800 |
| 9 | status pill | [class*="st-key-pill_"] | display:inline-flex; flex-direction:row; align-items:center; gap:8px; padding:7px 12px; border-radius:999px; background:var(--surface); border:1px solid var(--line). `p`: margin:0; font-size:12.5px; font-weight:600; color:var(--muted). `::before`: content:""; width:8px; height:8px; border-radius:50%; flex:none. Dot colours: .st-key-pill_live::before #1FA971 + animation:wbmon-pulse 2s infinite; _stale #F2A33A; _none #A0A0B0; _demo var(--primary) |
| 10 | mode switch | .st-key-mode [data-testid="stButtonGroup"] / buttons | group: padding:4px; border-radius:999px; background:var(--surface); border:1px solid var(--line). `button[kind="segmented_control"]`: border:0; background:transparent; border-radius:999px; font-size:13px; font-weight:700; color:var(--muted). `button[kind="segmented_controlActive"]`: GLASS + jelly; border:0; border-radius:999px; color:var(--primary-dark) |
| 11 | period (WB) | .st-key-period button[kind="segmented_control"] / ...Active | inactive: background:var(--surface); border-color:var(--line); font-weight:600. Active: background:var(--primary-dark); color:#fff; border-color:var(--primary-dark) |
| 12 | info cards | [data-testid="stAlertContainer"] | border-radius:14px; font-weight:500. With :has([data-testid="stAlertContentWarning"]): background:var(--warn-bg); color:var(--warn-ink), and its p too. With ...Error: background:#FDECEC; color:#8A1C1C. With ...Info: background:var(--surface); color:var(--ink). [data-testid="stAlertDynamicIcon"]: width:22px; height:22px; border-radius:50%; color:#fff; display:flex; align-items:center; justify-content:center; background #F08A24 (warning), #EF6B6F (error), var(--primary) (info) |
| 13 | demo card | .st-key-demo_card / .st-key-demo_off button | card: background:var(--primary-soft); color:#4A1A88; border-radius:14px; padding:12px 18px. Button: background:#fff; border:1px solid var(--line); border-radius:12px; font-weight:700 |
| 14 | white cards | [class*="st-key-card_"] | border:1px solid var(--line); border-radius:18px; padding:20px 22px; background:#fff. Use NO border=True on these containers; CSS draws the card |
| 15 | empty state | .st-key-card_empty / .st-key-empty_orb / .st-key-empty_chips p | card: text-align:center; padding:46px 24px. Orb: width:82px; height:82px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:34px; margin:0 auto 16px; GLASS without jelly. Chips p: margin:0; padding:8px 12px; border-radius:999px; background:var(--surface); border:1px solid var(--line); font-size:12.5px; font-weight:600 |
| 16 | KPI tiles | .st-key-kpis [data-testid="stMetric"] | border-radius:18px; border-color:var(--line); padding:16px 18px. [data-testid="stMetricLabel"] p: font-size:12px; font-weight:600; color:var(--muted) |
| 17 | product cards | [class*="st-key-pcard_"] / [class*="st-key-pimg_"] | card: border:1px solid var(--line); border-radius:18px; padding:10px; transition:transform .3s var(--spring), box-shadow .3s. Hover: transform:translateY(-3px); box-shadow:0 18px 30px -18px rgba(31,31,41,.35). pimg: min-height:96px; border-radius:12px; padding:8px; background:linear-gradient(135deg,#F3F0FA,#E9E6F2) |
| 18 | ghost CSV | .st-key-filters [data-testid="stDownloadButton"] button | background:#fff; color:var(--ink); border:1px solid var(--line); border-radius:12px; font-weight:700 |
| 19 | headings | h1 | letter-spacing:-.02em |
| 20 | motion | @media (prefers-reduced-motion:reduce) | *,*::before,*::after{animation:none !important; transition:none !important} |

FONT RULE: never set font-family on *, span, i or any icon element. The theme font already applies Montserrat, and overriding it breaks Material Symbols icons (D-02).
WORD RULE: the CSS must not contain the uppercase words listed in the guard facts.
</css_contract>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: White theme, Montserrat, glass CSS and the new shell (top bar, left menu, info cards, filter row, empty states, white cards) replacing st.tabs</name>
  <files>.streamlit/config.toml, src/wbmon/ui/theme.py, src/wbmon/ui/texts.py, src/wbmon/ui/series.py, src/wbmon/ui/app.py, tests/ui/test_app.py, tests/ui/test_series.py, tests/ui/test_ui_guardrails.py</files>
  <behavior>
    - heartbeat_pill: None gives ("none", PILL_ABSENT). 12 s gives ("live", text starting "Сборщик на связи · следующий сбор в"). 2 h gives ("stale", "Сборщик: нет сигнала уже 2 ч 0 мин"). A young running run gives ("live", "Сборщик: идёт сбор (начат …").
    - empty_chips on scenario-like data (3 runs, the latest blocking 3 of 3) gives ["3 сбора", "3 из 3 запросов заблокировано", "последний — DD.MM HH:MM МСК"]. With no runs it gives the third chip CHIP_LAST_NONE.
    - texts.subtitle(70, 200) == "70 запросов из таблицы · топ-200 · каждый час · доставка в Москву".
    - no_lead_icon("⛔ Последний …") == "Последний …", and no_lead_icon("Wildberries …") is unchanged.
    - banner with n_runs=17, all blocked, no ok runs: the text starts with the old ALL_BLOCKED text and contains "17 сборов без успешных".
    - AppTest real mode, all blocked: the subheader "Данных о товарах пока нет", chip markdown "3 сбора", a button with key demo_on, no plotly chart and no multiselect. Section Выдача shows "Выдача пока пустая".
  </behavior>
  <action>
1. In .streamlit/config.toml [theme] (D-01, D-02):
   - Colours: primaryColor "#7B10F5", backgroundColor "#FFFFFF", secondaryBackgroundColor "#F6F6F9", textColor "#1F1F29", linkColor "#4A0E8F", borderColor "#E7E7EE".
   - Font: font = "Montserrat:https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&display=swap, sans-serif".
   - Shape and type: baseRadius "12px", headingFontSizes ["28px","20px","17px"], headingFontWeights [800,700,700], metricValueFontSize "26px", metricValueFontWeight 800.
   - Status colours: greenColor "#2BBF7E", yellowColor "#F5B04A", yellowTextColor "#7A4E00", redColor "#EF6B6F", orangeColor "#F08A24", grayColor "#6E6E80".
   - chartCategoricalColors = the 15 SERIES_COLORS from interfaces.
   - [client]: toolbarMode = "minimal". Keep showErrorDetails and disableDataExport.
   - Add no nested tables. Keep the other existing keys.

2. Create src/wbmon/ui/theme.py:
   - A docstring stating that this is the only HTML sink of the UI: one constant stylesheet, no runtime data.
   - `STYLE` per css_contract rows 1-20 (D-01, D-03).
   - `def inject() -> None` that calls st.html(STYLE). This is the single st.html call.

3. In texts.py, add the Task-1 constants and functions from interfaces, and change subtitle() (D-04, D-05, D-06, D-07).

4. In series.py, add heartbeat_pill, empty_chips and the banner n_runs kwarg per interfaces. Keep heartbeat_line.

5. Restructure app.py (D-04, D-05, D-06, D-07, D-10, D-11). There must be NO st.tabs and NO bare expression statements. main() order:
   a. st.set_page_config(…, layout="wide", initial_sidebar_state="expanded"), then theme.inject(), then st.session_state.setdefault("mode", texts.MODE_REAL).
   b. Settings.from_env(). On ConfigError keep the existing error and stop.
   c. Sidebar:
      - `with st.sidebar.container(key="nav_label"): st.caption(texts.NAV_LABEL)`;
      - section = st.sidebar.radio(texts.NAV_LABEL, texts.NAV_OPTIONS, key="section", label_visibility="collapsed").
      Nothing else goes in the sidebar. The totals move to Журнал.
   d. Top bar:
      - `with st.container(key="topbar"): left, right = st.columns([3, 1.3], vertical_alignment="center")`.
      - In right: mode = st.segmented_control(texts.MODE_LABEL, texts.MODE_OPTIONS, key="mode", required=True, label_visibility="collapsed"). Give it NO default; it is right-aligned via st.container(horizontal=True, horizontal_alignment="right").
      - In left: a st.container(horizontal=True, vertical_alignment="center") holding st.markdown(f"**{texts.TITLE}**") and the pill.
      - The pill is `st.container(key=f"pill_{state}", width="content")` with st.markdown(text) inside. Render the pill after latest is known. On the early-stop paths (missing, uninitialised, error) render it with latest=None before st.stop(). Guard the pill with _guarded.
   e. Task 1 always reads the real path. Task 2 adds the demo path switch here.
   f. Keep the db_status checks and the data-loading try/except unchanged. Also pass n_runs=overview["n_runs"] to series.banner.
   g. Header via _guarded(render_header, …):
      - st.title(texts.SECTION_TITLES[section]) and st.caption(texts.subtitle(...));
      - then the info card: the existing banner rendered as st.info/st.warning/st.error(texts.no_lead_icon(message), icon=":material/info:" for info, ":material/priority_high:" for warning and error). This is the orange WB card with the round "!" icon, styled by css row 12.
      - Freshness is no longer in the header.
   h. Dispatch the section through _guarded to render_positions_section, render_snapshot_section, render_runlog_section or render_about_section.

6. Empty state (D-07). render_empty_state(kind, overview, latest, top_n) renders, inside st.container(key="card_empty"):
   - st.container(key="empty_orb", width="content") holding st.markdown("🛡️");
   - st.subheader(title);
   - st.caption(text);
   - st.container(key="empty_chips", horizontal=True, horizontal_alignment="center") with one st.markdown(chip, width="content") per series.empty_chips(...);
   - st.button(texts.DEMO_ON, type="primary", key="demo_on", on_click=_set_mode, args=(texts.MODE_DEMO,)).
   _set_mode(value) assigns st.session_state["mode"]. Позиции and Выдача call render_empty_state and return BEFORE the filter row whenever int(overview["n_obs"]) == 0 (this includes zero runs). There is nothing to filter while the DB has no products.

7. Позиции (D-06, keeping today's filter semantics):
   - Filter row: `with st.container(key="filters")` holds st.columns([4, 3, 2, 1.5], vertical_alignment="bottom") with:
     - query st.selectbox(texts.QUERY_LABEL, …, key=f"{ns}query", persist_state="page");
     - period st.segmented_control(texts.PERIOD_LABEL, texts.PERIOD_OPTIONS, default=texts.PERIOD_ALL, required=True, key="period", persist_state="page");
     - topn st.number_input(texts.TOPN_LABEL, 1..20, value=5, key="topn", persist_state="page", help=texts.TOPN_HELP);
     - the 4th column reserved for CSV.
     The products multiselect follows on its own line inside the same "filters" container. Keep its key, default, max_selections and placeholder logic.
   - Move the CSV st.download_button into the reserved column with type="secondary" (the ghost style comes from css row 18). Fill the column after points are computed, keeping the same data, disabled and help logic and the key f"{ns}csv_{query_id}".
   - Below the filters: the chart inside st.container(key="card_chart") with its captions. The characteristics table, TABLE_NULL_CAPTION and CSV_CAPTION go inside st.container(key="card_table"), with the existing st.subheader(texts.CHAR_TITLE). The history expander stays.
   - Keep all existing info, attempts and empty-period paths for DBs that have observations.
   - Delete the GAP_* layout scaffolding that only served the old spacing test, if it becomes unused.

8. Выдача:
   - After the empty-state check, the filter row is st.container(key="filters") holding the same query selectbox (same key and persist_state) and the existing select_slider (keep its key f"{ns}snap_{query_id}_{newest}").
   - Then the existing metrics, plus the table inside st.container(key="card_snapshot") under st.subheader(texts.SNAPSHOT_TITLE) and the query caption.
   - Keep the no-data info and attempts path.

9. Журнал сборов (D-10, D-11):
   - st.container(key="card_summary") holds the freshness st.badge, moved from the header, and the 5 series.totals_lines captions. Render it FIRST, before the "no runs" early return.
   - Then the summary badges and the heatmap block inside st.container(key="card_heatmap"); keep the 480 px scroll container.
   - Then the run table inside st.container(key="card_runs").
   - Drop the RUNLOG_TITLE subheader; the page title says it.

10. О сервисе: the same content inside st.container(key="card_about"). Drop the duplicate ABOUT_TITLE subheader and keep the other subheaders.

11. Update test_ui_guardrails.py (D-14):
    - In test_no_writer_or_html_sinks_anywhere_in_the_ui, allow "st.html(" ONLY for theme.py.
    - Add test_theme_is_the_only_html_sink_and_injects_a_constant:
      - AST: theme.py has exactly one st.html call, and its single argument is ast.Name id "STYLE";
      - "STYLE" is assigned an ast.Constant str;
      - theme.STYLE starts with "<style>", ends with "</style>", and contains no "<script", "javascript:" or "@import";
      - no other UI file contains "st.html(".

12. Adapt tests/ui/test_app.py mechanically (D-14):
    a. Helpers:
       - run_app(section=None, *, mode=None) presets at.session_state["section"] and at.session_state["mode"] before at.run();
       - caption_values also returns [m.value for m in at.markdown], so the pill, chips and totals are found;
       - add menu(at) = at.radio(key="section").
    b. Replace widget access:
       - `at.sidebar.selectbox[0]` and `at.selectbox[0]` become `at.selectbox(key="query")`;
       - `at.sidebar.segmented_control[0]` becomes `at.segmented_control(key="period")`;
       - `at.sidebar.number_input[0]` becomes `at.number_input(key="topn")`;
       - `[t.label for t in at.tabs]` becomes `menu(at).options`.
    c. Route by section:
       - Tests reading run-log content (find_df "Тип", heatmap_specs or heatmap_z, summary badges, freshness badges NO_SUCCESS or "Последний успешный сбор", totals lines) use run_app(texts.TAB_RUNLOG).
       - Snapshot tests (select_slider, metric, SNAPSHOT_FRAME_COLUMNS, "Выдача по запросу") use run_app(texts.TAB_SNAPSHOT).
       - About tests use run_app(texts.TAB_ABOUT).
       - A test that checks two sections switches mid-test with menu(at).set_value(...).run(). This applies to test_a_query_without_data_… (heatmap at the end) and test_header_and_about_… (subtitle, then about).
    d. Remove the leading emoji from the banner needles at the current lines 208, 219, 225, 227, 228 and 1044. The app now strips them with no_lead_icon.
    e. Update text and colour expectations:
       - heartbeat-fresh needle becomes "Сборщик на связи · следующий сбор в";
       - subtitle_caption matches "· каждый час ·", and the expected value becomes "3 запроса из таблицы · топ-200 · каждый час · доставка в Москву";
       - the theme subprocess test expects "#7B10F5".
    f. Rewrite these tests:
       - test_spacing_tokens_of_the_ui_spec_are_applied becomes test_shell_has_the_menu_topbar_and_filters:
         - menu options == FINAL_TABS;
         - the sidebar has no selectbox, segmented_control or number_input;
         - at.segmented_control(key="mode").value == texts.MODE_REAL;
         - query, period and topn exist in main;
         - at.get("html") is non-empty;
         - at.title[0].value == "Позиции в поиске".
       - test_a_header_failure_… monkeypatches series.banner instead of series.freshness and asserts the menu and the query selectbox still render.
       - test_state_matrix: the menu is always present. For non-reaching states, keep the error and info assertion and drop `not at.tabs`.
       - The three all-blocked Позиции/Выдача tests (band-only skeleton, attempts table, snapshot attempts) become empty-state assertions as in behavior. The attempts-table coverage stays in test_a_query_without_data_gets_the_info_and_attempts_while_others_have_a_chart.
       - Zero-runs tests keep their "no chart / no multiselect / no slider / no metric" checks.

13. Add the behavior tests above to test_series.py (heartbeat_pill, empty_chips, subtitle, no_lead_icon, banner tail) and to test_app.py (empty state). Update test_series test_subtitle.

Run tests/ui/test_series.py, tests/ui/test_ui_guardrails.py and tests/ui/test_app.py until green, then the full suite. Commit: "feat(ui): white WB-style theme, left menu and top bar replace tabs (quick 261017-niw)".
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest -q tests/ui && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && test "$(grep -v '^\s*#' src/wbmon/ui/app.py | grep -c 'st\.tabs(')" = "0" && test "$(grep -l 'st\.html(' src/wbmon/ui/*.py)" = "src/wbmon/ui/theme.py"</automated>
  </verify>
  <done>
    - The UI suite and ruff are green.
    - The sidebar holds only the label and the 4-section radio. The top bar has the title, the pill and the mode switch.
    - Real mode with no observations shows the empty-state card.
    - theme.py is the only st.html sink, and it injects only a constant.
    - The commit has been made.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Demo mode on a separate synthetic temp DB with guardrails, plus the chart restyle with gap end-caps and the «ДЕМО» watermark</name>
  <files>src/wbmon/ui/demo.py, src/wbmon/ui/app.py, src/wbmon/ui/charts.py, src/wbmon/ui/series.py, src/wbmon/ui/texts.py, tests/ui/conftest.py, tests/ui/test_demo.py, tests/ui/test_charts.py, tests/ui/test_series.py, tests/ui/test_ui_guardrails.py, tests/ui/test_app.py</files>
  <behavior>
    - edge_caps([F, F, A, F, F]) == [False, True, False, True, False]; edge_caps([A, F, A]) == [False, True, False]; edge_caps([F, U, F]) == [False, False, False]; edge_caps([F]) == [False]; edge_caps([]) == []. Here F = FOUND, A = ABSENT and U = UNKNOWN.
    - In the test_charts "mixed" fixture, product F (NM["F"]) is found in run 5 and absent in the complete run 6. Its marker at run 5 has color "#FFFFFF", line width 2.5, symbol "circle" and size 10. Product A keeps the old sizes, so the diamond test stays green.
    - position_figure(..., watermark="ДЕМО") has exactly one annotation with text "ДЕМО". With watermark None it has none.
    - check_demo_path(p, p) raises ValueError. A path outside tempfile.gettempdir() raises ValueError. demo_db_path() is inside tempfile.gettempdir().
    - ensure_demo_db(db_path) builds the database:
      - 6 queries equal to load_bundled()[:6] and 48 runs;
      - every runs.params_json parses to {"demo": true};
      - max(position) ≤ 30, at least one is_promoted=1 row, and at least 2 runs whose status is not "ok";
      - at least one (query, product) present in a complete run, absent in the next complete run and present again later;
      - no name contains "ТЕСТ".
    - With mode=demo: the real DB file keeps its st_size, st_mtime_ns and runs/run_queries/observations counts after visiting all 4 sections. The demo path differs from the real path.
    - With mode=demo on Позиции: the chart spec layout.annotations contains "ДЕМО", the markdown contains PILL_DEMO and the "Демо-режим." card text, and at.segmented_control(key="mode").value == texts.MODE_DEMO.
    - With real mode and all blocked: clicking button demo_on gives a demo chart with the watermark. Clicking demo_off returns to the real empty state.
  </behavior>
  <action>
1. Create src/wbmon/ui/demo.py (D-12):
   - Pure Python with no streamlit. Its imports are stdlib (contextlib, json, os, tempfile, datetime, pathlib) plus:
     - wbmon.clock: floor_hour, iso_z, utc_now;
     - wbmon.models: ObservationRow, QueryResult;
     - wbmon.queries: load_bundled;
     - wbmon.storage.db: connect_rw;
     - wbmon.storage.migrations: init_db;
     - wbmon.storage.writer: Writer.
   - Docstring and comments must avoid the guarded uppercase words (say "build" or "write", not the SQL verbs).
   - Implement demo_db_path, check_demo_path and ensure_demo_db per interfaces.
   - build_demo_db(path, now):
     - init_db(path), then contextlib.closing(connect_rw(path)) and Writer.
     - queries = list(load_bundled()[:6]) and ids = writer.upsert_queries(queries, iso_z(now)).
     - For k in 1..48: started = floor_hour(now) − (48 − k) h. Call writer.start_run(trigger="scheduler", started_at=iso_z(started), query_source="bundled", params_json=json.dumps({"demo": True}), top_n=30, n_queries=6, slot_utc=iso_z(started)).
     - Each query index qi (1-based) is recorded at started + 20 s × (qi − 1) through writer.record_query(run_id, ids[text], result).
     - writer.finish_run(run_id, iso_z(min(started + 4 min, now))).
   - Deterministic catalogue: no random module and no "ТЕСТ" text. There are 40 products per query, indexed p = 0..39:
     - nm_id = 150_000_000 + qi*1_000_003 + p*7919;
     - name = f"{KINDS[p % 3]} «{DESIGNS[p % 16]}», {FABRICS[(p + qi) % 8]}, {SIZES[qi % 4]}", where:
       - KINDS = ("Постельное бельё", "Комплект постельного белья", "КПБ");
       - DESIGNS: 16 names (Лаванда, Nordic, Royal, Морской бриз, Графит, Ромашки, Облако, Classic, Эвкалипт, Пионы, Шалфей, Мрамор, Сакура, Звёздная ночь, Лён натуральный, Клетка);
       - FABRICS = (сатин, поплин, бязь, перкаль, страйп-сатин, тенсел, мако-сатин, лён);
       - SIZES = ("2-сп.", "евро", "1,5-сп.", "семейный");
     - brand = BRANDS[(p*7 + qi) % 10] and seller_name = SELLERS[(p*5 + qi) % 10], from two 10-item lists of realistic names (Текстиль Дом, Иваново Опт, Nordic Home, Royal Sleep, Бриз, Urban Linen, Soft Cloud, Сонный кот, Ecotex, Cleo; sellers may reuse them with an "ИП"/"ООО" prefix);
     - price_rub = 990 + ((p*389 + qi*97) % 50)*100 + ((k + p) % 3)*10;
     - price_basic_rub = round(price_rub * 1.45, -1);
     - rating = 4.3 + (p*7 % 7)/10;
     - feedbacks = 40 + (p*733 + qi*91) % 5000;
     - delivery_days = 1 + p % 3;
     - is_promoted = 1 when position % 11 == 0;
     - every other ObservationRow field is None.
   - Copy the full field list from tests/ui/dbbuilder.py obs_row.
   - Order per run: sort range(40) by the dbbuilder _visual_order score (p*10 + ((p*37 + k*53 + qi*11) % 301) − 150) and take the top 30. This gives stable leaders and a moving fringe, so real gaps appear.
   - Failed runs:
     - k == 12: every query is blocked (status "blocked", http_status 403, error_class "http_403", pages_fetched 1, duration_ms 300, no rows);
     - k == 31: queries 4-6 are blocked;
     - k == 40: query 2 is partial (first 15 rows, is_complete False, http_status 503, error_class "http_5xx").
     - OK results use pages_fetched 1, is_complete True and duration_ms 900.

2. Wire app.py (D-12):
   - Add `@st.cache_resource(show_spinner=texts.DEMO_SPINNER) def _demo_db(real_db_path: str) -> str` that returns str(demo.ensure_demo_db(real_db_path)).
   - demo_on = mode == texts.MODE_DEMO and ns = "demo_" if demo_on else "".
   - When demo_on, path = _demo_db(str(settings.db_path)) inside try. On (ValueError, OSError, *data.READ_ERRORS): logger.exception, st.error(texts.DEMO_ERROR), st.stop().
   - In demo mode:
     - the pill is ("demo", PILL_DEMO), with no heartbeat read;
     - instead of the banner, the info card is st.container(key="demo_card", horizontal=True, vertical_alignment="center") holding st.markdown(texts.DEMO_CARD) and st.button(texts.DEMO_OFF, key="demo_off", on_click=_set_mode, args=(texts.MODE_REAL,));
     - pass watermark=texts.DEMO_WATERMARK to charts.position_figure.
   - Namespace every data-dependent widget key with ns: query, products, snap and csv. data.py loaders already take path, so the caches never mix.
   - The real path is still opened only through data.py (connect_ro).

3. charts.py (D-08):
   - Apply the palette, colour and FONT_FAMILY constants from interfaces.
   - In _product_trace: caps = series.edge_caps(list(d["state"])) after sorting. Build the marker per point:
     - symbol: "diamond" if promoted else "circle";
     - size: 10 if promoted or cap, else 7;
     - color: "#FFFFFF" if cap and not promoted, else the series colour;
     - line: {"color": series colour, "width": [2.5 if cap and not promoted else 0 per point]}.
     Ads take precedence over caps. The trace count stays the same: NO extra legend entry, because the caption explains the circles.
   - Add the watermark parameter.
   - In app.py the chart card gets st.subheader(texts.CHART_TITLE), and the caption uses the new CHART_CAPTION.

4. In series.py add edge_caps per interfaces. In texts.py add CHART_TITLE, DEMO_WATERMARK and the new CHART_CAPTION.

5. Guardrails (test_ui_guardrails.py):
   - Exclude demo.py from the default import check.
   - Add test_demo_module_is_the_only_writer: demo.py passes import_violations with an extended allow-list, added as an `allowed` parameter that defaults to WBMON_ALLOWED. The extension adds:
     - "wbmon.storage.db": {"connect_ro", "connect_rw"};
     - "wbmon.storage.migrations": {"init_db"};
     - "wbmon.storage.writer": {"Writer"};
     - "wbmon.models": {"ObservationRow", "QueryResult"};
     - "wbmon.queries": {"load_bundled"}.
   - demo.py has no streamlit import. No UI file other than demo.py contains "connect_rw", "Writer" or "init_db". In the FORBIDDEN_CALLS test, only demo.py may contain "connect_rw".
   - demo.py still passes sql_violations and widget_violations.

6. Tests:
   - tests/ui/conftest.py:
     - the autouse fixture also calls st.cache_resource.clear() before and after each test;
     - add a non-autouse fixture demo_tmp(tmp_path, monkeypatch) that calls monkeypatch.setattr(tempfile, "tempdir", str(tmp_path)) and returns tmp_path. Do NOT make it autouse: it would break dbbuilder.assert_temporary for session-scoped perf DBs.
   - Create tests/ui/test_demo.py with the behavior cases. Use run_app-style AppTest presets (copy the small helper locally or import it from ui.test_app), scenario_all_blocked for the real DB, and connect_ro to read the demo DB.
   - In test_series, add the edge_caps cases.
   - In test_charts, add the cap and watermark tests. Update SERIES_COLORS[0] to "#7B10F5" and [14] to "#475569", and layout.font.color to "#1F1F29".
   - grep tests/ui for "Серая полоса" and "Разрыв линии" and update any caption assertions to the new CHART_CAPTION.

Run the touched files, then the full suite. Commit: "feat(ui): demo mode on a separate synthetic temp DB, gap end-caps and ДЕМО watermark (quick 261017-niw)".
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest -q tests/ui && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && test "$(grep -l 'connect_rw' src/wbmon/ui/*.py)" = "src/wbmon/ui/demo.py"</automated>
  </verify>
  <done>
    - The demo switch shows synthetic data with all 4 labels (card, watermark, switch, pill).
    - The demo DB lives only under tempfile.gettempdir()/wbmon-demo-uid and is marked demo in every run.
    - The real-DB-untouched test passes.
    - The chart has hollow end caps and the new palette.
    - Guardrails prove demo.py is the only writer.
    - The suite is green and the commit has been made.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: DATA_ACCESS.md, README and SUBMISSION, then KPI tiles, Выдача product cards and run-log recolour, then the full gate</name>
  <files>docs/DATA_ACCESS.md, README.md, SUBMISSION.md, src/wbmon/ui/data.py, src/wbmon/ui/series.py, src/wbmon/ui/texts.py, src/wbmon/ui/charts.py, src/wbmon/ui/app.py, tests/ui/test_series.py, tests/ui/test_data.py, tests/ui/test_charts.py, tests/ui/test_app.py</files>
  <behavior>
    - docs/DATA_ACCESS.md is identical to lines 186-226 of 261017-niw-CONTEXT.md, the body between the ```markdown fence and the closing fence.
    - kpi_tiles on a 12-row snapshot with known values gives the exact strings: mean price over positions 1-10 with NaN ignored, rating "4,8", delivery "1–3 дн.". An empty or all-NaN price gives "—".
    - product_cards limits to 40 and keeps position order. basic is None when it is not higher than price. promoted mirrors is_promoted == 1.
    - load_snapshot columns include price_basic_rub.
    - AppTest Позиции (mixed) shows 4 metrics with the KPI labels. Выдача shows the card markdown "#1" and the full table inside the expander SNAPSHOT_TABLE_EXPANDER. The hostile-name snapshot test still finds no raw "e.x)".
  </behavior>
  <action>
1. Docs first (D-13). These are the highest value and the cheapest.
   a. Create docs/DATA_ACCESS.md with `sed -n '186,226p' .planning/quick/261017-niw-ui-redesign-demo-mode/261017-niw-CONTEXT.md > docs/DATA_ACCESS.md`, then check that line 1 is "# Доступ к данным Wildberries" and the last line starts with "Для этого задания:". Change nothing.
   b. README.md (keep the H2 list unchanged: test_readme_has_the_required_sections_in_order):
      - In «Что это», line 26 "Четыре вкладки" becomes "Четыре раздела в меню слева" and gets the note that the «Реальные данные | ✨ Демо» switch is in the top bar. Line 32 "Откройте вкладку" becomes "Откройте раздел".
      - Add an H3 "### Демо-режим" at the end of «## Честность данных», 3-5 sentences in Russian:
        - the switch sits in the top bar;
        - the data is synthetic (6 real queries, 48 hourly runs, top-30, gaps, ads and blocked runs) and kept in a separate temporary SQLite database (`src/wbmon/ui/demo.py` builds it under the temp directory on the first demo request of each web process; it never touches the volume);
        - every screen is labelled (purple card, «ДЕМО» watermark, status pill);
        - the working database is opened read-only and is never written.
        It must not contradict the sentence "Синтетические или подставленные данные в рабочую базу не пишутся."
      - Add one line with the link [docs/DATA_ACCESS.md](docs/DATA_ACCESS.md) right after the first line of «## Ограничения». Add a second one at the end of the first paragraph of «## Проверка доступности Wildberries», e.g. «Что пробовал, ограничения и варианты — в docs/DATA_ACCESS.md».
   c. SUBMISSION.md: in «## Сложности и решения», add one bullet after the anti-bot bullet: «**Доступ к данным.** Что пробовал, ограничения и варианты решения — https://github.com/U-Steph/wb-serp-monitor/blob/main/docs/DATA_ACCESS.md; чтобы интерфейс можно было оценить без данных, в нём есть демо-режим на отдельной синтетической базе с пометкой «ДЕМО».» Do NOT change "≈ 5 ч". Stay ≤ 5000 characters (now 2884).
   d. Run `.venv/bin/python -m pytest -q tests/test_docs.py` and commit nothing yet. One commit at the end of the task.

2. KPI tiles (D-08):
   - Add kpi_tiles and fmt_rating per interfaces.
   - In Позиции, after the filters container and when latest_run is not None: snapshot = data.load_snapshot(path, ver, latest_run, query_id). Then `with st.container(key="kpis")`, and for each column of st.columns(4) and each (label, value) in tiles: column.metric(label, value, border=True).

3. Выдача cards (D-09):
   - Add price_basic_rub to SNAPSHOT_SQL right after price_rub and update the expected SNAPSHOT_COLUMNS list in tests/ui/test_data.py.
   - Add product_cards.
   - In Выдача, after the metrics, render the cards in rows of st.columns(4). Each card is col.container(key=f"pcard_{i}") holding:
     - st.container(key=f"pimg_{i}", horizontal=True) with st.markdown(f"**{position}**") and, for promoted cards, st.badge(texts.AD_BADGE, color="violet");
     - st.markdown(f"**{price}**" plus f" ~~{basic}~~" when basic is set). Only our own numbers go here;
     - st.caption(texts.md_literal(name));
     - st.caption(rating + " · " + texts.md_literal(seller)).
     Marketplace text reaches markdown ONLY through md_literal.
   - Move the full table into st.expander(texts.SNAPSHOT_TABLE_EXPANDER) under the cards, with the same st.dataframe arguments.
   - In demo mode the cards come from demo data automatically (same path).

4. Run-log recolour (D-10): apply the STATUS_STYLE fills from interfaces and update the expected colours in tests/ui/test_charts.py (around lines 447-449).

5. Full gate (D-14, D-15):
   - .venv/bin/python -m pytest -q
   - .venv/bin/ruff check src tests
   - .venv/bin/ruff format --check src tests
   - `git diff --name-only 3a4e6cd..HEAD -- src/wbmon | grep -v '^src/wbmon/ui/'` prints nothing (no collector, scheduler, WB client or storage change).
   - `git status --porcelain | grep -E '(^|/)\.env'` prints nothing.
   Commit: "feat(ui): KPI tiles, Выдача product cards, status recolour; docs: DATA_ACCESS note (quick 261017-niw)".

6. OPTIONAL, only if the gate is green and at least 20 minutes remain (D-05 stretch goal, default SKIP): a components-v2 sliding pill for the menu. Otherwise record "skipped (time box)" in the SUMMARY.

Do not push and do not deploy.
  </action>
  <verify>
    <automated>diff <(sed -n '186,226p' .planning/quick/261017-niw-ui-redesign-demo-mode/261017-niw-CONTEXT.md) docs/DATA_ACCESS.md && .venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && test -z "$(git diff --name-only 3a4e6cd..HEAD -- src/wbmon | grep -v '^src/wbmon/ui/')" && grep -q 'docs/DATA_ACCESS.md' README.md && grep -q 'DATA_ACCESS.md' SUBMISSION.md</automated>
  </verify>
  <done>
    - DATA_ACCESS.md is verbatim. README has «Демо-режим» and 2 links; SUBMISSION has 1 link line and keeps the time figure.
    - KPI tiles, product cards and the new status colours render.
    - The full pytest and ruff gate is green. Nothing outside src/wbmon/ui changed in src/wbmon.
    - 3 commits exist, with no push and no deploy.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Wildberries/marketplace text → browser | Product names, brands and sellers are untrusted. They now also appear in the Выдача cards. |
| web process → filesystem | The web process gains a writer, for the demo DB only. The real DB must stay read-only. |
| browser → session_state | The user picks the mode and section. This has no privilege meaning. |
| browser → fonts.googleapis.com | A third-party font fetch made by the reviewer's browser. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-niw-01 | Tampering | demo.py vs the real DB | mitigate | check_demo_path refuses the real path, refuses paths outside tempfile.gettempdir() and refuses a symlinked parent. connect_rw exists only in demo.py (guard test). test_demo proves the real DB keeps its size, mtime_ns and row counts. |
| T-niw-02 | Spoofing/Repudiation | synthetic data shown as real | mitigate | Every run carries params_json {"demo": true}. 4 visible labels: purple card, ДЕМО watermark, ✨ Демо switch state, demo pill. The mode is session-only and never persisted. |
| T-niw-03 | Tampering (XSS / markdown injection) | product cards, chips, pill | mitigate | Marketplace text reaches markdown only through texts.md_literal. Our own numbers and copy are the only other markdown. theme.py is the only st.html call and injects a constant without script, javascript: or @import (AST guard test). |
| T-niw-04 | Elevation | /tmp/wbmon-demo-<uid> pre-created by another local user | mitigate | Per-uid directory with mkdir mode 0o700. A symlinked parent is refused, and the path is resolved before the equality check. |
| T-niw-05 | Denial of service | demo DB rebuild storm | mitigate | @st.cache_resource builds once per web process, about 9k rows. |
| T-niw-06 | Information disclosure | demo build failure | mitigate | Fixed DEMO_ERROR copy; details go to logger.exception only, and showErrorDetails stays "none". |
| T-niw-07 | Information disclosure | Google Fonts fetch | accept | Only the reviewer's IP reaches Google, as on any site with web fonts. A sans-serif fallback is set if it is blocked. |
| T-niw-SC | Tampering | package installs | accept | No new dependencies and no npm/pip/cargo installs in this plan, so no legitimacy gate applies. |
</threat_model>

<source_audit>
| Source | Item | Covered by |
|---|---|---|
| GOAL | Redesign per mockup, demo mode, DATA_ACCESS note | Tasks 1, 2, 3 |
| CONTEXT | D-01 look and tokens, config theme | Task 1 (config, css rows 1, 5, 14) |
| CONTEXT | D-02 Montserrat, icons intact | Task 1 (theme.font, FONT RULE) |
| CONTEXT | D-03 white glass, rim, sheen, highlight, jelly, reduced motion | Task 1 (css rows 2, 3, 7, 10, 15, 20) |
| CONTEXT | D-04 top bar, pill, switch, hidden chrome | Task 1 (steps 5d, 5g; css 4, 8, 9, 10); demo pill in Task 2 |
| CONTEXT | D-05 sidebar menu via st.radio, jelly; sliding pill optional | Task 1 (step 5c, css 6-7); stretch in Task 3 step 6 (skip-if-over-20-min) |
| CONTEXT | D-06 title, subtitle, info cards, filter row, ghost CSV | Task 1 (steps 3, 5g, 7, 8; css 11, 12, 18); demo card in Task 2 |
| CONTEXT | D-07 empty states with real chips and demo button | Task 1 (step 6, tests); the switch flow is tested in Task 2 |
| CONTEXT | D-08 KPI, chart card restyle, end caps, caption, watermark, tables | Task 2 (chart, caps, watermark); Task 3 (KPI) |
| CONTEXT | D-09 Выдача cards top 40, 4 per row, table in expander | Task 3 (step 3) |
| CONTEXT | D-10 run log recolour in white cards | Task 1 (cards), Task 3 (colours); badge colours in Task 1 config |
| CONTEXT | D-11 О сервисе white card | Task 1 (step 10) |
| CONTEXT | D-12 demo: session-only, temp DB via cache_resource, same migrations and Writer, 6 queries/48 runs/top-30, demo flag, guardrails, labels | Task 2 |
| CONTEXT | D-13 DATA_ACCESS.md verbatim, README subsection and links, SUBMISSION line | Task 3 (step 1) |
| CONTEXT | D-14 suite green plus the 5 named new tests | Tasks 1-3 (empty state: T1; demo path, real DB untouched, watermark: T2; end caps: T2) |
| CONTEXT | D-15 no collector/scheduler/WB client changes, no .env, no new deps, texts in texts.py, atomic commits, no push | Task 3 gate (git diff scope, .env check); 3 commits |
| REQ | none (quick task; decisions serve as requirements) | — |
| RESEARCH | none (no research phase) | — |
</source_audit>

<verification>
- `.venv/bin/python -m pytest -q`: all green.
- `.venv/bin/ruff check src tests` and `.venv/bin/ruff format --check src tests`: clean.
- `grep -l 'st\.html(' src/wbmon/ui/*.py` gives only theme.py, and `grep -l 'connect_rw' src/wbmon/ui/*.py` gives only demo.py.
- `diff` of docs/DATA_ACCESS.md against CONTEXT lines 186-226 is empty.
- `git diff --name-only 3a4e6cd..HEAD -- src/wbmon` lists only src/wbmon/ui/* files. No .env files.
- Optional and non-blocking: `.venv/bin/streamlit run src/wbmon/ui/app.py --server.headless=true --server.port=8599 &` from the repo root, then `curl -s localhost:8599/_stcore/health` returns ok; stop it afterwards. Visual QA is done by the orchestrator after deploy.
</verification>

<success_criteria>
- All locked decisions D-01..D-15 are implemented. The only open option is the components-v2 sliding pill, which is optional by decision.
- Real mode on the live, all-blocked database shows: the white shell, the orange "!" card with real numbers, the empty-state card with real chips, and a working «✨ Посмотреть на демо-данных».
- Demo mode shows the full UI on synthetic data with 4 labels, and the real DB is provably untouched.
- 3 atomic commits exist locally, not pushed, and the full gate is green.
</success_criteria>

<output>
Create `.planning/quick/261017-niw-ui-redesign-demo-mode/261017-niw-SUMMARY.md` when done. Include the time per task, whether the sliding pill was skipped, and any selector that needed adjusting.
</output>
