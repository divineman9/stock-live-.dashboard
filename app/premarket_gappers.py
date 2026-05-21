"""Pre-Market Gappers feature (Requirement 4).

This module provides the pure-logic ranker :func:`compute_gappers` and the
cached fetcher :func:`fetch_premarket_quotes`. The Streamlit renderer
(``render_premarket_gappers``) is added by task 5.4.

The ranker is deliberately decoupled from ``streamlit_app.py``: the index
and macro ticker exclusion lists are passed in as parameters with sensible
defaults that resolve lazily, so importing this module never imports the
Streamlit app at module load time. This avoids circular imports while
still letting callers omit the lists in normal use. The fetcher imports
``streamlit_app`` lazily for the same reason.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

import pandas as pd
import streamlit as st

# --- Public constants (Requirement 4.4, 4.5, 4.7) ---
GAPPERS_TTL_S: int = 60
GAPPER_THRESHOLD_PCT: float = 2.0
GAPPER_TOP_N: int = 10


# Sentinel used to defer resolving the default exclusion lists until call
# time. Resolving them at import time would force an early import of
# ``streamlit_app``, which itself imports Streamlit and triggers the
# ``st.set_page_config`` side effect.
_DEFAULT = object()


def _resolve_exclusions(
    indices: object,
    macro_tickers: object,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return the exclusion sets, importing from ``streamlit_app`` lazily.

    If the caller passes ``_DEFAULT`` (the sentinel used in
    :func:`compute_gappers`'s signature), the lists are pulled from the
    Streamlit app module. If that import fails (for example in a test
    environment that has not stubbed Streamlit), the defaults fall back to
    empty sets so that ``compute_gappers`` still produces a sensible
    result over arbitrary input dicts.
    """

    if indices is _DEFAULT or macro_tickers is _DEFAULT:
        try:
            import streamlit_app  # local import to avoid circular import

            default_indices = streamlit_app.INDICES
            default_macro = streamlit_app.MACRO_TICKERS
        except Exception:  # pragma: no cover - defensive only
            default_indices = ()
            default_macro = ()
    else:
        default_indices = ()
        default_macro = ()

    resolved_indices = default_indices if indices is _DEFAULT else indices
    resolved_macro = default_macro if macro_tickers is _DEFAULT else macro_tickers

    return frozenset(resolved_indices or ()), frozenset(resolved_macro or ())


def _entry(quote: dict) -> dict:
    """Project a quote dict to the gapper entry shape.

    The entry exposes exactly the four fields documented for a Gapper row
    in the design's data models: ``ticker``, ``price``, ``pct_change``,
    ``volume``. Missing numeric fields default to ``0`` so callers always
    receive a fully-populated row.
    """

    return {
        "ticker": quote.get("ticker"),
        "price": quote.get("price", 0),
        "pct_change": quote.get("pct_change", 0),
        "volume": quote.get("volume", 0),
    }


