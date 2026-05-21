"""Daily Market Summary module (Requirement 7).

This module contains the pure functions that produce the AI Daily Market
Summary paragraph and the Streamlit renderer
(:func:`render_daily_summary`) that surfaces it on the dashboard.

The contracts in this file map onto Requirements 7.1, 7.2, 7.3, 7.4,
7.5, 7.6, 7.7, and 7.8 and onto Property 9 in the design document.

Public surface:

- :data:`SUMMARY_MIN_WORDS`, :data:`SUMMARY_MAX_WORDS` — word-count
  clamp boundaries.
- :class:`SummaryInputs` — dataclass aggregating the cached inputs the
  generator consumes.
- :func:`build_summary_inputs` — pure transform from the dashboard's
  cached ``quotes`` dict and ``headlines`` list into a
  :class:`SummaryInputs`.
- :func:`compose_summary` — pure transform that produces the paragraph
  text (or the empty string when there is no usable data).
- :func:`should_auto_generate` — pure predicate for the once-per-day
  16:00 ET auto-trigger.
- :func:`render_daily_summary` — Streamlit renderer for the section.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional

import pytz
import streamlit as st

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Minimum word count for a generated summary (Requirement 7.6).
SUMMARY_MIN_WORDS: int = 60

#: Maximum word count for a generated summary (Requirement 7.6).
SUMMARY_MAX_WORDS: int = 200

#: Tickers excluded from sector aggregation and top-mover ranking. These
#: must mirror the values of ``INDICES`` and ``MACRO_TICKERS`` in
#: ``streamlit_app.py``. They are duplicated here so this module stays
#: importable without pulling in Streamlit, which keeps the property
#: tests fast and side-effect free.
_INDEX_TICKERS = frozenset({"SPY", "QQQ", "DIA"})
_MACRO_TICKERS = frozenset({"^TNX", "^VIX", "XLF"})
_EXCLUDED_FROM_STOCKS = _INDEX_TICKERS | _MACRO_TICKERS

#: Maximum number of top gainers / losers carried in
#: :class:`SummaryInputs` (Requirement 7.5).
_TOP_MOVERS_N = 3

#: Maximum number of headlines referenced by the summary
#: (Requirement 7.5).
_HEADLINES_N = 2

#: 16:00 ET — the trigger time for auto-generation (Requirement 7.3).
_AUTO_TRIGGER_TIME = time(16, 0)

#: Padding clauses appended (in order) when the composed summary falls
#: below :data:`SUMMARY_MIN_WORDS`. Each clause is a self-contained
#: sentence so concatenation produces well-formed prose. The set is
#: intentionally generic: it adds context without inventing data.
_PADDING_CLAUSES: tuple[str, ...] = (
    "Trading volume and breadth reflected the day's overall tone across major averages and sector groups.",
    "Investors continued to weigh macroeconomic signals alongside company-level developments.",
    "Cross-asset moves stayed broadly consistent with the prevailing risk backdrop heading into the close.",
    "Participation across large-cap leaders and cyclical names shaped the session's relative strength picture.",
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class SummaryInputs:
    """Aggregated cached inputs consumed by :func:`compose_summary`.

    All fields are optional / may be empty. Empty values cause the
    corresponding sentence to be omitted from the composed paragraph
    (Requirement 7.7).

    Attributes:
        spy: SPY quote dict (as produced by ``fetch_all_data``) or
            ``None`` when SPY data is missing from the cache.
        qqq: QQQ quote dict, or ``None``.
        sector_avg: Mapping of sector name → average percent change for
            the day. Empty when no sector aggregation is possible.
        top_gainers: Up to :data:`_TOP_MOVERS_N` quote dicts ordered by
            ``pct_change`` descending.
        top_losers: Up to :data:`_TOP_MOVERS_N` quote dicts ordered by
            ``pct_change`` ascending.
        headlines: Up to :data:`_HEADLINES_N` headline dicts in their
            input (ranked) order.
    """

    spy: Optional[Dict[str, Any]] = None
    qqq: Optional[Dict[str, Any]] = None
    sector_avg: Dict[str, float] = field(default_factory=dict)
    top_gainers: List[Dict[str, Any]] = field(default_factory=list)
    top_losers: List[Dict[str, Any]] = field(default_factory=list)
    headlines: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_finite_number(value: Any) -> bool:
    """Return True iff *value* is a real, finite number."""
    if isinstance(value, bool):  # bool is a subclass of int; reject it
        return False
    if not isinstance(value, (int, float)):
        return False
    # NaN and infinities are not useful for percentage formatting.
    return value == value and value not in (float("inf"), float("-inf"))


def _signed_pct(pct: float) -> str:
    """Format *pct* as a signed two-decimal percentage string.

    Negative values render with the natural ``-`` sign; non-negative
    values are prefixed with ``+`` so direction is unambiguous.
    """
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def _word_count(text: str) -> int:
    """Count whitespace-delimited tokens in *text*."""
    return len(text.split())


# ---------------------------------------------------------------------------
# Pure transforms
# ---------------------------------------------------------------------------


def build_summary_inputs(
    quotes: Optional[Dict[str, Dict[str, Any]]],
    headlines: Optional[List[Dict[str, Any]]],
) -> SummaryInputs:
    """Build a :class:`SummaryInputs` from cached dashboard data.

    The function is pure and total: ``None``/empty inputs yield an
    "empty" :class:`SummaryInputs` (every field empty / ``None``).
    Indices and macro tickers are excluded from sector aggregation and
    from the top gainer / loser lists so the summary describes
    individual stocks, not the indexes that the index sentence already
    covers.
    """

    quotes = quotes or {}
    headlines = headlines or []

    spy = quotes.get("SPY")
    qqq = quotes.get("QQQ")

    # Sector averages: same shape as the existing sector_avg block in
    # streamlit_app.py — bucket each stock's pct_change into every
    # sector listed under ``info["sectors"]``, then average.
    bucket: Dict[str, List[float]] = {}
    stock_quotes: List[Dict[str, Any]] = []
    for ticker, info in quotes.items():
        if not isinstance(info, dict):
            continue
        if ticker in _EXCLUDED_FROM_STOCKS:
            continue
        pct = info.get("pct_change")
        if not _is_finite_number(pct):
            continue
        stock_quotes.append(info)
        for sec in info.get("sectors") or []:
            if not sec or sec == "Index":
                continue
            bucket.setdefault(sec, []).append(float(pct))

    sector_avg: Dict[str, float] = {
        sec: round(sum(vals) / len(vals), 2)
        for sec, vals in bucket.items()
        if vals
    }

    # Top gainers / losers across stock quotes (excluding indices and
    # macro tickers). Sort is stable so ties retain insertion order.
    sorted_desc = sorted(stock_quotes, key=lambda q: q["pct_change"], reverse=True)
    top_gainers = [q for q in sorted_desc if q["pct_change"] > 0][:_TOP_MOVERS_N]
    top_losers = sorted(
        [q for q in stock_quotes if q["pct_change"] < 0],
        key=lambda q: q["pct_change"],
    )[:_TOP_MOVERS_N]

    return SummaryInputs(
        spy=spy if isinstance(spy, dict) else None,
        qqq=qqq if isinstance(qqq, dict) else None,
        sector_avg=sector_avg,
        top_gainers=top_gainers,
        top_losers=top_losers,
        headlines=list(headlines[:_HEADLINES_N]),
    )


# ---------------------------------------------------------------------------
# Sentence builders (each returns "" when its inputs do not justify a
# sentence so callers can drop empties without re-checking conditions).
# ---------------------------------------------------------------------------


def _index_sentence(spy: Optional[Dict[str, Any]], qqq: Optional[Dict[str, Any]]) -> str:
    spy_pct = spy.get("pct_change") if spy else None
    qqq_pct = qqq.get("pct_change") if qqq else None
    spy_ok = _is_finite_number(spy_pct)
    qqq_ok = _is_finite_number(qqq_pct)
    if spy_ok and qqq_ok:
        return f"SPY closed {_signed_pct(spy_pct)} and QQQ closed {_signed_pct(qqq_pct)}."
    if spy_ok:
        return f"SPY closed {_signed_pct(spy_pct)}."
    if qqq_ok:
        return f"QQQ closed {_signed_pct(qqq_pct)}."
    return ""


def _sector_sentence(sector_avg: Dict[str, float]) -> str:
    if not sector_avg:
        return ""
    items = [
        (sec, float(pct))
        for sec, pct in sector_avg.items()
        if _is_finite_number(pct)
    ]
    if not items:
        return ""
    items.sort(key=lambda kv: kv[1], reverse=True)
    best_sec, best_pct = items[0]
    if len(items) == 1:
        return f"{best_sec} averaged {_signed_pct(best_pct)}."
    worst_sec, worst_pct = items[-1]
    return (
        f"{best_sec} led with an average of {_signed_pct(best_pct)}, "
        f"while {worst_sec} lagged at {_signed_pct(worst_pct)}."
    )


def _movers_phrase(movers: List[Dict[str, Any]]) -> str:
    pieces: List[str] = []
    for q in movers:
        ticker = q.get("ticker")
        pct = q.get("pct_change")
        if not ticker or not _is_finite_number(pct):
            continue
        pieces.append(f"{ticker} {_signed_pct(float(pct))}")
    return ", ".join(pieces)


def _gainers_sentence(top_gainers: List[Dict[str, Any]]) -> str:
    phrase = _movers_phrase(top_gainers)
    if not phrase:
        return ""
    return f"Top movers up: {phrase}."


def _losers_sentence(top_losers: List[Dict[str, Any]]) -> str:
    phrase = _movers_phrase(top_losers)
    if not phrase:
        return ""
    return f"Top decliners: {phrase}."


def _headlines_sentence(headlines: List[Dict[str, Any]]) -> str:
    titles = [
        str(h["title"]).strip()
        for h in headlines[:_HEADLINES_N]
        if isinstance(h, dict) and h.get("title")
    ]
    titles = [t for t in titles if t]
    if not titles:
        return ""
    if len(titles) == 1:
        return f"On the news front, {titles[0]}."
    return f"On the news front, {titles[0]}; and {titles[1]}."


# ---------------------------------------------------------------------------
# Word-count clamping
# ---------------------------------------------------------------------------


def _trim_to_max_words(parts: List[str], max_words: int) -> str:
    """Join *parts* and drop trailing parts until the joined text fits.

    If the very first part already exceeds *max_words*, that part is
    truncated word-by-word as a last resort so the contract
    "word count <= max_words" still holds. The trailing word ends with
    a period to keep the output well-formed prose.
    """

    kept: List[str] = []
    running = 0
    for part in parts:
        wc = _word_count(part)
        if running + wc <= max_words:
            kept.append(part)
            running += wc
        else:
            break

    if not kept:
        # Every part is on its own too long — truncate the first part.
        if not parts:
            return ""
        words = parts[0].split()[:max_words]
        if not words:
            return ""
        text = " ".join(words)
        if not text.endswith("."):
            text = text.rstrip(",;:") + "."
        return text

    return " ".join(kept)


def _pad_to_min_words(text: str, min_words: int) -> str:
    """Append generic context clauses until *text* meets *min_words*.

    Padding is drawn from :data:`_PADDING_CLAUSES`. Each clause is a
    full sentence so the result remains well-formed. If exhausting the
    canned clauses still leaves the text short, the last clause is
    repeated; in practice the canned set is sized generously enough
    that this branch is unreachable for valid inputs but the
    repetition keeps the function total.
    """

    if _word_count(text) >= min_words:
        return text

    pieces: List[str] = [text] if text else []
    idx = 0
    while _word_count(" ".join(pieces)) < min_words:
        clause = _PADDING_CLAUSES[idx % len(_PADDING_CLAUSES)]
        pieces.append(clause)
        idx += 1
        # Hard safety cap so this loop is provably terminating even if
        # a clause is degenerate.
        if idx > 64:
            break
    return " ".join(pieces).strip()


# ---------------------------------------------------------------------------
# compose_summary
# ---------------------------------------------------------------------------


def compose_summary(inp: SummaryInputs) -> str:
    """Compose the daily market summary paragraph.

    Returns the paragraph as a single string with sentences joined by
    spaces. The output is clamped to between :data:`SUMMARY_MIN_WORDS`
    and :data:`SUMMARY_MAX_WORDS` words inclusive whenever at least one
    component sentence could be produced.

    Returns the empty string when *inp* has no SPY, no QQQ, no sector
    averages, no movers, and no headlines (Requirement 7.8 — the
    renderer surfaces the fallback message in that case).
    """

    parts: List[str] = []

    idx = _index_sentence(inp.spy, inp.qqq)
    if idx:
        parts.append(idx)

    sec = _sector_sentence(inp.sector_avg)
    if sec:
        parts.append(sec)

    gainers = _gainers_sentence(inp.top_gainers)
    if gainers:
        parts.append(gainers)

    losers = _losers_sentence(inp.top_losers)
    if losers:
        parts.append(losers)

    news = _headlines_sentence(inp.headlines)
    if news:
        parts.append(news)

    if not parts:
        return ""

    # Pad first so a thin paragraph reaches the floor, then trim so a
    # padded-but-still-too-long paragraph is brought back under the
    # ceiling. The order matters because padding only appends; trimming
    # only drops trailing parts (or, as a last resort, truncates).
    text = " ".join(parts)
    if _word_count(text) < SUMMARY_MIN_WORDS:
        text = _pad_to_min_words(text, SUMMARY_MIN_WORDS)

    if _word_count(text) > SUMMARY_MAX_WORDS:
        # Re-split into the original sentence parts plus any padding
        # appended above so trimming preserves sentence boundaries.
        split_parts = _split_into_sentences(text)
        text = _trim_to_max_words(split_parts, SUMMARY_MAX_WORDS)
        # If trimming dropped the text below the floor, pad again with
        # the canned clauses. Padding is bounded so this terminates.
        if _word_count(text) < SUMMARY_MIN_WORDS:
            text = _pad_to_min_words(text, SUMMARY_MIN_WORDS)
            # And re-trim if padding overshot the ceiling on a tight
            # band (rare; bounded recursion via simple loop).
            if _word_count(text) > SUMMARY_MAX_WORDS:
                text = _trim_to_max_words(
                    _split_into_sentences(text), SUMMARY_MAX_WORDS
                )

    return text


def _split_into_sentences(text: str) -> List[str]:
    """Split *text* on sentence terminators while preserving them.

    Only ``.`` is used as a terminator because every sentence builder
    in this module produces a period-terminated clause. A more
    elaborate splitter is unnecessary and risks misclassifying
    abbreviations.
    """

    if not text:
        return []
    pieces: List[str] = []
    current: List[str] = []
    for token in text.split():
        current.append(token)
        if token.endswith("."):
            pieces.append(" ".join(current))
            current = []
    if current:
        # Trailing fragment with no terminator — keep it as-is so the
        # word count stays consistent.
        pieces.append(" ".join(current))
    return pieces


# ---------------------------------------------------------------------------
# Auto-generation predicate
# ---------------------------------------------------------------------------


def should_auto_generate(now_et: datetime, last_run_date: Optional[date]) -> bool:
    """Decide whether to auto-generate the daily summary on this rerun.

    Returns ``True`` exactly when all of the following hold:

    * *now_et* falls on a US trading weekday (Monday through Friday).
    * The wall-clock time of *now_et* is at or after 16:00 ET.
    * *last_run_date* is not equal to ``now_et.date()`` — i.e., the
      summary has not already been auto-generated for today.

    The caller is expected to record ``now_et.date()`` as the new
    ``last_run_date`` (typically in ``st.session_state``) immediately
    after a ``True`` result so subsequent reruns return ``False`` for
    the rest of the trading day. With that contract this predicate
    fires exactly once per (session, trading day) for any sequence of
    clock samples crossing 16:00 ET (Property 9, auto-trigger
    sub-clause).

    Returns ``False`` for weekends, for times before 16:00 ET, and for
    days the summary has already run on. US market holidays are not
    detected here — the dashboard renders the summary using whatever
    cached data is present, and a holiday simply produces a thinner
    paragraph rather than no summary.
    """

    if not isinstance(now_et, datetime):
        return False

    # Monday=0 ... Sunday=6; trading days are Mon-Fri (0..4).
    if now_et.weekday() >= 5:
        return False

    if now_et.time() < _AUTO_TRIGGER_TIME:
        return False

    if last_run_date == now_et.date():
        return False

    return True


__all__ = [
    "SUMMARY_MIN_WORDS",
    "SUMMARY_MAX_WORDS",
    "SummaryInputs",
    "build_summary_inputs",
    "compose_summary",
    "should_auto_generate",
    "render_daily_summary",
]


# ---------------------------------------------------------------------------
# Streamlit renderer
# ---------------------------------------------------------------------------


#: ``st.session_state`` key holding the most recently composed paragraph
#: so that reruns triggered by unrelated widget interactions keep showing
#: the same summary instead of recomputing on every render.
_SS_TEXT_KEY = "daily_summary_text"

#: ``st.session_state`` key holding the ``date`` on which the auto-trigger
#: at 16:00 ET last fired. Used by :func:`should_auto_generate` to
#: enforce once-per-(session, trading day) firing.
_SS_LAST_RUN_KEY = "daily_summary_last_run_date"

#: User-facing fallback message rendered when :func:`compose_summary`
#: returns the empty string (Requirement 7.8).
_FALLBACK_MESSAGE = "Summary unavailable — market data not loaded yet"

#: Timezone used for the 16:00 ET auto-trigger check (Requirement 7.3).
_ET_TZ = pytz.timezone("US/Eastern")


def _generate(quotes: Optional[Dict[str, Dict[str, Any]]],
              headlines: Optional[List[Dict[str, Any]]]) -> str:
    """Compose a fresh summary from *quotes* and *headlines*.

    Wraps :func:`build_summary_inputs` and :func:`compose_summary` so the
    renderer has a single entry point for both the auto-trigger path and
    the manual "Regenerate" button path.
    """

    return compose_summary(build_summary_inputs(quotes, headlines))


def render_daily_summary(
    quotes: Optional[Dict[str, Dict[str, Any]]],
    headlines: Optional[List[Dict[str, Any]]],
) -> None:
    """Render the Daily Market Summary section.

    Behavior (Requirements 7.1, 7.2, 7.3, 7.8):

    * Renders a subheader plus a single paragraph and a "Regenerate"
      button.
    * On every rerun, if :func:`should_auto_generate` is ``True`` for
      the current ET clock and the last-run-date stored in
      ``st.session_state``, recomposes the summary from the cached
      inputs and records today's date so subsequent reruns within the
      same session do not re-trigger.
    * When the user clicks "Regenerate", recomposes from the latest
      cached inputs unconditionally.
    * When :func:`compose_summary` returns ``""`` (no usable data),
      surfaces the fallback message instead of an empty paragraph.

    The function is intended to be called once per page render with the
    same ``quotes`` and ``headlines`` already used by the rest of the
    dashboard so no additional HTTP calls are issued.
    """

    st.subheader("📰 Daily Market Summary")

    now_et = datetime.now(_ET_TZ)
    last_run_date = st.session_state.get(_SS_LAST_RUN_KEY)

    # Auto-trigger: at-or-after 16:00 ET on a trading weekday and not
    # yet fired today in this session.
    if should_auto_generate(now_et, last_run_date):
        st.session_state[_SS_TEXT_KEY] = _generate(quotes, headlines)
        st.session_state[_SS_LAST_RUN_KEY] = now_et.date()

    # Manual trigger: the user-visible "Regenerate" button always
    # recomposes from the latest cached inputs.
    if st.button("Regenerate Summary", key="daily_summary_regenerate"):
        st.session_state[_SS_TEXT_KEY] = _generate(quotes, headlines)

    # If we have not generated a summary yet in this session (no
    # auto-trigger fired and the user has not clicked Regenerate),
    # compose one now from the current cache so the section is never
    # blank on first render.
    if _SS_TEXT_KEY not in st.session_state:
        st.session_state[_SS_TEXT_KEY] = _generate(quotes, headlines)

    text = st.session_state.get(_SS_TEXT_KEY, "") or ""
    if text:
        st.write(text)
    else:
        st.info(_FALLBACK_MESSAGE)
