# Requirements Document

## Introduction

The Dashboard Pro Pack adds eight features to the existing Stock Live Dashboard Streamlit application (`streamlit_app.py`). The pack is organized into four categories: Trading Insights (Custom Watchlist, Sparkline Mini-Charts), Discovery (Universal Stock Search, Pre-Market Gappers), Performance (PWA Support, Mobile-First Responsive Tweaks), and Smart/AI (AI Daily Market Summary, Volatility Scanner).

All features integrate with the existing Streamlit dashboard as additional sections or panels, reuse the Yahoo Finance endpoints already in use (`/v8/finance/spark`, `/v8/finance/chart`, `/v1/finance/search`), and run within the constraints of the Render free tier (no paid APIs, no expensive compute, no persistent server-side storage beyond Streamlit's in-process cache).

## Glossary

- **Dashboard**: The Stock Live Dashboard Streamlit application defined in `streamlit_app.py`.
- **User**: An end user accessing the Dashboard through a web browser.
- **Yahoo_Finance**: The external Yahoo Finance HTTP API used by the Dashboard for quotes, charts, and search.
- **Tracked_Stocks**: The set of ticker symbols defined in the `STOCKS` dictionary plus `INDICES` plus `MACRO_TICKERS` in `streamlit_app.py`.
- **Watchlist**: A user-defined ordered collection of ticker symbols persisted across browser sessions for the same User.
- **Watchlist_Manager**: The Dashboard component that adds, removes, validates, persists, and renders Watchlist tickers.
- **Sparkline**: A small inline line chart rendered next to a stock row showing that ticker's daily closing prices over the last 30 trading days.
- **Sparkline_Renderer**: The Dashboard component that fetches and renders Sparklines.
- **Universal_Search**: The Dashboard component that lets a User search for any US-listed ticker using a text input with autocomplete suggestions.
- **Search_Result**: A single autocomplete entry returned by Universal_Search containing at minimum a ticker symbol and a display name.
- **Pre_Market**: The market phase between 4:00 AM ET and 9:20 AM ET on US trading weekdays, as returned by `get_market_phase()` with value `"premarket"`.
- **Pre_Market_Gappers**: The Dashboard section listing the top 10 Tracked_Stocks gapping up and the top 10 gapping down by percent change versus prior close during Pre_Market.
- **PWA**: Progressive Web App, an installable web application using a Web App Manifest and a Service Worker per the W3C / WHATWG specifications.
- **Service_Worker**: A JavaScript script registered by the Dashboard that caches static assets to enable offline launch of the app shell.
- **Manifest**: A `manifest.json` file describing the PWA name, icons, start URL, display mode, and theme colors.
- **Mobile_Viewport**: A browser viewport with a CSS width strictly less than 768 pixels.
- **Daily_Summary**: A rule-based written paragraph summarizing the trading day generated from existing Dashboard data (index changes, sector averages, top movers, headlines).
- **Daily_Summary_Generator**: The Dashboard component that produces a Daily_Summary.
- **Market_Close**: 16:00 ET (4:00 PM ET) on a US trading weekday.
- **Volatility_Scanner**: The Dashboard section that scans Tracked_Stocks and lists each ticker whose absolute intraday percent change is at least 3.0%.
- **Volatility_Threshold**: The configurable absolute percent change threshold used by Volatility_Scanner, defaulting to 3.0%.
- **Session_State**: Streamlit's per-session in-memory key-value store (`st.session_state`).
- **Browser_Storage**: The browser's `localStorage` API, accessed from Streamlit via the `streamlit_javascript` package or an equivalent component.
- **ET**: The `US/Eastern` IANA timezone.

## Requirements

### Requirement 1: Custom Watchlist

**User Story:** As a User, I want to maintain my own Watchlist of ticker symbols that survives across browser sessions, so that I can monitor stocks not covered by the predefined sectors without re-entering them every visit.

#### Acceptance Criteria

1. THE Watchlist_Manager SHALL render a dedicated "My Watchlist" section in the Dashboard alongside the predefined sector views.
2. THE Watchlist_Manager SHALL provide a text input that accepts one or more ticker symbols separated by commas, spaces, or newlines.
3. WHEN the User submits one or more ticker symbols through the input, THE Watchlist_Manager SHALL normalize each symbol to uppercase, trim whitespace, deduplicate against the current Watchlist, and append new symbols to the Watchlist.
4. WHEN the User clicks the remove control next to a Watchlist ticker, THE Watchlist_Manager SHALL remove that ticker from the Watchlist.
5. THE Watchlist_Manager SHALL persist the Watchlist to Browser_Storage under a single named key so that the Watchlist is restored when the same User reopens the Dashboard in the same browser.
6. WHEN the Dashboard loads, THE Watchlist_Manager SHALL read the persisted Watchlist from Browser_Storage and load it into Session_State before rendering the Watchlist section.
7. WHEN a Watchlist ticker is rendered, THE Watchlist_Manager SHALL display the ticker symbol, the latest price, the absolute price change versus prior close, and the percent change versus prior close, using data fetched from Yahoo_Finance.
8. IF a Watchlist ticker does not return valid data from Yahoo_Finance, THEN THE Watchlist_Manager SHALL display the ticker with the status text "No data" and SHALL keep the ticker in the Watchlist.
9. IF the User attempts to add a ticker that does not match the regular expression `^[A-Z][A-Z0-9.\-]{0,9}$` after normalization, THEN THE Watchlist_Manager SHALL reject that ticker, leave the Watchlist unchanged for that entry, and display an inline error identifying the rejected symbol.
10. THE Watchlist_Manager SHALL cap the Watchlist length at 50 tickers and SHALL reject additions that would exceed that cap with an inline error message.

### Requirement 2: Sparkline Mini-Charts

**User Story:** As a User, I want to see a 30-day price Sparkline next to each stock in the gainers, losers, and Watchlist views, so that I can judge recent trend direction at a glance.

#### Acceptance Criteria

1. THE Sparkline_Renderer SHALL render a Sparkline next to each row in the Top Gainers list, the Top Losers list, and the Watchlist list.
2. THE Sparkline_Renderer SHALL fetch 30 daily closing prices per ticker from Yahoo_Finance using the `/v8/finance/chart` endpoint with `interval=1d` and `range=1mo`.
3. THE Sparkline_Renderer SHALL cache fetched 30-day price series for at least 600 seconds keyed by ticker symbol.
4. WHEN a ticker has at least two valid daily closes available, THE Sparkline_Renderer SHALL render a line chart of those closes with no axes, no gridlines, and no legend.
5. WHEN the last close in the series is greater than or equal to the first close in the series, THE Sparkline_Renderer SHALL render the Sparkline line in the Dashboard's positive color.
6. WHEN the last close in the series is strictly less than the first close in the series, THE Sparkline_Renderer SHALL render the Sparkline line in the Dashboard's negative color.
7. THE Sparkline_Renderer SHALL render each Sparkline at a width of at most 120 pixels and a height of at most 40 pixels.
8. IF Yahoo_Finance returns fewer than two valid daily closes for a ticker, THEN THE Sparkline_Renderer SHALL render a placeholder dash ("—") in place of the Sparkline for that ticker.

### Requirement 3: Universal Stock Search

**User Story:** As a User, I want to search for any US-listed ticker by symbol or company name with autocomplete, so that I can view live data and a Sparkline for stocks outside the predefined sectors.

#### Acceptance Criteria

1. THE Universal_Search SHALL render a single text input labeled "Search any US-listed ticker" at a fixed location in the Dashboard.
2. WHEN the User types at least two characters into the Universal_Search input, THE Universal_Search SHALL query Yahoo_Finance at `https://query1.finance.yahoo.com/v1/finance/search` with the typed text and SHALL render up to 10 Search_Results as autocomplete suggestions.
3. THE Universal_Search SHALL filter Search_Results to entries whose `quoteType` field equals `"EQUITY"` and whose `exchange` field identifies a US exchange (one of `NMS`, `NYQ`, `ASE`, `BATS`, `PCX`, `NCM`, `NGM`).
4. THE Universal_Search SHALL display each suggestion as the ticker symbol followed by the short company name.
5. THE Universal_Search SHALL cache search responses for at least 300 seconds keyed by the typed query string.
6. WHEN the User selects a suggestion, THE Universal_Search SHALL fetch quote and chart data for the selected ticker from Yahoo_Finance and SHALL render a detail panel containing the ticker symbol, latest price, absolute price change versus prior close, percent change versus prior close, a 30-day Sparkline, and at least the following key metrics when available: previous close, day's range, 52-week range, and average daily volume.
7. IF the User submits a query that returns zero matching Search_Results, THEN THE Universal_Search SHALL display the message "No US-listed equities found".
8. IF Yahoo_Finance returns an HTTP error or times out, THEN THE Universal_Search SHALL display the message "Search temporarily unavailable" and SHALL leave any previously shown detail panel unchanged.
9. WHERE the User has previously selected a ticker through Universal_Search, THE Universal_Search SHALL provide a control to add that ticker to the Watchlist that, when activated, invokes the Watchlist_Manager add operation defined in Requirement 1.

### Requirement 4: Pre-Market Gappers

**User Story:** As a User, I want to see the top pre-market gappers up and down before the regular session opens, so that I can spot stocks moving on overnight news.

#### Acceptance Criteria

1. WHILE `get_market_phase()` returns `"premarket"`, THE Dashboard SHALL render a "Pre-Market Gappers" section containing two ranked lists labeled "Gapping Up" and "Gapping Down".
2. WHILE `get_market_phase()` does not return `"premarket"`, THE Dashboard SHALL hide the Pre-Market Gappers section.
3. THE Pre-Market Gappers section SHALL compute each Tracked_Stock's pre-market percent change as `(pre_market_price - prior_close) / prior_close * 100` using the `/v8/finance/chart` endpoint with `includePrePost=true`.
4. THE Pre-Market Gappers section SHALL include in "Gapping Up" only Tracked_Stocks whose computed pre-market percent change is greater than or equal to 2.0%, sorted in descending order of percent change, and SHALL display at most the top 10 entries.
5. THE Pre-Market Gappers section SHALL include in "Gapping Down" only Tracked_Stocks whose computed pre-market percent change is less than or equal to -2.0%, sorted in ascending order of percent change, and SHALL display at most the top 10 entries.
6. THE Pre-Market Gappers section SHALL display for each entry the ticker symbol, pre-market price, percent change versus prior close, and pre-market volume.
7. THE Pre-Market Gappers section SHALL refresh its data at most once every 60 seconds.
8. IF either list contains zero qualifying tickers, THEN THE Pre-Market Gappers section SHALL display the message "No qualifying gappers" under that list's heading.

### Requirement 5: PWA Support

**User Story:** As a User, I want to install the Dashboard as a PWA on my mobile device or desktop, so that I can launch it like a native app and have a faster reopening experience.

#### Acceptance Criteria

1. THE Dashboard SHALL serve a Manifest at the path `/manifest.json` containing at minimum the fields `name`, `short_name`, `start_url`, `display`, `background_color`, `theme_color`, and an `icons` array with at least one 192x192 PNG icon and at least one 512x512 PNG icon.
2. THE Dashboard SHALL include in its served HTML a `<link rel="manifest" href="/manifest.json">` element and a `<meta name="theme-color">` element with a value matching the Manifest's `theme_color`.
3. THE Dashboard SHALL register a Service_Worker at the path `/service-worker.js` from the served HTML using `navigator.serviceWorker.register`.
4. THE Service_Worker SHALL precache, on its `install` event, the Dashboard's static asset files (the Manifest, the Service_Worker script itself excluded, declared icons, and any CSS or JS files served by the Dashboard for the PWA shell).
5. WHEN the browser issues a `fetch` event for a precached static asset, THE Service_Worker SHALL respond with the cached asset before falling back to the network.
6. WHEN the browser issues a `fetch` event for a non-precached request, THE Service_Worker SHALL forward the request to the network without caching the response.
7. WHEN the Manifest's content hash or the Service_Worker script changes, THE Service_Worker SHALL use a new cache name on the next `install` event and SHALL delete caches with prior names on its `activate` event.
8. THE Dashboard SHALL pass the Chrome DevTools Lighthouse "Installable" PWA criteria as defined for Lighthouse 11.

### Requirement 6: Mobile-First Responsive Tweaks

**User Story:** As a User on a phone, I want the sector heatmap, panels, and tables to adapt to a small screen, so that I can read and scroll the Dashboard comfortably without horizontal scrolling.

#### Acceptance Criteria

1. WHILE the browser's viewport width is less than 768 pixels, THE Dashboard SHALL render the sector heatmap in a single column with each sector tile occupying 100% of the available content width.
2. WHILE the browser's viewport width is less than 768 pixels, THE Dashboard SHALL stack the Top Gainers panel and the Top Losers panel vertically rather than side by side.
3. WHILE the browser's viewport width is less than 768 pixels, THE Dashboard SHALL render body text at a computed font size of at least 14 pixels and panel headings at a computed font size of at least 16 pixels.
4. WHILE the browser's viewport width is less than 768 pixels, THE Dashboard SHALL constrain every table and list to the content width of its container and SHALL enable horizontal scrolling within that container for any content that exceeds the container width.
5. WHILE the browser's viewport width is less than 768 pixels, THE Dashboard SHALL keep all interactive controls (buttons, links, remove icons) at a minimum hit area of 44x44 CSS pixels.
6. WHILE the browser's viewport width is greater than or equal to 768 pixels, THE Dashboard SHALL preserve the existing multi-column layout for the sector heatmap and the gainers/losers panels.
7. THE Dashboard SHALL include a `<meta name="viewport" content="width=device-width, initial-scale=1.0">` element in its served HTML.

### Requirement 7: AI Daily Market Summary

**User Story:** As a User, I want a written paragraph summarizing the trading day at market close or on demand, so that I can get a quick narrative without scanning every panel.

#### Acceptance Criteria

1. THE Daily_Summary_Generator SHALL render a "Daily Market Summary" section in the Dashboard containing a single paragraph of generated text and a "Regenerate" button.
2. WHEN the User clicks the "Regenerate" button, THE Daily_Summary_Generator SHALL produce a new Daily_Summary using the most recent cached Dashboard data.
3. WHEN the local clock first crosses 16:00 ET on a US trading weekday during a User's session, THE Daily_Summary_Generator SHALL produce a Daily_Summary automatically and SHALL display it in the section described in criterion 1.
4. THE Daily_Summary_Generator SHALL produce the Daily_Summary using only data already available to the Dashboard (the cached quotes used by `generate_insights()`, the sector averages, the top gainers and losers, and the macro/catalyst headlines fetched by `fetch_macro_headlines()`), without calling any external LLM service.
5. THE Daily_Summary SHALL contain at minimum: the SPY percent change for the day, the QQQ percent change for the day, the name and average percent change of the strongest sector, the name and average percent change of the weakest sector, the top 3 gainers with their percent changes, the bottom 3 losers with their percent changes, and a reference to at most 2 headlines from the most recent macro headlines fetch.
6. THE Daily_Summary SHALL be at least 60 words and at most 200 words.
7. IF SPY data, QQQ data, sector data, or top movers data is missing from the cache at generation time, THEN THE Daily_Summary_Generator SHALL omit the corresponding sentence from the Daily_Summary and SHALL still produce a valid summary using the remaining available data.
8. IF no Dashboard data is available at generation time, THEN THE Daily_Summary_Generator SHALL display the message "Summary unavailable — market data not loaded yet" instead of a Daily_Summary.

### Requirement 8: Volatility Scanner

**User Story:** As a User, I want a section that lists every Tracked_Stock currently moving more than ±3% in the session, so that I can quickly see which stocks are unusually active.

#### Acceptance Criteria

1. THE Volatility_Scanner SHALL render a "Volatility Scanner" section in the Dashboard containing a single ranked list of qualifying tickers and a numeric input for adjusting Volatility_Threshold.
2. THE Volatility_Scanner SHALL include in its list every Tracked_Stock whose absolute intraday percent change versus prior close is greater than or equal to Volatility_Threshold.
3. THE Volatility_Scanner SHALL sort its list by absolute intraday percent change in descending order.
4. THE Volatility_Scanner SHALL display for each entry the ticker symbol, the signed intraday percent change, the latest price, and a single "reason" cell.
5. WHEN the most recent macro or catalyst headlines fetch contains at least one headline that matches the entry's ticker through the existing `match_to_tickers()` function, THE Volatility_Scanner SHALL set the entry's "reason" cell to the title of the highest-ranked matching headline truncated to 100 characters.
6. WHEN no headline matches the entry's ticker, THE Volatility_Scanner SHALL set the entry's "reason" cell to an em dash ("—").
7. THE Volatility_Scanner SHALL accept Volatility_Threshold values from the numeric input in the inclusive range 1.0 to 20.0 percent and SHALL default to 3.0 percent.
8. IF zero Tracked_Stocks meet the Volatility_Threshold, THEN THE Volatility_Scanner SHALL display the message "No stocks above threshold".
9. THE Volatility_Scanner SHALL refresh its list whenever the underlying cached quote data refreshes and SHALL not issue additional Yahoo_Finance quote requests beyond what the existing `fetch_all_data()` cache already provides.
