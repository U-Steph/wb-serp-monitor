# Phase 3 — UI Review

**Audited:** 2026-10-16
**Baseline:** 03-UI-SPEC.md (approved; deviations D3-A2 accepted)
**Screenshots:** not captured. The app served on port 8601, but the headless shell only returned Streamlit's loading skeleton (the websocket session did not hydrate under `--screenshot`). The playwright npm package is not installed and was not installed. This is a static code audit of `app.py`, `charts.py`, `texts.py` and `.streamlit/config.toml`. `series.py` and `data.py` were not read line by line. Rendered contrast, the font fallback for U+202F and heatmap glyph colours are unverified: `needs_human_review: true`.

---

## Pillar Scores

| Pillar | Score | Key Finding |
|--------|-------|-------------|
| 1. Copywriting | 3/4 | Copy matches the spec almost verbatim; a few unrecorded wording changes |
| 2. Visuals | 3/4 | Hierarchy and anchors follow the spec; the Plotly charts have no accessible name |
| 3. Color | 3/4 | Theme, palette and status table match; heatmap glyph colours are not set explicitly |
| 4. Typography | 3/4 | Theme matches; `st.text` in the Выдача tab brings in a monospace face |
| 5. Spacing | 2/4 | The lg/xl gap tokens from the spec are not implemented anywhere |
| 6. Experience Design | 3/4 | Good state coverage and guarding; the header is unguarded and the spinner covers only part of the work |

**Overall: 17/24**

---

## Top 3 Priority Fixes

1. **Spacing scale is not applied (WARNING)**
   - `app.py` has only two `gap="small"` calls (lines 46 and 305). The spec's lg (24px) gap before the tabs and xl (32px) gap between blocks in «Позиции» are absent. The sidebar totals lines have no 8px gap.
   - Everything falls back to Streamlit's default 16px, so the chart, characteristics, CSV and history blocks run together.
   - Fix: wrap the major blocks in `st.container(gap="medium")` and the header in `st.container(gap=24)`. Put the sidebar totals in `st.container(border=True, gap="xsmall")`. Or record a deviation in CONTEXT.

2. **`st.text` for the query label in «Выдача» (`app.py:281`) (WARNING)**
   - `st.text` renders in a fixed-width face. That breaks the "Source Sans, 4 sizes" typography contract and looks out of place under a subheader.
   - The reason is sound: the query text is untrusted and must not go through Markdown.
   - Fix: use `st.caption(..., unsafe_allow_html=False)` with the Markdown characters escaped, or `st.markdown` on an escaped string. Or accept the deviation and record it.

3. **Unrecorded copy and behaviour deviations (WARNING)**
   - `texts.TABLE_NULL_CAPTION` reads «Прочерк «—» — …» instead of the spec's «Пустая ячейка — …» (`texts.py:76`).
   - `CSV_CAPTION` gets an extra sentence about apostrophe-escaping of cells.
   - `BANNER_PARTIAL` drops «запросов» after «из {n}».
   - `BANNER_BLOCKED` drops «запросам» and renders the HTTP code through `{http}`.
   - These are probably improvements. Add them to the D3-A2 addendum so the contract and the code agree.

Further issues (lower priority):
- `render_header` and the Settings/DB block are not wrapped in `_guarded`. An exception in `series.freshness`, `series.banner` or `data.read_heartbeat` would blank the page, which contradicts "one failure does not blank the page".
- The spinner wraps only the chart, not the tables (`app.py:254`). The spec says to wrap the first heavy block (chart plus tables).
- Plotly figures have no alt text or aria label. The summary caption mitigates this, and the caption exists.
- Heatmap `textfont` is a single size-14 font with no explicit colour (`charts.py:318`). The spec requires white glyphs and a dark `~` on yellow. Plotly's auto-contrast probably approximates this, but it is not guaranteed.
- On narrow viewports the heatmap left margin of 248px plus the 480px container leaves little plot width. The spec does not support mobile, so this is informational.

---

## Detailed Findings

### Pillar 1: Copywriting (3/4)
- The status labels in `texts.py:25-33` match the spec's status table exactly.
- Tab labels, sidebar labels, the picker label, the slider label, the expander, the spinner, the About prose and the error copy are verbatim (`texts.py:19-22, 46-65, 124-157, 200-240`).
- A grep for generic labels (Submit/OK/Cancel/No data) found none. The only CTA is «Скачать CSV».
- Deviations (WARNING, minor):
  - `texts.py:76` table null caption wording.
  - `texts.py:78-81` extended CSV caption.
  - `texts.py:137-148` partial/blocked banners use «из {n}» and `{http}` without «запросов» or «(HTTP …)» exactly as in the spec.
