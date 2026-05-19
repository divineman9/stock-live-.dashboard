import streamlit as st
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import pytz

# --- Page Config ---
st.set_page_config(
    page_title="Live Stock Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

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
        if vix["pct_change"] > 10:
            insights.append(f"🚨 VIX spiking +{vix['pct_change']:.1f}% — fear rising sharply, expect volatility")
        elif vix["pct_change"] > 5:
            insights.append(f"⚠️ VIX up +{vix['pct_change']:.1f}% — market anxiety increasing")
        elif vix["pct_change"] < -5:
            insights.append(f"😌 VIX down {vix['pct_change']:.1f}% — fear fading, risk-on sentiment")
        # VIX level context
        if vix["price"] > 30:
            insights.append(f"🔴 VIX at {vix['price']:.1f} — extreme fear territory")
        elif vix["price"] > 20:
            insights.append(f"🟡 VIX at {vix['price']:.1f} — elevated caution")

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

# Phase badge
phase_labels = {
    "premarket": "🌅 Pre-Market",
    "market": "🟢 Market Open",
    "transition": "⏳ Waiting for Open",
    "afterhours": "🌙 After Hours",
    "closed": "🔴 Market Closed",
}
st.caption(f"{phase_labels.get(phase, phase)} | Last updated: {datetime.now(ET).strftime('%I:%M:%S %p ET')}")

# --- Bulls vs Bears ---
stocks_all = {k: v for k, v in data.items() if k not in INDICES and k not in MACRO_TICKERS}
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

# --- Index Bar ---
st.subheader("Market Indices")
idx_cols = st.columns(3)
for i, sym in enumerate(INDICES):
    info = data.get(sym)
    if info:
        with idx_cols[i]:
            delta_color = "normal"
            st.metric(
                label=sym,
                value=f"${info['price']:.2f}",
                delta=f"{info['change']:+.2f} ({info['pct_change']:+.2f}%)",
            )

# --- Macro Indicators: VIX, 10Y Yield, XLF ---
st.subheader("Macro Indicators")
macro_cols = st.columns(3)

vix_data = data.get("^VIX")
tnx_data = data.get("^TNX")
xlf_data = data.get("XLF")

with macro_cols[0]:
    if vix_data:
        vix_color = "🔴" if vix_data["price"] > 25 else "🟡" if vix_data["price"] > 18 else "🟢"
        st.metric(
            label=f"{vix_color} VIX (Fear Index)",
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

# --- Insights ---
st.subheader("🧠 Market Insights")
insights = generate_insights(data)
for insight in insights:
    st.markdown(f'<div class="insight-box">{insight}</div>', unsafe_allow_html=True)

# --- Sector Heatmap (clickable) ---
st.subheader("Sector Performance (click to explore)")
stocks_only = {k: v for k, v in data.items() if k not in INDICES and k not in MACRO_TICKERS}
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
    sector_data = sorted([data[t] for t in sector_tickers if t in data], key=lambda x: x["pct_change"], reverse=True)
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

# --- Auto Refresh ---
st.markdown("---")
st.caption("Data refreshes every 20 seconds (cache TTL). Click below to force refresh.")
if st.button("🔄 Refresh Now"):
    st.cache_data.clear()
    st.rerun()
