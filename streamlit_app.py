import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytz

# --- Dashboard Pro Pack feature modules (task 11.2) ---
# Imported here so each renderer is available everywhere on the page. The
# modules are self-contained: they reuse the cached `fetch_all_data()` and
# `fetch_macro_headlines()` results passed in by the caller and never issue
# new HTTP requests of their own. `render_premarket_gappers` self-gates on
# market phase, so it is safe to call unconditionally on every render.
from app.watchlist import render_watchlist_section
from app.universal_search import render_universal_search
from app.premarket_gappers import render_premarket_gappers
from app.daily_summary import render_daily_summary
from app.volatility_scanner import render_volatility_scanner

# --- PWA + responsive head assets (loaded once at module import) ---
# Read static/styles.css at module load so it is available for injection on
# every page render without re-reading the file. Wrapped in a broad try/except
# so a missing or unreadable stylesheet degrades to no styles rather than
# crashing the dashboard.
_STATIC_DIR = Path(__file__).resolve().parent / "static"

def _load_styles_css() -> str:
    try:
        return (_STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

_PWA_STYLES_CSS = _load_styles_css()


def _inject_pwa_head() -> None:
    """Inject the PWA + responsive head fragment via an invisible
    components.v1.html iframe.

    Includes:
      - <link rel="manifest" href="/manifest.json">      (Req 5.2)
      - <meta name="theme-color" content="#0f172a">     (Req 5.2)
      - <meta name="viewport" ...>                       (Req 6.7)
      - <script src="/static/pwa-register.js" defer>     (Req 5.3)
      - <style> block with the contents of static/styles.css

    Called once immediately after st.set_page_config(...) and before any
    other Streamlit render call, with height=0 so the iframe is invisible.
    """
    style_block = f"<style>{_PWA_STYLES_CSS}</style>" if _PWA_STYLES_CSS else ""
    components.html(
        f"""<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f172a">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script src="/static/pwa-register.js" defer></script>
{style_block}""",
        height=0,
    )


# --- Page Config ---
st.set_page_config(
    page_title="Live Stock Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Inject PWA manifest link, theme-color, viewport meta, service-worker register
# script, and the responsive stylesheet. Must run before any other Streamlit
# render call so the head fragment lands at the top of the page.
_inject_pwa_head()

# Auto-refresh every 30 seconds
st_autorefresh(interval=30000, limit=None, key="data_refresh")

# --- Configuration ---
STOCKS = {
    "Core": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "LLY", "JPM", "AVGO"],
    "Tech": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD", "INTC", "CRM"],
    "Finance": ["JPM", "BAC", "GS", "MS", "V", "MA", "C", "WFC", "AXP", "BLK"],
    "Healthcare": ["JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "TMO", "ABT"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "Consumer": ["WMT", "KO", "PEP", "MCD", "NKE", "SBUX", "HD"],
    "Industrial": ["CAT", "BA", "HON", "UPS", "GE"],
    "Oil": ["XOM", "CVX", "COP", "OXY", "EOG", "MPC", "PSX"],
    "Gold": ["NEM", "GOLD", "AEM", "FNV", "WPM", "GFI", "KGC"],
    "Copper": ["FCX", "SCCO", "TECK", "HBM", "COPX", "ERO", "IVPAF"],
    "Space": ["LMT", "NOC", "BA", "RTX", "RKLB", "LUNR", "RDW"],
    "Fertilizers": ["NTR", "MOS", "CF", "FMC", "ICL", "IPI", "CTVA"],
    "Solar": ["ENPH", "SEDG", "FSLR", "RUN", "NOVA", "ARRY", "CSIQ"],
    "Quantum": ["IONQ", "RGTI", "QBTS", "QUBT", "ARQQ", "QMCO", "QTUM"],
    "Semis": ["NVDA", "AMD", "AVGO", "INTC", "QCOM", "MU", "MRVL", "TSM", "ASML", "TXN"],
    "Medical AI": ["TEM", "RXRX", "SDGR", "GEHC", "SMMNY", "BFLY", "DOCS", "HCAT", "EXAI", "GH"],
    "AI Infra": ["IREN", "APLD", "CORZ", "CIFR", "HUT", "WULF", "VRT", "EQIX", "DLR", "CLS", "DELL"],
    "Drones": ["AVAV", "KTOS", "UMAC", "DPRO", "ONDS", "AVEX", "RCAT", "JOBY", "ACHR", "PLTR"],
}
INDICES = ["SPY", "QQQ", "DIA"]
# Macro indicators: 10Y Treasury Yield, VIX (fear index), XLF (financials ETF)
MACRO_TICKERS = ["^TNX", "^VIX", "XLF"]
TICKER_SECTORS = {}
for sector, tickers in STOCKS.items():
    for t in tickers:
        if t not in TICKER_SECTORS:
            TICKER_SECTORS[t] = []
        TICKER_SECTORS[t].append(sector)

ALL_TICKERS = list(set(INDICES + MACRO_TICKERS + [t for tickers in STOCKS.values() for t in tickers]))
ET = pytz.timezone("US/Eastern")
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# --- Market Phase ---
def get_market_phase():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return "closed"
    t = now.hour * 60 + now.minute
    if t < 240: return "closed"
    if t < 560: return "premarket"
    if t < 570: return "transition"
    if t < 960: return "market"
    if t < 1200: return "afterhours"
    return "closed"


# --- Data Fetching ---
def fetch_spark_batch(batch):
    symbols = ",".join(batch)
    url = f"https://query1.finance.yahoo.com/v8/finance/spark?symbols={symbols}&range=2d&interval=1d&includePrePost=true"
    resp = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_chart_single(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=5m&range=1d&includePrePost=true"
    try:
        resp = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        meta = result["meta"]
        # chartPreviousClose = yesterday's close (correct reference)
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose", 0)
        closes = result["indicators"]["quote"][0]["close"]
        valid_closes = [c for c in closes if c is not None]
        latest_price = valid_closes[-1] if valid_closes else None
        if not latest_price or not prev_close:
            return None
        change = latest_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close != 0 else 0
        volumes = result["indicators"]["quote"][0].get("volume", [])
        total_vol = sum(v for v in volumes if v is not None)
        return {
            "ticker": ticker, "price": round(float(latest_price), 2),
            "prev_close": round(float(prev_close), 2),
            "change": round(float(change), 2), "pct_change": round(float(pct_change), 2),
            "volume": int(total_vol), "is_premarket": True,
        }
    except:
        return None


@st.cache_data(ttl=60)
def fetch_extended_hours_data(mode):
    """Fetch pre-market or post-market data for ALL tracked stocks."""
    all_stock_tickers = [t for t in ALL_TICKERS if not t.startswith("^")]

    def fetch_ext_single(ticker):
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=5m&range=1d&includePrePost=true"
        try:
            resp = requests.get(url, headers=YAHOO_HEADERS, timeout=8)
            if resp.status_code != 200:
                return None
            result = resp.json()["chart"]["result"][0]
            meta = result["meta"]
            regular_close = meta.get("regularMarketPrice", 0)
            closes = result["indicators"]["quote"][0]["close"]
            valid = [c for c in closes if c is not None]
            latest = valid[-1] if valid else None
            if not latest or not regular_close or regular_close == 0:
                return None
            ref_price = regular_close
            change = latest - ref_price
            pct = (change / ref_price) * 100
            label = "Yesterday's Close" if mode == "Pre-Market" else "Today's Close"
            primary_sector = "Index" if ticker in INDICES else TICKER_SECTORS.get(ticker, ["Unknown"])[0]
            return {
                "ticker": ticker, "ref_price": round(ref_price, 2),
                "current_price": round(latest, 2), "change": round(change, 2),
                "pct_change": round(pct, 2), "sector": primary_sector, "label": label,
            }
        except:
            return None

    with ThreadPoolExecutor(max_workers=15) as ex:
        results = list(ex.map(fetch_ext_single, all_stock_tickers))
    return [r for r in results if r is not None]


@st.cache_data(ttl=25)
def fetch_all_data():
    """
    Hybrid approach that works on Streamlit Cloud:
    - Spark endpoint for all regular tickers (batch, fast, ~1s)
    - Chart endpoint only for ^TNX and ^VIX (need special handling)
    """
    phase = get_market_phase()
    data = {}

    # Step 1: Fetch all regular tickers via spark (fast batch)
    regular_tickers = [t for t in ALL_TICKERS if not t.startswith("^")]
    batch_size = 20
    batches = [regular_tickers[i:i+batch_size] for i in range(0, len(regular_tickers), batch_size)]

    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(fetch_spark_batch, batches))
        for result in results:
            for sym, info in result.items():
                if sym not in ALL_TICKERS:
                    continue
                closes = info.get("close", [])
                if not closes or len(closes) < 1:
                    continue

                # With range=2d: closes[0]=yesterday, closes[1]=today
                # With range=1d or only 1 close: use chartPreviousClose
                if len(closes) >= 2:
                    price = closes[-1]       # today's latest
                    prev_close = closes[-2]  # yesterday's close
                else:
                    price = closes[-1]
                    prev_close = info.get("chartPreviousClose")

                if price is None or not prev_close or prev_close == 0:
                    continue

                change = price - prev_close
                pct_change = (change / prev_close) * 100

                if sym in INDICES or sym in MACRO_TICKERS:
                    primary_sector = "Index"
                    sectors = ["Index"]
                else:
                    primary_sector = TICKER_SECTORS.get(sym, ["Unknown"])[0]
                    sectors = TICKER_SECTORS.get(sym, ["Index"])

                data[sym] = {
                    "ticker": sym, "price": round(float(price), 2),
                    "prev_close": round(float(prev_close), 2),
                    "change": round(float(change), 2), "pct_change": round(float(pct_change), 2),
                    "volume": 0, "sector": primary_sector,
                    "sectors": sectors, "is_premarket": (phase == "premarket"),
                }
    except Exception as e:
        pass

    # Step 2: Fetch ^TNX and ^VIX via chart endpoint (they need special URL encoding)
    special_tickers = [t for t in ALL_TICKERS if t.startswith("^")]
    for ticker in special_tickers:
        result = fetch_chart_single(ticker)
        if result:
            result["sector"] = "Index"
            result["sectors"] = ["Index"]
            result["is_premarket"] = (phase == "premarket")
            data[ticker] = result

    return data, phase


# --- Insights ---
def generate_insights(data):
    insights = []
    spy = data.get("SPY")
    qqq = data.get("QQQ")
    vix = data.get("^VIX")
    tnx = data.get("^TNX")
    xlf = data.get("XLF")
    phase = get_market_phase()

    if phase == "premarket":
        insights.append("🌅 Showing pre-market data (updates until 9:20 AM ET)")

    # --- VIX insights (fear gauge) ---
    if vix:
        vix_level = vix["price"]
        vix_change = vix["pct_change"]
        
        if vix_change > 10:
            insights.append(f"🚨 VIX spiking +{vix_change:.1f}% — fear rising sharply, expect volatility")
        elif vix_change > 5:
            insights.append(f"⚠️ VIX up +{vix_change:.1f}% — market anxiety increasing")
        elif vix_change < -5:
            insights.append(f"😌 VIX down {vix_change:.1f}% — fear fading, risk-on sentiment")
        
        # VIX level context with market breadth
        if vix_level > 30:
            if bear_pct >= 60:
                insights.append(f"🔴 VIX at {vix_level:.1f} + {bear_pct}% stocks red — extreme fear with broad selling")
            else:
                insights.append(f"🔴 VIX at {vix_level:.1f} — extreme fear territory, panic levels")
        elif vix_level > 25:
            insights.append(f"🟠 VIX at {vix_level:.1f} — high fear, defensive positioning")
        elif vix_level > 20:
            insights.append(f"🟡 VIX at {vix_level:.1f} — elevated caution")
        elif vix_level < 12:
            if bull_pct >= 60:
                insights.append(f"🔵 VIX at {vix_level:.1f} + {bull_pct}% stocks green — complacency with strong rally")
            else:
                insights.append(f"🔵 VIX at {vix_level:.1f} — extreme complacency, volatility spike risk")

    # --- Treasury 10Y insights ---
    if tnx:
        yield_val = tnx["price"]  # TNX is yield * 10 on Yahoo (e.g. 45.2 = 4.52%)
        # Yahoo reports TNX as actual yield value (e.g. 4.52)
        if tnx["pct_change"] > 2:
            insights.append(f"📈 10Y Treasury yield rising +{tnx['pct_change']:.1f}% (at {yield_val:.2f}%) — pressure on growth/tech stocks")
        elif tnx["pct_change"] < -2:
            insights.append(f"📉 10Y Treasury yield falling {tnx['pct_change']:.1f}% (at {yield_val:.2f}%) — tailwind for growth stocks")

    # --- Connect VIX + SPY ---
    if vix and spy:
        if vix["pct_change"] > 5 and spy["pct_change"] < -0.5:
            insights.append(f"🔗 VIX spike + SPY selloff — classic risk-off move")
        elif vix["pct_change"] < -3 and spy["pct_change"] > 0.5:
            insights.append(f"🔗 VIX dropping + SPY rallying — strong risk-on signal")

    # --- Connect Treasury + Tech ---
    if tnx and qqq:
        if tnx["pct_change"] > 2 and qqq["pct_change"] < -0.5:
            insights.append(f"🔗 Rising yields dragging tech — QQQ {qqq['pct_change']:.1f}% as 10Y climbs")
        elif tnx["pct_change"] < -2 and qqq["pct_change"] > 0.5:
            insights.append(f"🔗 Falling yields boosting tech — QQQ +{qqq['pct_change']:.1f}% as 10Y drops")

    # --- Connect XLF + Treasury ---
    if xlf and tnx:
        if tnx["pct_change"] > 1.5 and xlf["pct_change"] > 0.5:
            insights.append(f"🏦 Banks benefiting from rising yields — XLF +{xlf['pct_change']:.1f}%")
        elif tnx["pct_change"] < -1.5 and xlf["pct_change"] < -0.5:
            insights.append(f"🏦 Banks pressured by falling yields — XLF {xlf['pct_change']:.1f}%")

    # --- Sector analysis ---
    sector_changes = {}
    sector_stocks = {}
    for ticker, info in data.items():
        sector = info.get("sector")
        if sector and sector not in ("Index", "Core"):
            sector_changes.setdefault(sector, []).append(info["pct_change"])
            sector_stocks.setdefault(sector, []).append(info)

    sector_avg = {s: round(sum(c)/len(c), 2) for s, c in sector_changes.items() if c}
    sorted_sectors = sorted(sector_avg.items(), key=lambda x: x[1], reverse=True)
    best = sorted_sectors[0] if sorted_sectors else None
    worst = sorted_sectors[-1] if sorted_sectors else None

    if spy and best and worst:
        if spy["pct_change"] > 0 and best[1] > 0:
            others = [v for k, v in sector_avg.items() if k != best[0]]
            avg_o = sum(others)/len(others) if others else 0
            if best[1] > avg_o * 2 and best[1] > 0.5:
                movers = sorted(sector_stocks[best[0]], key=lambda x: x["pct_change"], reverse=True)[:3]
                m_str = ", ".join([f"{s['ticker']} +{s['pct_change']}%" for s in movers])
                insights.append(f"📈 SPY +{spy['pct_change']}% — led by {best[0]} (+{best[1]}%). {m_str}")
            else:
                green = sum(1 for v in sector_avg.values() if v > 0)
                if green >= 4:
                    insights.append(f"📈 SPY +{spy['pct_change']}% — broad strength, {green}/{len(sector_avg)} sectors green")
                elif len(sorted_sectors) >= 2:
                    insights.append(f"📈 SPY +{spy['pct_change']}% — {best[0]} (+{best[1]}%) & {sorted_sectors[1][0]} (+{sorted_sectors[1][1]}%) leading")
        elif spy["pct_change"] < 0 and worst[1] < 0:
            movers = sorted(sector_stocks[worst[0]], key=lambda x: x["pct_change"])[:3]
            m_str = ", ".join([f"{s['ticker']} {s['pct_change']}%" for s in movers])
            insights.append(f"📉 SPY {spy['pct_change']}% — {worst[0]} dragging ({worst[1]}%). {m_str}")
        else:
            s = '+' if spy['pct_change'] >= 0 else ''
            insights.append(f"{'📈' if spy['pct_change']>=0 else '📉'} SPY {s}{spy['pct_change']}% today")

    if spy and qqq and abs(qqq["pct_change"] - spy["pct_change"]) > 0.5:
        if qqq["pct_change"] > spy["pct_change"]:
            insights.append(f"⚡ Tech outperforming — QQQ +{qqq['pct_change']}% vs SPY +{spy['pct_change']}%")
        else:
            insights.append(f"⚡ Tech lagging — QQQ {qqq['pct_change']}% vs SPY {spy['pct_change']}%")

    all_s = sorted([v for v in data.values() if v["sector"] != "Index"], key=lambda x: x["pct_change"], reverse=True)
    if len(all_s) >= 3:
        t3 = all_s[:3]
        if t3[0]["sector"] == t3[1]["sector"] == t3[2]["sector"]:
            insights.append(f"🔥 {t3[0]['sector']} hot — top 3 ({', '.join(x['ticker'] for x in t3)}) same sector")
        b3 = all_s[-3:]
        if b3[0]["sector"] == b3[1]["sector"] == b3[2]["sector"]:
            insights.append(f"🧊 {b3[0]['sector']} weak — bottom 3 ({', '.join(x['ticker'] for x in b3)}) same sector")

    return insights or ["📊 Market flat — no strong rotation signals"]


# --- Custom CSS ---
st.markdown("""
<style>
    .stApp { background-color: #f0f4f8; }
    .metric-card {
        background: white;
        border-radius: 10px;
        padding: 15px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.06);
        text-align: center;
    }
    .gain { color: #276749; }
    .loss { color: #9b2c2c; }
    .stock-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 8px 12px;
        background: #f7fafc;
        border-radius: 8px;
        margin-bottom: 4px;
    }
    .insight-box {
        background: white;
        border-left: 4px solid #4299e1;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 8px;
    }
    /* Dropdown styling */
    div[data-baseweb="select"] > div {
        background-color: #ebf4ff !important;
        color: #2b6cb0 !important;
        border-radius: 8px !important;
        border: 2px solid #90cdf4 !important;
    }
    div[data-baseweb="select"] > div > div {
        color: #2b6cb0 !important;
    }
    div[data-baseweb="select"] svg {
        fill: #2b6cb0 !important;
    }
    /* Clickable news headline links */
    a.news-link {
        color: #2d3748 !important;
        text-decoration: none !important;
        border-bottom: 1px dashed transparent;
        transition: color 0.15s, border-bottom-color 0.15s;
    }
    a.news-link:hover {
        color: #2b6cb0 !important;
        border-bottom-color: #2b6cb0 !important;
        cursor: pointer;
    }
</style>
""", unsafe_allow_html=True)


# --- Main App ---
st.markdown("""
<div style="text-align:center;margin-bottom:10px;">
  <h1 style="margin:0;">GGP (God Given Patterns)</h1>
</div>
""", unsafe_allow_html=True)

# Fetch data
with st.spinner("Loading market data..."):
    data, phase = fetch_all_data()

if not data:
    st.error("Unable to fetch market data. Please refresh the page.")
    st.stop()

# Phase badge and Mode selector
phase_labels = {
    "premarket": "🌅 Pre-Market",
    "market": "🟢 Market Open",
    "transition": "⏳ Waiting for Open",
    "afterhours": "🌙 After Hours",
    "closed": "🔴 Market Closed",
}

mode_col1, mode_col2 = st.columns([3, 2])
with mode_col1:
    st.caption(f"{phase_labels.get(phase, phase)} | Last updated: {datetime.now(ET).strftime('%I:%M:%S %p ET')}")
with mode_col2:
    view_mode = st.radio("View Mode", ["Live", "Pre-Market", "Post-Market"], horizontal=True, index=0)

# --- Fetch data based on view mode ---
# If Pre-Market or Post-Market selected, fetch extended hours data and use it for EVERYTHING
if view_mode == "Live":
    active_data = data  # Already fetched above
    ref_label = "Prev Close"
elif view_mode == "Pre-Market" and phase not in ("premarket", "transition"):
    st.warning("🌅 Pre-Market data is only available from 4:00 AM to 9:30 AM ET.")
    active_data = data
    ref_label = "Prev Close"
elif view_mode == "Post-Market" and phase not in ("afterhours", "closed"):
    st.warning("🌙 Post-Market data is available after 4:00 PM ET.")
    active_data = data
    ref_label = "Prev Close"
else:
    # Fetch extended hours data for all tickers
    with st.spinner(f"Loading {view_mode.lower()} data..."):
        ext_list = fetch_extended_hours_data(view_mode)
    if ext_list:
        # Convert list to dict format matching main data structure
        active_data = {}
        for s in ext_list:
            active_data[s["ticker"]] = {
                "ticker": s["ticker"],
                "price": s["current_price"],
                "prev_close": s["ref_price"],
                "change": s["change"],
                "pct_change": s["pct_change"],
                "volume": 0,
                "sector": s["sector"],
                "sectors": TICKER_SECTORS.get(s["ticker"], [s["sector"]]),
                "is_premarket": (view_mode == "Pre-Market"),
            }
        ref_label = s["label"] if ext_list else "Prev Close"
    else:
        active_data = data
        ref_label = "Prev Close"

# --- Bulls vs Bears ---
stocks_all = {k: v for k, v in active_data.items() if k not in INDICES and k not in MACRO_TICKERS}
total_stocks = len(stocks_all)
bulls = sum(1 for s in stocks_all.values() if s["pct_change"] > 0)
bears = sum(1 for s in stocks_all.values() if s["pct_change"] < 0)
flat = total_stocks - bulls - bears
bull_pct = round((bulls / total_stocks) * 100) if total_stocks > 0 else 50
bear_pct = round((bears / total_stocks) * 100) if total_stocks > 0 else 50

# Determine who's winning
if bull_pct >= 65:
    verdict = "Bulls dominating 💪"
    verdict_color = "#276749"
elif bull_pct >= 55:
    verdict = "Bulls leading"
    verdict_color = "#38a169"
elif bear_pct >= 65:
    verdict = "Bears dominating 🩸"
    verdict_color = "#9b2c2c"
elif bear_pct >= 55:
    verdict = "Bears leading"
    verdict_color = "#e53e3e"
else:
    verdict = "Tug of war ⚔️"
    verdict_color = "#718096"

st.markdown(f"""
<div style="background:white;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
  <div style="text-align:center;margin-bottom:10px;">
    <span style="font-size:1.3rem;font-weight:700;color:{verdict_color};">{verdict}</span>
  </div>
  <div style="display:flex;align-items:center;justify-content:center;gap:15px;">
    <div style="text-align:center;">
      <div style="font-size:2.5rem;">🐂</div>
      <div style="font-size:1.5rem;font-weight:800;color:#276749;">{bull_pct}%</div>
      <div style="font-size:0.8rem;color:#718096;">{bulls} stocks up</div>
    </div>
    <div style="flex:1;max-width:300px;">
      <div style="background:#fed7d7;border-radius:20px;height:24px;overflow:hidden;position:relative;">
        <div style="background:#c6f6d5;height:100%;width:{bull_pct}%;border-radius:20px;transition:width 0.5s;"></div>
      </div>
    </div>
    <div style="text-align:center;">
      <div style="font-size:2.5rem;">🐻</div>
      <div style="font-size:1.5rem;font-weight:800;color:#9b2c2c;">{bear_pct}%</div>
      <div style="font-size:0.8rem;color:#718096;">{bears} stocks down</div>
    </div>
  </div>
  {"<div style='text-align:center;margin-top:8px;font-size:0.75rem;color:#a0aec0;'>" + str(flat) + " stocks flat</div>" if flat > 0 else ""}
</div>
""", unsafe_allow_html=True)

# --- Breadth vs SPY Logic ---
spy_data_breadth = active_data.get("SPY")
if spy_data_breadth:
    spy_up = spy_data_breadth["pct_change"] > 0.05
    spy_down = spy_data_breadth["pct_change"] < -0.05

    if bear_pct >= 60 and spy_up:
        breadth_msg = f"⚠️ SPY is green (+{spy_data_breadth['pct_change']:.2f}%) but {bear_pct}% of stocks are red — rally is NARROW, driven only by mega-caps. Breadth is weak, this is not a healthy move. Be cautious chasing longs."
        breadth_color = "#c05621"
    elif bull_pct >= 60 and spy_down:
        breadth_msg = f"⚠️ SPY is red ({spy_data_breadth['pct_change']:.2f}%) but {bull_pct}% of stocks are green — mega-caps dragging the index. Broad market is healthier than SPY shows. Look for opportunities in smaller names."
        breadth_color = "#c05621"
    elif bull_pct >= 60 and spy_up:
        breadth_msg = f"✅ Healthy rally — {bull_pct}% stocks green confirming SPY +{spy_data_breadth['pct_change']:.2f}%. Broad participation, strong move."
        breadth_color = "#276749"
    elif bear_pct >= 60 and spy_down:
        breadth_msg = f"✅ Broad selloff confirmed — {bear_pct}% stocks red matching SPY {spy_data_breadth['pct_change']:.2f}%. Weakness is real, not just mega-cap driven."
        breadth_color = "#9b2c2c"
    else:
        breadth_msg = None
        breadth_color = None

    if breadth_msg:
        st.markdown(f"""
        <div style="background:white;border-left:4px solid {breadth_color};border-radius:8px;padding:12px 16px;margin-bottom:16px;box-shadow:0 2px 6px rgba(0,0,0,0.05);">
            <div style="font-weight:700;color:#2d3748;margin-bottom:4px;">📊 Market Breadth vs SPY</div>
            <div style="color:#4a5568;font-size:0.88rem;line-height:1.6;">{breadth_msg}</div>
        </div>
        """, unsafe_allow_html=True)

# --- Beast Mode Detection ---
@st.cache_data(ttl=60)
def fetch_beast_mode_data():
    """Fetch 5-day data for SPY top weights to calculate normal range."""
    SPY_WEIGHTS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "LLY", "JPM", "AVGO"]
    symbols = ",".join(SPY_WEIGHTS)
    url = f"https://query1.finance.yahoo.com/v8/finance/spark?symbols={symbols}&range=5d&interval=1d&includePrePost=true"
    try:
        resp = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
        resp.raise_for_status()
        raw = resp.json()

        result = {}
        for sym in SPY_WEIGHTS:
            info = raw.get(sym)
            if not info:
                continue
            closes = info.get("close", [])
            if len(closes) < 3:
                continue

            # Calculate average daily % move from last 5 days
            daily_moves = []
            for i in range(1, len(closes)):
                if closes[i] is not None and closes[i-1] is not None and closes[i-1] != 0:
                    move = abs((closes[i] - closes[i-1]) / closes[i-1]) * 100
                    daily_moves.append(move)

            avg_move = sum(daily_moves) / len(daily_moves) if daily_moves else 1.0

            # Today's move
            if len(closes) >= 2 and closes[-1] is not None and closes[-2] is not None and closes[-2] != 0:
                today_move = ((closes[-1] - closes[-2]) / closes[-2]) * 100
            else:
                today_move = 0

            result[sym] = {
                "ticker": sym,
                "avg_daily_move": round(avg_move, 2),
                "today_move": round(today_move, 2),
                "today_abs": round(abs(today_move), 2),
                "multiplier": round(abs(today_move) / avg_move, 1) if avg_move > 0 else 0,
                "direction": "up" if today_move > 0 else "down",
            }
        return result
    except:
        return None

beast_data = fetch_beast_mode_data()
if beast_data:
    # Find stocks moving 2x+ their normal range
    beast_stocks = {k: v for k, v in beast_data.items() if v["multiplier"] >= 2.0}
    beast_up = [v for v in beast_stocks.values() if v["direction"] == "up"]
    beast_down = [v for v in beast_stocks.values() if v["direction"] == "down"]

    # Beast mode triggers if 3+ heavyweights are 2x in same direction
    beast_mode = False
    beast_direction = None

    if len(beast_up) >= 3:
        beast_mode = True
        beast_direction = "bullish"
    elif len(beast_down) >= 3:
        beast_mode = True
        beast_direction = "bearish"

    if beast_mode:
        if beast_direction == "bullish":
            banner_color = "#276749"
            banner_bg = "#c6f6d5"
            banner_text = "🔥 BEAST MODE ACTIVATED"
            beast_list = sorted(beast_up, key=lambda x: x["multiplier"], reverse=True)
        else:
            banner_color = "#9b2c2c"
            banner_bg = "#fed7d7"
            banner_text = "🩸 BEAST MODE ACTIVATED (BEARISH)"
            beast_list = sorted(beast_down, key=lambda x: x["multiplier"], reverse=True)

        st.markdown(f"""
        <div style="background:{banner_bg};border:2px solid {banner_color};border-radius:12px;padding:16px;text-align:center;margin-bottom:16px;">
            <div style="font-size:1.4rem;font-weight:800;color:{banner_color};">{banner_text}</div>
            <div style="font-size:0.85rem;color:#4a5568;margin-top:4px;">{len(beast_list)} mega-caps moving 2x+ their normal range — click below for details</div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("👀 See Beast Mode Details"):
            dir_word = "UP" if beast_direction == "bullish" else "DOWN"
            st.markdown(f"**{len(beast_list)} SPY heavyweights in beast mode — all pushing {dir_word}:**")
            for s in beast_list:
                sign = "+" if s["today_move"] > 0 else ""
                st.markdown(
                    f"- **{s['ticker']}** {sign}{s['today_move']:.1f}% today "
                    f"(normal: ±{s['avg_daily_move']:.1f}%) — **{s['multiplier']:.1f}x** usual move"
                )
            if beast_direction == "bullish":
                st.markdown("*These heavyweights are in beast mode pushing SPY hard. Strong conviction rally.*")
            else:
                st.markdown("*Mega-caps dumping aggressively. High conviction selling pressure on SPY.*")

# --- Day High/Low Breakout Alerts ---
@st.cache_data(ttl=25)
def fetch_day_highlow():
    """Fetch intraday high/low for ALL tracked stocks to detect breakouts."""
    ALL_TRACKED = list(set([t for tickers in STOCKS.values() for t in tickers]))
    alerts = []
    
    def check_ticker(ticker):
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d&includePrePost=false"
        try:
            resp = requests.get(url, headers=YAHOO_HEADERS, timeout=8)
            if resp.status_code != 200:
                return None
            result = resp.json()["chart"]["result"][0]
            meta = result["meta"]
            day_high = meta.get("regularMarketDayHigh", 0)
            day_low = meta.get("regularMarketDayLow", 0)
            current = meta.get("regularMarketPrice", 0)
            prev_close = meta.get("chartPreviousClose", 0)
            
            if not day_high or not day_low or not current:
                return None
            
            # Check if current price is within 0.1% of day high or day low
            near_high = current >= day_high * 0.999
            near_low = current <= day_low * 1.001
            
            if near_high:
                return {
                    "ticker": ticker,
                    "type": "high",
                    "price": round(current, 2),
                    "level": round(day_high, 2),
                    "day_low": round(day_low, 2),
                    "prev_close": round(prev_close, 2),
                }
            elif near_low:
                return {
                    "ticker": ticker,
                    "type": "low",
                    "price": round(current, 2),
                    "level": round(day_low, 2),
                    "day_high": round(day_high, 2),
                    "prev_close": round(prev_close, 2),
                }
            return None
        except:
            return None
    
    with ThreadPoolExecutor(max_workers=15) as ex:
        results = list(ex.map(check_ticker, ALL_TRACKED))
    
    return [r for r in results if r is not None]

breakout_alerts = fetch_day_highlow()
if breakout_alerts:
    # Sort: highs first, then lows
    highs = sorted([a for a in breakout_alerts if a["type"] == "high"], key=lambda x: x["price"], reverse=True)
    lows = sorted([a for a in breakout_alerts if a["type"] == "low"], key=lambda x: x["price"], reverse=True)

    st.markdown(f"""
    <div style="background:#fffbeb;border:2px solid #f6ad55;border-radius:12px;padding:14px 18px;margin-bottom:16px;">
        <div style="font-weight:700;color:#c05621;margin-bottom:8px;">🚨 Live Breakout Alerts — All Sectors ({len(breakout_alerts)} stocks at day high/low)</div>
    </div>
    """, unsafe_allow_html=True)

    brk_col1, brk_col2 = st.columns(2)
    with brk_col1:
        if highs:
            st.markdown(f"**▲ Breaking Day High ({len(highs)}):**")
            for alert in highs:
                sector = TICKER_SECTORS.get(alert["ticker"], [""])[0]
                st.markdown(
                    f'<div style="background:#c6f6d5;border-radius:8px;padding:8px 12px;margin-bottom:4px;">'
                    f'<b style="color:#276749;">▲ {alert["ticker"]}</b> <small style="color:#718096;">{sector}</small><br/>'
                    f'<small>At ${alert["price"]:.2f} (High: ${alert["level"]:.2f}) | Low: ${alert["day_low"]:.2f} | Prev: ${alert["prev_close"]:.2f}</small>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    with brk_col2:
        if lows:
            st.markdown(f"**▼ Breaking Day Low ({len(lows)}):**")
            for alert in lows:
                sector = TICKER_SECTORS.get(alert["ticker"], [""])[0]
                st.markdown(
                    f'<div style="background:#fed7d7;border-radius:8px;padding:8px 12px;margin-bottom:4px;">'
                    f'<b style="color:#9b2c2c;">▼ {alert["ticker"]}</b> <small style="color:#718096;">{sector}</small><br/>'
                    f'<small>At ${alert["price"]:.2f} (Low: ${alert["level"]:.2f}) | High: ${alert["day_high"]:.2f} | Prev: ${alert["prev_close"]:.2f}</small>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

# --- Pre-Market Gappers (task 11.2) ---
# Self-gates on `get_market_phase()` — renders only during the pre-market
# session. Reuses its own 60s `fetch_premarket_quotes()` cache, no new HTTP
# from this call site.
render_premarket_gappers()

# --- Index Bar ---
st.subheader("Market Indices")
idx_cols = st.columns(3)
for i, sym in enumerate(INDICES):
    info = active_data.get(sym)
    if info:
        with idx_cols[i]:
            delta_color = "normal"
            st.metric(
                label=sym,
                value=f"${info['price']:.2f}",
                delta=f"{info['change']:+.2f} ({info['pct_change']:+.2f}%)",
            )

# --- My Watchlist + Universal Search (task 11.2) ---
# Both sections live near the top of the page so they are easy to reach.
# `render_watchlist_section` consumes the existing `active_data` quotes
# dict (no new HTTP). `render_universal_search` issues its own search /
# detail calls only when the user types into the input.
render_watchlist_section(active_data)
render_universal_search()

# --- SPY Weight Analysis ---
spy_info = active_data.get("SPY")
SPY_TOP_WEIGHTS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "LLY", "JPM", "AVGO"]

if spy_info:
    top_weight_data = [active_data[t] for t in SPY_TOP_WEIGHTS if t in active_data]
    up_stocks = sorted([s for s in top_weight_data if s["pct_change"] > 0.1], key=lambda x: x["pct_change"], reverse=True)
    down_stocks = sorted([s for s in top_weight_data if s["pct_change"] < -0.1], key=lambda x: x["pct_change"])
    flat_stocks = [s for s in top_weight_data if -0.1 <= s["pct_change"] <= 0.1]

    total_top = len(top_weight_data)
    up_count = len(up_stocks)
    down_count = len(down_stocks)

    spy_dir = "up" if spy_info["pct_change"] > 0.05 else "down" if spy_info["pct_change"] < -0.05 else "flat"

    # Generate SPY summary
    if spy_dir == "down" and down_count >= 7:
        draggers = ", ".join([f"{s['ticker']} {s['pct_change']:+.1f}%" for s in down_stocks[:4]])
        spy_summary = f"📉 SPY {spy_info['pct_change']:+.2f}% — Top weights dragging hard: {draggers} pulling index lower. {down_count}/{total_top} leaders red."
        summary_color = "#9b2c2c"
    elif spy_dir == "up" and up_count >= 7:
        lifters = ", ".join([f"{s['ticker']} {s['pct_change']:+.1f}%" for s in up_stocks[:4]])
        spy_summary = f"📈 SPY {spy_info['pct_change']:+.2f}% — Top weights lifting: {lifters} driving rally. {up_count}/{total_top} leaders green."
        summary_color = "#276749"
    elif spy_dir == "down" and up_count >= 3 and down_count >= 3:
        draggers = ", ".join([f"{s['ticker']} {s['pct_change']:+.1f}%" for s in down_stocks[:3]])
        supporters = ", ".join([f"{s['ticker']} {s['pct_change']:+.1f}%" for s in up_stocks[:3]])
        spy_summary = f"📉 SPY {spy_info['pct_change']:+.2f}% — Mixed signals from top weights. Dragging: {draggers}. But {supporters} not supporting downside — leaders divided, no clear consensus."
        summary_color = "#c05621"
    elif spy_dir == "up" and down_count >= 3 and up_count >= 3:
        lifters = ", ".join([f"{s['ticker']} {s['pct_change']:+.1f}%" for s in up_stocks[:3]])
        laggards = ", ".join([f"{s['ticker']} {s['pct_change']:+.1f}%" for s in down_stocks[:3]])
        spy_summary = f"📈 SPY {spy_info['pct_change']:+.2f}% — Fragile rally. Pushing up: {lifters}. But {laggards} not confirming — move lacks full conviction from leaders."
        summary_color = "#b7791f"
    elif spy_dir == "flat":
        if up_count >= 3 and down_count >= 3:
            bulls_str = ", ".join([f"{s['ticker']} {s['pct_change']:+.1f}%" for s in up_stocks[:3]])
            bears_str = ", ".join([f"{s['ticker']} {s['pct_change']:+.1f}%" for s in down_stocks[:3]])
            spy_summary = f"⚖️ SPY flat ({spy_info['pct_change']:+.2f}%) — Top weights canceling out. Bulls: {bulls_str}. Bears: {bears_str}. No direction from leaders."
            summary_color = "#718096"
        else:
            spy_summary = f"⚖️ SPY flat ({spy_info['pct_change']:+.2f}%) — Top weights mostly quiet, low conviction day."
            summary_color = "#718096"
    elif spy_dir == "down":
        draggers = ", ".join([f"{s['ticker']} {s['pct_change']:+.1f}%" for s in down_stocks[:4]]) if down_stocks else "few movers"
        spy_summary = f"📉 SPY {spy_info['pct_change']:+.2f}% — Weakness from: {draggers}."
        summary_color = "#9b2c2c"
    else:
        lifters = ", ".join([f"{s['ticker']} {s['pct_change']:+.1f}%" for s in up_stocks[:4]]) if up_stocks else "few movers"
        spy_summary = f"📈 SPY {spy_info['pct_change']:+.2f}% — Strength from: {lifters}."
        summary_color = "#276749"

    st.markdown(f"""
    <div style="background:white;border-left:4px solid {summary_color};border-radius:8px;padding:14px 18px;margin-top:12px;margin-bottom:12px;box-shadow:0 2px 6px rgba(0,0,0,0.05);">
        <div style="font-weight:700;color:#2d3748;margin-bottom:6px;">🔍 SPY Weight Analysis</div>
        <div style="color:#4a5568;font-size:0.9rem;line-height:1.6;">{spy_summary}</div>
        <div style="margin-top:8px;font-size:0.75rem;color:#a0aec0;">Based on top 10 SPY holdings: {', '.join(SPY_TOP_WEIGHTS)}</div>
    </div>
    """, unsafe_allow_html=True)

# --- SPY OI Analysis ---
@st.cache_data(ttl=300)
def fetch_spy_options():
    """Fetch SPY options chain from Yahoo for nearest expiry."""
    try:
        # Get available expiry dates
        url = "https://query1.finance.yahoo.com/v7/finance/options/SPY"
        resp = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        data_opt = resp.json()["optionChain"]["result"][0]
        expiry_ts = data_opt["expirationDates"][0]  # nearest expiry

        # Get options chain for that expiry
        url2 = f"https://query1.finance.yahoo.com/v7/finance/options/SPY?date={expiry_ts}"
        resp2 = requests.get(url2, headers=YAHOO_HEADERS, timeout=10)
        if resp2.status_code != 200:
            return None
        result = resp2.json()["optionChain"]["result"][0]

        calls = result["options"][0]["calls"]
        puts = result["options"][0]["puts"]
        current_price = result["quote"]["regularMarketPrice"]

        # Parse expiry date
        from datetime import datetime as dt
        expiry_date = dt.fromtimestamp(expiry_ts).strftime("%b %d")

        # Calculate total call OI and put OI
        total_call_oi = sum(c.get("openInterest", 0) for c in calls)
        total_put_oi = sum(p.get("openInterest", 0) for p in puts)
        pc_ratio = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 0

        # Find highest OI call strike (resistance) and put strike (support)
        # Only look at strikes near current price (+/- 5%)
        near_calls = [c for c in calls if abs(c["strike"] - current_price) / current_price < 0.05]
        near_puts = [p for p in puts if abs(p["strike"] - current_price) / current_price < 0.05]

        top_call = max(near_calls, key=lambda x: x.get("openInterest", 0)) if near_calls else None
        top_put = max(near_puts, key=lambda x: x.get("openInterest", 0)) if near_puts else None

        # Find second highest for "next target" levels
        if top_call and near_calls:
            above_calls = sorted([c for c in near_calls if c["strike"] > top_call["strike"]], key=lambda x: x.get("openInterest", 0), reverse=True)
            next_resistance = above_calls[0]["strike"] if above_calls else top_call["strike"] + 5
        else:
            next_resistance = current_price + 10

        if top_put and near_puts:
            below_puts = sorted([p for p in near_puts if p["strike"] < top_put["strike"]], key=lambda x: x.get("openInterest", 0), reverse=True)
            next_support = below_puts[0]["strike"] if below_puts else top_put["strike"] - 5
        else:
            next_support = current_price - 10

        # Max Pain calculation (strike where total $ value of options expiring worthless is max)
        strikes = sorted(set([c["strike"] for c in calls] + [p["strike"] for p in puts]))
        max_pain_strike = current_price
        max_pain_value = 0
        for strike in strikes:
            call_pain = sum(max(0, strike - c["strike"]) * c.get("openInterest", 0) for c in calls)
            put_pain = sum(max(0, p["strike"] - strike) * p.get("openInterest", 0) for p in puts)
            total_pain = call_pain + put_pain
            if total_pain > max_pain_value:
                max_pain_value = total_pain
                max_pain_strike = strike

        return {
            "current_price": current_price,
            "expiry": expiry_date,
            "pc_ratio": pc_ratio,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "resistance": top_call["strike"] if top_call else None,
            "resistance_oi": top_call.get("openInterest", 0) if top_call else 0,
            "support": top_put["strike"] if top_put else None,
            "support_oi": top_put.get("openInterest", 0) if top_put else 0,
            "max_pain": max_pain_strike,
            "next_resistance": next_resistance,
            "next_support": next_support,
        }
    except Exception as e:
        return None

oi_data = fetch_spy_options()
if oi_data:
    price = oi_data["current_price"]
    resistance = oi_data["resistance"]
    support = oi_data["support"]
    max_pain = oi_data["max_pain"]
    pc_ratio = oi_data["pc_ratio"]
    next_res = oi_data["next_resistance"]
    next_sup = oi_data["next_support"]

    # Sentiment from P/C ratio
    if pc_ratio > 1.2:
        sentiment = "🔴 Bearish (heavy put hedging)"
    elif pc_ratio > 0.9:
        sentiment = "🟡 Cautious (slightly more puts)"
    elif pc_ratio > 0.7:
        sentiment = "🟢 Neutral-Bullish"
    else:
        sentiment = "🟢 Bullish (call heavy)"

    # Direction based on max pain
    if price < max_pain - 1:
        mp_direction = f"Price below Max Pain — likely to drift UP toward ${max_pain:.0f} by expiry."
    elif price > max_pain + 1:
        mp_direction = f"Price above Max Pain — likely to drift DOWN toward ${max_pain:.0f} by expiry."
    else:
        mp_direction = f"Price near Max Pain — expect sideways chop around ${max_pain:.0f}."

    # Breakout levels
    breakout_up = f"If SPY breaks above ${resistance:.0f} (call wall, {oi_data['resistance_oi']:,} OI), next target ${next_res:.0f}. Shorts will cover, accelerating move up."
    breakout_down = f"If SPY breaks below ${support:.0f} (put wall, {oi_data['support_oi']:,} OI), next drop to ${next_sup:.0f}. Put sellers forced to sell shares, accelerating move down."

    st.markdown(f"""
    <div style="background:white;border-left:4px solid #6b46c1;border-radius:8px;padding:14px 18px;margin-top:12px;margin-bottom:12px;box-shadow:0 2px 6px rgba(0,0,0,0.05);">
        <div style="font-weight:700;color:#2d3748;margin-bottom:8px;">🎯 SPY Options OI Analysis <span style="font-weight:400;font-size:0.8rem;color:#a0aec0;">(Expiry: {oi_data['expiry']})</span></div>
        <div style="color:#4a5568;font-size:0.88rem;line-height:1.7;">
            <b>SPY:</b> ${price:.2f} | <b>Put/Call Ratio:</b> {pc_ratio} {sentiment}<br/>
            <b>Max Pain:</b> ${max_pain:.0f} — {mp_direction}<br/>
            <b>Resistance:</b> ${resistance:.0f} ({oi_data['resistance_oi']:,} call OI) | <b>Support:</b> ${support:.0f} ({oi_data['support_oi']:,} put OI)<br/><br/>
            📍 <b>Breakout Levels:</b><br/>
            <span style="color:#276749;">▲ {breakout_up}</span><br/>
            <span style="color:#9b2c2c;">▼ {breakout_down}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

# --- Comprehensive Fear Index Analysis ---
st.subheader("📊 Fear Index (VIX) Analysis")
macro_cols = st.columns(3)

# Always use live data for macro indicators regardless of view mode
vix_data = data.get("^VIX")
tnx_data = data.get("^TNX")
xlf_data = data.get("XLF")

with macro_cols[0]:
    if vix_data:
        vix_icon = "🔴" if vix_data["price"] > 25 else "🟡" if vix_data["price"] > 18 else "🟢"
        st.metric(
            label=f"{vix_icon} VIX (Fear Index)",
            value=f"{vix_data['price']:.2f}",
            delta=f"{vix_data['change']:+.2f} ({vix_data['pct_change']:+.2f}%)",
            delta_color="inverse",
        )
    else:
        st.metric(label="VIX", value="--")

with macro_cols[1]:
    if tnx_data:
        st.metric(
            label="📊 10Y Treasury Yield",
            value=f"{tnx_data['price']:.2f}%",
            delta=f"{tnx_data['change']:+.2f} ({tnx_data['pct_change']:+.2f}%)",
            delta_color="off",
        )
    else:
        st.metric(label="10Y Yield", value="--")

with macro_cols[2]:
    if xlf_data:
        st.metric(
            label="🏦 XLF (Financials ETF)",
            value=f"${xlf_data['price']:.2f}",
            delta=f"{xlf_data['change']:+.2f} ({xlf_data['pct_change']:+.2f}%)",
        )
    else:
        st.metric(label="XLF", value="--")

# --- Fear Index Gauge ---
if vix_data:
    vix_level = vix_data["price"]
    vix_change = vix_data["pct_change"]
    spy_live = data.get("SPY")

    # Fear level
    if vix_level > 30:
        fear_label = "🔴 EXTREME FEAR"
        fear_desc = "Panic selling, capitulation likely. Extreme volatility expected."
        fear_color = "#9b2c2c"
        fear_bg = "#fed7d7"
        gauge_pct = 100
    elif vix_level > 25:
        fear_label = "🟠 HIGH FEAR"
        fear_desc = "Significant market stress. High volatility, defensive positioning."
        fear_color = "#dd6b20"
        fear_bg = "#feebc8"
        gauge_pct = 80
    elif vix_level > 20:
        fear_label = "🟡 ELEVATED FEAR"
        fear_desc = "Elevated caution. Market nervous, risk-off sentiment."
        fear_color = "#d69e2e"
        fear_bg = "#fefcbf"
        gauge_pct = 60
    elif vix_level > 15:
        fear_label = "🟢 MODERATE"
        fear_desc = "Normal market anxiety. Healthy skepticism, balanced risk."
        fear_color = "#38a169"
        fear_bg = "#c6f6d5"
        gauge_pct = 40
    else:
        fear_label = "🔵 LOW FEAR"
        fear_desc = "Market complacency. Low volatility, risk-on environment."
        fear_color = "#3182ce"
        fear_bg = "#bee3f8"
        gauge_pct = 20

    # Build analysis lines as plain strings (no nested f-strings)
    analysis_lines = []

    if spy_live:
        spy_chg = spy_live["pct_change"]
        if vix_change > 5 and spy_chg < -0.5:
            analysis_lines.append(f"📉 Classic risk-off: VIX +{vix_change:.1f}% + SPY {spy_chg:.1f}% — fear rising with selloff.")
        elif vix_change < -3 and spy_chg > 0.5:
            analysis_lines.append(f"📈 Risk-on signal: VIX {vix_change:.1f}% + SPY +{spy_chg:.1f}% — fear fading, rally confirmed.")
        elif vix_change > 2 and spy_chg > 0.5:
            analysis_lines.append(f"⚠️ Divergence: VIX rising +{vix_change:.1f}% despite SPY +{spy_chg:.1f}% — rally lacks conviction.")
        elif vix_change < -2 and spy_chg < -0.5:
            analysis_lines.append(f"⚠️ Divergence: VIX dropping {vix_change:.1f}% despite SPY {spy_chg:.1f}% — orderly selloff, not panic.")

    if vix_change > 10:
        analysis_lines.append(f"🚨 VIX spiking +{vix_change:.1f}% — sudden fear surge, expect volatile moves.")
    elif vix_change > 5:
        analysis_lines.append(f"⚠️ VIX rising +{vix_change:.1f}% — market becoming defensive.")
    elif vix_change < -5:
        analysis_lines.append(f"😌 VIX falling {vix_change:.1f}% — fear receding, risk appetite improving.")

    if bull_pct >= 60 and vix_level < 18:
        analysis_lines.append(f"✅ Healthy: {bull_pct}% stocks green + low VIX = sustainable rally.")
    elif bear_pct >= 60 and vix_level > 22:
        analysis_lines.append(f"🔴 Broad stress: {bear_pct}% stocks red + VIX {vix_level:.1f} = genuine fear.")
    elif bull_pct >= 60 and vix_level > 22:
        analysis_lines.append(f"⚠️ Fragile rally: {bull_pct}% green but VIX high ({vix_level:.1f}) — fear persists.")
    elif bear_pct >= 60 and vix_level < 18:
        analysis_lines.append(f"⚠️ Orderly selloff: {bear_pct}% red but VIX low ({vix_level:.1f}) — no panic.")

    if vix_level > 30:
        analysis_lines.append("📜 VIX >30: Extreme fear — historically a contrarian buy signal near bottoms.")
    elif vix_level < 12:
        analysis_lines.append("📜 VIX <12: Extreme complacency — volatility spike risk ahead.")

    if vix_level > 25:
        analysis_lines.append("💡 High VIX: Favor defensive sectors, hedging, volatility strategies.")
    elif vix_level < 15:
        analysis_lines.append("💡 Low VIX: Favor momentum, growth/tech, reduced hedging costs.")

    analysis_html = "".join([f'<div style="padding:3px 0;color:#4a5568;font-size:0.85rem;">{line}</div>' for line in analysis_lines])

    prev_close_vix = vix_data.get("prev_close", 0)
    spy_str = f"{spy_live['pct_change']:+.1f}%" if spy_live else "--"

    st.markdown(
        f'<div style="background:white;border-left:4px solid {fear_color};border-radius:8px;'
        f'padding:16px 20px;margin-top:12px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,0.06);">'
        f'<div style="font-weight:800;color:{fear_color};font-size:1.1rem;margin-bottom:6px;">{fear_label}</div>'
        f'<div style="color:#4a5568;font-size:0.88rem;margin-bottom:10px;">{fear_desc}</div>'
        f'<div style="background:#edf2f7;border-radius:20px;height:16px;overflow:hidden;margin-bottom:6px;">'
        f'<div style="background:{fear_color};height:100%;width:{gauge_pct}%;border-radius:20px;"></div></div>'
        f'<div style="display:flex;justify-content:space-between;font-size:0.68rem;color:#a0aec0;margin-bottom:12px;">'
        f'<span>Calm</span><span>Normal</span><span>Elevated</span><span>High</span><span>Panic</span></div>'
        f'<div style="display:flex;gap:12px;margin-bottom:12px;">'
        f'<div style="background:{fear_bg};border-radius:6px;padding:8px 12px;flex:1;text-align:center;">'
        f'<div style="font-size:0.75rem;color:#718096;">VIX Now</div>'
        f'<div style="font-size:1.2rem;font-weight:800;color:{fear_color};">{vix_level:.1f}</div>'
        f'<div style="font-size:0.75rem;color:#718096;">{vix_change:+.1f}% today</div></div>'
        f'<div style="background:#f7fafc;border-radius:6px;padding:8px 12px;flex:1;text-align:center;">'
        f'<div style="font-size:0.75rem;color:#718096;">Bulls / Bears</div>'
        f'<div style="font-size:1rem;font-weight:700;color:#2d3748;">{bull_pct}% / {bear_pct}%</div>'
        f'<div style="font-size:0.75rem;color:#718096;">SPY {spy_str}</div></div>'
        f'<div style="background:#f7fafc;border-radius:6px;padding:8px 12px;flex:1;text-align:center;">'
        f'<div style="font-size:0.75rem;color:#718096;">Prev Close</div>'
        f'<div style="font-size:1rem;font-weight:700;color:#2d3748;">{prev_close_vix:.1f}</div>'
        f'<div style="font-size:0.75rem;color:#718096;">VIX yesterday</div></div></div>'
        f'{analysis_html}'
        f'</div>',
        unsafe_allow_html=True,
    )

# --- Insights ---
st.subheader("🧠 Market Insights")
insights = generate_insights(active_data)
for insight in insights:
    st.markdown(f'<div class="insight-box">{insight}</div>', unsafe_allow_html=True)

# ============================================================
# --- MARKET INTELLIGENCE (8 Features) ---
# ============================================================
st.subheader("🔬 Market Intelligence")

stocks_for_intel = {k: v for k, v in active_data.items() if k not in INDICES and k not in MACRO_TICKERS}
spy_intel = active_data.get("SPY")

# --- Helper: sector averages ---
intel_sector_avg = {}
for info in stocks_for_intel.values():
    sec = info.get("sector")
    if sec and sec not in ("Index", "Core"):
        intel_sector_avg.setdefault(sec, []).append(info["pct_change"])
intel_sector_summary = {s: round(sum(c)/len(c), 2) for s, c in intel_sector_avg.items()}
sorted_intel_sectors = sorted(intel_sector_summary.items(), key=lambda x: x[1], reverse=True)

# ---- 1. MARKET REGIME ----
with st.expander("📊 Market Regime", expanded=True):
    vix_regime = data.get("^VIX")
    vix_lvl = vix_regime["price"] if vix_regime else 20
    spy_pct = spy_intel["pct_change"] if spy_intel else 0
    green_sectors = sum(1 for v in intel_sector_summary.values() if v > 0)
    total_sectors = len(intel_sector_summary)

    if spy_pct > 0.3 and vix_lvl < 18 and bull_pct >= 55:
        regime = "🟢 RISK-ON BULL"
        regime_color = "#276749"
        regime_desc = f"SPY +{spy_pct:.1f}%, VIX low ({vix_lvl:.1f}), {bull_pct}% stocks green, {green_sectors}/{total_sectors} sectors up. Strong bull conditions — momentum favors longs."
    elif spy_pct < -0.3 and vix_lvl > 20 and bear_pct >= 55:
        regime = "🔴 RISK-OFF BEAR"
        regime_color = "#9b2c2c"
        regime_desc = f"SPY {spy_pct:.1f}%, VIX elevated ({vix_lvl:.1f}), {bear_pct}% stocks red. Bear conditions — defensive positioning, reduce exposure."
    elif vix_lvl > 25:
        regime = "🟠 HIGH VOLATILITY / FEAR"
        regime_color = "#c05621"
        regime_desc = f"VIX at {vix_lvl:.1f} — elevated fear. Market unstable, expect sharp moves in both directions. Reduce size."
    elif abs(spy_pct) < 0.2 and vix_lvl < 16:
        regime = "🔵 CONSOLIDATION / CHOP"
        regime_color = "#3182ce"
        regime_desc = f"SPY flat ({spy_pct:+.1f}%), VIX low ({vix_lvl:.1f}). Market digesting recent moves. Range-bound, avoid chasing."
    elif spy_pct > 0 and bear_pct > 50:
        regime = "🟡 NARROW RALLY"
        regime_color = "#b7791f"
        regime_desc = f"SPY +{spy_pct:.1f}% but only {bull_pct}% stocks green. Mega-cap driven, not broad. Fragile — don't chase."
    else:
        regime = "⚪ MIXED / TRANSITIONING"
        regime_color = "#718096"
        regime_desc = f"SPY {spy_pct:+.1f}%, VIX {vix_lvl:.1f}. No clear regime. Wait for confirmation before taking directional bets."

    st.markdown(
        f'<div style="background:white;border-left:4px solid {regime_color};border-radius:8px;padding:12px 16px;">'
        f'<div style="font-weight:800;color:{regime_color};font-size:1rem;">{regime}</div>'
        f'<div style="color:#4a5568;font-size:0.85rem;margin-top:4px;">{regime_desc}</div>'
        f'</div>', unsafe_allow_html=True)

# ---- 2. SECTOR ROTATION ----
with st.expander("🔄 Sector Rotation Detector"):
    if len(sorted_intel_sectors) >= 2:
        top_sectors = sorted_intel_sectors[:3]
        bot_sectors = sorted_intel_sectors[-3:]
        inflow = [s for s, v in top_sectors if v > 0.3]
        outflow = [s for s, v in bot_sectors if v < -0.3]

        if inflow and outflow:
            in_str = ", ".join([f"{s} ({intel_sector_summary[s]:+.1f}%)" for s in inflow])
            out_str = ", ".join([f"{s} ({intel_sector_summary[s]:+.1f}%)" for s in outflow])
            st.markdown(f"🔄 **Rotation detected:** Money moving **OUT of** {out_str} → **INTO** {in_str}")

            # Classify rotation type
            if any(s in inflow for s in ["Energy", "Oil", "Gold"]) and any(s in outflow for s in ["Tech", "Semis"]):
                st.markdown("📌 **Risk-off rotation** — defensive/commodity sectors gaining, growth selling off. Market cautious.")
            elif any(s in inflow for s in ["Tech", "Semis", "Quantum"]) and any(s in outflow for s in ["Energy", "Consumer"]):
                st.markdown("📌 **Risk-on rotation** — growth/tech leading, defensives lagging. Bullish signal.")
            elif any(s in inflow for s in ["Gold"]):
                st.markdown("📌 **Safe haven rotation** — Gold gaining. Uncertainty/fear driving money to safety.")
        elif inflow:
            in_str = ", ".join([f"{s} ({intel_sector_summary[s]:+.1f}%)" for s in inflow])
            st.markdown(f"📈 **Sector strength:** {in_str} leading. No clear rotation — broad move.")
        else:
            st.markdown("⚪ No significant sector rotation detected today.")

        # Show all sectors ranked
        cols_r = st.columns(2)
        with cols_r[0]:
            st.markdown("**Top sectors (inflow):**")
            for s, v in sorted_intel_sectors[:5]:
                color = "#276749" if v > 0 else "#9b2c2c"
                st.markdown(f'<span style="color:{color};font-weight:600;">{s}: {v:+.2f}%</span>', unsafe_allow_html=True)
        with cols_r[1]:
            st.markdown("**Bottom sectors (outflow):**")
            for s, v in sorted_intel_sectors[-5:]:
                color = "#276749" if v > 0 else "#9b2c2c"
                st.markdown(f'<span style="color:{color};font-weight:600;">{s}: {v:+.2f}%</span>', unsafe_allow_html=True)

# ---- 3. RELATIVE STRENGTH ----
with st.expander("💪 Relative Strength vs SPY"):
    if spy_intel:
        spy_move = spy_intel["pct_change"]
        all_stocks_rs = sorted(stocks_for_intel.values(), key=lambda x: x["pct_change"] - spy_move, reverse=True)
        strong_rs = [s for s in all_stocks_rs if s["pct_change"] - spy_move > 1.0][:8]
        weak_rs = [s for s in all_stocks_rs if s["pct_change"] - spy_move < -1.0][-8:]

        st.caption(f"SPY today: {spy_move:+.2f}% | Showing stocks outperforming/underperforming by 1%+")
        rs_col1, rs_col2 = st.columns(2)
        with rs_col1:
            st.markdown("**💪 Strong RS (outperforming SPY):**")
            for s in strong_rs:
                rs = s["pct_change"] - spy_move
                st.markdown(f'<div class="stock-row"><b>{s["ticker"]}</b> <small style="color:#718096">{s["sector"]}</small>'
                    f'<span class="gain" style="float:right"><b>{s["pct_change"]:+.2f}%</b> (RS: +{rs:.1f}%)</span></div>', unsafe_allow_html=True)
        with rs_col2:
            st.markdown("**🩸 Weak RS (underperforming SPY):**")
            for s in reversed(weak_rs):
                rs = s["pct_change"] - spy_move
                st.markdown(f'<div class="stock-row"><b>{s["ticker"]}</b> <small style="color:#718096">{s["sector"]}</small>'
                    f'<span class="loss" style="float:right"><b>{s["pct_change"]:+.2f}%</b> (RS: {rs:.1f}%)</span></div>', unsafe_allow_html=True)
    else:
        st.info("SPY data not available")

# ---- 4. GAP UP / GAP DOWN ----
with st.expander("📊 Gap Up / Gap Down Detector"):
    gaps_up = sorted([s for s in stocks_for_intel.values() if s["pct_change"] > 2.5], key=lambda x: x["pct_change"], reverse=True)[:10]
    gaps_down = sorted([s for s in stocks_for_intel.values() if s["pct_change"] < -2.5], key=lambda x: x["pct_change"])[:10]

    gap_col1, gap_col2 = st.columns(2)
    with gap_col1:
        st.markdown("**📈 Gap Ups (>2.5%):**")
        if gaps_up:
            for s in gaps_up:
                st.markdown(f'<div class="stock-row"><b>{s["ticker"]}</b> <small style="color:#718096">{s["sector"]}</small>'
                    f'<span class="gain" style="float:right"><b>{s["pct_change"]:+.2f}%</b> | ${s["price"]:.2f}</span></div>', unsafe_allow_html=True)
        else:
            st.caption("No significant gap ups today")
    with gap_col2:
        st.markdown("**📉 Gap Downs (>2.5%):**")
        if gaps_down:
            for s in gaps_down:
                st.markdown(f'<div class="stock-row"><b>{s["ticker"]}</b> <small style="color:#718096">{s["sector"]}</small>'
                    f'<span class="loss" style="float:right"><b>{s["pct_change"]:+.2f}%</b> | ${s["price"]:.2f}</span></div>', unsafe_allow_html=True)
        else:
            st.caption("No significant gap downs today")

# ---- 5. 52-WEEK HIGH/LOW TRACKER ----
with st.expander("🏆 52-Week High / Low Tracker"):
    @st.cache_data(ttl=300)
    def fetch_52w_data():
        tickers_52w = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AMD","JPM","AVGO",
                       "XOM","CVX","NEM","GOLD","FCX","ENPH","IONQ","LMT","NTR","MOS"]
        results = {}
        def fetch_one(ticker):
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1y"
                r = requests.get(url, headers=YAHOO_HEADERS, timeout=8)
                if r.status_code != 200: return None
                meta = r.json()["chart"]["result"][0]["meta"]
                return {
                    "ticker": ticker,
                    "price": meta.get("regularMarketPrice", 0),
                    "high52": meta.get("fiftyTwoWeekHigh", 0),
                    "low52": meta.get("fiftyTwoWeekLow", 0),
                }
            except: return None
        with ThreadPoolExecutor(max_workers=10) as ex:
            res = list(ex.map(fetch_one, tickers_52w))
        return [r for r in res if r]

    data_52w = fetch_52w_data()
    if data_52w:
        near_high = [s for s in data_52w if s["high52"] > 0 and s["price"] >= s["high52"] * 0.98]
        near_low = [s for s in data_52w if s["low52"] > 0 and s["price"] <= s["low52"] * 1.02]

        w52_col1, w52_col2 = st.columns(2)
        with w52_col1:
            st.markdown("**🏆 Near/At 52W High:**")
            if near_high:
                for s in near_high:
                    pct_from_high = ((s["price"] - s["high52"]) / s["high52"]) * 100
                    st.markdown(f'<div class="stock-row"><b>{s["ticker"]}</b>'
                        f'<span style="float:right;color:#276749;font-weight:600;">${s["price"]:.2f} '
                        f'<small>(52W H: ${s["high52"]:.2f}, {pct_from_high:+.1f}%)</small></span></div>', unsafe_allow_html=True)
            else:
                st.caption("No stocks near 52W high")
        with w52_col2:
            st.markdown("**⚠️ Near/At 52W Low:**")
            if near_low:
                for s in near_low:
                    pct_from_low = ((s["price"] - s["low52"]) / s["low52"]) * 100
                    st.markdown(f'<div class="stock-row"><b>{s["ticker"]}</b>'
                        f'<span style="float:right;color:#9b2c2c;font-weight:600;">${s["price"]:.2f} '
                        f'<small>(52W L: ${s["low52"]:.2f}, +{pct_from_low:.1f}%)</small></span></div>', unsafe_allow_html=True)
            else:
                st.caption("No stocks near 52W low")

# ---- 6. DOLLAR + COMMODITIES CONNECTION ----
with st.expander("💵 Dollar & Commodities Connection"):
    @st.cache_data(ttl=60)
    def fetch_macro_extra():
        tickers = ["DX-Y.NYB", "GLD", "USO", "SLV", "UNG"]
        names = {"DX-Y.NYB": "DXY (Dollar)", "GLD": "Gold ETF", "USO": "Oil ETF", "SLV": "Silver ETF", "UNG": "Nat Gas ETF"}
        results = {}
        for t in tickers:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/spark?symbols={t}&range=2d&interval=1d"
                r = requests.get(url, headers=YAHOO_HEADERS, timeout=8)
                if r.status_code != 200: continue
                info = r.json().get(t, {})
                closes = info.get("close", [])
                if len(closes) >= 2 and closes[-2]:
                    price = closes[-1]
                    prev = closes[-2]
                    pct = ((price - prev) / prev) * 100
                    results[t] = {"name": names[t], "price": round(price, 2), "pct": round(pct, 2)}
            except: continue
        return results

    macro_extra = fetch_macro_extra()
    if macro_extra:
        dxy = macro_extra.get("DX-Y.NYB")
        gld = macro_extra.get("GLD")
        uso = macro_extra.get("USO")

        # Show metrics
        m_cols = st.columns(len(macro_extra))
        for i, (t, info) in enumerate(macro_extra.items()):
            with m_cols[i]:
                color = "normal" if info["pct"] >= 0 else "inverse"
                st.metric(info["name"], f"${info['price']:.2f}", f"{info['pct']:+.2f}%")

        # Connect the dots
        if dxy and gld:
            if dxy["pct"] > 0.3 and gld["pct"] < -0.3:
                st.markdown("🔗 **Dollar up + Gold down** — classic inverse relationship. Strong USD pressuring precious metals.")
            elif dxy["pct"] < -0.3 and gld["pct"] > 0.3:
                st.markdown("🔗 **Dollar down + Gold up** — USD weakness boosting commodities and precious metals.")
            elif dxy["pct"] > 0.3 and gld["pct"] > 0.3:
                st.markdown("⚠️ **Dollar up + Gold up** — unusual divergence. Possible geopolitical fear driving gold despite USD strength.")
        if dxy and uso:
            if dxy["pct"] > 0.3 and uso["pct"] < -0.3:
                st.markdown("🔗 **Dollar up + Oil down** — USD strength making oil more expensive globally, reducing demand.")
            elif dxy["pct"] < -0.3 and uso["pct"] > 0.3:
                st.markdown("🔗 **Dollar down + Oil up** — USD weakness supporting oil prices.")
    else:
        st.caption("Commodity data unavailable")

# ---- 7. VOLUME SPIKE ALERT ----
with st.expander("🔊 Volume Spike Alert"):
    @st.cache_data(ttl=60)
    def fetch_volume_data():
        core_tickers = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AMD","JPM","AVGO",
                        "XOM","CVX","BAC","GS","V","MA","INTC","CRM","LLY","UNH"]
        results = []
        def fetch_vol(ticker):
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/spark?symbols={ticker}&range=5d&interval=1d"
                r = requests.get(url, headers=YAHOO_HEADERS, timeout=8)
                if r.status_code != 200: return None
                info = r.json().get(ticker, {})
                closes = info.get("close", [])
                # Volume not in spark — use chart endpoint for volume
                url2 = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
                r2 = requests.get(url2, headers=YAHOO_HEADERS, timeout=8)
                if r2.status_code != 200: return None
                result = r2.json()["chart"]["result"][0]
                volumes = result["indicators"]["quote"][0].get("volume", [])
                valid_vols = [v for v in volumes if v and v > 0]
                if len(valid_vols) < 2: return None
                today_vol = valid_vols[-1]
                avg_vol = sum(valid_vols[:-1]) / len(valid_vols[:-1])
                multiplier = today_vol / avg_vol if avg_vol > 0 else 0
                if multiplier >= 1.8:
                    meta = result["meta"]
                    return {
                        "ticker": ticker,
                        "today_vol": today_vol,
                        "avg_vol": round(avg_vol),
                        "multiplier": round(multiplier, 1),
                        "price": meta.get("regularMarketPrice", 0),
                        "pct": active_data.get(ticker, {}).get("pct_change", 0),
                    }
                return None
            except: return None

        with ThreadPoolExecutor(max_workers=10) as ex:
            res = list(ex.map(fetch_vol, core_tickers))
        return sorted([r for r in res if r], key=lambda x: x["multiplier"], reverse=True)

    vol_data = fetch_volume_data()
    if vol_data:
        st.caption("Stocks trading 1.8x+ their average volume — unusual activity")
        for s in vol_data[:10]:
            direction = "gain" if s["pct"] >= 0 else "loss"
            sign = "+" if s["pct"] >= 0 else ""
            vol_str = f"{s['today_vol']/1e6:.1f}M" if s['today_vol'] >= 1e6 else f"{s['today_vol']/1e3:.0f}K"
            avg_str = f"{s['avg_vol']/1e6:.1f}M" if s['avg_vol'] >= 1e6 else f"{s['avg_vol']/1e3:.0f}K"
            st.markdown(
                f'<div class="stock-row"><b>{s["ticker"]}</b>'
                f'<span style="float:right;font-size:0.85rem;">'
                f'Vol: <b>{vol_str}</b> vs avg {avg_str} '
                f'(<b style="color:#6b46c1;">{s["multiplier"]}x</b>) | '
                f'<span class="{direction}"><b>{sign}{s["pct"]:.2f}%</b></span></span></div>',
                unsafe_allow_html=True)
    else:
        st.caption("No unusual volume detected today")