def _is_valid_pct(value: object) -> bool:
    """True iff ``value`` is a finite real number usable for ranking."""

    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def compute_gappers(
    quotes: dict,
    indices: Optional[Iterable[str]] = _DEFAULT,  # type: ignore[assignment]
    macro_tickers: Optional[Iterable[str]] = _DEFAULT,  # type: ignore[assignment]
) -> tuple[list[dict], list[dict]]:
    """Return ``(up, down)`` ranked pre-market gappers for ``quotes``.

    Parameters
    ----------
    quotes:
        Mapping from ticker symbol to quote dict, in the same shape
        produced by ``fetch_all_data`` in ``streamlit_app.py``. Each value
        is expected to expose at minimum ``ticker``, ``price``,
        ``pct_change``, and ``volume``.
    indices:
        Iterable of index ticker symbols to exclude from both lists.
        Defaults to ``streamlit_app.INDICES`` (resolved lazily) so this
        module can be imported without triggering a circular import.
    macro_tickers:
        Iterable of macro/indicator ticker symbols to exclude from both
        lists. Defaults to ``streamlit_app.MACRO_TICKERS`` (lazy).

    Returns
    -------
    tuple[list[dict], list[dict]]
        ``(up, down)`` where:

        * ``up`` contains the top :data:`GAPPER_TOP_N` entries with
          ``pct_change >= +GAPPER_THRESHOLD_PCT`` sorted by ``pct_change``
          descending.
        * ``down`` contains the top :data:`GAPPER_TOP_N` entries with
          ``pct_change <= -GAPPER_THRESHOLD_PCT`` sorted by ``pct_change``
          ascending (most negative first).

        Both lists are restricted to TRACKED_STOCKS — i.e. quote keys that
        are not in ``indices`` and not in ``macro_tickers``. Quotes whose
        ``pct_change`` is missing or non-finite are skipped silently.

    Notes
    -----
    This function is pure: it makes no network calls, performs no I/O,
    and never mutates ``quotes``. It can be called safely outside the
    pre-market window; the caller (``render_premarket_gappers``) is
    responsible for gating on ``get_market_phase()``.
    """

    if not quotes:
        return [], []

    excl_indices, excl_macro = _resolve_exclusions(indices, macro_tickers)

    up_candidates: list[dict] = []
    down_candidates: list[dict] = []

    for key, quote in quotes.items():
        if not isinstance(quote, dict):
            continue

        # Resolve the canonical ticker symbol. Prefer the dict's own
        # ``ticker`` field and fall back to the dict key for safety.
        ticker = quote.get("ticker", key)
        if ticker is None:
            continue

        if ticker in excl_indices or ticker in excl_macro:
            continue

        pct = quote.get("pct_change")
        if not _is_valid_pct(pct):
            continue
        pct_value = float(pct)

        if pct_value >= GAPPER_THRESHOLD_PCT:
            up_candidates.append(_entry({**quote, "ticker": ticker}))
        elif pct_value <= -GAPPER_THRESHOLD_PCT:
            down_candidates.append(_entry({**quote, "ticker": ticker}))

    up_candidates.sort(key=lambda e: e["pct_change"], reverse=True)
    down_candidates.sort(key=lambda e: e["pct_change"])

    return up_candidates[:GAPPER_TOP_N], down_candidates[:GAPPER_TOP_N]


# --- Cached fetcher (Requirement 4.7) ---

# Match the parallelism used by ``fetch_extended_hours_data`` in
# ``streamlit_app.py`` so we stay within the same Yahoo rate-limit budget.
_GAPPERS_MAX_WORKERS = 15


@st.cache_data(ttl=GAPPERS_TTL_S)
def fetch_premarket_quotes() -> dict:
    """Fetch pre-market quotes for tracked stocks in parallel.

    Calls ``streamlit_app.fetch_chart_single`` (which already passes
    ``includePrePost=true`` to Yahoo's chart endpoint) for each ticker in
    ``streamlit_app.ALL_TICKERS`` — i.e. the full Tracked_Stocks set as
    defined in the requirements glossary (STOCKS + INDICES +
    MACRO_TICKERS). The exclusion of indices and macro tickers from the
    final ranking is the responsibility of :func:`compute_gappers`, not
    this fetcher.

    The returned dict matches the shape of ``fetch_all_data()``'s output:
    ``ticker``, ``price``, ``prev_close``, ``change``, ``pct_change``,
    ``volume``, ``sector``, ``sectors``, ``is_premarket``.

    The function is cached for :data:`GAPPERS_TTL_S` seconds via
    ``st.cache_data`` so the dashboard's 30-second auto-refresh hits the
    cache between fetches. Tickers whose chart call fails or returns no
    data are silently dropped from the result.

    Returns
    -------
    dict
        Mapping ``{ticker: quote_dict}``. Empty if no tickers resolve.
    """

    # Lazy import to avoid the circular ``streamlit_app -> app.* ->
    # streamlit_app`` cycle that would otherwise occur at module load.
    import streamlit_app  # local import on purpose

    indices = frozenset(getattr(streamlit_app, "INDICES", ()) or ())
    macro = frozenset(getattr(streamlit_app, "MACRO_TICKERS", ()) or ())
    tracked = list(getattr(streamlit_app, "ALL_TICKERS", ()) or ())
    ticker_sectors = getattr(streamlit_app, "TICKER_SECTORS", {}) or {}

    if not tracked:
        return {}

    fetch_one = streamlit_app.fetch_chart_single

    with ThreadPoolExecutor(max_workers=_GAPPERS_MAX_WORKERS) as ex:
        results = list(ex.map(fetch_one, tracked))

    quotes: dict = {}
    for ticker, quote in zip(tracked, results):
        if not quote:
            continue

        # ``fetch_chart_single`` already returns the core numeric fields
        # in the same shape as ``fetch_all_data``; we layer the sector
        # metadata on top so downstream consumers (compute_gappers,
        # render_premarket_gappers) see a uniform shape.
        if ticker in indices or ticker in macro:
            primary_sector = "Index"
            sectors = ["Index"]
        else:
            sectors = ticker_sectors.get(ticker, ["Unknown"])
            primary_sector = sectors[0] if sectors else "Unknown"

        enriched = dict(quote)
        enriched.setdefault("ticker", ticker)
        enriched["sector"] = primary_sector
        enriched["sectors"] = list(sectors)
        enriched["is_premarket"] = True

        quotes[ticker] = enriched

    return quotes


