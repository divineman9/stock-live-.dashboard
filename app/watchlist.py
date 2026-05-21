"""Watchlist module (Requirement 1).

This module contains the pure functions that drive the Custom Watchlist
feature plus the browser-storage persistence layer
(``load_from_browser`` / ``save_to_browser``) added in task 2.5 and the
Streamlit renderer (``render_watchlist_section``) added in task 2.6.

The pure functions at the top of the file have no Streamlit
dependency. The persistence functions reach into ``localStorage``
through the ``streamlit_javascript`` component bridge and are isolated
below the pure-functions section. The renderer at the bottom is the
only function that touches ``st.session_state`` and the Streamlit UI.

The contracts in this file map directly onto requirements 1.1, 1.2,
1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9 and 1.10 and onto Properties 1, 2,
3 and 4 in the design document.
"""

from __future__ import annotations

import json
import re
from typing import List, Tuple

import streamlit as st
from streamlit_javascript import st_javascript

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Ticker = str
Watchlist = List[Ticker]
RejectionReason = str  # 'invalid_format' | 'duplicate' | 'capacity_exceeded'
RejectedEntry = Tuple[str, RejectionReason]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum number of tickers allowed in a watchlist (Requirement 1.10).
WATCHLIST_MAX_LEN: int = 50

#: Validation regex applied to a normalized ticker (Requirement 1.9).
#: Must start with an uppercase letter, then up to 9 more characters from
#: [A-Z0-9.-].
WATCHLIST_TICKER_RE: re.Pattern[str] = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

#: localStorage key used by the browser-storage persistence layer
#: (Requirement 1.5). The ``v1`` suffix is the schema version.
WATCHLIST_LS_KEY: str = "stockdash.watchlist.v1"

#: Rejection reason constants. Centralized so callers (renderers, tests)
#: do not need to repeat the string literals.
REASON_INVALID_FORMAT: RejectionReason = "invalid_format"
REASON_DUPLICATE: RejectionReason = "duplicate"
REASON_CAPACITY_EXCEEDED: RejectionReason = "capacity_exceeded"


# Splits on any run of commas and/or whitespace (which includes spaces,
# tabs, and newlines). Used by parse_input.
_SPLIT_RE = re.compile(r"[,\s]+")


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def parse_input(raw: str) -> List[str]:
    """Split *raw* into trimmed uppercase tokens.

    Tokens are separated by any combination of commas, spaces, tabs and
    newlines. Empty tokens (produced by adjacent separators or leading /
    trailing separators) are dropped. Surviving tokens are uppercased so
    downstream validation and dedupe can compare them as canonical
    symbols.

    The function is total: it accepts ``None``-like inputs (empty
    string) and returns ``[]`` rather than raising.
    """

    if not raw:
        return []
    # The regex split already handles internal separators; calling
    # ``.strip()`` first avoids a leading/trailing empty token before
    # the comprehension filters them out anyway, but keeps the result
    # consistent with the textual contract "split on commas, whitespace,
    # newlines; trim".
    parts = _SPLIT_RE.split(raw.strip())
    return [p.upper() for p in parts if p]


def validate_ticker(t: str) -> bool:
    """Return ``True`` iff *t* matches :data:`WATCHLIST_TICKER_RE`.

    The function compares the input verbatim — callers are expected to
    have already normalized the ticker via :func:`parse_input` (or an
    equivalent uppercase/trim step) when working with raw user input.
    """

    if not isinstance(t, str):
        return False
    return WATCHLIST_TICKER_RE.match(t) is not None


