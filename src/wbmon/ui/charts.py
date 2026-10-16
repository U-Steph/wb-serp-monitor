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
from collections.abc import Mapping, Sequence

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
