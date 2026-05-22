from flask import Flask, jsonify, send_from_directory, make_response
import requests as http_requests
from datetime import datetime
import pytz
import threading
import time
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__, static_folder='static')

# --- Stock Configuration ---

STOCKS = {
    "Core": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "LLY", "JPM", "AVGO"],
    "Tech": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD", "INTC", "CRM"],
    "Finance": ["JPM", "BAC", "GS", "MS", "V", "MA", "C", "WFC", "AXP", "BLK"],
    "Healthcare": ["JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "TMO", "ABT"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "Consumer": ["WMT", "KO", "PEP", "MCD", "NKE", "SBUX", "HD"],
    "Industrial": ["CAT", "BA", "HON", "UPS", "GE"],
    "Medical AI": ["ISRG", "VEEV", "DXCM", "RXRX", "SDGR", "GMED", "NNOX", "HIMS", "DOCS", "MDAI"],
}

INDICES = ["SPY", "QQQ", "DIA"]

TICKER_SECTORS = {}
for sector, tickers in STOCKS.items():
    for t in tickers:
        if t not in TICKER_SECTORS:
            TICKER_SECTORS[t] = []
        TICKER_SECTORS[t].append(sector)

ALL_TICKERS = list(set(INDICES + [t for tickers in STOCKS.values() for t in tickers]))

ET = pytz.timezone("US/Eastern")
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# --- Cache ---

cache = {"data": None, "phase": "closed", "last_updated": None, "ready": False}
cache_lock = threading.Lock()


def get_market_phase():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return "closed"
    t = now.hour * 60 + now.minute
    if t < 240:
        return "closed"
    if t < 560:
        return "premarket"      # 4:00 AM - 9:20 AM: show premarket data
    if t < 570:
        return "transition"     # 9:20 AM - 9:30 AM: freeze, wait for open
    if t < 960:
        return "market"         # 9:30 AM - 4:00 PM: live market
    if t < 1200:
        return "afterhours"     # 4:00 PM - 8:00 PM
    return "closed"


def get_refresh_interval(phase):
    return {"premarket": 25, "market": 20, "transition": 10, "afterhours": 60, "closed": 120}.get(phase, 60)



# --- Data Fetching ---

def fetch_chart_single(ticker):
    """
    Fetch a single ticker via chart endpoint.
    Returns premarket prices when includePrePost=true.
    Uses regularMarketPrice as prev close (yesterday's actual close).
    """
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=5m&range=1d&includePrePost=true"
    )
    try:
        resp = http_requests.get(url, headers=YAHOO_HEADERS, timeout=10)
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        meta = result["meta"]

        # regularMarketPrice = yesterday's closing price (the true prev close)
        prev_close = meta.get("regularMarketPrice", 0)

        # Get latest price from candle data (includes premarket candles)
        closes = result["indicators"]["quote"][0]["close"]
        valid_closes = [c for c in closes if c is not None]
        latest_price = valid_closes[-1] if valid_closes else None

        if not latest_price or not prev_close:
            return None

        change = latest_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close != 0 else 0

        # Volume from candles
        volumes = result["indicators"]["quote"][0].get("volume", [])
        total_vol = sum(v for v in volumes if v is not None)

        return {
            "ticker": ticker,
            "price": round(float(latest_price), 2),
            "prev_close": round(float(prev_close), 2),
            "change": round(float(change), 2),
            "pct_change": round(float(pct_change), 2),
            "volume": int(total_vol),
            "is_premarket": True,
        }
    except Exception as e:
        return None


def fetch_premarket_data(tickers):
    """
    Fetch premarket data for all tickers using parallel chart requests.
    ~4-5 seconds for 50 tickers with 15 threads.
    """
    data = {}
    with ThreadPoolExecutor(max_workers=15) as executor:
        results = list(executor.map(fetch_chart_single, tickers))

    for result in results:
        if result:
            sym = result["ticker"]
            primary_sector = "Index" if sym in INDICES else TICKER_SECTORS.get(sym, ["Unknown"])[0]
            result["sector"] = primary_sector
            result["sectors"] = TICKER_SECTORS.get(sym, ["Index"])
            data[sym] = result

    return data