def add_tickers(
    current: Watchlist,
    raw_input: str,
) -> Tuple[Watchlist, List[Ticker], List[RejectedEntry]]:
    """Add tickers parsed from *raw_input* to *current*.

    Returns a 3-tuple ``(new_watchlist, accepted, rejected_with_reason)``
    where:

    * ``new_watchlist`` is *current* with the accepted tickers appended,
      in input order. The original list is **not** mutated.
    * ``accepted`` lists every token that was actually appended.
    * ``rejected_with_reason`` lists ``(token, reason)`` pairs for every
      token that was not appended. ``reason`` is one of
      :data:`REASON_INVALID_FORMAT`, :data:`REASON_DUPLICATE` or
      :data:`REASON_CAPACITY_EXCEEDED`.

    Invariants (Property 1 in the design):

    * *new_watchlist* has *current* as a strict prefix.
    * *new_watchlist* contains no duplicates if *current* contained
      none.
    * ``len(new_watchlist) <= WATCHLIST_MAX_LEN``.
    * Every accepted token satisfies :func:`validate_ticker`.
    * The set of all tokens produced by ``parse_input(raw_input)`` is
      partitioned across ``accepted`` and the tokens of
      ``rejected_with_reason``.
    """

    accepted: List[Ticker] = []
    rejected: List[RejectedEntry] = []
    # Track membership against current + already-accepted in this call
    # so duplicates inside the same input are reported as duplicates.
    seen: set[str] = set(current)

    for tok in parse_input(raw_input):
        if not validate_ticker(tok):
            rejected.append((tok, REASON_INVALID_FORMAT))
            continue
        if tok in seen:
            rejected.append((tok, REASON_DUPLICATE))
            continue
        if len(current) + len(accepted) >= WATCHLIST_MAX_LEN:
            rejected.append((tok, REASON_CAPACITY_EXCEEDED))
            continue
        accepted.append(tok)
        seen.add(tok)

    new_watchlist: Watchlist = list(current) + accepted
    return new_watchlist, accepted, rejected


def remove_ticker(current: Watchlist, t: Ticker) -> Watchlist:
    """Remove the first occurrence of *t* from *current*.

    Returns a new list. Idempotent: if *t* is not in *current*, returns
    a copy of *current* unchanged. Length decreases by exactly one when
    *t* is present, by zero otherwise (Property 3).
    """

    new_list: Watchlist = list(current)
    try:
        new_list.remove(t)
    except ValueError:
        # Idempotent: ticker absent, return the unchanged copy.
        pass
    return new_list


# ---------------------------------------------------------------------------
# Browser-storage persistence (Requirements 1.5, 1.6)
# ---------------------------------------------------------------------------
#
# These helpers round-trip the watchlist through the browser's
# ``localStorage`` via the ``streamlit_javascript`` component. They are
# kept tolerant of every realistic failure mode (private/incognito mode
# blocking storage, the component bridge returning ``None`` on first
# render, malformed JSON written by an older client, an unexpected
# payload shape) so a misbehaving browser never crashes the page.
#
# Persistence shape (versioned wrapper, per design "Watchlist
# persistence shape"):
#
#     localStorage["stockdash.watchlist.v1"] =
#         '{"version": 1, "tickers": ["AAPL", "TSLA", ...]}'
#
# The version number lets us migrate the payload schema in future
# without losing user data.

#: Schema version embedded in the persisted JSON wrapper.
_WATCHLIST_STORAGE_VERSION: int = 1


def load_from_browser() -> Watchlist:
    """Read the persisted watchlist from ``localStorage``.

    Returns ``[]`` whenever the value is missing, malformed, or of an
    unexpected shape so the caller can treat the result as a safe
    starting watchlist with no further branching.

    The function is intentionally noisy-fault-tolerant: any exception
    raised by the JS bridge, the JSON parser, or the shape check is
    swallowed and surfaces as an empty list.
    """

    try:
        raw = st_javascript(
            f"localStorage.getItem({json.dumps(WATCHLIST_LS_KEY)})"
        )
    except Exception:
        return []

    # ``streamlit_javascript`` returns ``None`` on the first render
    # (before the JS round-trip completes) and when the key is not
    # set. Treat both the same as "no stored watchlist".
    if raw is None or raw == 0:
        return []
    if not isinstance(raw, str) or not raw:
        return []

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return []

    if not isinstance(payload, dict):
        return []

    tickers = payload.get("tickers")
    if not isinstance(tickers, list):
        return []

    # Filter to strings only; the regex/validity check is the caller's
    # job (the renderer applies ``validate_ticker`` before re-saving).
    return [t for t in tickers if isinstance(t, str)]


