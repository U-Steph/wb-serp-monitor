---
phase: quick-261017-niw
plan: 01
status: complete
completed: 2026-10-17
commits:
  - 19a97c9 feat(ui): white WB-style theme, left menu and top bar replace tabs
  - 80678de feat(ui): demo mode on a separate synthetic temp DB, gap end-caps and ДЕМО watermark
  - 50fd995 feat(ui): KPI tiles, Выдача product cards, status recolour; docs: DATA_ACCESS note
  - 296a5fb fix(ui): CSS hooks of Streamlit 1.66 checked in a real browser
tests: "1287 passed, 1 skipped (docker), 24 deselected (slow); ruff check and format clean"
---

# Quick 261017-niw: UI redesign, demo mode, data-access note

White "WB Seller" UI (Montserrat, liquid-glass menu, top bar with heartbeat pill and real/demo switch), a demo mode on a separate synthetic temp SQLite file, and `docs/DATA_ACCESS.md` with the approved text. About 15 minutes of wall-clock work in total.

## What was built

- **Task 1 (shell).** `theme.py` is the only `st.html` sink and injects one constant stylesheet. The sidebar holds only the label and a 4-section `st.radio`. The top bar has the bold title, a heartbeat pill (`series.heartbeat_pill`) and the `st.segmented_control` mode switch. Filters moved into the main area (`persist_state="page"`). Sections sit in white cards. The empty-state card shows real counts and the «✨ Посмотреть на демо-данных» button. The banner is an orange card without its leading emoji (`texts.no_lead_icon`) and carries the new "N сборов без успешных, последний — …" tail.
- **Task 2 (demo and chart).** `demo.py` has no Streamlit import. It builds 6 queries x 48 hourly runs, top-30, gaps, ads and 3 failed runs through `init_db` + `Writer` under `tempfile.gettempdir()/wbmon-demo-<uid>/`. Every run carries `{"demo": true}`. The path guard refuses the real path, paths outside the temp dir and a symlinked parent. The chart has hollow end caps (`series.edge_caps`), the new palette and font, and a faint «ДЕМО» watermark. Guard tests prove that `demo.py` is the only writer.
- **Task 3 (docs, KPI, cards).** `docs/DATA_ACCESS.md` is byte-identical to CONTEXT lines 186-226 (checked with `diff`). The README has a «Демо-режим» subsection and 2 links. SUBMISSION has 1 line (3148 characters, "≈ 5 ч" untouched). Позиции has 4 KPI tiles. Выдача has top-40 product cards, 4 per row, with the table in an expander. Status colours are updated.

## Time per task

Roughly: Task 1 about 6 min, Task 2 about 4 min, Task 3 about 3 min, browser check and CSS fixes about 4 min (system-clock figures; the plan budgets were 30, 25 and 20 min).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] CSS hooks from the plan do not match Streamlit 1.66's real DOM**
- **Found during:** visual check in a real browser, after the 3 plan tasks were green.
- **Issue:** the plan said `button[kind="segmented_control"]` and `[role="radiogroup"] > label`. In 1.66 the DOM is `button[data-variant="segmented_control"][data-selected="true"]` and `label[data-testid="stRadioOption"][data-selected="true"]`, with the radio dot as the first child div of the label's content. With the plan selectors the menu dots stayed visible and no glass appeared.
- **Fix:** selectors rewritten; the empty-state card is centred; the demo card uses `wrap=False` so the button stays on the right; the CSV column is wider ([4, 3, 2, 2]).
- **Files:** `src/wbmon/ui/theme.py`, `src/wbmon/ui/app.py`. **Commit:** 296a5fb (a 4th commit instead of amending).

**2. [Rule 1 - Bug] The plan's demo rating formula was constant**
- `4.3 + (p*7 % 7)/10` is always 4.3 because `p*7 % 7 == 0`. I used `4.3 + ((p*3 + qi) % 6)/10`, which gives 4.3 to 4.8.

**3. [Rule 3] The `emoji` in the mode option label**
- `st.segmented_control` lifts a leading emoji of an option into an icon, so `at.segmented_control(key="mode").options` reads `["Реальные данные", "Демо"]`. The `.value` is still the full `texts.MODE_DEMO`. The test asserts the value, not the option labels.

### Other notes

- `texts._RUN_FORMS` was renamed to the public `RUN_FORMS` because `series.py` now needs it.
- `STYLE` is built as a literal constant (an AST test requires an `ast.Constant` string).
- The demo card sits in the header on every section, because the plan puts it in the info-card slot.

## Skipped

- **Optional components-v2 sliding pill:** skipped (the menu uses the CSS jelly bounce on the selected item only).
- Product-card hover polish and the `pimg` gradient were kept (both are in the CSS); nothing from the "skip first if late" list was dropped.

## Browser check (done by me, not only by AppTest)

Streamlit 1.66 + gstack browse on a copy of a test database (`scenario_all_blocked`, under the scratchpad, never a real DB). Seen working: glass menu with the selected item, mode switch glass, dark period control, purple demo card, KPI tiles, chart with the watermark region, product cards, orange banner with the "!" icon, centred empty state, run-log cards and the heatmap with the new colours. Not seen: the mobile layout and the sliding-pill stretch goal.

## What the orchestrator should check visually after deploy

1. Real mode on the live (all-blocked) DB: orange card text, the "N сборов без успешных" tail and the empty state with the right counts. Click «✨ Посмотреть на демо-данных» (it must switch the switch to «✨ Демо» too).
2. The first demo request builds the DB (about 0.3 s locally). Check the spinner copy and that the pill reads «Демо-данные · синтетика для показа интерфейса».
3. The «ДЕМО» watermark and the hollow circles on the chart; the 4 product-card rows in Выдача.
4. Fonts: Montserrat is fetched from Google Fonts by the browser; if the reviewer's network blocks it, the fallback is `sans-serif` and nothing breaks.
5. The `/tmp` of the web container must be writable by the app user (the demo writes `/tmp/wbmon-demo-<uid>/`); a failure shows the fixed `DEMO_ERROR` copy, not a crash.
6. Narrow screens: the title and pill wrap onto 2 lines at about 1440 px wide (by design, not a bug).

## Known Stubs

None. The product-card image area is a gradient placeholder by design (the data has no image URLs).

## Threat Flags

None beyond the plan's threat model. `connect_rw` now exists in `src/wbmon/ui/demo.py` only, and a guard test enforces it.

## Self-Check: PASSED

- Files exist: `src/wbmon/ui/theme.py`, `src/wbmon/ui/demo.py`, `tests/ui/test_demo.py`, `docs/DATA_ACCESS.md`.
- Commits exist: 19a97c9, 80678de, 50fd995, 296a5fb.
- Gate: pytest 1287 passed, ruff check and format clean; `git diff 3a4e6cd..HEAD -- src/wbmon` touches only `src/wbmon/ui/`; no `.env*` files.
