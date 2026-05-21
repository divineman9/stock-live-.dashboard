# Implementation Plan: Dashboard Pro Pack

## Overview

Build the Dashboard Pro Pack as eight features added non-invasively to the existing Stock Live Dashboard. New code lives in a new `app/` Python package alongside `streamlit_app.py`, plus PWA assets under `static/` and route extensions in the existing `server.py` Flask front-door. Each feature module ships pure-logic functions first (for property-based tests), then I/O (cached fetches), then the Streamlit renderer. The eleven correctness properties from the design map one-to-one to optional Hypothesis test sub-tasks placed close to the implementation they validate.

The work is sequenced so independent files are created in parallel in early waves, extensions to the same source files happen in later waves, and final wiring into `streamlit_app.py` happens last.

## Tasks

- [x] 1. Set up testing infrastructure and project structure
  - [x] 1.1 Create `app/` package, `tests/properties/` directory, and add dev dependencies
    - Create `app/__init__.py`
    - Create `tests/__init__.py`, `tests/properties/__init__.py`, `tests/conftest.py`
    - Add `pytest`, `hypothesis`, `responses`, `streamlit-javascript`, `plotly` to `requirements.txt` (or a `requirements-dev.txt`); pin versions consistent with `streamlit==1.57.0`
    - Add a `pytest.ini` (or `pyproject.toml [tool.pytest.ini_options]`) registering the `tests/` directory
    - _Requirements: foundation for all_

- [x] 2. Implement Custom Watchlist (Requirement 1)
  - [x] 2.1 Create `app/watchlist.py` with pure functions
    - Define `WATCHLIST_MAX_LEN = 50`, `WATCHLIST_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")`, `WATCHLIST_LS_KEY = "stockdash.watchlist.v1"`
    - Implement `parse_input(raw)` (split on commas, whitespace, newlines; trim; uppercase)
    - Implement `validate_ticker(t)` (regex match)
    - Implement `add_tickers(current, raw_input)` returning `(new_watchlist, accepted, rejected_with_reason)` with reasons `'invalid_format' | 'duplicate' | 'capacity_exceeded'`
    - Implement `remove_ticker(current, t)` (first-occurrence delete, idempotent)
    - _Requirements: 1.2, 1.3, 1.4, 1.9, 1.10_

  - [ ]* 2.2 Write property test for `validate_ticker`
    - **Property 2: validate_ticker equals the regex semantics**
    - **Validates: Requirement 1.9**
    - File: `tests/properties/test_property_02_validate_ticker.py`

  - [ ]* 2.3 Write property test for `add_tickers`
    - **Property 1: Watchlist add preserves invariants across arbitrary inputs**
    - **Validates: Requirements 1.2, 1.3, 1.9, 1.10**
    - File: `tests/properties/test_property_01_add_tickers.py`
    - Use Hypothesis strategies for current watchlists (length 0..50, distinct valid tickers) and arbitrary raw input strings; assert prefix preservation, no duplicates, length cap, and `accepted ∪ rejected_tokens == parse_input(raw)`

  - [ ]* 2.4 Write property test for `remove_ticker`
    - **Property 3: Watchlist remove is the first-occurrence delete**
    - **Validates: Requirement 1.4**
    - File: `tests/properties/test_property_03_remove_ticker.py`

  - [x] 2.5 Add browser-storage persistence to `app/watchlist.py`
    - Implement `load_from_browser()` using `streamlit_javascript` to read `localStorage[WATCHLIST_LS_KEY]`; return `[]` on `None`/parse error
    - Implement `save_to_browser(wl)` JSON-encoding `{"version": 1, "tickers": [...]}`; swallow errors
    - _Requirements: 1.5, 1.6_

  - [x] 2.6 Add `render_watchlist_section()` to `app/watchlist.py`
    - On first run hydrate `st.session_state.watchlist` from `load_from_browser()`
    - Render text input + remove buttons; on submit call `add_tickers`, surface inline errors per rejected reason, persist via `save_to_browser`
    - For each ticker render symbol, formatted price, absolute change, percent change from `quotes`; display "No data" when ticker is absent from `quotes`; never drop the ticker
    - _Requirements: 1.1, 1.7, 1.8_

  - [ ]* 2.7 Write property test for `render_watchlist` contract
    - **Property 4: Watchlist render contract over present and missing quotes**
    - **Validates: Requirements 1.7, 1.8**
    - File: `tests/properties/test_property_04_render_watchlist.py`
    - Use `streamlit.testing.v1.AppTest` (or a thin pure helper that returns the row dicts) to assert order preservation, formatted fields when quote present, and "No data" status when absent