def save_to_browser(wl: Watchlist) -> None:
    """Persist *wl* to ``localStorage`` under :data:`WATCHLIST_LS_KEY`.

    The payload is the versioned wrapper
    ``{"version": 1, "tickers": [...]}``. Any failure (JS bridge
    error, storage quota exceeded, private-mode block, JSON encode
    error) is swallowed: persistence is best-effort and must never
    interrupt the render.
    """

    try:
        payload = json.dumps(
            {"version": _WATCHLIST_STORAGE_VERSION, "tickers": list(wl)}
        )
        # ``json.dumps`` of a JSON string yields a properly escaped JS
        # string literal, which is exactly what ``setItem`` needs.
        st_javascript(
            f"localStorage.setItem("
            f"{json.dumps(WATCHLIST_LS_KEY)}, {json.dumps(payload)})"
        )
    except Exception:
        # Best-effort: never let a storage failure surface to the UI.
        return


__all__ = [
    "Ticker",
    "Watchlist",
    "RejectionReason",
    "RejectedEntry",
    "WATCHLIST_MAX_LEN",
    "WATCHLIST_TICKER_RE",
    "WATCHLIST_LS_KEY",
    "REASON_INVALID_FORMAT",
    "REASON_DUPLICATE",
    "REASON_CAPACITY_EXCEEDED",
    "parse_input",
    "validate_ticker",
    "add_tickers",
    "remove_ticker",
    "load_from_browser",
    "save_to_browser",
    "render_watchlist_section",
]


# ---------------------------------------------------------------------------
# Streamlit renderer (Requirements 1.1, 1.7, 1.8)
# ---------------------------------------------------------------------------


#: Session-state key holding the user's working watchlist.
_SESSION_WATCHLIST_KEY: str = "watchlist"

#: Session-state flag set after the first successful localStorage hydrate
#: so the bridge round-trip only fires once per session.
_SESSION_HYDRATED_KEY: str = "watchlist_hydrated"


def _format_rejection(token: str, reason: RejectionReason) -> Tuple[str, str]:
    """Map a ``(token, reason)`` pair to ``(severity, message)``.

    Severity is the name of the Streamlit method to call (``"error"``
    for hard validation failures, ``"warning"`` for soft conditions
    like duplicates and capacity). Centralizing the mapping keeps the
    renderer free of branchy literal strings.
    """

    if reason == REASON_INVALID_FORMAT:
        return "error", f"Invalid ticker format: {token}"
    if reason == REASON_DUPLICATE:
        return "warning", f"Already in watchlist: {token}"
    if reason == REASON_CAPACITY_EXCEEDED:
        return (
            "warning",
            f"Watchlist full ({WATCHLIST_MAX_LEN} max): {token}",
        )
    # Unknown reason — surface as an error so it is visible during
    # development without silently dropping the rejection.
    return "error", f"Rejected: {token} ({reason})"


def _format_signed_pct(pct: float) -> str:
    """Return ``pct`` formatted to two decimals with an explicit sign."""

    return f"{pct:+.2f}%"


def _format_signed_change(change: float) -> str:
    """Return ``change`` formatted to two decimals with an explicit sign."""

    return f"{change:+.2f}"


def _format_price(price: float) -> str:
    """Return ``price`` formatted as a USD amount with two decimals."""

    return f"${price:,.2f}"


