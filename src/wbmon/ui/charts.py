"""Pure Plotly builder of the position chart: honest gaps, failed-run bands, ad diamonds.

No Streamlit, no database, no clock. The figure draws only positions that were observed: every
other point (product not in a complete result, or the query could not be read) is ``None`` so the
line breaks and nothing is interpolated. Runs where the query could not be read are drawn as grey
bands, all in one ``layout.shapes`` list (one call per band is quadratic in Plotly). Marketplace
text is escaped, because Plotly renders a subset of HTML in trace names and hover text.
"""

from __future__ import annotations

import html
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go

from wbmon.ui import series, texts

SERIES_COLORS: tuple[str, ...] = (
    "#1F77B4",
    "#E8590C",
    "#2B8A3E",
    "#7048E8",
    "#C92A2A",
    "#8C564B",
    "#D6336C",
    "#0C8599",
    "#5C940D",
    "#364FC7",
    "#B26A00",
    "#495057",
    "#A61E4D",
    "#087F5B",
    "#862E9C",
)
DASH_FROM = 9  # series 10..15 are dashed so that 15 hues are not the only signal
BAND_FILL = "rgba(134,142,150,0.20)"
HALF_RUN = series.HALF_RUN
CHART_HEIGHT = 520
CHART_CONFIG: dict[str, object] = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
}
LEGEND_AD = "◆ рекламное размещение"
LEGEND_BAND = "сбор не удался"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
AD_FRAGMENT = "<br>реклама"
HOVER_TEMPLATE = (
    "%{x|%d.%m.%Y %H:%M} МСК<br>ID %{customdata[0]}<br>Позиция %{y}"
    "<br>Цена %{customdata[1]}%{customdata[2]}<extra>%{fullData.name}</extra>"
)
GRID_COLOR = "#E9ECEF"
AXIS_COLOR = "#CED4DA"
TEXT_COLOR = "#1A1D21"
GREY = "#868E96"
Y_TITLE = "Позиция в выдаче"
X_TITLE = "Время сбора, МСК"

# Heatmap: code -> (integer code, fill, glyph). The glyph is the non-colour channel of the status.
STATUS_STYLE: dict[str, tuple[int, str, str]] = {
    "ok": (1, "#2B8A3E", "✓"),
    "partial": (2, "#F2C037", "~"),
    "blocked": (3, "#C92A2A", "✕"),
    "rate_limited": (4, "#D9480F", "!"),
    "error": (5, "#6C757D", "E"),
}
MISSING_STYLE: tuple[int, str, str] = (0, "#FFFFFF", "")
# Glyph colour per status (UI-SPEC status table): white on the dark fills, ink on the amber one.
# A heatmap trace takes ONE text colour, so glyphs are drawn as text-mode scatter overlays, one
# trace per colour (UI-4) instead of relying on Plotly's automatic contrast.
GLYPH_LIGHT = "#FFFFFF"
GLYPH_DARK = "#1A1D21"
GLYPH_COLORS: dict[str, str] = {
    "ok": GLYPH_LIGHT,
    "partial": GLYPH_DARK,
    "blocked": GLYPH_LIGHT,
    "rate_limited": GLYPH_LIGHT,
    "error": GLYPH_LIGHT,
}
HEATMAP_FONT = {"size": 14, "family": "Source Sans Pro, Source Sans 3, sans-serif"}
MISSING_LABEL = "пусто — запрос не выполнялся"
HEATMAP_CELL_PX = 20
HEATMAP_FRAME_PX = 96  # top + bottom margin
HEATMAP_LABEL_CHARS = 24
HEATMAP_BACKGROUND = "#E9ECEF"


def esc(text: object) -> str:
    """Escape marketplace text for Plotly (markup becomes entities); None and NA become empty."""
    if text is None or pd.isna(text):  # type: ignore[arg-type]
        return ""
    return html.escape(str(text), quote=False)


def _tick_step(bottom: float) -> int:
    if bottom <= 40:
        return 5
    if bottom <= 120:
        return 10
    if bottom <= 260:
        return 20
    return 50


def y_axis_spec(positions: Sequence[int], top_n: int) -> dict:
    """Reversed axis (position 1 on top) with headroom and integer ticks that start at 1.

    The top bound is -0.5 whenever padding would reach 0 or below, so a marker at position 1 is
    never clipped and no tick at or below 0 is ever shown.
    """
    if positions:
        lo, hi = min(positions), max(positions)
        pad = max(2, math.ceil(0.05 * (hi - lo)))
        top = lo - pad if lo - pad > 0 else -0.5
        bottom = hi + pad
    else:
        top, bottom = -0.5, top_n + 0.5
    step = _tick_step(bottom)
    ticks = [1] + [v for v in range(step, int(bottom) + 1, step) if v - 1 >= step / 2]
    return {
        "title": {"text": Y_TITLE},
        "range": [bottom, top],
        "tickmode": "array",
        "tickvals": [v for v in ticks if top <= v <= bottom],
        "tickformat": "d",
        "zeroline": False,
        "gridcolor": GRID_COLOR,
        "linecolor": AXIS_COLOR,
    }


