# Quick task 261017-niw — UI redesign, demo mode, data-access note

**Decided:** 2026-10-17 with the user in chat (brainstorming + three mockup rounds). Everything below is LOCKED — do not revisit.
**Deadline:** the code must be finished, tested and committed by about 18:30 МСК. The orchestrator deploys it and publishes it afterwards.

## Visual reference (approved)

- `approved-mockup.html` in this folder. Open it with `body.glass-white`, which is the style the user chose: "Белое, как у Apple". Ignore its mock toolbar. Copy colours, radii, shadows and animations from its CSS.
- Screenshots of the approved state, outside the repository and readable by absolute path:
  - `/private/tmp/claude-501/-Users-fvst-Desktop------------------/7e0f8dee-1196-4375-bcff-e89e74b3bf87/scratchpad/mockup/F2-белое-демо.png`: demo mode.
  - `.../F4-белое-реальные.png`: real data, all runs blocked.
- The current live UI before the redesign is `.../0-сейчас-на-сайте.png`.

## Locked decisions

### 1. Look

White, like the WB Seller cabinet. There is no coloured background gradient anywhere. Tokens:

| Token | Value |
|---|---|
| bg | `#FFFFFF` |
| surface (rows, inputs, chips) | `#F6F6F9` |
| border | `#E7E7EE` |
| text | `#1F1F29` |
| muted | `#6E6E80` |
| primary | `#7B10F5` |
| primary-dark | `#4A0E8F` |
| primary-soft | `#F1E6FF` |
| warn-bg | `#FFF2D9` |
| warn-ink | `#7A4E00` |

- Corner radii: cards 18px, controls 12px, pills 999px.
- Update `.streamlit/config.toml` [theme] to these values.

### 2. Font

Use **Montserrat** everywhere (weights 400–800, Cyrillic). Preferred: Streamlit theme `font` plus `[[theme.fontFaces]]`, either from Google Fonts URLs or from static files served by Streamlit. A CSS `@import` from Google Fonts is acceptable. Do NOT break Streamlit's Material icons (elements using the Material Symbols font).

### 3. Liquid glass — white, Apple-style, single colour, NO rainbow

Selected elements look like the mockup's `.glass` under `body.glass-white`:

- **Background:** `linear-gradient(180deg,#fff,#F7F7FA)`.
- **Shadow:** `0 0 0 1px rgba(31,31,41,.06), 0 12px 26px -12px rgba(31,31,41,.45), 0 3px 8px rgba(31,31,41,.08), inset 0 1px 1px #fff, inset 0 -5px 10px -6px rgba(31,31,41,.12)`.
- **Rim:** a 1.5px ring drawn with the mask technique. Its background is `linear-gradient(110deg,#fff 0%,#D9D9E3 25%,#fff 50%,#D9D9E3 75%,#fff 100%)` at `background-size:300% 100%`, animated by `@keyframes sheen{to{background-position:150% 0}}` 6s linear infinite.
- **Highlight:** a top highlight strip.
- **Jelly bounce on selection:** 0.6s `cubic-bezier(.34,1.56,.64,1)`, scale 1 → (1.12,.88) → (.96,1.04) → 1.
- Honour `prefers-reduced-motion`.

### 4. Top bar

- No logo and no "SERP Monitor" name.
- **Left:** bold "Мониторинг позиций в поиске Wildberries", then a status pill "● Сборщик на связи · следующий сбор в HH:00 МСК". It uses the existing heartbeat data: a green pulsing dot when the heartbeat is fresh, amber or grey otherwise, with existing wording for stale states.
- **Right:** the mode switch "Реальные данные | ✨ Демо". Use `st.segmented_control` or an equivalent, with white glass on the selected option.
- Hide Streamlit's default header, toolbar, main menu and footer.

### 5. Left menu (sidebar)

- The sidebar holds only:
  - a small "РАЗДЕЛЫ" label;
  - 4 sections: 📈 Позиции, 🗂 Выдача, 🩺 Журнал сборов, ℹ️ О сервисе.
- Each section is a rectangular item; the selected one gets the white glass with the jelly animation.
- Implement with `st.radio`: hide the label and the radio dots, style it with CSS.
- A sliding pill through a Streamlit components-v2 element is a stretch goal. Do it only if it takes under 20 minutes; otherwise skip it.
- The `st.tabs` layout is replaced by this menu. The filters move from the sidebar to the main area.

### 6. Main area of every section