def render_watchlist_section(quotes: dict) -> None:
    """Render the "My Watchlist" section.

    The function is idempotent across reruns:

    * On first run it hydrates ``st.session_state.watchlist`` from
      ``localStorage`` via :func:`load_from_browser` so the user's
      previous watchlist survives across browser sessions
      (Requirement 1.6). The hydrate runs at most once per Streamlit
      session, guarded by ``st.session_state[_SESSION_HYDRATED_KEY]``.
    * It renders a text input and an "Add" button. On submit it parses,
      validates and dedupes the input via :func:`add_tickers`, surfaces
      one inline error or warning per rejected entry (Requirement 1.9,
      1.10), updates ``session_state``, and persists the new watchlist
      via :func:`save_to_browser` (Requirement 1.5).
    * For every ticker in ``session_state.watchlist`` it renders a row
      with the symbol, a 30-day sparkline, the formatted price, signed
      absolute change, signed percent change, and a remove button. When
      a ticker is missing from ``quotes`` the row shows the symbol and
      the status text "No data" but the ticker is **never** dropped
      from the watchlist (Requirement 1.7, 1.8).

    ``quotes`` is the dict returned by ``fetch_all_data()`` in
    ``streamlit_app.py``: a mapping of ticker symbol to a dict with at
    least the keys ``price``, ``change`` and ``pct_change``. The
    renderer reads from the dict by key and tolerates absent tickers
    without raising.

    Validates: Requirements 1.1, 1.7, 1.8
    """

    # Lazy-import the sparkline renderer so the watchlist module stays
    # importable in test environments that mock or skip plotly.
    from app.sparkline import render_sparkline

    # --- 1. Hydrate session state from localStorage (once per session).
    if _SESSION_WATCHLIST_KEY not in st.session_state:
        # First render in this session: pull the persisted list. The
        # streamlit_javascript bridge can return ``None`` on the very
        # first round-trip; ``load_from_browser`` already coerces that
        # to ``[]`` so the assignment is always a list.
        st.session_state[_SESSION_WATCHLIST_KEY] = load_from_browser()
        st.session_state[_SESSION_HYDRATED_KEY] = True

    watchlist: Watchlist = st.session_state[_SESSION_WATCHLIST_KEY]

    # --- 2. Section header.
    st.subheader("⭐ My Watchlist")

    # --- 3. Add-ticker form.
    # Using ``st.form`` so the Enter key submits the input the same way
    # as clicking the button, and so the input clears on a successful
    # submit.
    with st.form("watchlist_add_form", clear_on_submit=True):
        cols = st.columns([4, 1])
        raw_input = cols[0].text_input(
            "Add tickers (e.g. PLTR, MSFT)",
            key="watchlist_add_input",
            label_visibility="visible",
        )
        submitted = cols[1].form_submit_button("Add")

    if submitted and raw_input:
        new_wl, _accepted, rejected = add_tickers(watchlist, raw_input)
        if new_wl != watchlist:
            st.session_state[_SESSION_WATCHLIST_KEY] = new_wl
            watchlist = new_wl
            # Best-effort persistence — ``save_to_browser`` swallows
            # storage failures so the UI never breaks on a
            # private-mode block or quota error.
            save_to_browser(watchlist)
        # Surface one inline message per rejection, in input order.
        for tok, reason in rejected:
            severity, message = _format_rejection(tok, reason)
            if severity == "error":
                st.error(message)
            else:
                st.warning(message)

    # --- 4. Empty-state hint.
    if not watchlist:
        st.caption(
            "Your watchlist is empty. Add tickers above to start tracking."
        )
        return

    # --- 5. Per-ticker rows.
    # Layout per row: ticker | sparkline | price | change | pct | remove.
    # We collapse price/change/pct into a single metrics column when a
    # quote is available so the row stays readable on mobile; missing
    # quotes show "No data" in that same column.
    for ticker in list(watchlist):
        cols = st.columns([1.2, 1.5, 3, 0.8])
        cols[0].markdown(f"**{ticker}**")

        with cols[1]:
            # ``render_sparkline`` is responsible for its own placeholder
            # when fewer than two valid closes are available.
            render_sparkline(ticker)

        quote = quotes.get(ticker) if isinstance(quotes, dict) else None
        if isinstance(quote, dict) and quote.get("price") is not None:
            price = float(quote["price"])
            change = float(quote.get("change", 0.0))
            pct_change = float(quote.get("pct_change", 0.0))
            # Color the metrics with the same palette the sparkline uses
            # so the row reads consistently.
            color = "#10b981" if pct_change >= 0 else "#ef4444"
            cols[2].markdown(
                f"<div style='line-height:1.3'>"
                f"<span style='font-size:1.05rem;font-weight:600'>"
                f"{_format_price(price)}</span>"
                f" &nbsp; "
                f"<span style='color:{color}'>"
                f"{_format_signed_change(change)} "
                f"({_format_signed_pct(pct_change)})</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            # Requirement 1.8: render "No data" but keep the ticker.
            cols[2].markdown(
                "<span style='color:#94a3b8'>No data</span>",
                unsafe_allow_html=True,
            )

        # Per-row remove button. ``key`` must be unique per ticker so
        # Streamlit can attribute the click correctly across reruns.
        if cols[3].button("✕", key=f"watchlist_remove_{ticker}"):
            new_wl = remove_ticker(watchlist, ticker)
            st.session_state[_SESSION_WATCHLIST_KEY] = new_wl
            save_to_browser(new_wl)
            st.rerun()
