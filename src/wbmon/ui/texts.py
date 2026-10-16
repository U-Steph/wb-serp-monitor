"""Russian UI copy, status labels and number/date formatters (stdlib only, no I/O).

Copy follows the approved UI-SPEC verbatim. Numbers use U+202F as the thousands separator; if a
font renders it as a missing glyph, change ``_THOUSANDS`` here and nothing else.
"""

from __future__ import annotations

from datetime import datetime

# ------------------------------------------------------------------------------------- page
PAGE_TITLE = "WB SERP Monitor — позиции в поиске Wildberries"
PAGE_ICON = "📈"
TITLE = "Мониторинг позиций в поиске Wildberries"

TAB_POSITIONS = "📈 Позиции"
TAB_SNAPSHOT = "🗂 Выдача"
TAB_RUNLOG = "🩺 Журнал сборов"
TAB_ABOUT = "ℹ️ О сервисе"

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
BANNER_PARTIAL = (
    "🟡 Последний сбор завершён частично: успешно {ok} из {n}, заблокировано {blocked}, "
    "с ошибкой {error}. Пропуски на графике за этот сбор помечены серой полосой. "
    "Следующая попытка в {next_attempt} МСК."
)
BANNER_ALL_BLOCKED = (
    "⛔ Wildberries отклоняет запросы сборщика (антибот{http}). Запуски продолжаются каждый час "
    "и фиксируются в «Журнале сборов»; данные не подставляются."
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
    return f"{n_queries} {noun} · топ-{top_n} · Москва · сбор каждый час"