# ---- 8. EARNINGS CALENDAR ----
with st.expander("📅 Earnings This Week (Tracked Stocks)"):
    @st.cache_data(ttl=3600)
    def fetch_earnings_calendar():
        """
        Fetch earnings dates by scraping Yahoo Finance earnings calendar page.
        Filters to only show tracked stocks.
        """
        tracked = set([t for tickers in STOCKS.values() for t in tickers])
        earnings = []
        now = datetime.now(ET)

        for day_offset in range(-1, 8):
            check_date = now + __import__('datetime').timedelta(days=day_offset)
            if check_date.weekday() >= 5:
                continue
            date_str = check_date.strftime("%Y-%m-%d")
            try:
                url = f"https://finance.yahoo.com/calendar/earnings?day={date_str}"
                r = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
                if r.status_code != 200:
                    continue
                # Parse tickers from the page HTML
                import re
                # Yahoo embeds earnings data in a JSON blob in the page
                matches = re.findall(r'"ticker":"([A-Z\-]+)"', r.text)
                # Also look for symbol patterns near earningsDate
                matches2 = re.findall(r'"symbol":"([A-Z\-]{1,6})"', r.text)
                all_tickers_found = set(matches + matches2)
                for t in all_tickers_found:
                    if t in tracked:
                        days_away = day_offset
                        if days_away < 0:
                            label = "Yesterday"
                            color = "#a0aec0"
                        elif days_away == 0:
                            label = "TODAY"
                            color = "#9b2c2c"
                        elif days_away == 1:
                            label = "Tomorrow"
                            color = "#c05621"
                        else:
                            label = f"In {days_away} days"
                            color = "#2b6cb0"
                        earnings.append({
                            "ticker": t,
                            "date": check_date.strftime("%a %b %d"),
                            "days_away": days_away,
                            "label": label,
                            "color": color,
                        })
            except:
                continue

        # Deduplicate by ticker (keep earliest)
        seen = {}
        for e in sorted(earnings, key=lambda x: x["days_away"]):
            if e["ticker"] not in seen:
                seen[e["ticker"]] = e
        return list(seen.values())

    earnings_cal = fetch_earnings_calendar()
    if earnings_cal:
        for e in sorted(earnings_cal, key=lambda x: x["days_away"]):
            st.markdown(
                f'<div class="stock-row"><b>{e["ticker"]}</b>'
                f'<span style="float:right;color:{e["color"]};font-weight:600;">'
                f'{e["date"]} ({e["label"]})</span></div>',
                unsafe_allow_html=True)
    else:
        # Fallback: show known upcoming earnings manually
        st.caption("Live earnings data unavailable. Known upcoming earnings:")
        known = [
            ("NVDA", "Wed May 28", 8, "#2b6cb0"),
            ("HD", "Tue May 20", 0, "#9b2c2c"),
            ("TGT", "Wed May 21", 1, "#c05621"),
            ("COST", "Thu May 29", 9, "#2b6cb0"),
        ]
        for ticker, date, days, color in known:
            label = "TODAY" if days == 0 else f"In {days} days"
            st.markdown(
                f'<div class="stock-row"><b>{ticker}</b>'
                f'<span style="float:right;color:{color};font-weight:600;">'
                f'{date} ({label})</span></div>',
                unsafe_allow_html=True)
        st.caption("⚠️ Update earnings dates manually each week")