- [x] 3. Implement Sparkline Mini-Charts (Requirement 2)
  - [x] 3.1 Create `app/sparkline.py` with series fetch and color logic
    - Define `SPARKLINE_TTL_S = 600`, `SPARKLINE_RANGE = "1mo"`, `SPARKLINE_INTERVAL = "1d"`, `SPARKLINE_W = 120`, `SPARKLINE_H = 40`, `COLOR_POSITIVE = "#10b981"`, `COLOR_NEGATIVE = "#ef4444"`
    - Implement `@st.cache_data(ttl=SPARKLINE_TTL_S)` `fetch_sparkline_series(ticker)` calling `/v8/finance/chart` with `interval=1d&range=1mo`; strip `None` closes; swallow exceptions and return `[]`
    - Implement `sparkline_color(series)` (positive iff `series[-1] >= series[0]`)
    - _Requirements: 2.2, 2.3, 2.5, 2.6_

  - [ ]* 3.2 Write property test for `sparkline_color`
    - **Property 5: Sparkline color reflects series direction**
    - **Validates: Requirements 2.5, 2.6**
    - File: `tests/properties/test_property_05_sparkline_color.py`

  - [x] 3.3 Add `render_sparkline(ticker)` and `render_sparkline_for_series(series)` to `app/sparkline.py`
    - Build `plotly.graph_objects.Figure` with `Scatter`, axes/legend hidden, margins zero, width ≤ 120, height ≤ 40, line color from `sparkline_color`
    - When series has fewer than 2 valid closes render the dash placeholder `"—"`
    - _Requirements: 2.1, 2.4, 2.7, 2.8_

  - [ ]* 3.4 Write property test for sparkline placeholder
    - **Property 6: Sparkline placeholder for insufficient data**
    - **Validates: Requirement 2.8**
    - File: `tests/properties/test_property_06_sparkline_placeholder.py`

- [x] 4. Implement Universal Search (Requirement 3)
  - [x] 4.1 Create `app/universal_search.py` with `search_tickers`
    - Define `US_EXCHANGES = {"NMS", "NYQ", "ASE", "BATS", "PCX", "NCM", "NGM"}`, `SEARCH_TTL_S = 300`, `SEARCH_MIN_CHARS = 2`, `SEARCH_MAX_RESULTS = 10`
    - Implement `@st.cache_data(ttl=SEARCH_TTL_S)` `search_tickers(query)` calling `/v1/finance/search`; filter `quoteType == "EQUITY"` and `exchange in US_EXCHANGES`; cap at 10; return `[]` for `len(query) < 2` or any error; each result is `{symbol, name, exchange}` with `name = shortname or longname or symbol`
    - _Requirements: 3.2, 3.3, 3.5, 3.8_

  - [ ]* 4.2 Write property test for `search_tickers` filter
    - **Property 7: Universal search results respect query, filter, and shape**
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.6**
    - File: `tests/properties/test_property_07_search_tickers.py`
    - Use `responses` (or monkeypatched `requests.get`) to feed Hypothesis-generated Yahoo payloads with mixed `quoteType`/`exchange` rows; assert filter, cap, and required keys

  - [x] 4.3 Add `fetch_detail(ticker)` to `app/universal_search.py`
    - Call existing `fetch_chart_single` for live price/change; pull `regularMarketDayLow`, `regularMarketDayHigh`, `fiftyTwoWeekLow`, `fiftyTwoWeekHigh`, `averageDailyVolume3Month` from chart `meta`
    - Return `{ticker, price, prev_close, change, pct_change, day_low, day_high, w52_low, w52_high, avg_daily_volume}` or `None` on error
    - _Requirements: 3.6_

  - [x] 4.4 Add `render_universal_search()` to `app/universal_search.py`
    - Text input labeled "Search any US-listed ticker" at a fixed location
    - Display each suggestion as `"{symbol} — {name}"`; on selection store ticker in `st.session_state.universal_search_selection`
    - Detail panel renders symbol, price, abs change, pct change, prev close, day's range, 52-week range, avg daily volume, plus 30-day Sparkline via `render_sparkline`
    - Empty-result message: `"No US-listed equities found"`; HTTP-error message: `"Search temporarily unavailable"` (preserve any existing detail panel)
    - "Add to Watchlist" button invokes `watchlist.add_tickers` against `st.session_state.watchlist` and persists
    - _Requirements: 3.1, 3.4, 3.6, 3.7, 3.8, 3.9_