def fetch_spark_batch(batch):
    """Fetch a batch of tickers from spark endpoint."""
    symbols = ",".join(batch)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/spark"
        f"?symbols={symbols}&range=2d&interval=1d"
    )
    resp = http_requests.get(url, headers=YAHOO_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_market_data(tickers):
    """
    Fetch regular market data using spark endpoint (fast, ~0.7s).
    Used during market hours, after hours, and closed.
    """
    batch_size = 20
    batches = [tickers[i:i+batch_size] for i in range(0, len(tickers), batch_size)]
    combined = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(fetch_spark_batch, batches))

    for result in results:
        combined.update(result)

    # Parse spark data
    data = {}
    for sym, info in combined.items():
        if sym not in ALL_TICKERS:
            continue
        closes = info.get("close", [])
        prev_close = info.get("chartPreviousClose")
        if not closes or prev_close is None:
            continue
        price = closes[-1]
        if price is None or prev_close == 0:
            continue

        change = price - prev_close
        pct_change = (change / prev_close) * 100

        primary_sector = "Index" if sym in INDICES else TICKER_SECTORS.get(sym, ["Unknown"])[0]

        data[sym] = {
            "ticker": sym,
            "price": round(float(price), 2),
            "prev_close": round(float(prev_close), 2),
            "change": round(float(change), 2),
            "pct_change": round(float(pct_change), 2),
            "volume": 0,
            "sector": primary_sector,
            "sectors": TICKER_SECTORS.get(sym, ["Index"]),
            "is_premarket": False,
        }

    return data


# --- Background Worker ---

def fetch_quotes_background():
    """
    Hybrid approach:
    1. Always fetch spark first (fast, ~0.7s) to get data showing immediately
    2. During premarket, follow up with chart endpoint for accurate premarket prices
    """
    phase = get_market_phase()
    try:
        # Step 1: Fast spark fetch — gets data into cache ASAP
        spark_data = fetch_market_data(ALL_TICKERS)
        if spark_data:
            with cache_lock:
                # Only overwrite if we don't already have better premarket data
                if not cache["ready"]:
                    cache["data"] = spark_data
                    cache["phase"] = phase
                    cache["last_updated"] = datetime.now(ET).strftime("%I:%M:%S %p ET")
                    cache["ready"] = True
                    print(f"[Cache] Quick load: {len(spark_data)} tickers | {cache['last_updated']}")
                elif phase != "premarket":
                    cache["data"] = spark_data
                    cache["phase"] = phase
                    cache["last_updated"] = datetime.now(ET).strftime("%I:%M:%S %p ET")

        # Step 2: During premarket, fetch accurate premarket prices
        if phase == "premarket":
            premarket_data = fetch_premarket_data(ALL_TICKERS)
            if premarket_data:
                with cache_lock:
                    cache["data"] = premarket_data
                    cache["phase"] = phase
                    cache["last_updated"] = datetime.now(ET).strftime("%I:%M:%S %p ET")
                    cache["ready"] = True
                print(f"[Cache] Premarket update: {len(premarket_data)} tickers | {cache['last_updated']}")
        elif phase == "transition":
            # Keep last data, don't fetch
            pass
        else:
            # Market/afterhours/closed — spark data is already accurate
            if spark_data:
                with cache_lock:
                    cache["data"] = spark_data
                    cache["phase"] = phase
                    cache["last_updated"] = datetime.now(ET).strftime("%I:%M:%S %p ET")
                    cache["ready"] = True
                print(f"[Cache] {len(spark_data)} tickers | Phase: {phase} | {cache['last_updated']}")

    except Exception as e:
        print(f"[Cache] Error: {e}")


def background_worker():
    fetch_quotes_background()
    while True:
        phase = get_market_phase()
        time.sleep(get_refresh_interval(phase))
        try:
            fetch_quotes_background()
        except Exception as e:
            print(f"[Worker] {e}")


def keep_alive():
    """Self-ping to prevent Render from sleeping. Pings every 10 minutes."""
    import os
    app_url = os.environ.get("RENDER_EXTERNAL_URL", "http://127.0.0.1:5000")
    while True:
        time.sleep(600)  # 10 minutes
        try:
            http_requests.get(f"{app_url}/health", timeout=5)
            print("[KeepAlive] Ping sent")
        except Exception:
            pass


worker_started = False

def start_workers():
    global worker_started
    if worker_started:
        return
    worker_started = True
    worker_thread = threading.Thread(target=background_worker, daemon=True)
    worker_thread.start()
    keepalive_thread = threading.Thread(target=keep_alive, daemon=True)
    keepalive_thread.start()
    print("[Init] Background workers started")