def band_shapes(times: pd.Series) -> list[dict]:
    """One grey rectangle per failed run, as a single list (assigned once to the layout)."""
    starts = (times - HALF_RUN).dt.strftime(TIME_FORMAT).tolist()
    ends = (times + HALF_RUN).dt.strftime(TIME_FORMAT).tolist()
    return [
        {
            "type": "rect",
            "xref": "x",
            "yref": "paper",
            "x0": x0,
            "x1": x1,
            "y0": 0,
            "y1": 1,
            "fillcolor": BAND_FILL,
            "line": {"width": 0},
            "layer": "below",
        }
        for x0, x1 in zip(starts, ends, strict=True)
    ]


def _product_trace(
    index: int, nm_id: int, d: pd.DataFrame, name: str
) -> tuple[go.Scatter, list[int]]:
    """One product: positions for FOUND points, ``None`` for everything else."""
    d = d.sort_values(["t_msk", "run_id"])
    found = [None if pd.isna(v) else int(v) for v in d["position"]]
    promoted = [
        bool(p == 1) if not pd.isna(p) else False  # type: ignore[arg-type]
        for p in d["is_promoted"]
    ]
    colour = SERIES_COLORS[index % len(SERIES_COLORS)]
    trace = go.Scatter(
        x=d["t_msk"].dt.strftime(TIME_FORMAT).tolist(),
        y=found,
        mode="lines+markers",
        connectgaps=False,
        name=esc(name),
        line={"color": colour, "width": 2, "dash": "dash" if index >= DASH_FROM else "solid"},
        marker={
            "color": colour,
            "size": [10 if p else 7 for p in promoted],
            "symbol": ["diamond" if p else "circle" for p in promoted],
        },
        customdata=[
            [int(nm_id), texts.fmt_price(price), AD_FRAGMENT if p else ""]
            for price, p in zip(d["price_rub"], promoted, strict=True)
        ],
        hovertemplate=HOVER_TEMPLATE,
    )
    return trace, [v for v in found if v is not None]