- [x] 5. Implement Pre-Market Gappers (Requirement 4)
  - [x] 5.1 Create `app/premarket_gappers.py` with `compute_gappers`
    - Define `GAPPERS_TTL_S = 60`, `GAPPER_THRESHOLD_PCT = 2.0`, `GAPPER_TOP_N = 10`
    - Implement `compute_gappers(quotes)` returning `(up, down)` filtered to TRACKED_STOCKS (exclude `INDICES` and `MACRO_TICKERS`), `pct_change >= +2.0` desc for up and `pct_change <= -2.0` asc for down, capped at top 10
    - _Requirements: 4.3, 4.4, 4.5, 4.6_

  - [ ]* 5.2 Write property test for `compute_gappers`
    - **Property 8: Pre-market gappers partition, sort, and cap**
    - **Validates: Requirements 4.3, 4.4, 4.5, 4.6**
    - File: `tests/properties/test_property_08_compute_gappers.py`
    - Use the strategy scaffold from the design's Testing Strategy section; assert disjointness, ordering, length caps, and exact "top-N of qualifying" semantics

  - [x] 5.3 Add `fetch_premarket_quotes()` to `app/premarket_gappers.py`
    - `@st.cache_data(ttl=GAPPERS_TTL_S)` parallel calls to existing `fetch_chart_single` for TRACKED_STOCKS with `includePrePost=true`
    - Return same dict shape as `fetch_all_data()` output
    - _Requirements: 4.7_

  - [x] 5.4 Add `render_premarket_gappers()` to `app/premarket_gappers.py`
    - If `get_market_phase() != "premarket"` render nothing
    - Otherwise render two ranked tables (ticker, pre-market price, pct change, pre-market volume)
    - Empty-list message: `"No qualifying gappers"` per side
    - _Requirements: 4.1, 4.2, 4.6, 4.8_

- [x] 6. Checkpoint - validate pure-function layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement PWA shell (Requirement 5)
  - [x] 7.1 Create `static/manifest.json`
    - Fields: `name = "Stock Live Dashboard"`, `short_name = "StockDash"`, `start_url = "/"`, `scope = "/"`, `display = "standalone"`, `background_color = "#0f172a"`, `theme_color = "#0f172a"`
    - Icons array with 192×192, 512×512, and a 512×512 maskable PNG
    - _Requirements: 5.1_

  - [x] 7.2 Create `static/service-worker.js`
    - Cache name `stockdash-shell-v1`; precache `SHELL` list (`/manifest.json`, three icon URLs, `/static/pwa-register.js`)
    - `install`: `cache.addAll(SHELL)` + `skipWaiting`
    - `activate`: delete caches whose name != current + `clients.claim`
    - `fetch`: cache-first for paths in `SHELL`, network passthrough otherwise
    - _Requirements: 5.4, 5.5, 5.6, 5.7_

  - [x] 7.3 Create `static/pwa-register.js`
    - Guard with `if ('serviceWorker' in navigator)`; register `/service-worker.js` with `scope: '/'` on `window.load`; `.catch(console.warn)`
    - _Requirements: 5.3_

  - [x] 7.4 Create PWA icon assets under `static/icons/`
    - `static/icons/icon-192.png` (192×192 PNG)
    - `static/icons/icon-512.png` (512×512 PNG)
    - `static/icons/icon-maskable-512.png` (512×512 PNG with maskable safe area)
    - Use a script (e.g., `tools/make_icons.py` with Pillow) committed alongside the binaries so the icons are reproducible
    - _Requirements: 5.1_

  - [x] 7.5 Extend `server.py` with PWA routes
    - Add `/manifest.json` route serving `static/manifest.json` with `mimetype="application/manifest+json"`
    - Add `/service-worker.js` route with `mimetype="application/javascript"`, header `Service-Worker-Allowed: /`, `Cache-Control: no-cache`
    - Ensure `/static/<path:filename>` route exists and reorder existing catch-all so it does not shadow the new routes
    - _Requirements: 5.1, 5.3_

  - [ ]* 7.6 Write smoke tests for PWA routes
    - File: `tests/test_pwa_routes.py`
    - Use Flask test client to GET `/manifest.json` and assert presence of `name`, `short_name`, `start_url`, `display ∈ {standalone, fullscreen, minimal-ui}`, and at least one 192×192 + one 512×512 icon
    - GET `/service-worker.js` and assert `Service-Worker-Allowed: /` header
    - _Requirements: 5.1, 5.3, 5.8_

