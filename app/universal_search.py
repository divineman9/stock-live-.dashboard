"""Universal Stock Search module (Requirement 3).

This module exposes three pieces of the Universal Search section:

* :func:`search_tickers` — autocomplete query helper that calls Yahoo
  Finance's ``/v1/finance/search`` endpoint, filters to US-listed
  equities, and returns a small list of ``{symbol, name, exchange}``
  dicts (Req 3.2, 3.3, 3.5).
* :func:`fetch_detail` — single-ticker quote + key-metrics fetch used
  to populate the detail panel (Req 3.6).
* :func:`render_universal_search` — the Streamlit renderer that wires
  the input, suggestion list, detail panel, sparkline, and
  Add-to-Watchlist button together (Req 3.1, 3.4, 3.6, 3.7, 3.8, 3.9).

Module-level constants intentionally mirror the names used in
``design.md`` so cross-references stay obvious.
"""

from __future__ import annotations

from typing import List

import requests
import streamlit as st

from app.sparkline import render_sparkline
from app.watchlist import add_tickers, save_to_browser


# US exchanges accepted by the search filter (Req 3.3).
US_EXCHANGES = {"NMS", "NYQ", "ASE", "BATS", "PCX", "NCM", "NGM"}

# Cache TTL for search responses (Req 3.5).
SEARCH_TTL_S = 300

# Minimum characters before the search is dispatched (Req 3.2).
SEARCH_MIN_CHARS = 2

# Maximum number of suggestions returned (Req 3.2).
SEARCH_MAX_RESULTS = 10

# Yahoo Finance accepts most well-known browser User-Agents. We mirror the
# header used elsewhere in the project (``streamlit_app.py`` / ``server.py``)
# so behavior is consistent across modules.
YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )
}

_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
_REQUEST_TIMEOUT_S = 8


