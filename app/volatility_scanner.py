"""Volatility Scanner — pure logic + Streamlit renderer.

Tasks 10.1 and 10.4 of the Dashboard Pro Pack spec live here.

The pure :func:`scan` function performs zero outbound HTTP calls. It
consumes the same in-memory ``quotes`` dict produced by
``streamlit_app.fetch_all_data`` and the headline list produced by
``streamlit_app.fetch_macro_headlines``, and returns a ranked list of
tickers whose absolute intraday percent change meets a caller-supplied
threshold.

The Streamlit renderer :func:`render_volatility_scanner` (task 10.4) draws
the section: a threshold ``number_input`` and a table of qualifying rows.
It lazy-imports ``streamlit_app.match_to_tickers`` to keep this module
free of import-time cycles with the main app.

Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import pandas as pd
import streamlit as st

# --- Threshold bounds (used by the renderer's number_input in task 10.4) ----

VOLATILITY_MIN: float = 1.0
VOLATILITY_MAX: float = 20.0
VOLATILITY_DEFAULT: float = 3.0

# --- Default exclusion sets ---------------------------------------------------
# Mirrors the constants in ``streamlit_app.py``. They are kept local as
# defaults so this module never imports from ``streamlit_app`` (which would
# create a circular import) but the caller can still override them when the
# upstream constants change.
DEFAULT_INDICES: frozenset[str] = frozenset({"SPY", "QQQ", "DIA"})
DEFAULT_MACRO_TICKERS: frozenset[str] = frozenset({"^TNX", "^VIX", "XLF"})

REASON_NONE: str = "—"
REASON_MAX_LEN: int = 100


def _default_match_to_tickers(title: str, tickers: Sequence[str]) -> list[str]:
    """Fallback matcher used when the caller does not supply one.

    The real ``streamlit_app.match_to_tickers`` does fuzzy name-aware matching;
    here we keep things conservative and only flag a hit when the ticker
    appears as a discrete uppercase token in the title. This avoids accidental
    substring matches (e.g. ``"AI"`` inside ``"chair"``).
    """
    if not title:
        return []
    tokens = title.upper().split()
    return [t for t in tickers if t in tokens]


def scan(
    quotes: Mapping[str, Mapping[str, Any]],
    headlines: Iterable[Mapping[str, Any]],
    threshold_pct: float,
    match_to_tickers_fn: Optional[Callable[[str, Sequence[str]], Sequence[str]]] = None,
    indices: Optional[Iterable[str]] = None,
    macro_tickers: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    """Scan ``quotes`` for tickers whose absolute pct_change meets the threshold.

    Args:
        quotes: Mapping of ticker -> quote dict. Each quote is expected to
            carry at least ``pct_change`` and ``price``. Tickers with missing
            or non-numeric ``pct_change`` are skipped.
        headlines: Iterable of headline dicts in ranked order. Each headline
            should have a ``title`` field; missing/empty titles contribute no
            match.
        threshold_pct: Inclusive minimum for ``abs(pct_change)``. Tickers with
            ``abs(pct_change) >= threshold_pct`` qualify.
        match_to_tickers_fn: Optional callable ``(title, [ticker]) -> list[str]``
            used to resolve the "reason" headline. Defaults to a simple
            uppercase token match. Injected to avoid a circular import with
            ``streamlit_app``.
        indices: Iterable of ticker symbols to exclude (e.g. ``"SPY"``).
            Defaults to ``DEFAULT_INDICES``.
        macro_tickers: Iterable of macro ticker symbols to exclude
            (e.g. ``"^VIX"``). Defaults to ``DEFAULT_MACRO_TICKERS``.

    Returns:
        List of ``{"ticker", "pct_change", "price", "reason"}`` dicts sorted
        by ``abs(pct_change)`` descending. Empty list when no ticker
        qualifies. Performs zero outbound HTTP calls.
    """
    excluded: set[str] = set(indices) if indices is not None else set(DEFAULT_INDICES)
    if macro_tickers is not None:
        excluded.update(macro_tickers)
    else:
        excluded.update(DEFAULT_MACRO_TICKERS)

    matcher = match_to_tickers_fn or _default_match_to_tickers

    # Materialize headlines once so we can iterate per ticker without
    # exhausting a generator.
    headline_list: list[Mapping[str, Any]] = list(headlines or [])

    rows: list[dict[str, Any]] = []
    for ticker, info in quotes.items():
        if ticker in excluded:
            continue
        if info is None:
            continue

        pct_change = info.get("pct_change")
        if pct_change is None:
            continue
        try:
            pct_value = float(pct_change)
        except (TypeError, ValueError):
            continue
        # Skip NaN — abs(NaN) is NaN which compares False against the
        # threshold, but being explicit avoids surprising downstream consumers.
        if pct_value != pct_value:  # NaN check
            continue
        if abs(pct_value) < threshold_pct:
            continue

        reason = REASON_NONE
        for headline in headline_list:
            title = headline.get("title") if isinstance(headline, Mapping) else None
            if not title:
                continue
            try:
                hits = matcher(title, [ticker])
            except Exception:
                # A faulty matcher must not break the scan for other tickers.
                hits = []
            if hits:
                reason = title[:REASON_MAX_LEN]
                break

        rows.append(
            {
                "ticker": ticker,
                "pct_change": pct_value,
                "price": info.get("price"),
                "reason": reason,
            }
        )

    rows.sort(key=lambda r: abs(r["pct_change"]), reverse=True)
    return rows


# --- Streamlit renderer (task 10.4) ------------------------------------------


def _format_pct(value: Any) -> str:
    """Format a percent change as e.g. ``"+3.42%"`` / ``"-1.05%"``."""
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _format_price(value: Any) -> str:
    """Format a price as e.g. ``"$182.34"``. Returns ``"—"`` on bad input."""
    if value is None:
        return "—"
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _resolve_match_to_tickers() -> Optional[
    Callable[[str, Sequence[str]], Sequence[str]]
]:
    """Lazy-import ``streamlit_app.match_to_tickers`` to avoid cycles.

    Returns ``None`` when the import fails (e.g. in unit tests that run the
    module in isolation). :func:`scan` will then fall back to its built-in
    matcher.
    """
    try:
        from streamlit_app import match_to_tickers  # type: ignore

        return match_to_tickers
    except Exception:
        return None


def render_volatility_scanner(
    quotes: Mapping[str, Mapping[str, Any]],
    headlines: Iterable[Mapping[str, Any]],
) -> None:
    """Render the Volatility Scanner section.

    Validates: Requirements 8.1, 8.7, 8.8.

    Layout:
        1. ``st.subheader("⚡ Volatility Scanner")``
        2. ``st.number_input`` for the threshold, bounded by
           :data:`VOLATILITY_MIN`/:data:`VOLATILITY_MAX`, step ``0.5``,
           default :data:`VOLATILITY_DEFAULT`.
        3. ``scan(...)`` is called with the chosen threshold. Resulting
           rows are displayed in a ``st.dataframe`` with columns
           ``Ticker, % Change, Price, Reason``. An empty result shows the
           informational message ``"No stocks above threshold"``.

    The function performs no outbound HTTP requests — all data is sourced
    from the cached ``quotes`` and ``headlines`` arguments.
    """

    st.subheader("⚡ Volatility Scanner")

    threshold = st.number_input(
        "Threshold (%)",
        min_value=VOLATILITY_MIN,
        max_value=VOLATILITY_MAX,
        value=VOLATILITY_DEFAULT,
        step=0.5,
    )

    rows = scan(
        quotes,
        headlines,
        float(threshold),
        match_to_tickers_fn=_resolve_match_to_tickers(),
    )

    if not rows:
        st.info("No stocks above threshold")
        return

    df = pd.DataFrame(
        [
            {
                "Ticker": row["ticker"],
                "% Change": _format_pct(row["pct_change"]),
                "Price": _format_price(row["price"]),
                "Reason": row["reason"],
            }
            for row in rows
        ]
    )

    st.dataframe(df, hide_index=True, use_container_width=True)


__all__ = [
    "VOLATILITY_MIN",
    "VOLATILITY_MAX",
    "VOLATILITY_DEFAULT",
    "DEFAULT_INDICES",
    "DEFAULT_MACRO_TICKERS",
    "REASON_NONE",
    "REASON_MAX_LEN",
    "scan",
    "render_volatility_scanner",
]