- [x] 8. Implement Mobile-First Responsive CSS (Requirement 6)
  - [x] 8.1 Author `static/styles.css`
    - `@media (max-width: 767px)` block: stack `.stColumns > div` to 100% width, stack gainers/losers panels vertically, body `font-size: 14px`, headings `font-size: 16px`, `[data-testid="stTable"], [data-testid="stDataFrame"] { max-width: 100%; overflow-x: auto; }`, `button, [role="button"], a.stButton { min-width: 44px; min-height: 44px; }`
    - No overrides for ≥ 768px so existing layout is preserved
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [x] 9. Implement AI Daily Market Summary (Requirement 7)
  - [x] 9.1 Create `app/daily_summary.py` with pure summary logic
    - Define `SUMMARY_MIN_WORDS = 60`, `SUMMARY_MAX_WORDS = 200`
    - Define `@dataclass SummaryInputs` with fields `spy, qqq, sector_avg, top_gainers, top_losers, headlines`
    - Implement `build_summary_inputs(quotes, headlines)` (filter indices/macro from sector calc; top 3 gainers desc; bottom 3 losers asc; ≤ 2 headlines)
    - Implement `compose_summary(inp)` per the design's pseudocode (conditional sentences, word-count clamp 60..200, empty-input case returns `""`)
    - Implement `should_auto_generate(now_et, last_run_date)` returning `True` exactly once per (session, trading day) when ET ≥ 16:00
    - _Requirements: 7.4, 7.5, 7.6, 7.7, 7.8_

  - [x] 9.2 Add `render_daily_summary(quotes, headlines)` to `app/daily_summary.py`
    - Render section with paragraph + "Regenerate" button; on click recompose from latest cache
    - On rerun, if `should_auto_generate(...)` is `True`, generate once and set the per-day session flag
    - When `compose_summary` returns `""`, render `"Summary unavailable — market data not loaded yet"`
    - _Requirements: 7.1, 7.2, 7.3, 7.8_

  - [ ]* 9.3 Write property test for `compose_summary`
    - **Property 9: Daily summary content, length, and omission rules**
    - **Validates: Requirements 7.3, 7.5, 7.6, 7.7, 7.8**
    - File: `tests/properties/test_property_09_compose_summary.py`
    - Hypothesis strategy generates `SummaryInputs` with arbitrary subsets present; assert 60..200 word count when at least one input is non-empty, presence of every present ticker, no placeholders for absent fields, ≤ 2 headlines referenced, and empty string for fully-empty inputs
    - Add a sub-assertion exercising `should_auto_generate` over a sequence of clock samples crossing 16:00 ET to confirm exactly-once-per-(session, trading day) firing

