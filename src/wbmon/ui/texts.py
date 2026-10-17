"""Russian UI copy, status labels and number/date formatters (stdlib only, no I/O).

Copy follows the approved UI-SPEC verbatim. Numbers use U+202F as the thousands separator; if a
font renders it as a missing glyph, change ``_THOUSANDS`` here and nothing else.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime

from wbmon import REPO_URL

# ------------------------------------------------------------------------------------- page
PAGE_TITLE = "WB SERP Monitor — позиции в поиске Wildberries"
PAGE_ICON = "📈"
TITLE = "Мониторинг позиций в поиске Wildberries"
# Shown only while the database has no run yet (nothing to derive the real number from).
DEFAULT_N_QUERIES = 70

TAB_POSITIONS = "📈 Позиции"
TAB_SNAPSHOT = "🗂 Выдача"
TAB_RUNLOG = "🩺 Журнал сборов"
TAB_ABOUT = "ℹ️ О сервисе"

# ------------------------------------------------------------- left menu, top bar, demo switch
NAV_LABEL = "Разделы"
NAV_OPTIONS = (TAB_POSITIONS, TAB_SNAPSHOT, TAB_RUNLOG, TAB_ABOUT)
SECTION_TITLES: dict[str, str] = {
    TAB_POSITIONS: "Позиции в поиске",
    TAB_SNAPSHOT: "Выдача на момент сбора",
    TAB_RUNLOG: "Журнал сборов",
    TAB_ABOUT: "О сервисе",
}
MODE_LABEL = "Режим данных"
MODE_REAL = "Реальные данные"
MODE_DEMO = "✨ Демо"
MODE_OPTIONS = (MODE_REAL, MODE_DEMO)
PILL_LIVE = "Сборщик на связи · следующий сбор в {next_attempt} МСК"
PILL_RUNNING = "Сборщик: идёт сбор (начат {hm} МСК)"
PILL_STALE = "Сборщик: нет сигнала уже {age}"
PILL_ABSENT = "Сборщик: нет данных (файл сигнала не найден)"
PILL_DEMO = "Демо-данные · синтетика для показа интерфейса"
DEMO_CARD = (
    "✨ **Демо-режим.** Данные сгенерированы, чтобы показать интерфейс. "
    "Реальный сбор сейчас блокирует Wildberries."
)
DEMO_OFF = "Выключить демо"
DEMO_ON = "✨ Посмотреть на демо-данных"
DEMO_SPINNER = "Готовим демо-данные…"
DEMO_ERROR = (
    "Не удалось подготовить демо-данные. Реальные данные доступны в режиме «Реальные данные»."
)
EMPTY_POSITIONS_TITLE = "Данных о товарах пока нет"
EMPTY_POSITIONS_TEXT = (
    "Как только Wildberries начнёт отвечать, здесь появятся график позиций "
    "и характеристики товаров."
)
EMPTY_SNAPSHOT_TITLE = "Выдача пока пустая"
EMPTY_SNAPSHOT_TEXT = (
    "Здесь будет весь топ-{top_n} на момент выбранного сбора: позиция, цена, рейтинг, "
    "продавец и реклама."
)
CHIP_RUNS = "{n} {noun}"
CHIP_BLOCKED = "{blocked} из {n} заблокировано"
CHIP_LAST = "последний — {when} МСК"
CHIP_LAST_NONE = "сборов ещё не было"

# ---------------------------------------------------------------------------------- statuses
STATUS_LABELS: dict[str, str] = {
    "ok": "✅ Успешно",
    "partial": "🟡 Частично",
    "blocked": "⛔ Заблокировано",
    "rate_limited": "🚦 Лимит запросов",
    "error": "❌ Ошибка",
    "abandoned": "⏸ Прерван",
    "running": "⏳ Идёт",
}
TRIGGER_LABELS: dict[str, str] = {
    "scheduler": "По расписанию",
    "manual": "Вручную",
    "probe": "Проверка",
}

# ------------------------------------------------------------------------------ periods
PERIOD_OPTIONS = ("24 ч", "7 дней", "Всё время")
PERIOD_ALL = "Всё время"
NOT_IN_LATEST_SUFFIX = " · не в последней выдаче"

# ------------------------------------------------------------- sidebar filters and positions tab
FILTERS_TITLE = "**Фильтры**"
QUERY_LABEL = "Поисковый запрос"
PERIOD_LABEL = "Период"
TOPN_LABEL = "Товаров по умолчанию"
TOPN_HELP = "Сколько товаров из текущего топа выбирается автоматически"
PICKER_LABEL = "Товары (ID, бренд или название)"
PICKER_PLACEHOLDER = "Выберите товары"
PICKER_CAP_CAPTION = "Показано не более 15 товаров — ограничение для читаемости графика."
EMPTY_NOTHING_SELECTED = "Выберите хотя бы один товар, чтобы увидеть график."
EMPTY_PERIOD = "За выбранный период нет сборов по этому запросу. Выберите «Всё время»."
EMPTY_QUERY_NO_DATA = (
    "По этому запросу пока нет успешных сборов. Статус попыток — на вкладке «Журнал сборов»."
)
CHART_CAPTION = (
    "Позиция 1 — сверху. Разрыв линии — товара не было в топ-{top_n} в этом сборе; "
    "значения не достраиваются. Серая полоса — сбор по запросу не удался, данных за этот час "
    "нет. ◆ — рекламное размещение."
)
CHART_SUMMARY = "Показано товаров: {k}; сборов в периоде: {n}; без данных (сбор не удался): {u}."
SPINNER = "Загрузка данных…"


def query_label(sheet_order: int, text: str) -> str:
    """``NN. text`` with the query wording untouched (ё, Cyrillic х and Latin x stay distinct)."""
    return f"{int(sheet_order):02d}. {text}"


_ASCII_PUNCTUATION = re.compile(r"([!-/:-@\[-`{-~])")
_LINE_BREAKS = re.compile(r"[\r\n\t\f\v\u2028\u2029\x85]+")


def md_literal(text: object) -> str:
    """Untrusted text as a Markdown string that renders exactly as typed (UI-2).

    Every ASCII punctuation character is backslash-escaped (CommonMark allows that for all of
    them), so ``*``, ``_``, ``[``, ``` ` ```, ``<``, ``#``, ``!``, a leading ``01.`` and friends
    stay literal. Line breaks and tabs become one space so the text cannot open a new block
    (heading, list, code). The caller still passes it to ``st.caption`` without HTML.
    """
    return _ASCII_PUNCTUATION.sub(r"\\\1", _LINE_BREAKS.sub(" ", str(text)))


# ------------------------------------------------------------------ tables, CSV and snapshot tab
CHAR_TITLE = "Характеристики"
HISTORY_EXPANDER = "История наблюдений"
TABLE_NULL_CAPTION = "Прочерк «—» — данных нет: поле не получено от Wildberries."
CSV_LABEL = "Скачать CSV"
CSV_CAPTION = (
    "CSV: разделитель «;», десятичная запятая, кодировка UTF-8 с BOM — открывается в Excel. "
    "Ячейки, начинающиеся с «=», «+», «-», «@», экранированы апострофом."
)
CSV_DISABLED_HELP = "Нет данных для выгрузки"
DELIVERY_HELP = "0 — сегодня, 1 — завтра. Оценка по данным Wildberries (time1 + time2)."
AD_HELP = "Рекламное место в последнем наблюдении"
AD_MARK = "✓"
SNAPSHOT_TITLE = "Выдача по запросу"
SNAPSHOT_SLIDER_LABEL = "Момент сбора (МСК)"
SNAPSHOT_EMPTY = "По этому запросу пока нет успешных сборов. Ниже — результат последних попыток."

# Column titles: the single source for every table built in series.py and configured in app.py.
COL_QUERY = "Запрос"
COL_TIME = "Время (МСК)"
COL_ID = "ID"
COL_NAME = "Название"
COL_BRAND = "Бренд"
COL_SELLER = "Продавец"
COL_POSITION = "Позиция"
COL_POSITION_NOW = "Позиция (текущая)"
COL_DELTA = "Δ к прошлому сбору"
COL_BEST = "Лучшая"
COL_WORST = "Худшая"
COL_SHARE = "В выдаче"
COL_PRICE = "Цена, ₽"
COL_RATING = "Рейтинг"
COL_FEEDBACKS = "Отзывов"
COL_DELIVERY = "Доставка, дн."
COL_AD = "Реклама"
COL_LAST_SEEN = "Последний раз в выдаче (МСК)"
COL_STATUS = "Статус"
COL_HTTP = "HTTP"
COL_PAGES = "Страниц"
COL_ITEMS = "Товаров"
COL_REASON = "Причина"
COL_DURATION = "Длительность"

METRIC_COLLECTED = "Товаров собрано"
METRIC_ADS = "Из них рекламных"
METRIC_MEDIAN = "Медианная цена, ₽"
METRIC_STATUS = "Статус сбора"

DELTA_NEW = "новый"

# ----------------------------------------------------------------------- empty / error states
EMPTY_NO_DB = "База данных ещё не создана. Запустите `wbmon init-db`, затем `wbmon collect`."
ERROR_DB_READ = (
    "Не удалось прочитать базу данных. Подождите минуту и обновите страницу; "
    "если ошибка повторяется, проверьте путь WBMON_DB_PATH и журнал контейнера."
)
ERROR_BLOCK = (
    "Не удалось построить этот блок. Остальные вкладки работают. "
    "Подробности записаны в журнал приложения."
)

# --------------------------------------------------------------------------------- banners
BANNER_NO_RUNS = "Сборов ещё не было. Первый сбор — в ближайшие :00 по МСК."
BANNER_RUNNING = "⏳ Идёт сбор (начат {hm} МСК). Данные появятся после завершения."
BANNER_RUNNING_STALE = (
    "⏸ Последний сбор начат {hm} МСК и не завершился — возможно, сборщик остановлен. "
    "Следующая попытка в {next_attempt} МСК."
)
BANNER_PARTIAL = (
    "🟡 Последний сбор завершён частично: успешно {ok} из {n}, заблокировано {blocked}, "
    "с ошибкой {error}. Пропуски на графике за этот сбор помечены серой полосой. "
    "Следующая попытка в {next_attempt} МСК."
)
BANNER_ALL_BLOCKED = (
    "⛔ Wildberries отклоняет запросы сборщика (антибот{http}). Запуски продолжаются каждый час "
    "и фиксируются в «Журнале сборов»; данные не подставляются."
)
BANNER_ALL_BLOCKED_TAIL = (
    " Сервис пробует каждый час: {n} {noun} без успешных, последний — {when} МСК."
)
BANNER_BLOCKED = (
    "⛔ Последний сбор заблокирован Wildberries{http} по {blocked} из {n}. "
    "Данные за этот час не подставляются. Следующая попытка в {next_attempt} МСК."
)
BANNER_ERROR = (
    "❌ Последний сбор завершился ошибкой. Подробности — на вкладке «Журнал сборов». "
    "Следующая попытка в {next_attempt} МСК."
)
BANNER_ABANDONED = (
    "⏸ Последний сбор был прерван (перезапуск или остановка сборщика). "
    "Следующая попытка в {next_attempt} МСК."
)

# ----------------------------------------------------------------- freshness and collector line
FRESHNESS_NONE = "⚪ Успешных сборов пока нет"
FRESHNESS_TEMPLATE = "{emoji} Последний успешный сбор: {when} МСК ({age})"
HEARTBEAT_LIVE = "🟢 Сборщик: на связи, сигнал {age} назад"
HEARTBEAT_STALE = "🟠 Сборщик: нет сигнала уже {age}"
HEARTBEAT_ABSENT = "⚪ Сборщик: нет данных (файл сигнала не найден)"
HEARTBEAT_RUNNING = "🟢 Сборщик: идёт сбор (начат {hm} МСК)"

# ----------------------------------------------------------------------------- sidebar totals
TOTALS_RUNS = "Всего сборов: **{n}**"
TOTALS_OK_RUNS = "Успешных сборов: **{n}**"
TOTALS_FIRST = "Первый сбор: **{when}** МСК"
TOTALS_FIRST_NONE = "Первый сбор: —"
TOTALS_OBS = "Наблюдений в базе: **{n}**"
TOTALS_PRODUCTS = "Уникальных товаров: **{n}**"

# ---------------------------------------------------------------------------------- run log
RUNLOG_TITLE = "Журнал сборов"
RUNLOG_COUNTS_CAPTION = "Количество запросов по статусам; всего запросов в сборе — {n}"
RUNLOG_FOOTER = "Журнал показывает последние 200 сборов; история в базе не удаляется."
NO_RUNS_RUNLOG = "Данных о сборах пока нет — тепловая карта появится после первого сбора."
RUNLOG_RUNNING = "идёт"
MISSING = "—"
HEATMAP_TITLE = "**Состояние запросов по сборам**"
HEATMAP_LEGEND = (
    ":green-badge[✓ Успешно] :yellow-badge[~ Частично] :red-badge[✕ Заблокировано] "
    ":orange-badge[! Лимит запросов] :gray-badge[E Ошибка] пусто — запрос не выполнялся"
)
_BADGE_COLOURS: dict[str, str] = {
    "ok": "green",
    "partial": "yellow",
    "blocked": "red",
    "rate_limited": "orange",
    "error": "gray",
    "abandoned": "gray",
    "running": "blue",
}
RUN_FORMS = ("сбор", "сбора", "сборов")

# ------------------------------------------------------------------------------------- about
# Our own copy only: the only text rendered as Markdown that is not a fixed label or a number.
ABOUT_TITLE = "О сервисе"
ABOUT_INTRO = (
    "Сервис раз в час собирает выдачу Wildberries по поисковым запросам из таблицы "
    "(сейчас их {n}) и хранит историю позиций товаров. Данные не изменяются и не удаляются: "
    "каждая запись — реальное наблюдение."
)
ABOUT_COLLECTED_TITLE = "Что собирается"
ABOUT_COLLECTED = (
    "- Запросы: формулировки из Google-таблицы «Запросы WB» (сейчас {n}; при её "
    "недоступности используется встроенная копия); текст запроса передаётся в Wildberries "
    "без изменений.\n"
    "- Глубина: топ-{top_n} по сортировке «по популярности», регион — Москва.\n"
    "- Расписание: каждый час в :00 по московскому времени (МСК); время в базе хранится в UTC, "
    "на экране — МСК."
)
ABOUT_READING_TITLE = "Как читать данные"
ABOUT_READING = (
    "- Позиция — место в выдаче, считая рекламные места; 1 — первое.\n"
    "- Цена — цена для покупателя без авторизации, минимальная по размерам товара.\n"
    "- Рейтинг и отзывы — по карточке товара в выдаче.\n"
    "- Срок доставки — календарные дни от момента сбора (0 — сегодня, 1 — завтра) по данным "
    "Wildberries (time1 + time2); оценка приблизительная.\n"
    "- ◆ на графике — рекламное размещение."
)
ABOUT_HONESTY_TITLE = "Честность данных"
ABOUT_HONESTY = (
    "- Разрыв линии на графике — товара не было в топе при полном сборе. "
    "Значения не достраиваются.\n"
    "- Серая полоса — сбор по запросу не удался (блокировка, ошибка, неполный ответ): "
    "о товарах за этот час ничего не известно.\n"
    "- Если поле не получено от Wildberries, оно остаётся пустым, а не заменяется нулём."
)
ABOUT_LIMITS_TITLE = "Ограничения"
ABOUT_LIMITS = (
    "- Wildberries может блокировать автоматические запросы (антибот). Сервис не обходит "
    "защиту; заблокированные сборы фиксируются как таковые и видны в «Журнале сборов».\n"
    "- Автоматизированный сбор может не соответствовать правилам площадки; сервис создан как "
    "тестовое задание.\n"
    "- Страница не обновляется сама: новые данные появляются после перезагрузки или любого "
    "действия на странице."
)
ABOUT_SOURCE_TITLE = "Исходный код"
ABOUT_SOURCE = f"[{REPO_URL.removeprefix('https://')}]({REPO_URL})"

_THOUSANDS = " "
_QUERY_FORMS = ("запрос", "запроса", "запросов")


def _missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)  # NaN
    except TypeError:  # pandas NA refuses a truth value
        return True


def fmt_int(value: float) -> str:
    """Integer with U+202F between thousands: ``1 234 567``."""
    return f"{int(value):,}".replace(",", _THOUSANDS)


def fmt_price(value: object) -> str:
    """Price in rubles, or the dash for an unknown value (never a fabricated zero)."""
    if _missing(value):
        return MISSING
    return fmt_int(round(value)) + " ₽"  # type: ignore[call-overload]


def fmt_dt(dt: datetime) -> str:
    """``DD.MM.YYYY HH:MM``."""
    return dt.strftime("%d.%m.%Y %H:%M")


def fmt_dt_short(dt: datetime) -> str:
    """``DD.MM HH:MM``."""
    return dt.strftime("%d.%m %H:%M")


def fmt_hm(dt: datetime) -> str:
    """``HH:MM``."""
    return dt.strftime("%H:%M")


def fmt_span(seconds: float) -> str:
    """A time span: ``42 с`` / ``5 мин`` / ``2 ч 3 мин`` / ``3 дн. 4 ч``."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total} с"
    if total < 3600:
        return f"{total // 60} мин"
    if total < 86400:
        return f"{total // 3600} ч {total % 3600 // 60} мин"
    return f"{total // 86400} дн. {total % 86400 // 3600} ч"


def fmt_age(seconds: float) -> str:
    """How long ago: ``только что`` below a minute, else the span plus ``назад``."""
    if max(0, int(seconds)) < 60:
        return "только что"
    return f"{fmt_span(seconds)} назад"


def fmt_duration(seconds: float | None) -> str:
    """A run duration: ``42 с`` / ``4 мин 12 с`` / ``1 ч 2 мин``; unknown is the dash."""
    if _missing(seconds):
        return MISSING
    total = max(0, int(seconds))  # type: ignore[arg-type]
    if total < 60:
        return f"{total} с"
    if total < 3600:
        return f"{total // 60} мин {total % 60} с"
    return f"{total // 3600} ч {total % 3600 // 60} мин"


def plural_ru(n: int, forms: tuple[str, str, str]) -> str:
    """Pick the Russian noun form for ``n`` (1 / 2-4 / 5-20 pattern)."""
    n = abs(int(n))
    if n % 100 in range(11, 15):
        return forms[2]
    if n % 10 == 1:
        return forms[0]
    if n % 10 in (2, 3, 4):
        return forms[1]
    return forms[2]


def queries_genitive(n: int) -> str:
    """``3 запросов`` / ``21 запроса``: the genitive that follows «из»."""
    n = int(n)
    word = "запроса" if n % 10 == 1 and n % 100 != 11 else "запросов"
    return f"{n} {word}"


def subtitle(n_queries: int, top_n: int) -> str:
    """The one-line caption under the title."""
    noun = plural_ru(n_queries, _QUERY_FORMS)
    return f"{n_queries} {noun} из таблицы · топ-{top_n} · каждый час · доставка в Москву"


def no_lead_icon(text: str) -> str:
    """Drop a leading emoji token (no letter or digit in it); other text is returned unchanged.

    The status banners keep their emoji in the copy for the log and the tests, while the alert
    card draws its own round icon.
    """
    head, sep, rest = text.partition(" ")
    if sep and head and not any(ch.isalnum() for ch in head):
        return rest
    return text


def summary_badges(counts: Mapping[str, int], n: int) -> str:
    """Markdown badge line for the newest ``n`` runs, built only from our labels and integers.

    Zero counts are omitted; when there is nothing to count the line shows a single green zero.
    """
    head = "Последний сбор:" if int(n) == 1 else f"Последние {int(n)} {plural_ru(n, RUN_FORMS)}:"
    badges = [
        f":{_BADGE_COLOURS.get(status, 'gray')}-badge[{STATUS_LABELS.get(status, status)}: {count}]"
        for status, count in counts.items()
        if int(count) > 0
    ]
    return " ".join([head, *(badges or [f":green-badge[{STATUS_LABELS['ok']}: 0]"])])