- `HEARTBEAT_RUNNING` (`texts.py:165`) is the D3-A4 addition and is acceptable.
- The spec's empty-state "heading + body" pair for no runs is a single `st.info` line (`BANNER_NO_RUNS`). The spec's body sentence («Если сборщик запущен…») is not used.
- The English word «Source»/«GitHub» appears only in the repository URL, which is fine.

### Pillar 2: Visuals (3/4)
- The reading order is title, subtitle, freshness badge with heartbeat, banner, then tabs (`app.py:412-426`).
- Tab anchors match the spec: chart 520px, snapshot table 640px, heatmap in a 480px container, About prose.
- Status is never colour-only. Every status has an emoji plus a word. Heatmap cells carry glyphs, ads use a diamond marker plus «реклама» in the hover and a «Реклама» column, and failed runs get a band, a legend entry and a caption.
- The download button is `type="primary"` with a material icon. There are no other icon-only buttons.
- The badge is `st.badge` instead of the markdown `:green-badge[...]`. This is visually equivalent.
- The Plotly figures have no aria name (see above).

### Pillar 3: Color (3/4)
- `.streamlit/config.toml` matches the contract on every colour key. It adds `showErrorDetails = "none"` (a sound addition for a public app).
- `charts.SERIES_COLORS` (`charts.py:22-38`) is identical to the spec's 15-colour palette and to `chartCategoricalColors`. Series 10-15 are dashed (`DASH_FROM = 9`), matching "index >= 9".
- Band fill, grid, axis and heatmap fills match the spec. `STATUS_STYLE` hex values and glyphs match the status table.
- Accent use is limited to the theme's primary button, tabs and segmented control. There are no hardcoded accent colours in `app.py`.
- The hardcoded hex values in `charts.py` are all spec-mandated.
- Open point: per-status glyph colour is not set explicitly in the heatmap (`charts.py:318`).
- Dark theme is out of scope.

### Pillar 4: Typography (3/4)
- The theme declares headings 28/20/20 at weight 600, base 16/400 and metric value 20/600. This is the spec's 4-size, 2-weight contract.
- Plotly sets `font.size = 14` and the Source Sans family for both figures. Heatmap ticks and glyphs are 14.
- No custom CSS or HTML is used.
- Deviation: `st.text` at `app.py:281` renders monospace.
- The sidebar uses `st.markdown("**Фильтры**")` and captions, with no sidebar headings, as specified.
- `_THOUSANDS` is U+202F. Tofu rendering was not verified (needs human review).

### Pillar 5: Spacing (2/4)
- Only `st.columns(..., gap="small")` is used (`app.py:46, 305`). Both match the md token.
- Missing against the spec:
  - lg (24px) between the header and the tabs.
  - xl (32px) between the major blocks in «Позиции».
  - xs/sm gaps for the badge/caption stacks and the sidebar totals lines.
- Fixed heights are right: chart 520, history 400, run table 420, snapshot 640, heatmap container 480, heatmap cell 20px, and `margin = dict(l=248, r=16, t=92, b=4)` (`charts.py:331`). The chart margins are `l=64, r=16, t=16, b=88`, as specified.
- Result: the sizes are right but the rhythm between blocks is not what the spec defines. No arbitrary values are used, so this is a WARNING, not a BLOCKER.

### Pillar 6: Experience Design (3/4)
- State coverage is good:
  - Missing, uninitialised and error DB states stop with the right copy (`app.py:388-395`).
  - Zero runs, empty period, empty query and nothing-selected states each have their own message.
  - The query attempts table is shown when a query has no observations (`app.py:238, 285`).
  - The CSV button is disabled with help text when there are no rows (`app.py:192-193`).
  - The picker is disabled when there are no ids.
  - A deselected segmented control is handled (`required=True` plus an `or PERIOD_ALL` fallback).
  - The picker key includes the query id and N, so the selection resets as the Interaction Contract requires.
  - Every tab body is wrapped in `_guarded` and the technical details go to the log, not the browser.
- There is no destructive action, so no confirmation is needed. The app is read-only.
- Gaps:
  - The header and pre-tab data loading are partly unguarded: the `READ_ERRORS` handler covers loaders, but not the rendering functions.
  - The spinner covers only the chart.
  - No auto-refresh, by design.
- Registry audit: skipped (no `components.json`, no third-party registries).

---

## Files Audited
- `src/wbmon/ui/app.py`
- `src/wbmon/ui/charts.py`
- `src/wbmon/ui/texts.py`
- `.streamlit/config.toml`
- `.planning/phases/03-web-ui/03-UI-SPEC.md`
- `.planning/phases/03-web-ui/03-CONTEXT.md` (D3-A2..A7 only; the SUMMARY files were not read)
- Not read in detail: `series.py`, `data.py`