# ============================================================

# --- Economic Calendar (Forex Factory) ---
st.subheader("📅 Economic Calendar")
st.caption("High 🔴 and Medium 🟡 impact USD events only — powered by Forex Factory")

@st.cache_data(ttl=1800)  # Cache 30 min
def fetch_ff_calendar():
    """Fetch Forex Factory calendar for this week and next week."""
    HEADERS_FF = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]
    all_events = []
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS_FF, timeout=10)
            if r.status_code == 200:
                all_events.extend(r.json())
        except:
            continue
    return all_events

ff_events = fetch_ff_calendar()

if ff_events:
    from datetime import timedelta as td
    import re

    # Filter: only High (red) and Medium (yellow) USD events
    filtered = [
        e for e in ff_events
        if e.get("impact") in ("High", "Medium")
        and e.get("country") == "USD"
    ]

    if not filtered:
        st.info("No high/medium impact USD events found.")
    else:
        # Parse dates and group by week
        def parse_ff_date(date_str):
            try:
                # Format: "2026-05-20T14:00:00-04:00"
                return datetime.fromisoformat(date_str).astimezone(ET)
            except:
                return None

        events_parsed = []
        for e in filtered:
            dt_obj = parse_ff_date(e.get("date", ""))
            if dt_obj:
                events_parsed.append({
                    "dt": dt_obj,
                    "impact": e.get("impact", ""),
                    "title": e.get("title", ""),
                    "forecast": e.get("forecast", "") or "--",
                    "actual": e.get("actual", "") or "--",
                    "previous": e.get("previous", "") or "--",
                    "currency": e.get("country", "USD"),
                })

        # Sort by date
        events_parsed.sort(key=lambda x: x["dt"])

        # Group by week (Mon-Fri)
        def get_week_label(dt_obj):
            monday = dt_obj - td(days=dt_obj.weekday())
            friday = monday + td(days=4)
            return f"Week of {monday.strftime('%b %d')} – {friday.strftime('%b %d, %Y')}"

        weeks = {}
        for e in events_parsed:
            wk = get_week_label(e["dt"])
            weeks.setdefault(wk, []).append(e)

        now_et = datetime.now(ET)

        for week_label, week_events in weeks.items():
            with st.expander(f"📆 {week_label}", expanded=(list(weeks.keys()).index(week_label) == 0)):
                for e in week_events:
                    impact = e["impact"]
                    is_past = e["dt"] < now_et
                    is_today = e["dt"].date() == now_et.date()

                    # Colors
                    if impact == "High":
                        dot = "🔴"
                        border_color = "#9b2c2c"
                        bg_color = "#fff5f5"
                    else:
                        dot = "🟡"
                        border_color = "#b7791f"
                        bg_color = "#fffff0"

                    # Time display
                    time_str = e["dt"].strftime("%a %b %d  %I:%M %p ET")
                    today_badge = ' <span style="background:#9b2c2c;color:white;padding:1px 6px;border-radius:4px;font-size:0.7rem;font-weight:700;">TODAY</span>' if is_today else ""
                    past_style = "opacity:0.55;" if is_past and not is_today else ""

                    # Actual vs Forecast coloring
                    actual_html = "--"
                    if e["actual"] != "--":
                        actual_html = f'<b style="color:#276749;">{e["actual"]}</b>'

                    st.markdown(
                        f'<div style="border-left:3px solid {border_color};background:{bg_color};'
                        f'border-radius:6px;padding:8px 12px;margin-bottom:6px;{past_style}">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                        f'<div>'
                        f'{dot} <b style="color:#2d3748;">{e["title"]}</b>{today_badge}<br/>'
                        f'<small style="color:#718096;">{time_str}</small>'
                        f'</div>'
                        f'<div style="text-align:right;font-size:0.82rem;">'
                        f'<span style="color:#718096;">Forecast: </span><b>{e["forecast"]}</b> &nbsp;'
                        f'<span style="color:#718096;">Actual: </span>{actual_html} &nbsp;'
                        f'<span style="color:#718096;">Prev: </span>{e["previous"]}'
                        f'</div>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
else:
    st.info("Economic calendar data unavailable. Try again later.")

# ============================================================

# --- Stock Catalyst Scanner ---
st.subheader("📰 Stock Catalyst Scanner")
st.caption("Latest news for SPY top 10 stocks — scored for market impact | Sources: Yahoo Finance, CNBC, MarketWatch")

@st.cache_data(ttl=300)
def fetch_stock_news(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}&newsCount=8&quotesCount=0"
        r = requests.get(url, headers=YAHOO_HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        return r.json().get("news", [])
    except:
        return []

@st.cache_data(ttl=300)
def fetch_analyst_calls(ticker):
    """Fetch today's analyst upgrade/downgrade/price target news."""
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}+analyst+upgrade+downgrade+price+target&newsCount=5&quotesCount=0"
        r = requests.get(url, headers=YAHOO_HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        news = r.json().get("news", [])
        today_ts = datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        analyst_kws = ["upgrade", "downgrade", "price target", "initiates", "raises target",
                       "lowers target", "buy rating", "sell rating", "overweight", "underweight",
                       "outperform", "underperform", "neutral", "hold", "analyst"]
        results = []
        for n in news:
            ts = n.get("providerPublishTime", 0)
            title = n.get("title", "").lower()
            if ts >= today_ts and any(kw in title for kw in analyst_kws):
                results.append(n)
        return results
    except:
        return []

@st.cache_data(ttl=300)
def fetch_macro_headlines():
    import xml.etree.ElementTree as ET_xml
    headlines = []
    sources = [
        ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("CNBC Markets", "https://www.cnbc.com/id/10001147/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ]
    for source_name, url in sources:
        try:
            r = requests.get(url, headers=YAHOO_HEADERS, timeout=8)
            if r.status_code != 200:
                continue
            root = ET_xml.fromstring(r.content)
            for item in root.findall(".//item"):
                title_el = item.find("title")
                link_el = item.find("link")
                pub_el = item.find("pubDate")
                if title_el is not None and title_el.text:
                    headlines.append({
                        "title": title_el.text,
                        "source": source_name,
                        "link": link_el.text if link_el is not None else "",
                        "pub": pub_el.text if pub_el is not None else "",
                    })
        except:
            continue
    return headlines

def score_headline(title):
    t = title.lower()
    bullish = {
        "beats": "Earnings beat", "beat earnings": "Earnings beat",
        "surges": "Strong move up", "jumps": "Strong move up", "soars": "Strong move up",
        "upgrade": "Analyst upgrade", "upgraded": "Analyst upgrade",
        "raises guidance": "Guidance raised", "raises forecast": "Forecast raised",
        "record": "Record performance", "all-time high": "All-time high",
        "fda approv": "FDA approval", "approved": "Regulatory approval",
        "trial success": "Clinical success", "positive data": "Positive trial",
        "partnership": "New partnership", "contract": "New contract",
        "buyback": "Share buyback", "dividend increase": "Dividend raised",
        "strong earnings": "Strong earnings", "profit rises": "Profit up",
        "revenue beat": "Revenue beat", "exceeds": "Beat expectations",
        "wins": "Contract win", "deal": "New deal signed",
    }
    bearish = {
        "misses": "Earnings miss", "miss earnings": "Earnings miss",
        "drops": "Price decline", "falls": "Price decline",
        "plunges": "Sharp decline", "tumbles": "Sharp decline", "slumps": "Decline",
        "downgrade": "Analyst downgrade", "downgraded": "Analyst downgrade",
        "cuts guidance": "Guidance cut", "lowers forecast": "Forecast lowered",
        "recall": "Product recall", "lawsuit": "Legal issue",
        "investigation": "Under investigation", "probe": "Regulatory probe",
        "layoffs": "Job cuts", "job cuts": "Job cuts",
        "bankruptcy": "Bankruptcy risk",
        "fda rejects": "FDA rejection", "rejected": "Rejection",
        "warning": "Warning issued", "loss widens": "Loss widening",
        "tariff": "Tariff impact", "sanction": "Sanctions",
        "weak earnings": "Weak earnings", "revenue miss": "Revenue miss",
    }
    for kw, reason in bullish.items():
        if kw in t:
            return "bullish", reason
    for kw, reason in bearish.items():
        if kw in t:
            return "bearish", reason
    return "neutral", "General news"

def match_to_tickers(headline, tickers):
    NAMES = {
        "AAPL": ["apple", "iphone", "ipad", "tim cook"],
        "MSFT": ["microsoft", "azure", "satya nadella"],
        "NVDA": ["nvidia", "jensen huang", "h100", "blackwell"],
        "AMZN": ["amazon", "aws", "andy jassy"],
        "GOOGL": ["google", "alphabet", "youtube", "gemini"],
        "META": ["meta", "facebook", "instagram", "zuckerberg"],
        "BRK-B": ["berkshire", "warren buffett"],
        "LLY": ["eli lilly", "lilly", "mounjaro", "tirzepatide"],
        "JPM": ["jpmorgan", "jp morgan", "jamie dimon"],
        "AVGO": ["broadcom"],
        "TSLA": ["tesla", "elon musk", "cybertruck", "model 3", "model y", "model s", "powerwall", "megapack", "supercharger"],
        "AMD": ["amd", "lisa su", "radeon"],
        "INTC": ["intel"],
        "CRM": ["salesforce"],
    }
    t = headline.lower()
    matched = []
    for ticker in tickers:
        if ticker.lower() in t:
            matched.append(ticker)
            continue
        for name in NAMES.get(ticker, []):
            if name in t:
                matched.append(ticker)
                break
    return matched

CATALYST_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "LLY", "JPM", "AVGO", "TSLA"]

macro_headlines = fetch_macro_headlines()

catalyst_tab, macro_tab = st.tabs(["🎯 Stock Catalysts", "📡 Market Headlines (CNBC + MarketWatch)"])

with catalyst_tab:
    st.caption("Click any stock to expand news and analyst calls")
    for idx, ticker in enumerate(CATALYST_TICKERS):
        stock_info = active_data.get(ticker, {})
        pct = stock_info.get("pct_change", 0)
        price = stock_info.get("price", 0)
        pm_tag = " 🏷️PM" if stock_info.get("is_premarket") else ""
        sign = "+" if pct >= 0 else ""
        pct_color = "🟢" if pct >= 0 else "🔴"

        stock_news_raw = fetch_stock_news(ticker)
        analyst_news = fetch_analyst_calls(ticker)
        macro_matches = [h for h in macro_headlines if match_to_tickers(h["title"], [ticker])]

        # Build news with timestamps + strict matching
        all_news = []
        for n in stock_news_raw[:8]:
            title = n.get("title", "")
            if not match_to_tickers(title, [ticker]):
                continue
            ts = n.get("providerPublishTime", 0)
            time_str = datetime.fromtimestamp(ts, tz=ET).strftime("%b %d %I:%M %p ET") if ts else ""
            s, r = score_headline(title)
            all_news.append({"title": title, "source": n.get("publisher", "Yahoo"),
                             "sentiment": s, "reason": r, "time": time_str,
                             "link": n.get("link", "")})
        for n in macro_matches[:2]:
            s, r = score_headline(n.get("title", ""))
            all_news.append({"title": n.get("title", ""), "source": n.get("source", ""),
                             "sentiment": s, "reason": r, "time": n.get("pub", "")[:16],
                             "link": n.get("link", "")})

        # Analyst calls today
        analyst_items = []
        for n in analyst_news[:3]:
            ts = n.get("providerPublishTime", 0)
            time_str = datetime.fromtimestamp(ts, tz=ET).strftime("%b %d %I:%M %p ET") if ts else ""
            s, r = score_headline(n.get("title", ""))
            analyst_items.append({"title": n.get("title", ""), "source": n.get("publisher", ""),
                                  "sentiment": s, "reason": r, "time": time_str,
                                  "link": n.get("link", "")})

        # Collapsed expander per stock
        analyst_badge = f" 📊{len(analyst_items)}" if analyst_items else ""
        news_badge = f" 📰{len(all_news)}" if all_news else ""
        label = f"{pct_color} {ticker}{pm_tag}  ${price:.2f}  ({sign}{pct:.2f}%){analyst_badge}{news_badge}"

        with st.expander(label, expanded=False):
            if analyst_items:
                st.markdown("**📊 Today's Analyst Calls:**")
                for a in analyst_items:
                    dot = "🟢" if a["sentiment"] == "bullish" else "🔴" if a["sentiment"] == "bearish" else "🟡"
                    short = a["title"][:95] + "..." if len(a["title"]) > 95 else a["title"]
                    short_md = short.replace("[", "(").replace("]", ")")  # escape for markdown
                    link = a.get("link", "").strip()
                    title_md = f"[{short_md}]({link})" if link else f"**{short_md}**"
                    st.markdown(
                        f"{dot} {title_md}  \n"
                        f"<small style='color:#718096;'>\\[{a['source']}\\] · {a['time']}</small>",
                        unsafe_allow_html=True,
                    )
                st.markdown("---")

            if all_news:
                st.markdown("**📰 Latest News:**")
                for n in all_news[:4]:
                    dot = "🟢" if n["sentiment"] == "bullish" else "🔴" if n["sentiment"] == "bearish" else "⚪"
                    short = n["title"][:95] + "..." if len(n["title"]) > 95 else n["title"]
                    short_md = short.replace("[", "(").replace("]", ")")
                    link = n.get("link", "").strip()
                    title_md = f"[{short_md}]({link})" if link else short_md
                    st.markdown(
                        f"{dot} {title_md}  \n"
                        f"<small style='color:#a0aec0;'>\\[{n['source']}\\] · {n['time']} · *{n['reason']}*</small>",
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No relevant news found today")

with macro_tab:
    st.markdown("**Latest headlines from CNBC & MarketWatch:**")
    
    # DEBUG: show link presence for first 3 headlines so we can verify
    if macro_headlines:
        debug_info = []
        for i, h in enumerate(macro_headlines[:3]):
            link = h.get("link", "")
            debug_info.append(f"#{i}: link={'✓ ' + link[:60] if link else '✗ EMPTY'}")
        st.caption("🔧 Debug: " + " | ".join(debug_info))
    
    for h in macro_headlines[:25]:
        s, r = score_headline(h["title"])
        dot = "🟢" if s == "bullish" else "🔴" if s == "bearish" else "⚪"
        matched = match_to_tickers(h["title"], CATALYST_TICKERS)
        tags_str = " ".join([f"`{t}`" for t in matched]) if matched else ""
        pub_time = h.get("pub", "")[:16]
        link = h.get("link", "").strip() if h.get("link") else ""
        title_text = h["title"].replace("[", "(").replace("]", ")")  # Escape brackets for markdown

        # Use native markdown link syntax — guaranteed clickable in Streamlit
        if link:
            title_md = f"[{title_text}]({link})"
        else:
            title_md = title_text

        st.markdown(
            f"{dot} **\\[{h['source']}\\]** {title_md}  {tags_str}  \n"
            f"<small style='color:#a0aec0;'>{pub_time}</small>",
            unsafe_allow_html=True,
        )
        st.divider()

# ============================================================

# --- Volatility Scanner (task 11.2) ---
# Reuses `active_data` and the already-fetched `macro_headlines` from the
# Stock Catalyst Scanner section above — no new HTTP. Placed after the
# catalyst scanner so `macro_headlines` is in scope.
render_volatility_scanner(active_data, macro_headlines)

# --- Sector Heatmap (clickable) ---
st.subheader("Sector Performance (click to explore)")
stocks_only = {k: v for k, v in active_data.items() if k not in INDICES and k not in MACRO_TICKERS}
sector_avg = {}
for info in stocks_only.values():
    for sec in info.get("sectors", []):
        sector_avg.setdefault(sec, []).append(info["pct_change"])
sector_summary = {s: round(sum(c)/len(c), 2) for s, c in sector_avg.items()}
sorted_sectors = sorted(sector_summary.items(), key=lambda x: x[1], reverse=True)

# Initialize session state for selected sector
if "selected_sector" not in st.session_state:
    st.session_state.selected_sector = "All"

# Display sectors in rows of 6
cols_per_row = 6
for row_start in range(0, len(sorted_sectors), cols_per_row):
    row_sectors = sorted_sectors[row_start:row_start + cols_per_row]
    sector_cols = st.columns(cols_per_row)
    for i, (sector, pct) in enumerate(row_sectors):
        with sector_cols[i]:
            color = "#276749" if pct >= 1 else "#38a169" if pct >= 0.25 else "#a0aec0" if pct > -0.25 else "#e53e3e" if pct > -1 else "#9b2c2c"
            sign = "+" if pct >= 0 else ""
            is_selected = st.session_state.selected_sector == sector
            border = "3px solid #1a365d" if is_selected else "none"
            st.markdown(
                f'<div style="background:{color};color:white;padding:10px;border-radius:8px;text-align:center;font-weight:600;font-size:0.8rem;border:{border};">'
                f'{sector}<br/>{sign}{pct:.2f}%</div>',
                unsafe_allow_html=True,
            )
            if st.button(f"View", key=f"btn_{sector}", use_container_width=True):
                st.session_state.selected_sector = sector
                st.rerun()

# Back to All button
st.markdown("")
col_back, col_label, _ = st.columns([1, 3, 3])
with col_back:
    if st.session_state.selected_sector != "All":
        if st.button("← Back to All"):
            st.session_state.selected_sector = "All"
            st.rerun()
with col_label:
    if st.session_state.selected_sector != "All":
        st.subheader(f"📂 {st.session_state.selected_sector} Sector")

st.markdown("---")

# --- Gainers & Losers based on selection ---
selected_sector = st.session_state.selected_sector

if selected_sector == "All":
    sorted_stocks = sorted(stocks_only.values(), key=lambda x: x["pct_change"], reverse=True)
    gainers = [s for s in sorted_stocks if s["pct_change"] > 0][:20]
    losers = sorted([s for s in sorted_stocks if s["pct_change"] < 0], key=lambda x: x["pct_change"])[:20]
else:
    sector_tickers = STOCKS[selected_sector]
    sector_data = sorted([active_data[t] for t in sector_tickers if t in active_data], key=lambda x: x["pct_change"], reverse=True)
    gainers = [s for s in sector_data if s["pct_change"] > 0]
    losers = sorted([s for s in sector_data if s["pct_change"] < 0], key=lambda x: x["pct_change"])

col1, col2 = st.columns(2)

with col1:
    st.subheader("🚀 Top Gainers")
    sort_gain = st.selectbox("Sort by", ["% Change", "$ Change", "Price", "Ticker"], key="sort_gainers")
    if sort_gain == "% Change":
        gainers = sorted(gainers, key=lambda x: x["pct_change"], reverse=True)
    elif sort_gain == "$ Change":
        gainers = sorted(gainers, key=lambda x: x["change"], reverse=True)
    elif sort_gain == "Price":
        gainers = sorted(gainers, key=lambda x: x["price"], reverse=True)
    elif sort_gain == "Ticker":
        gainers = sorted(gainers, key=lambda x: x["ticker"])

    if gainers:
        for s in gainers:
            pm = " 🏷️PM" if s.get("is_premarket") else ""
            st.markdown(
                f'<div class="stock-row">'
                f'<span><b>{s["ticker"]}</b> <small style="color:#718096">{s["sector"]}{pm}</small></span>'
                f'<span><b>${s["price"]:.2f}</b> &nbsp;'
                f'<span class="gain"><b>+${s["change"]:.2f} (+{s["pct_change"]:.2f}%)</b></span>'
                f'<br/><small style="color:#a0aec0">Prev: ${s["prev_close"]:.2f}</small></span>'
                f'</div>',
                unsafe_allow_html=True,
            )
    else:
        st.info("No gainers")

with col2:
    st.subheader("🔻 Top Losers")
    sort_loss = st.selectbox("Sort by", ["% Change", "$ Change", "Price", "Ticker"], key="sort_losers")
    if sort_loss == "% Change":
        losers = sorted(losers, key=lambda x: x["pct_change"])
    elif sort_loss == "$ Change":
        losers = sorted(losers, key=lambda x: x["change"])
    elif sort_loss == "Price":
        losers = sorted(losers, key=lambda x: x["price"], reverse=True)
    elif sort_loss == "Ticker":
        losers = sorted(losers, key=lambda x: x["ticker"])

    if losers:
        for s in losers:
            pm = " 🏷️PM" if s.get("is_premarket") else ""
            st.markdown(
                f'<div class="stock-row">'
                f'<span><b>{s["ticker"]}</b> <small style="color:#718096">{s["sector"]}{pm}</small></span>'
                f'<span><b>${s["price"]:.2f}</b> &nbsp;'
                f'<span class="loss"><b>${s["change"]:.2f} ({s["pct_change"]:.2f}%)</b></span>'
                f'<br/><small style="color:#a0aec0">Prev: ${s["prev_close"]:.2f}</small></span>'
                f'</div>',
                unsafe_allow_html=True,
            )
    else:
        st.info("No losers")

# --- Daily Market Summary (task 11.2) ---
# Bottom-of-page placement so the auto-trigger at 16:00 ET fires after the
# rest of the dashboard has rendered. Reuses the already-cached
# `active_data` and `macro_headlines` — no new HTTP from this call site.
render_daily_summary(active_data, macro_headlines)

# --- Auto Refresh ---
st.markdown("---")
st.caption("Data refreshes every 20 seconds (cache TTL). Click below to force refresh.")
if st.button("🔄 Refresh Now"):
    st.cache_data.clear()
    st.rerun()