- **Title:** the section title (28px, 800), with the subtitle "70 запросов из таблицы · топ-200 · каждый час · доставка в Москву" below it.
- **Info card, real mode:** an orange WB-style card with a round "!" icon and the blocked message built from real numbers. Use the existing banner logic and copy, adapted.
- **Info card, demo mode:** a purple card "✨ Демо-режим. Данные сгенерированы, чтобы показать интерфейс. Реальный сбор сейчас блокирует Wildberries.", with a button "Выключить демо".
- **Filter row** on Позиции and Выдача:
  - the query select;
  - the period control, styled like WB: the active option has a primary-dark fill and white text;
  - the products multiselect;
  - the CSV download, as a ghost button.
- Filter behaviour and semantics stay as they are now.

### 7. Empty states (no product data)

A white card containing:
- a white-glass orb with 🛡️;
- the title "Данных о товарах пока нет" and a short explanatory text;
- chips with the real counts: runs, blocked queries, last run time МСК;
- a primary button "✨ Посмотреть на демо-данных" that switches the mode to demo.

The "Выдача" tab uses the same pattern with its own title.

### 8. Позиции with data

- **KPI row:** four white bordered tiles computed from the latest successful run of the selected query — «Товаров в выдаче», «Средняя цена, топ-10», «Средний рейтинг, топ-10», «Доставка в Москву» (min–max days).
- **Chart card "Динамика позиций".** Restyle the existing honest Plotly chart:
  - Montserrat font;
  - palette `#7B10F5, #F0368F, #13A89E, #F59E0B`, then further colours;
  - light grid `#EEEEF3`.
- **Keep unchanged:** reversed Y with 1 on top, NaN gaps, failed-run bands and ad diamonds.
- **New: hollow circle markers.** Add one at every line endpoint that sits next to a gap, i.e. where a product left the top-N or came back. Compute them in the series layer and unit-test them.
- **Caption:** "Позиция 1 — сверху. Разрыв линии — не ошибка: в этот час товара не было в топ-N, и по ТЗ выдумывать позицию нельзя. Кружки — где товар вышел из выдачи и где вернулся. ◆ — реклама."
- **Demo watermark:** a faint rotated "ДЕМО" annotation on the chart in demo mode.
- **Characteristics and history tables:** keep `st.dataframe`, styled by the theme.

### 9. Выдача

- A product-card grid, 4 per row, for the selected run snapshot. Each card shows:
  - a position badge;
  - the price, with the basic price struck through when it is higher;
  - the name;
  - ★ rating · feedbacks and the seller;
  - a «Реклама» badge for promoted slots.
- Show the top 40 cards and keep the existing full table in an expander below.
- Use real data when it exists, demo data in demo mode, and the empty state otherwise.

### 10. Журнал сборов

- Keep the logic. Recolour the heatmap and the statuses:
  - ok `#2BBF7E`;
  - partial `#F5B04A`;
  - blocked `#EF6B6F`;
  - error grey.
- Show it in white cards.

### 11. О сервисе

The same content in a white card.

### 12. Demo mode

**Switching**
- The mode lives in `st.session_state` only and defaults to real. It is never persisted and never touches the collector.

**Data**
- A SEPARATE synthetic SQLite database, built once per web process with `st.cache_resource`.
- Location: a temporary directory, `tempfile.gettempdir()/wbmon-demo-<uid>/demo.sqlite3`.
- It is built through the same migrations and writer code, so every UI path is identical.
- Move or adapt the scenario logic of `tests/ui/dbbuilder.py` (`scenario_visual_check`) into `src/wbmon/ui/demo.py`. `tests/` is not in the Docker image, because `.dockerignore` is an allowlist.
- Contents:
  - about 6 real queries, using the bundled wording;
  - 48 hourly runs ending at the current hour;
  - top-30 per query;
  - realistic Russian bedding product names, brands, sellers, prices, ratings and feedbacks;
  - a few gaps, some ad slots, and 2–3 partial or blocked runs.
- Mark every run's `params_json` with `"demo": true`.

**Guardrails**
- Assert that the demo DB path differs from the configured real DB path.
- Open the real DB read-only, as today.
- Add a test proving demo mode never writes to the real DB file (its size, mtime and row counts stay unchanged).

**Labels in demo mode**
- the purple info card;
- the "ДЕМО" watermark on the chart;
- "✨ Демо" selected in the switch;
- the status pill reading "Демо-данные · синтетика для показа интерфейса".

### 13. Docs

- Add `docs/DATA_ACCESS.md` with exactly the text below.
- README:
  - a short «Демо-режим» subsection: what it is, that the data is synthetic and kept in a separate temporary database, and that the switch is in the top bar;
  - a link to `docs/DATA_ACCESS.md` from «Ограничения» and from «Проверка доступности Wildberries».