# --- Streamlit renderer (Requirement 4.1, 4.2, 4.6, 4.8) ---


def _format_pct(value: object) -> str:
    """Format ``value`` as a signed percent (``"+2.34%"`` / ``"-1.05%"``)."""
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _format_price(value: object) -> str:
    """Format ``value`` as a USD price (``"$182.34"``)."""
    if value is None:
        return "—"
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _format_volume(value: object) -> str:
    """Format ``value`` as a thousands-grouped integer."""
    if value is None:
        return "—"
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "—"


def _render_gapper_table(entries: list[dict]) -> None:
    """Render a single side's ranked table or the empty-list message.

    Builds a ``pandas.DataFrame`` with columns ``Ticker, Price, % Change,
    Volume`` from the list of gapper entries and hands it to
    ``st.dataframe``. When ``entries`` is empty, displays the
    ``"No qualifying gappers"`` info message instead (Requirement 4.8).
    """
    if not entries:
        st.info("No qualifying gappers")
        return

    df = pd.DataFrame(
        [
            {
                "Ticker": entry.get("ticker", "—"),
                "Price": _format_price(entry.get("price")),
                "% Change": _format_pct(entry.get("pct_change")),
                "Volume": _format_volume(entry.get("volume")),
            }
            for entry in entries
        ]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)


def render_premarket_gappers() -> None:
    """Render the Pre-Market Gappers section.

    Validates: Requirements 4.1, 4.2, 4.6, 4.8.

    Behavior:

    * If ``streamlit_app.get_market_phase()`` does not return
      ``"premarket"``, the function returns immediately without rendering
      anything (Requirement 4.2). The phase function is imported lazily
      so that importing this module never triggers the
      ``streamlit_app -> app.premarket_gappers -> streamlit_app`` cycle.
    * Otherwise the section renders a ``"🌅 Pre-Market Gappers"``
      subheader followed by two ``st.columns(2)``-laid-out tables
      showing the top gappers up and down (Requirement 4.1). Each table
      lists ticker, pre-market price, percent change, and pre-market
      volume (Requirement 4.6). If either side has no qualifying
      tickers, the prescribed ``"No qualifying gappers"`` info message
      is displayed instead of an empty table (Requirement 4.8).
    """

    # Lazy import to avoid the
    # ``streamlit_app -> app.premarket_gappers -> streamlit_app``
    # circular import at module load time.
    try:
        from streamlit_app import get_market_phase  # local import on purpose
    except Exception:
        # If we cannot resolve the phase function, fail closed by
        # rendering nothing rather than crashing the page.
        return

    try:
        phase = get_market_phase()
    except Exception:
        return

    if phase != "premarket":
        return

    st.subheader("🌅 Pre-Market Gappers")

    quotes = fetch_premarket_quotes()
    up, down = compute_gappers(quotes)

    col_up, col_down = st.columns(2)

    with col_up:
        st.markdown("### 🟢 Gapping Up")
        _render_gapper_table(up)

    with col_down:
        st.markdown("### 🔴 Gapping Down")
        _render_gapper_table(down)