# Start workers when module loads (works with both gunicorn and direct run)
start_workers()


# --- Insights ---

def generate_insights(data):
    insights = []
    spy = data.get("SPY")
    qqq = data.get("QQQ")
    phase = cache.get("phase", "")

    # Add premarket label
    if phase == "premarket" and spy:
        insights.append(f"🌅 Showing pre-market data (updates until 9:20 AM ET)")

    sector_changes = {}
    sector_stocks = {}
    for ticker, info in data.items():
        sector = info.get("sector")
        if sector and sector not in ("Index", "Core"):
            sector_changes.setdefault(sector, []).append(info["pct_change"])
            sector_stocks.setdefault(sector, []).append(info)

    sector_avg = {s: round(sum(c) / len(c), 2) for s, c in sector_changes.items() if c}
    sorted_sectors = sorted(sector_avg.items(), key=lambda x: x[1], reverse=True)
    best = sorted_sectors[0] if sorted_sectors else None
    worst = sorted_sectors[-1] if sorted_sectors else None

    if spy and best and worst:
        if spy["pct_change"] > 0 and best[1] > 0:
            others = [v for k, v in sector_avg.items() if k != best[0]]
            avg_o = sum(others) / len(others) if others else 0
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


# --- Routes ---

@app.route("/health")
def health():
    """Health check endpoint for keep-alive pings."""
    with cache_lock:
        ready = cache["ready"]
        phase = cache["phase"]
    return jsonify({"status": "ok", "ready": ready, "phase": phase})


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(
        "static", "manifest.json", mimetype="application/manifest+json"
    )


@app.route("/service-worker.js")
def service_worker():
    resp = make_response(
        send_from_directory(
            "static", "service-worker.js", mimetype="application/javascript"
        )
    )
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# Note: /static/<path:filename> is already provided by Flask via
# `Flask(__name__, static_folder='static')` (endpoint name: `static`).
# It is registered before the catch-all `/<path:filename>` below, so
# requests to /static/* are served from the static folder directly.


@app.route("/api/data")
def get_data():
    with cache_lock:
        if not cache["ready"]:
            return jsonify({"ready": False}), 202
        data = cache["data"]
        phase = cache["phase"]
        last_updated = cache["last_updated"]

    insights = generate_insights(data)
    indices = {k: v for k, v in data.items() if k in INDICES}
    stocks = {k: v for k, v in data.items() if k not in INDICES}

    sorted_stocks = sorted(stocks.values(), key=lambda x: x["pct_change"], reverse=True)
    gainers = [s for s in sorted_stocks if s["pct_change"] > 0]
    losers = sorted([s for s in sorted_stocks if s["pct_change"] < 0], key=lambda x: x["pct_change"])

    sector_avg = {}
    for info in stocks.values():
        for sec in info.get("sectors", []):
            sector_avg.setdefault(sec, []).append(info["pct_change"])
    sector_summary = {s: round(sum(c) / len(c), 2) for s, c in sector_avg.items()}

    return jsonify({
        "ready": True,
        "indices": indices,
        "gainers": gainers[:15],
        "losers": losers[:15],
        "insights": insights,
        "sector_summary": sector_summary,
        "phase": phase,
        "last_updated": last_updated,
    })


@app.route("/api/sector/<sector_name>")
def get_sector(sector_name):
    with cache_lock:
        if not cache["ready"]:
            return jsonify({"error": "Loading"}), 202
        data = cache["data"]
        phase = cache["phase"]

    matched = next((s for s in STOCKS if s.lower() == sector_name.lower()), None)
    if not matched:
        return jsonify({"error": "Not found"}), 404

    sector_data = sorted([data[t] for t in STOCKS[matched] if t in data], key=lambda x: x["pct_change"], reverse=True)
    gainers = [s for s in sector_data if s["pct_change"] > 0]
    losers = sorted([s for s in sector_data if s["pct_change"] < 0], key=lambda x: x["pct_change"])

    return jsonify({"sector": matched, "gainers": gainers, "losers": losers, "all": sector_data, "phase": phase})


@app.route("/<path:filename>")
def static_files(filename):
    """Catch-all for root-level static files (e.g., /styles.css, /app.js).

    Registered last so it does not shadow /manifest.json, /service-worker.js,
    or /static/<path:filename>.
    """
    return send_from_directory("static", filename)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