- SUBMISSION.md: one line with that link. Keep it under 5000 characters and do not change the time figure.

### 14. Tests

The whole suite must stay green: `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check src tests` and `.venv/bin/ruff format --check src tests`.

Update the UI tests that relied on `st.tabs` or on sidebar filters so they match the new structure. Add tests for:
- the demo DB path is temporary and never equals the real path;
- the real DB is not modified by demo mode;
- the empty state renders in real mode with no observations;
- demo mode renders the chart with the watermark;
- the gap end-cap markers.

### 15. Constraints

- Do not touch the collector, scheduler or WB client: no anti-bot changes of any kind.
- No `.env*` files.
- Streamlit 1.66 APIs only, and no new heavy dependencies (no React or JS build).
- English identifiers, Russian UI copy in `src/wbmon/ui/texts.py`.
- Commit atomically. Do not push and do not deploy; the orchestrator does that.

## docs/DATA_ACCESS.md — approved text (write as is)

```markdown
# Доступ к данным Wildberries

Коротко: поисковая выдача WB с конца сентября 2026 г. закрыта защитой от ботов. Сервис готов и работает, но на любой программный запрос WB отвечает 403. Ниже — что я пробовал, с какими ограничениями столкнулся и какие вижу варианты.

## Что пробовал

Источник — тот же поисковый API, которым пользуется сайт WB (`search.wb.ru/exactmatch/ru/common/v18/search`, регион Москва). Клиент — обычный HTTP (httpx) с честным User-Agent и ссылкой на репозиторий, пауза 1,5 с между запросами. Каждую сеть проверял одним запросом (`wbmon probe`), без повторов.

| Откуда | Когда (МСК) | Ответ WB |
|---|---|---|
| Компьютер разработчика, выход через VPN за рубежом | 16.10.2026 | 403, `server: wbaas`; главная страница — 498 с проверкой браузера |
| Сервер в России (VPS SpaceWeb, Санкт-Петербург) | 17.10.2026, 00:33 | 403, `server: wbaas` |
| Домашний интернет в России, без VPN | 17.10.2026, около 01:10 | 403, `server: wbaas` |

С 17.10 00:33 сервис на сервере сам собирает данные каждый час. Все сборы закончились статусом «заблокировано», товаров в базе 0, выдуманных данных нет. Актуальные цифры — во вкладке «Журнал сборов» работающего интерфейса.

## Ограничения

- Данные отдаются только браузеру, который прошёл проверку на странице WB. От сети и хостинга это не зависит: из трёх разных сетей ответ одинаковый.
- Публичного API поисковой выдачи у WB нет.
- Официальный API для продавцов работает по токену из личного кабинета и, насколько я знаю, отдаёт данные только по товарам самого продавца, а не всю выдачу по любому запросу.

## Варианты решения

| Вариант | Плюсы | Минусы |
|---|---|---|
| Официальный API продавцов (токен клиента-селлера) | Легально и стабильно | Только товары этого продавца, не весь топ-200 выдачи |
| Сервисы аналитики маркетплейсов с API (например, MPStats) | Быстро, есть история | Платно, зависимость от поставщика |
| Браузерное расширение у селлера: фиксирует позиции, когда человек сам ищет на WB | Реальные данные без обхода защиты | Нужен человек, данные не собираются автоматически каждый час |
| Управление браузером (Playwright, подключение к браузеру пользователя) | Технически работает | Это обход защиты: против правил WB, риск блокировки аккаунта и сервера, ломается при обновлениях защиты. Сознательно не делал |
| Ждать снятия ограничения | Ничего не нужно менять | Неизвестно, когда это произойдёт. Сервис уже пробует каждый час и начнёт сохранять товары сам |

## Что уже готово

- Получение данных изолировано в одном модуле (`src/wbmon/wb_client.py`). База, расписание и интерфейс от источника не зависят, поэтому новый источник подключается за несколько часов.
- Блокировки не теряются и не маскируются: каждая попытка видна в журнале сборов, а после трёх блокировок подряд предохранитель останавливает запросы до следующего часа.
- Чтобы интерфейс можно было оценить без данных, в нём есть отдельный демо-режим на синтетической базе с явной пометкой «ДЕМО». Реальная база в нём не используется.

## Что предлагаю

Для этого задания: если у вас есть разрешённый источник данных WB, подключу его к готовому сервису. Для продукта для селлеров: официальный API продавцов для их собственных товаров плюс данные сервисов аналитики для общей картины рынка.
```