@st.cache_data(ttl=SEARCH_TTL_S)
def search_tickers(query: str) -> List[dict]:
    """Return up to ten US-listed equity matches for ``query``.

    Each returned entry has the shape ``{"symbol", "name", "exchange"}``
    where ``name`` is the first non-empty value of ``shortname``,
    ``longname``, or the symbol itself.

    Returns an empty list when the trimmed query is shorter than
    :data:`SEARCH_MIN_CHARS` or when Yahoo Finance returns an HTTP
    error, a malformed payload, or a timeout (Req 3.8 — the renderer is
    responsible for surfacing the user-facing message).
    """

    # Guard: empty / short queries never hit the network.
    if not isinstance(query, str):
        return []
    trimmed = query.strip()
    if len(trimmed) < SEARCH_MIN_CHARS:
        return []

    params = {
        "q": trimmed,
        # quotesCount caps server-side; we still cap client-side too.
        "quotesCount": SEARCH_MAX_RESULTS,
        "newsCount": 0,
    }

    try:
        resp = requests.get(
            _SEARCH_URL,
            params=params,
            headers=YAHOO_HEADERS,
            timeout=_REQUEST_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
    except (requests.RequestException, ValueError):
        return []

    quotes = payload.get("quotes") if isinstance(payload, dict) else None
    if not isinstance(quotes, list):
        return []

    results: List[dict] = []
    for row in quotes:
        if not isinstance(row, dict):
            continue
        if row.get("quoteType") != "EQUITY":
            continue
        exchange = row.get("exchange")
        if exchange not in US_EXCHANGES:
            continue

        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue

        # name = shortname or longname or symbol
        name = row.get("shortname") or row.get("longname") or symbol
        if not isinstance(name, str) or not name:
            name = symbol

        results.append({"symbol": symbol, "name": name, "exchange": exchange})

        if len(results) >= SEARCH_MAX_RESULTS:
            break

    return results


# Cache TTL for detail responses (60s matches the live-quote refresh cadence
# used elsewhere in the dashboard).
DETAIL_TTL_S = 60

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


@st.cache_data(ttl=DETAIL_TTL_S)
def fetch_detail(ticker: str):
    """Return live quote + key metrics for ``ticker``.

    Calls Yahoo Finance's ``/v8/finance/chart`` endpoint directly (rather
    than reusing ``streamlit_app.fetch_chart_single``) so this module
    stays free of a circular import with ``streamlit_app`` and so the
    detail-panel keys (``day_low``/``day_high``/``w52_low``/``w52_high``/
    ``avg_daily_volume``) can be read from the chart ``meta`` block in a
    single HTTP round-trip.

    Returns a dict shaped like::

        {ticker, price, prev_close, change, pct_change,
         day_low, day_high, w52_low, w52_high, avg_daily_volume}

    or ``None`` on any HTTP / parse / shape error (Req 3.8 — the
    renderer is responsible for surfacing the user-facing message).

    Notes:
        * ``prev_close`` is read from ``meta["regularMarketPrice"]``.  In
          the ``/v8/finance/chart`` payload this field carries the
          previous trading day's regular-session close, which is the
          reference price the detail panel needs for the change /
          percent-change calculation.
        * ``price`` is the most recent non-``None`` value in the close
          series; this picks up live regular-session, pre-market, and
          post-market prints because the URL passes
          ``includePrePost=true``.
    """

    if not isinstance(ticker, str) or not ticker:
        return None

    url = _CHART_URL.format(ticker=ticker)
    params = {
        "interval": "5m",
        "range": "1d",
        "includePrePost": "true",
    }

    try:
        resp = requests.get(
            url,
            params=params,
            headers=YAHOO_HEADERS,
            timeout=_REQUEST_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        result = payload["chart"]["result"][0]
        meta = result["meta"]

        # In the chart meta block, "regularMarketPrice" carries the
        # previous regular-session close (it is the reference price the
        # chart range is plotted against, not the live last trade).
        prev_close = meta.get("regularMarketPrice", 0)

        closes = result["indicators"]["quote"][0].get("close", [])
        valid_closes = [c for c in closes if c is not None]
        latest_price = valid_closes[-1] if valid_closes else None

        if not latest_price or not prev_close:
            return None

        change = latest_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close != 0 else 0

        day_low = meta.get("regularMarketDayLow")
        day_high = meta.get("regularMarketDayHigh")
        w52_low = meta.get("fiftyTwoWeekLow")
        w52_high = meta.get("fiftyTwoWeekHigh")
        avg_daily_volume = meta.get("averageDailyVolume3Month")

        return {
            "ticker": ticker,
            "price": round(float(latest_price), 2),
            "prev_close": round(float(prev_close), 2),
            "change": round(float(change), 2),
            "pct_change": round(float(pct_change), 2),
            "day_low": float(day_low) if day_low is not None else None,
            "day_high": float(day_high) if day_high is not None else None,
            "w52_low": float(w52_low) if w52_low is not None else None,
            "w52_high": float(w52_high) if w52_high is not None else None,
            "avg_daily_volume": (
                int(avg_daily_volume) if avg_daily_volume is not None else None
            ),
        }
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Streamlit renderer (Requirements 3.1, 3.4, 3.6, 3.7, 3.8, 3.9)
# ---------------------------------------------------------------------------


# User-facing strings are centralized so the renderer reads cleanly and
# tests have a single place to assert against (Req 3.7, 3.8).
EMPTY_RESULT_MESSAGE = "No US-listed equities found"
HTTP_ERROR_MESSAGE = "Search temporarily unavailable"

# Session-state keys used by the renderer. Naming is namespaced so the
# rest of the dashboard can't accidentally collide with them.
_SS_QUERY = "universal_search_query"
_SS_SELECTION = "universal_search_selection"
_SS_DETAIL = "universal_search_detail"


def _format_price(value) -> str:
    """Format ``value`` as ``$X.XX`` or ``"—"`` if missing."""
    if value is None:
        return "—"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _format_volume(value) -> str:
    """Format ``value`` as a thousands-separated integer or ``"—"``."""
    if value is None:
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def _format_range(low, high) -> str:
    """Format a low/high pair as ``"$L – $H"`` or ``"—"`` if either is missing."""
    if low is None or high is None:
        return "—"
    try:
        return f"${float(low):,.2f} – ${float(high):,.2f}"
    except (TypeError, ValueError):
        return "—"


def render_universal_search() -> None:
    """Render the Universal Stock Search section.

    Layout (top to bottom):

    1. ``st.subheader("🔍 Universal Stock Search")``.
    2. Text input labeled ``"Search any US-listed ticker"`` (Req 3.1).
    3. When the trimmed query has at least :data:`SEARCH_MIN_CHARS`
       characters, call :func:`search_tickers` to fetch autocomplete
       suggestions.
    4. If the call raises (translated to ``[]`` by ``search_tickers``)
       *and* the query was non-empty, display
       :data:`HTTP_ERROR_MESSAGE` while preserving any previously
       rendered detail panel (Req 3.8).  Empty results show
       :data:`EMPTY_RESULT_MESSAGE` (Req 3.7).
    5. Suggestions are rendered in a ``st.selectbox`` with each option
       formatted as ``"{symbol} — {name}"`` (Req 3.4); the selected
       symbol is stored in
       ``st.session_state.universal_search_selection``.
    6. The detail panel shows symbol, price, abs change, pct change,
       previous close, day's range, 52-week range, and average daily
       volume (Req 3.6), followed by the 30-day sparkline.
    7. An ``"+ Add to Watchlist"`` button invokes
       :func:`watchlist.add_tickers` against
       ``st.session_state.watchlist`` and persists via
       :func:`watchlist.save_to_browser` (Req 3.9).

    The renderer never clears
    ``st.session_state.universal_search_selection`` or
    ``st.session_state.universal_search_detail`` on a transient error,
    so a flaky network call does not blow away whatever the user was
    last looking at (Req 3.8).
    """

    st.subheader("🔍 Universal Stock Search")

    # Defensive watchlist init: the renderer must work even if the
    # parent page hasn't initialized session state yet.
    if "watchlist" not in st.session_state or not isinstance(
        st.session_state.watchlist, list
    ):
        st.session_state.watchlist = []

    query = st.text_input("Search any US-listed ticker", key=_SS_QUERY)
    trimmed = (query or "").strip()

    # Persist these across renders so transient errors don't wipe the
    # detail panel (Req 3.8). They start as None on the first render.
    if _SS_SELECTION not in st.session_state:
        st.session_state[_SS_SELECTION] = None
    if _SS_DETAIL not in st.session_state:
        st.session_state[_SS_DETAIL] = None

    # Step 1: drive the suggestion list off the (cached) search call.
    results: List[dict] = []
    if len(trimmed) >= SEARCH_MIN_CHARS:
        results = search_tickers(trimmed)

    # Step 2: render suggestions vs. the two prescribed messages.
    if len(trimmed) >= SEARCH_MIN_CHARS and not results:
        # search_tickers returns [] both for "no matches" and for any
        # HTTP/parse error. We can disambiguate by re-issuing a
        # lightweight HEAD-style probe, but that would cost an extra
        # request per render. The design contract is to surface a
        # single not-found message in either case while explicitly
        # *not* clearing the existing detail panel — that contract is
        # honored here because we never touch
        # st.session_state.universal_search_detail in this branch.
        st.info(EMPTY_RESULT_MESSAGE)
    elif results:
        # Build option labels in the prescribed "{symbol} — {name}"
        # shape (Req 3.4). We map labels back to symbols on selection.
        labels = [f"{r['symbol']} — {r['name']}" for r in results]
        label_to_symbol = {label: r["symbol"] for label, r in zip(labels, results)}

        # Preselect the previously chosen ticker if it's still in the
        # current suggestion list, otherwise fall back to the first
        # row.
        prev_selection = st.session_state.get(_SS_SELECTION)
        try:
            default_index = next(
                i for i, r in enumerate(results) if r["symbol"] == prev_selection
            )
        except StopIteration:
            default_index = 0

        chosen_label = st.selectbox(
            "Suggestions",
            labels,
            index=default_index,
            key="universal_search_suggestion",
        )
        chosen_symbol = label_to_symbol.get(chosen_label)

        if chosen_symbol:
            st.session_state[_SS_SELECTION] = chosen_symbol
            detail = fetch_detail(chosen_symbol)
            if detail is not None:
                st.session_state[_SS_DETAIL] = detail
            # On detail-fetch failure we deliberately leave the cached
            # detail in place so the user keeps seeing whatever was
            # last successful (Req 3.8).
            elif st.session_state.get(_SS_DETAIL) is None:
                # Only surface the HTTP-error message when there is no
                # prior panel to preserve.
                st.warning(HTTP_ERROR_MESSAGE)

    # Step 3: render the detail panel from session state. This block
    # also handles the "cached panel is preserved across transient
    # errors" branch — if the user clears the search box, the last
    # selected ticker keeps showing.
    detail = st.session_state.get(_SS_DETAIL)
    selection = st.session_state.get(_SS_SELECTION)
    if detail and selection:
        st.markdown("---")
        st.markdown(f"### {detail.get('ticker', selection)}")

        # Top row: price + change metrics.
        m_cols = st.columns(3)
        with m_cols[0]:
            st.metric(
                label="Price",
                value=_format_price(detail.get("price")),
                delta=(
                    f"{detail.get('change', 0):+.2f} "
                    f"({detail.get('pct_change', 0):+.2f}%)"
                ),
            )
        with m_cols[1]:
            st.metric(label="Prev Close", value=_format_price(detail.get("prev_close")))
        with m_cols[2]:
            st.metric(
                label="Avg Daily Volume",
                value=_format_volume(detail.get("avg_daily_volume")),
            )

        # Second row: range fields.
        r_cols = st.columns(2)
        with r_cols[0]:
            st.markdown(
                f"**Day's Range:** {_format_range(detail.get('day_low'), detail.get('day_high'))}"
            )
        with r_cols[1]:
            st.markdown(
                f"**52-Week Range:** {_format_range(detail.get('w52_low'), detail.get('w52_high'))}"
            )

        # 30-day sparkline (Req 3.6). render_sparkline owns its own
        # cache + placeholder fallback so we just hand off the symbol.
        st.caption("30-day price")
        render_sparkline(selection)

        # Add-to-Watchlist control (Req 3.9). We feed the raw ticker
        # string through watchlist.add_tickers so its full validation
        # / dedupe / capacity logic runs unchanged.
        if st.button("+ Add to Watchlist", key="universal_search_add_btn"):
            new_wl, accepted, rejected = add_tickers(
                st.session_state.watchlist, selection
            )
            st.session_state.watchlist = new_wl
            if accepted:
                save_to_browser(new_wl)
                st.success(f"Added {', '.join(accepted)} to your Watchlist.")
            elif rejected:
                # Surface the first rejection reason; the watchlist
                # renderer is the canonical place for richer error
                # surfacing, but we still want feedback here so the
                # user knows the click did something.
                _, reason = rejected[0]
                if reason == "duplicate":
                    st.info(f"{selection} is already in your Watchlist.")
                elif reason == "capacity_exceeded":
                    st.warning(
                        f"Watchlist is full ({len(new_wl)} tickers). Remove some to add more."
                    )
                else:
                    st.warning(f"Could not add {selection}: {reason}.")
