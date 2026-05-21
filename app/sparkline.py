"""Sparkline mini-chart helpers for the Dashboard Pro Pack.

This module provides the fetch + color logic for 30-day price sparklines
rendered next to gainers, losers, and watchlist rows. The Streamlit
render function is added in a later task (3.3); this file currently
exposes:

- module-level constants for cache TTL, request shape, dimensions, and
  colors that match the dashboard's existing positive/negative palette
- ``fetch_sparkline_series(ticker)`` — a ``st.cache_data``-cached wrapper
  around Yahoo Finance's ``/v8/finance/chart`` endpoint that returns the
  list of valid daily closes (``None`` values stripped) for the trailing
  month
- ``sparkline_color(series)`` — pure helper that picks the line color
  based on whether the last close is at or above the first close

Validates: Requirements 2.2, 2.3, 2.5, 2.6
"""

from __future__ import annotations

import plotly.graph_objects as go
import requests
import streamlit as st


# --- Configuration constants (Requirements 2.2, 2.3, 2.5, 2.6) ---
SPARKLINE_TTL_S = 600
SPARKLINE_RANGE = "1mo"
SPARKLINE_INTERVAL = "1d"
SPARKLINE_W = 120
SPARKLINE_H = 40

# Dashboard positive/negative palette (tailwind emerald-500 / red-500).
COLOR_POSITIVE = "#10b981"
COLOR_NEGATIVE = "#ef4444"

# Match the User-Agent used by ``streamlit_app.py`` so Yahoo treats our
# requests the same way it treats the rest of the dashboard's traffic.
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_REQUEST_TIMEOUT_S = 8


@st.cache_data(ttl=SPARKLINE_TTL_S)
def fetch_sparkline_series(ticker: str) -> list[float]:
    """Fetch up to ~30 daily closes for ``ticker`` from Yahoo Finance.

    Calls ``/v8/finance/chart`` with ``interval=1d`` and ``range=1mo``,
    strips any ``None`` values from the close series, and returns the
    remaining floats in chronological order. Any exception (network
    error, non-200 response, malformed JSON, missing fields) is
    swallowed and an empty list is returned so callers can render a
    placeholder without special-casing every failure mode.

    Validates: Requirements 2.2, 2.3
    """
    url = _CHART_URL.format(ticker=ticker)
    params = {"interval": SPARKLINE_INTERVAL, "range": SPARKLINE_RANGE}
    try:
        resp = requests.get(
            url,
            params=params,
            headers=YAHOO_HEADERS,
            timeout=_REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        payload = resp.json()
        result = payload["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        return [float(c) for c in closes if c is not None]
    except Exception:
        return []


def sparkline_color(series: list[float]) -> str:
    """Return the line color for ``series`` based on its direction.

    Returns ``COLOR_POSITIVE`` when the last close is greater than or
    equal to the first close, ``COLOR_NEGATIVE`` otherwise. Behavior is
    undefined for series shorter than 2 elements; callers must check
    length and render the dash placeholder before calling this helper.

    Validates: Requirements 2.5, 2.6
    """
    return COLOR_POSITIVE if series[-1] >= series[0] else COLOR_NEGATIVE


# Placeholder rendered when fewer than two valid closes are available.
SPARKLINE_PLACEHOLDER = "—"


def render_sparkline_for_series(series: list[float]) -> None:
    """Render a sparkline for a pre-fetched ``series`` of daily closes.

    This is the pure renderer half of the sparkline pair: it accepts the
    already-fetched list of closes and draws either a Plotly line chart
    or the dash placeholder, but performs no I/O of its own. Splitting
    the renderer this way lets callers that already hold a series (for
    example, tests or a batch-fetched view) reuse the rendering code
    without re-hitting the cache.

    When ``series`` has fewer than two valid closes the function renders
    the dash placeholder ``"—"`` via ``st.markdown`` (Requirement 2.8).
    Otherwise it builds a ``plotly.graph_objects.Figure`` containing a
    single ``Scatter`` trace, hides the axes and legend, zeroes the
    margins, and constrains the figure to ``SPARKLINE_W`` × ``SPARKLINE_H``
    pixels (Requirements 2.4, 2.7). The line color is chosen by
    ``sparkline_color`` (Requirements 2.5, 2.6) and the chart is rendered
    with ``st.plotly_chart`` at its fixed size with the Plotly mode bar
    suppressed.

    Validates: Requirements 2.1, 2.4, 2.7, 2.8
    """
    if len(series) < 2:
        st.markdown(SPARKLINE_PLACEHOLDER)
        return

    color = sparkline_color(series)
    fig = go.Figure(
        data=[
            go.Scatter(
                x=list(range(len(series))),
                y=series,
                mode="lines",
                line=dict(color=color, width=1.5),
                hoverinfo="skip",
            )
        ]
    )
    fig.update_xaxes(visible=False, showgrid=False, zeroline=False)
    fig.update_yaxes(visible=False, showgrid=False, zeroline=False)
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
        width=SPARKLINE_W,
        height=SPARKLINE_H,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(
        fig,
        use_container_width=False,
        config={"displayModeBar": False},
    )


def render_sparkline(ticker: str) -> None:
    """Fetch and render the 30-day sparkline for ``ticker``.

    Thin wrapper around ``fetch_sparkline_series`` plus
    ``render_sparkline_for_series``: it pulls the cached daily closes for
    the trailing month and hands them off to the pure renderer. When the
    ticker has fewer than two valid closes (network failure, brand-new
    listing, etc.) the renderer draws the dash placeholder rather than
    an empty chart (Requirement 2.8).

    Validates: Requirements 2.1, 2.4, 2.7, 2.8
    """
    series = fetch_sparkline_series(ticker)
    render_sparkline_for_series(series)
