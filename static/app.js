// Adaptive refresh — polls faster when data isn't ready yet
const POLL_FAST = 2000;   // 2s while waiting for first load
const POLL_NORMAL = 30000; // 30s once data is live

let refreshTimer = null;
let dataReady = false;
let cachedData = null;
let currentView = 'overview';

// DOM refs
const phaseBadge = document.getElementById('phase-badge');
const lastUpdate = document.getElementById('last-update');
const insightsContent = document.getElementById('insights-content');
const heatmapGrid = document.getElementById('heatmap-grid');
const gainersList = document.getElementById('gainers-list');
const losersList = document.getElementById('losers-list');
const overviewView = document.getElementById('overview-view');
const sectorView = document.getElementById('sector-view');
const sectorTitle = document.getElementById('sector-title');
const sectorGainers = document.getElementById('sector-gainers');
const sectorLosers = document.getElementById('sector-losers');
const sectorAll = document.getElementById('sector-all');
const backBtn = document.getElementById('back-btn');

backBtn.addEventListener('click', showOverview);

// --- Fetching ---

async function fetchMainData() {
  try {
    const res = await fetch('/api/data');
    const json = await res.json();

    if (!json.ready) {
      // Data still loading on server — poll fast until ready
      scheduleRefresh(POLL_FAST);
      return;
    }

    dataReady = true;
    cachedData = json;
    renderMainDashboard(json);
    scheduleRefresh(POLL_NORMAL);
  } catch (err) {
    console.error('Fetch error:', err);
    scheduleRefresh(POLL_FAST);
  }
}

async function fetchSectorData(sectorName) {
  try {
    const res = await fetch(`/api/sector/${encodeURIComponent(sectorName)}`);
    if (!res.ok) return;
    const data = await res.json();
    renderSectorView(data);
  } catch (err) {
    console.error('Sector fetch error:', err);
  }
}

function scheduleRefresh(interval) {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(fetchMainData, interval);
}

// --- Rendering ---

function renderMainDashboard(data) {
  renderPhase(data.phase);
  renderIndices(data.indices);
  renderInsights(data.insights);
  renderHeatmap(data.sector_summary);

  if (currentView === 'overview') {
    renderStockList(gainersList, data.gainers);
    renderStockList(losersList, data.losers);
  }

  lastUpdate.textContent = new Date().toLocaleTimeString();
}

function renderPhase(phase) {
  const labels = {
    premarket: '🌅 Pre-Market',
    market: '🟢 Market Open',
    transition: '⏳ Waiting for Open',
    afterhours: '🌙 After Hours',
    closed: '🔴 Market Closed',
  };
  phaseBadge.textContent = labels[phase] || phase;
  phaseBadge.className = `phase-badge ${phase}`;
}

function renderIndices(indices) {
  ['SPY', 'QQQ', 'DIA'].forEach((sym) => {
    const card = document.getElementById(`idx-${sym}`);
    const info = indices[sym];
    if (!info) return;

    const priceEl = card.querySelector('.idx-price');
    const changeEl = card.querySelector('.idx-change');

    priceEl.textContent = `$${info.price.toFixed(2)}`;
    const sign = info.pct_change >= 0 ? '+' : '';
    changeEl.textContent = `${sign}${info.pct_change.toFixed(2)}%`;

    const cls = info.pct_change >= 0 ? 'positive' : 'negative';
    card.className = `index-card ${cls}`;
    changeEl.className = `idx-change ${cls}`;
  });
}

function renderInsights(insights) {
  if (!insights || insights.length === 0) {
    insightsContent.innerHTML = '<p class="muted">No insights available</p>';
    return;
  }
  insightsContent.innerHTML = insights
    .map((t) => `<div class="insight-item">${t}</div>`)
    .join('');
}

function renderHeatmap(sectorSummary) {
  if (!sectorSummary) return;
  const sorted = Object.entries(sectorSummary).sort((a, b) => b[1] - a[1]);

  heatmapGrid.innerHTML = sorted
    .map(([sector, pct]) => {
      const color = getHeatmapColor(pct);
      const sign = pct >= 0 ? '+' : '';
      return `<div class="heatmap-cell ${color}" onclick="openSector('${sector}')">${sector}<br/>${sign}${pct.toFixed(2)}%</div>`;
    })
    .join('');
}

function getHeatmapColor(pct) {
  if (pct >= 2) return 'strong-green';
  if (pct >= 1) return 'green';
  if (pct >= 0.25) return 'light-green';
  if (pct > -0.25) return 'neutral';
  if (pct > -1) return 'light-red';
  if (pct > -2) return 'red';
  return 'strong-red';
}

function renderStockList(container, stocks) {
  if (!stocks || stocks.length === 0) {
    container.innerHTML = '<p class="muted">No data</p>';
    return;
  }
  container.innerHTML = stocks.map(stockRowHTML).join('');
}

function stockRowHTML(s) {
  const sign = s.pct_change >= 0 ? '+' : '';
  const cls = s.pct_change >= 0 ? 'positive' : 'negative';
  const vol = s.volume ? formatVolume(s.volume) : '';
  const preTag = s.is_premarket ? '<span class="premarket-tag">PM</span>' : '';
  const dollarChange = `${sign}$${Math.abs(s.change).toFixed(2)}`;
  const prevClose = s.prev_close ? `<span class="prev-close">Prev: $${s.prev_close.toFixed(2)}</span>` : '';

  return `
    <div class="stock-row">
      <div class="stock-left">
        <span class="stock-ticker">${s.ticker}</span>
        <span class="stock-sector">${s.sector}</span>
        ${preTag}
      </div>
      <div class="stock-right">
        ${prevClose}
        ${vol ? `<span class="stock-volume">${vol}</span>` : ''}
        <span class="stock-price">$${s.price.toFixed(2)}</span>
        <span class="stock-change ${cls}">${dollarChange} (${sign}${s.pct_change.toFixed(2)}%)</span>
      </div>
    </div>`;
}

function renderSectorView(data) {
  sectorTitle.textContent = `${data.sector} Sector`;
  renderStockList(sectorGainers, data.gainers);
  renderStockList(sectorLosers, data.losers);
  renderStockList(sectorAll, data.all);
}

// --- Navigation ---

function openSector(sectorName) {
  currentView = sectorName;
  overviewView.classList.add('hidden');
  sectorView.classList.remove('hidden');
  sectorTitle.textContent = `${sectorName}...`;
  sectorGainers.innerHTML = '<p class="muted loading-pulse">Loading...</p>';
  sectorLosers.innerHTML = '';
  sectorAll.innerHTML = '';
  fetchSectorData(sectorName);
}

function showOverview() {
  currentView = 'overview';
  sectorView.classList.add('hidden');
  overviewView.classList.remove('hidden');
  if (cachedData) {
    renderStockList(gainersList, cachedData.gainers);
    renderStockList(losersList, cachedData.losers);
  }
}

window.openSector = openSector;

// --- Utilities ---

function formatVolume(vol) {
  if (vol >= 1e9) return (vol / 1e9).toFixed(1) + 'B';
  if (vol >= 1e6) return (vol / 1e6).toFixed(1) + 'M';
  if (vol >= 1e3) return (vol / 1e3).toFixed(1) + 'K';
  return vol.toString();
}

// --- Init ---
fetchMainData();