def position_figure(
    points: pd.DataFrame,
    nm_ids: Sequence[int],
    band_times: pd.Series,
    names: Mapping[int, str],
    x_range: tuple[pd.Timestamp, pd.Timestamp],
    top_n: int,
) -> go.Figure:
    """The position chart: one trace per selected product plus two legend-only entries."""
    fig = go.Figure()
    shown: list[int] = []
    for index, nm_id in enumerate(nm_ids):
        d = points[points["nm_id"] == nm_id]
        trace, positions = _product_trace(index, int(nm_id), d, names.get(nm_id, f"ID {nm_id}"))
        fig.add_trace(trace)
        shown += positions
    fig.add_trace(
        go.Scatter(
            x=[None],
            y=[None],
            mode="markers",
            name=LEGEND_AD,
            marker={"symbol": "diamond", "size": 10, "color": GREY},
            hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[None],
            y=[None],
            mode="markers",
            name=LEGEND_BAND,
            marker={"symbol": "square", "size": 12, "color": "rgba(134,142,150,0.5)"},
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        template="plotly_white",
        height=CHART_HEIGHT,
        hovermode="closest",
        shapes=band_shapes(band_times),
        xaxis={
            "title": {"text": X_TITLE},
            "type": "date",
            "range": [t.strftime(TIME_FORMAT) for t in x_range],
            "tickformat": "%d.%m<br>%H:%M",
            "hoverformat": "%d.%m.%Y %H:%M",
            "gridcolor": GRID_COLOR,
            "linecolor": AXIS_COLOR,
        },
        yaxis=y_axis_spec(shown, top_n),
        legend={"orientation": "h", "yanchor": "top", "y": -0.18, "xanchor": "left", "x": 0},
        margin={"l": 64, "r": 16, "t": 16, "b": 88},
        font={
            "size": 14,
            "color": TEXT_COLOR,
            "family": "Source Sans Pro, Source Sans 3, sans-serif",
        },
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
    )
    return fig


def heatmap_colorscale() -> list[list]:
    """A stepped scale: code ``i`` of 6 owns the band ``[i/6, (i+1)/6]`` and one flat colour."""
    styles = [MISSING_STYLE, *sorted(STATUS_STYLE.values())]
    scale: list[list] = []
    for i, (_code, colour, _glyph) in enumerate(styles):
        scale.append([i / 6, colour])
        scale.append([(i + 1) / 6, colour])
    return scale


def _msk_text(started_at: object, fmt: Callable[[datetime], str]) -> str:
    stamp = series.to_msk_one(str(started_at))
    return texts.MISSING if stamp is None else fmt(stamp)


def _tick_label(sheet_order: int, text: str) -> str:
    cut = len(text) > HEATMAP_LABEL_CHARS
    return esc(f"{int(sheet_order):02d}. {text[:HEATMAP_LABEL_CHARS]}" + ("…" if cut else ""))


def _hover(query_text: str, started: str, status: object, items: object, http: object) -> str:
    """Hover text of one cell; ``status`` None means no attempt row (never fabricated numbers)."""
    label = MISSING_LABEL if status is None else texts.STATUS_LABELS.get(str(status), esc(status))
    items_text = texts.MISSING if pd.isna(items) else str(int(items))  # type: ignore[arg-type]
    http_text = texts.MISSING if pd.isna(http) else str(int(http))  # type: ignore[arg-type]
    return (
        f"{esc(query_text)}<br>Сбор {started} МСК<br>Статус: {label}"
        f"<br>Товаров: {items_text}<br>HTTP: {http_text}"
    )


def status_heatmap(runs: pd.DataFrame, matrix: pd.DataFrame, queries: pd.DataFrame) -> go.Figure:
    """Query x run health grid: colour AND glyph per attempt, white and empty where none was made.

    ``runs`` arrives newest first and is drawn oldest to newest; ``queries`` is in sheet order.
    Axes are integer positions with custom tick text, so two queries or two runs that share a label
    can never be merged into one row or column.
    """
    run_rows = list(runs.iloc[::-1].itertuples(index=False))
    query_rows = list(queries.itertuples(index=False))
    # one attempt per (run, query): the schema makes the pair UNIQUE
    cells = {(int(r.query_id), int(r.run_id)): r for r in matrix.itertuples(index=False)}
    started = {int(r.run_id): _msk_text(r.started_at, texts.fmt_dt) for r in run_rows}
    z: list[list[int]] = []
    hover: list[list[str]] = []
    overlays: dict[str, tuple[list[int], list[int], list[str]]] = {
        GLYPH_LIGHT: ([], [], []),
        GLYPH_DARK: ([], [], []),
    }
    for row_index, q in enumerate(query_rows):
        z_row: list[int] = []
        hover_row: list[str] = []
        for col_index, r in enumerate(run_rows):
            cell = cells.get((int(q.query_id), int(r.run_id)))
            if cell is None:
                code = MISSING_STYLE[0]
                hover_row.append(_hover(str(q.text), started[int(r.run_id)], None, None, None))
            else:
                key = str(cell.status) if str(cell.status) in STATUS_STYLE else "error"
                code, _colour, glyph = STATUS_STYLE[key]
                xs, ys, glyphs = overlays[GLYPH_COLORS[key]]
                xs.append(col_index)
                ys.append(row_index)
                glyphs.append(glyph)
                hover_row.append(
                    _hover(
                        str(q.text),
                        started[int(r.run_id)],
                        cell.status,
                        cell.items_collected,
                        cell.http_status,
                    )
                )
            z_row.append(code)
        z.append(z_row)
        hover.append(hover_row)

    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=list(range(len(run_rows))),
            y=list(range(len(query_rows))),
            customdata=hover,
            hovertemplate="%{customdata}<extra></extra>",
            colorscale=heatmap_colorscale(),
            zmin=-0.5,
            zmax=5.5,
            showscale=False,
            xgap=1,
            ygap=1,
        )
    )
    for colour, (xs, ys, glyphs) in overlays.items():
        if xs:
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    text=glyphs,
                    mode="text",
                    textposition="middle center",
                    textfont={**HEATMAP_FONT, "color": colour},
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
    fig.update_layout(
        height=HEATMAP_CELL_PX * len(query_rows) + HEATMAP_FRAME_PX,
        margin={"l": 248, "r": 16, "t": 92, "b": 4},
        plot_bgcolor=HEATMAP_BACKGROUND,
        paper_bgcolor="#FFFFFF",
        font={**HEATMAP_FONT, "color": TEXT_COLOR},
        xaxis={
            "side": "top",
            "tickangle": -90,
            "tickmode": "array",
            "tickvals": list(range(len(run_rows))),
            "ticktext": [_msk_text(r.started_at, texts.fmt_dt_short) for r in run_rows],
            "showgrid": False,
        },
        yaxis={
            "autorange": "reversed",
            "tickmode": "array",
            "tickvals": list(range(len(query_rows))),
            "ticktext": [_tick_label(int(q.sheet_order), str(q.text)) for q in query_rows],
            "showgrid": False,
        },
    )
    return fig