- [x] 10. Implement Volatility Scanner (Requirement 8)
  - [x] 10.1 Create `app/volatility_scanner.py` with `scan`
    - Define `VOLATILITY_MIN = 1.0`, `VOLATILITY_MAX = 20.0`, `VOLATILITY_DEFAULT = 3.0`
    - Implement `scan(quotes, headlines, threshold_pct)` excluding `INDICES ∪ MACRO_TICKERS`, filtering `abs(pct_change) >= threshold`, sorting by `abs(pct_change)` desc; resolve `reason` as the first headline (in input order) whose `match_to_tickers(title, [ticker])` is non-empty, truncated to 100 chars, else `"—"`
    - Make zero outbound HTTP calls
    - _Requirements: 8.2, 8.3, 8.4, 8.5, 8.6, 8.9_

  - [ ]* 10.2 Write property test for `scan` filter and sort
    - **Property 10: Volatility scan filter and sort**
    - **Validates: Requirements 8.2, 8.3, 8.4, 8.9**
    - File: `tests/properties/test_property_10_scan_filter.py`
    - Patch `requests.get` to raise; assert it is never called; assert membership and ordering invariants

  - [ ]* 10.3 Write property test for `scan` reason resolution
    - **Property 11: Volatility scan reason resolution**
    - **Validates: Requirements 8.5, 8.6**
    - File: `tests/properties/test_property_11_scan_reason.py`
    - Generate ranked headline lists with controlled ticker mentions; assert first-match-wins, 100-char truncation, and `"—"` fallback

  - [x] 10.4 Add `render_volatility_scanner(quotes, headlines)` to `app/volatility_scanner.py`
    - `st.number_input` for threshold (`min_value=1.0, max_value=20.0, step=0.5, value=3.0`)
    - Render `scan(...)` rows as a table with columns `Ticker, % Change, Price, Reason`
    - Empty-result message: `"No stocks above threshold"`
    - _Requirements: 8.1, 8.7, 8.8_

- [x] 11. Wire features and PWA shell into the Streamlit app
  - [x] 11.1 Inject PWA + responsive head into `streamlit_app.py`
    - Near the top of the page (before any section render) call `streamlit.components.v1.html(...)` with `<link rel="manifest" href="/manifest.json">`, `<meta name="theme-color" content="#0f172a">`, `<meta name="viewport" content="width=device-width, initial-scale=1.0">`, `<script src="/static/pwa-register.js" defer></script>`, plus a `<style>` block whose contents are read from `static/styles.css`
    - Set `height=0` so the injection is invisible
    - _Requirements: 5.2, 5.3, 6.7_

  - [x] 11.2 Wire all feature sections into the page render in `streamlit_app.py`
    - Import and call `watchlist.render_watchlist_section`, `universal_search.render_universal_search`, `premarket_gappers.render_premarket_gappers`, `daily_summary.render_daily_summary`, `volatility_scanner.render_volatility_scanner`
    - Replace the existing per-row chart placeholder in the Top Gainers / Top Losers / Watchlist tables with `sparkline.render_sparkline(ticker)` calls
    - Re-use the existing `fetch_all_data()` and `fetch_macro_headlines()` cached calls as inputs (no new HTTP)
    - _Requirements: 1.1, 2.1, 3.1, 4.1, 7.1, 8.1_

- [x] 12. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP path; they cover the eleven property tests (one per design property) plus PWA route smoke tests.
- Each property test sub-task references its property number from the design and the requirement clauses it validates, per the design's Correctness Properties section.
- Property tests live under `tests/properties/test_property_NN_<name>.py` (one file per property) so they can run in parallel without write conflicts.
- Module extensions (persistence, fetchers, renderers) are split into separate sub-tasks from the initial pure-function module so they land in different waves and avoid same-file write conflicts during parallel execution.
- Checkpoints (tasks 6 and 12) and top-level epic headers are excluded from the dependency graph; only leaf decimal sub-tasks appear in the waves.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "5.1", "7.1", "7.2", "7.3", "7.4", "7.5", "8.1", "9.1", "10.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "2.5", "3.2", "3.3", "4.2", "4.3", "5.2", "5.3", "7.6", "9.2", "9.3", "10.2", "10.3", "10.4"] },
    { "id": 3, "tasks": ["2.6", "3.4", "4.4", "5.4"] },
    { "id": 4, "tasks": ["2.7", "11.1"] },
    { "id": 5, "tasks": ["11.2"] }
  ]
}
```
