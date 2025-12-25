"""
electric.py

Creates a static “epic” web dashboard (site/index.html) using the EIA Open Data API v2.

Pulls:
1) Retail electricity (AZ, Residential): sales, revenue, price (monthly; 2021-01 .. 2022-01)
2) Generation (AZ): from electricity/electric-power-operational-data (monthly; 2021-01 .. 2022-01)
   - Uses valid facets for that dataset: location, sectorid, fueltypeid
   - Discovers valid facet values via /facet/<facet_id>/ endpoint

Requirements:
  pip install requests

Usage:
  1) Put your key in key.txt as either:
       OPENEI_API_KEY=YOUR_KEY_HERE
     or just:
       YOUR_KEY_HERE
  2) python electric.py
  3) cd site && python -m http.server 8000
  4) Open http://localhost:8000
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

EIA_BASE = "https://api.eia.gov/v2"


@dataclass
class EIAResult:
    route: str
    rows: List[Dict[str, Any]]
    meta: Dict[str, Any]


# ----------------------------
# Key + HTTP helpers
# ----------------------------
def read_api_key(key_path: str = "key.txt") -> str:
    p = Path(key_path)
    if not p.exists():
        raise FileNotFoundError(
            f"Missing {key_path}. Create it with OPENEI_API_KEY=YOUR_KEY or just the key."
        )

    raw = p.read_text(encoding="utf-8").strip()

    # Allow either "OPENEI_API_KEY=..." or the key alone
    m = re.search(r"OPENEI_API_KEY\s*=\s*([A-Za-z0-9_\-]+)", raw)
    if m:
        return m.group(1).strip()

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        raise ValueError(f"{key_path} is empty.")

    if "=" in lines[0]:
        return lines[0].split("=", 1)[1].strip()

    return lines[0].strip()


def http_get_json(url: str, params: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    r = requests.get(url, params=params, timeout=timeout)
    try:
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"HTTP {r.status_code} for {r.url}\n{r.text[:4000]}") from e
    return r.json()


def eia_metadata(route: str, api_key: str) -> Dict[str, Any]:
    # Metadata is returned when you omit /data at the end of the route in API v2.
    url = f"{EIA_BASE}/{route}/"
    return http_get_json(url, params={"api_key": api_key})


def eia_data(route: str, api_key: str, params: Dict[str, Any]) -> EIAResult:
    url = f"{EIA_BASE}/{route}/data/"
    merged = {"api_key": api_key, **params}
    payload = http_get_json(url, params=merged)
    resp = payload.get("response", {})
    rows = resp.get("data", []) or []
    return EIAResult(route=route, rows=rows, meta=resp)


def eia_facet(route: str, facet_id: str, api_key: str, length: int = 5000, offset: int = 0) -> Dict[str, Any]:
    """
    Returns allowed values for a facet using:
      /v2/<route>/facet/<facet_id>/?api_key=...
    """
    url = f"{EIA_BASE}/{route}/facet/{facet_id}/"
    return http_get_json(url, params={"api_key": api_key, "length": length, "offset": offset})


# ----------------------------
# Metadata-driven selection
# ----------------------------
def normalize_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def pick_generation_data_column(route: str, api_key: str) -> str:
    """
    Pick a 'data' field key that most likely represents generation.
    """
    meta = eia_metadata(route, api_key)
    data_obj = meta.get("response", {}).get("data", {}) or {}
    if not data_obj:
        return "value"

    keys = list(data_obj.keys())
    preferred = [
        "net-generation",
        "net_generation",
        "netgeneration",
        "generation",
        "gen",
        "value",
    ]
    norm_map = {k: normalize_key(k) for k in keys}
    pref_norm = [normalize_key(p) for p in preferred]

    # Exact normalized matches
    for pn in pref_norm:
        for k, nk in norm_map.items():
            if nk == pn:
                return k

    # Substring matches
    for pn in pref_norm:
        for k, nk in norm_map.items():
            if pn and pn in nk:
                return k

    # Any key containing "gen"
    for k, nk in norm_map.items():
        if "gen" in nk:
            return k

    return keys[0]


def choose_facet_id(
    facet_values: List[Dict[str, Any]],
    prefer_ids: List[str],
    prefer_name_contains: List[str],
) -> Optional[str]:
    """
    Choose a facet value id.
      1) exact id match
      2) substring match against id/name/alias
      3) None (caller decides how to proceed)
    """
    if not facet_values:
        return None

    prefer_ids_u = [p.upper() for p in prefer_ids]
    for f in facet_values:
        fid = str(f.get("id") or "").upper()
        if fid in prefer_ids_u:
            return f.get("id")

    needles = [s.lower() for s in prefer_name_contains]
    for f in facet_values:
        blob = " ".join(
            [str(f.get("id") or ""), str(f.get("name") or ""), str(f.get("alias") or "")]
        ).lower()
        if any(n in blob for n in needles):
            return f.get("id")

    return None


def infer_units(route: str, api_key: str, fields: List[str]) -> Dict[str, str]:
    meta = eia_metadata(route, api_key).get("response", {}) or {}
    out: Dict[str, str] = {}
    for f in fields:
        out[f] = (meta.get("data", {}).get(f, {}) or {}).get("units", "")
    return out


# ----------------------------
# Dashboard builder
# ----------------------------
def build_dashboard(site_dir: Path, combined: Dict[str, Any]) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "data.json").write_text(json.dumps(combined, indent=2), encoding="utf-8")

    # IMPORTANT: This is a normal triple-quoted string (NOT an f-string),
    # so JS template literals like ${...} are safe.
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Arizona Residential Electricity — 2021-01 to 2022-01</title>

  <link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>

  <style>
    :root {
      --bg0: #05070e;
      --bg1: #071a1b;
      --card: rgba(255,255,255,0.06);
      --card2: rgba(255,255,255,0.10);
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.68);
      --line: rgba(255,255,255,0.10);
      --glow: 0 0 22px rgba(120,255,220,0.15), 0 0 60px rgba(120,180,255,0.10);
      --radius: 18px;
    }

    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Apple Color Emoji","Segoe UI Emoji";
      color: var(--text);
      overflow-x: hidden;
      background: radial-gradient(1200px 800px at 20% 15%, rgba(120,255,220,0.12), transparent 60%),
                  radial-gradient(900px 700px at 85% 20%, rgba(120,180,255,0.14), transparent 55%),
                  radial-gradient(1200px 900px at 60% 95%, rgba(255,120,220,0.08), transparent 60%),
                  linear-gradient(120deg, var(--bg0), var(--bg1));
    }

    .aurora {
      position: fixed;
      inset: -40vh -40vw;
      background:
        radial-gradient(circle at 15% 30%, rgba(0,255,190,0.18), transparent 45%),
        radial-gradient(circle at 70% 25%, rgba(90,160,255,0.18), transparent 50%),
        radial-gradient(circle at 55% 75%, rgba(255,90,210,0.12), transparent 55%);
      filter: blur(20px) saturate(120%);
      animation: drift 18s ease-in-out infinite alternate;
      pointer-events: none;
      z-index: 0;
      opacity: 0.9;
    }
    @keyframes drift {
      0% { transform: translate3d(-2%, -2%, 0) scale(1.02) rotate(-2deg); }
      100% { transform: translate3d(2%, 2%, 0) scale(1.06) rotate(2deg); }
    }

    #stars {
      position: fixed;
      inset: 0;
      z-index: 1;
      pointer-events: none;
      mix-blend-mode: screen;
      opacity: 0.65;
    }

    .wrap {
      position: relative;
      z-index: 2;
      max-width: 1200px;
      margin: 0 auto;
      padding: 28px 18px 64px;
    }

    header {
      padding: 20px 18px 16px;
      border: 1px solid var(--line);
      background: linear-gradient(180deg, var(--card2), rgba(255,255,255,0.04));
      border-radius: var(--radius);
      box-shadow: var(--glow);
      overflow: hidden;
      position: relative;
    }
    header:before {
      content: "";
      position: absolute;
      inset: -2px;
      background: linear-gradient(120deg, rgba(0,255,190,0.20), rgba(90,160,255,0.16), rgba(255,90,210,0.14));
      opacity: 0.18;
      filter: blur(18px);
    }
    header > * { position: relative; }

    .kicker {
      letter-spacing: 0.18em;
      text-transform: uppercase;
      font-size: 12px;
      color: var(--muted);
      margin: 0 0 10px;
    }
    .title {
      margin: 0;
      font-size: clamp(26px, 3.2vw, 42px);
      line-height: 1.08;
    }
    .subtitle {
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 14px;
      max-width: 76ch;
      line-height: 1.4;
    }

    .grid {
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 14px;
      margin-top: 14px;
      align-items: start;
    }
    @media (max-width: 980px) {
      .grid { grid-template-columns: 1fr; }
    }

    .panel {
      border: 1px solid var(--line);
      background: linear-gradient(180deg, var(--card), rgba(255,255,255,0.03));
      border-radius: var(--radius);
      box-shadow: var(--glow);
      padding: 16px;
    }

    .kpis {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .kpi {
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.04);
      border-radius: 14px;
      padding: 12px 12px 10px;
    }
    .kpi .label {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .kpi .value {
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0.01em;
    }
    .kpi .note {
      font-size: 12px;
      color: var(--muted);
      margin-top: 6px;
      line-height: 1.25;
    }

    .controls {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin: 10px 0 0;
    }
    .btn {
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.05);
      color: var(--text);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 12px;
      cursor: pointer;
      transition: transform .12s ease, background .12s ease, border-color .12s ease;
      user-select: none;
    }
    .btn:hover {
      transform: translateY(-1px);
      background: rgba(255,255,255,0.07);
      border-color: rgba(255,255,255,0.18);
    }
    .btn.active {
      background: linear-gradient(90deg, rgba(0,255,190,0.18), rgba(90,160,255,0.15));
      border-color: rgba(0,255,190,0.25);
    }

    .chart-wrap { height: 320px; }

    h2 {
      margin: 0 0 10px;
      font-size: 16px;
      letter-spacing: 0.02em;
    }
    .muted {
      color: var(--muted);
      font-size: 12px;
      margin: 0 0 12px;
      line-height: 1.35;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 14px;
      border: 1px solid var(--line);
    }
    th, td {
      padding: 10px 10px;
      border-bottom: 1px solid var(--line);
      font-size: 12px;
      text-align: left;
      white-space: nowrap;
    }
    th {
      color: rgba(255,255,255,0.78);
      font-weight: 650;
      background: rgba(255,255,255,0.05);
    }
    tr:hover td { background: rgba(255,255,255,0.03); }

    .foot {
      margin-top: 14px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }
    .pill {
      display: inline-block;
      padding: 3px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.04);
      margin-right: 6px;
    }
  </style>
</head>
<body>
  <div class="aurora"></div>
  <canvas id="stars"></canvas>

  <div class="wrap">
    <header>
      <p class="kicker">EIA OPEN DATA · ARIZONA · RESIDENTIAL</p>
      <h1 class="title">Electricity Dashboard: AZ Residential (2021-01 → 2022-01)</h1>
      <p class="subtitle">
        Retail sales (usage), revenue, and average price come from <code>electricity/retail-sales</code>
        (state AZ, sector RES). Generation comes from <code>electricity/electric-power-operational-data</code>
        (filtered by location + sector + fuel facets).
      </p>
    </header>

    <div class="grid">
      <section class="panel">
        <h2>Trends</h2>
        <p class="muted">Use the toggles to swap the primary series. Hover points to inspect values.</p>

        <div class="controls">
          <button class="btn active" data-series="sales">Residential Usage (Sales)</button>
          <button class="btn" data-series="revenue">Residential Revenue</button>
          <button class="btn" data-series="price">Average Price</button>
          <button class="btn" data-series="generation">Generation (AZ)</button>
        </div>

        <div class="chart-wrap">
          <canvas id="mainChart"></canvas>
        </div>

        <div class="foot" id="unitsNote"></div>
      </section>

      <aside class="panel">
        <h2>KPIs</h2>
        <p class="muted">Computed across the selected range (2021-01 through 2022-01).</p>

        <div class="kpis">
          <div class="kpi">
            <div class="label">Total Residential Sales</div>
            <div class="value" id="kpiSales">—</div>
            <div class="note" id="kpiSalesUnit">—</div>
          </div>
          <div class="kpi">
            <div class="label">Total Residential Revenue</div>
            <div class="value" id="kpiRevenue">—</div>
            <div class="note" id="kpiRevenueUnit">—</div>
          </div>
          <div class="kpi">
            <div class="label">Average Residential Price</div>
            <div class="value" id="kpiPrice">—</div>
            <div class="note" id="kpiPriceUnit">—</div>
          </div>
          <div class="kpi">
            <div class="label">Total Generation (AZ)</div>
            <div class="value" id="kpiGen">—</div>
            <div class="note" id="kpiGenUnit">—</div>
          </div>
        </div>

        <div class="foot">
          <span class="pill" id="metaRange">—</span>
          <span class="pill">EIA API v2</span>
          <span class="pill" id="metaBuiltAt">—</span>
        </div>
      </aside>
    </div>

    <section class="panel" style="margin-top:14px;">
      <h2>Raw Monthly Rows</h2>
      <p class="muted">Merged retail-sales (RES) and generation (AZ). Values are shown as returned by the API.</p>
      <div style="overflow:auto;">
        <table id="dataTable">
          <thead>
            <tr>
              <th>Period</th>
              <th>Sales</th>
              <th>Revenue</th>
              <th>Price</th>
              <th>Generation</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>

      <div class="foot">
        Generated locally by <code>electric.py</code>.
      </div>
    </section>

  </div>

<script>
(async function() {
  const resp = await fetch('./data.json', {cache:'no-store'});
  const data = await resp.json();

  const retail = data.retail_sales;
  const gen = data.generation;

  // Merge by period
  const byPeriod = new Map();
  for (const r of retail.rows) {
    byPeriod.set(r.period, {
      period: r.period,
      sales: (r.sales ?? null),
      revenue: (r.revenue ?? null),
      price: (r.price ?? null),
      generation: null
    });
  }
  for (const g of gen.rows) {
    const p = g.period;
    const cur = byPeriod.get(p) || {period:p, sales:null, revenue:null, price:null, generation:null};
    cur.generation = (g.generation ?? null);
    byPeriod.set(p, cur);
  }

  const periods = Array.from(byPeriod.keys()).sort();
  const merged = periods.map(p => byPeriod.get(p));

  // Units (best-effort, from metadata)
  const units = {
    sales: retail.units.sales || '',
    revenue: retail.units.revenue || '',
    price: retail.units.price || '',
    generation: gen.units.generation || ''
  };

  document.getElementById('metaRange').textContent = `${periods[0]} → ${periods[periods.length-1]}`;
  document.getElementById('metaBuiltAt').textContent = `Built: ${data.built_at || '—'}`;

  // KPIs
  function sumKey(key) {
    let s = 0;
    let n = 0;
    for (const r of merged) {
      const v = r[key];
      if (v !== null && v !== undefined && !isNaN(+v)) {
        s += +v;
        n++;
      }
    }
    return {sum:s, n:n};
  }
  function avgKey(key) {
    const {sum,n} = sumKey(key);
    return n ? sum / n : NaN;
  }
  function fmt(x) {
    if (x === null || x === undefined || isNaN(x)) return '—';
    const ax = Math.abs(x);
    if (ax >= 1e9) return (x/1e9).toFixed(2) + 'B';
    if (ax >= 1e6) return (x/1e6).toFixed(2) + 'M';
    if (ax >= 1e3) return (x/1e3).toFixed(2) + 'K';
    return (+x).toFixed(2);
  }

  const salesSum = sumKey('sales').sum;
  const revSum = sumKey('revenue').sum;
  const priceAvg = avgKey('price');
  const genSum = sumKey('generation').sum;

  document.getElementById('kpiSales').textContent = fmt(salesSum);
  document.getElementById('kpiRevenue').textContent = fmt(revSum);
  document.getElementById('kpiPrice').textContent = isNaN(priceAvg) ? '—' : (+priceAvg).toFixed(3);
  document.getElementById('kpiGen').textContent = fmt(genSum);

  document.getElementById('kpiSalesUnit').textContent = units.sales || 'Units: (see API metadata)';
  document.getElementById('kpiRevenueUnit').textContent = units.revenue || 'Units: (see API metadata)';
  document.getElementById('kpiPriceUnit').textContent = units.price || 'Units: (see API metadata)';
  document.getElementById('kpiGenUnit').textContent = units.generation || 'Units: (see API metadata)';

  // Table
  const tbody = document.querySelector('#dataTable tbody');
  for (const r of merged) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${r.period}</td>
      <td>${r.sales ?? '—'}</td>
      <td>${r.revenue ?? '—'}</td>
      <td>${r.price ?? '—'}</td>
      <td>${r.generation ?? '—'}</td>
    `;
    tbody.appendChild(tr);
  }

  // Chart
  const ctx = document.getElementById('mainChart');
  const datasetMap = {
    sales: { label: 'Residential Sales (Usage)', get: r => r.sales, unit: units.sales },
    revenue: { label: 'Residential Revenue', get: r => r.revenue, unit: units.revenue },
    price: { label: 'Average Price', get: r => r.price, unit: units.price },
    generation: { label: 'Generation (AZ)', get: r => r.generation, unit: units.generation }
  };

  const baseConfig = {
    type: 'line',
    data: {
      labels: periods,
      datasets: [{
        label: datasetMap.sales.label,
        data: merged.map(datasetMap.sales.get),
        tension: 0.25,
        pointRadius: 2,
        borderWidth: 2,
        fill: true
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: true, labels: { color: 'rgba(255,255,255,0.85)' } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.y;
              const u = document.getElementById('unitsNote').dataset.unit || '';
              const vv = (v === null || v === undefined) ? '—' : v;
              return `${ctx.dataset.label}: ${vv}${u ? ' ('+u+')' : ''}`;
            }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: 'rgba(255,255,255,0.70)' },
          grid: { color: 'rgba(255,255,255,0.07)' }
        },
        y: {
          ticks: { color: 'rgba(255,255,255,0.70)' },
          grid: { color: 'rgba(255,255,255,0.07)' }
        }
      }
    }
  };

  const chart = new Chart(ctx, baseConfig);

  function setSeries(key) {
    const m = datasetMap[key];
    chart.data.datasets[0].label = m.label;
    chart.data.datasets[0].data = merged.map(m.get);
    chart.update();

    const note = document.getElementById('unitsNote');
    note.dataset.unit = m.unit || '';
    note.textContent = (m.unit)
      ? `Units: ${m.unit}`
      : `Units: not provided in metadata (or could not be inferred).`;
  }
  setSeries('sales');

  for (const b of document.querySelectorAll('.btn')) {
    b.addEventListener('click', () => {
      document.querySelectorAll('.btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      setSeries(b.dataset.series);
    });
  }

  // Starfield canvas (lightweight)
  const canvas = document.getElementById('stars');
  const c = canvas.getContext('2d');
  function resize() {
    canvas.width = window.innerWidth * devicePixelRatio;
    canvas.height = window.innerHeight * devicePixelRatio;
  }
  window.addEventListener('resize', resize);
  resize();

  const stars = Array.from({length: 220}, () => ({
    x: Math.random() * canvas.width,
    y: Math.random() * canvas.height,
    r: (Math.random() * 1.2 + 0.2) * devicePixelRatio,
    v: (Math.random() * 0.25 + 0.05) * devicePixelRatio
  }));

  function tick() {
    c.clearRect(0,0,canvas.width,canvas.height);
    c.globalAlpha = 0.95;
    for (const s of stars) {
      s.y += s.v;
      if (s.y > canvas.height) {
        s.y = -5 * devicePixelRatio;
        s.x = Math.random() * canvas.width;
      }
      c.beginPath();
      c.arc(s.x, s.y, s.r, 0, Math.PI*2);
      c.fillStyle = 'rgba(255,255,255,0.75)';
      c.fill();
    }
    requestAnimationFrame(tick);
  }
  tick();
})();
</script>

</body>
</html>
"""
    (site_dir / "index.html").write_text(html, encoding="utf-8")


# ----------------------------
# Main
# ----------------------------
def main() -> None:
    api_key = read_api_key("key.txt")

    state = "AZ"
    sector = "RES"
    start = "2021-01"
    end = "2022-01"

    # 1) Retail sales: sales, revenue, price (RES, AZ)
    retail_route = "electricity/retail-sales"
    retail_params = {
        "frequency": "monthly",
        "data[]": ["sales", "revenue", "price"],
        "facets[stateid][]": [state],
        "facets[sectorid][]": [sector],
        "start": start,
        "end": end,
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": 5000,
    }
    retail = eia_data(retail_route, api_key, retail_params)
    retail_units = infer_units(retail_route, api_key, ["sales", "revenue", "price"])

    # 2) Generation (AZ): electricity/electric-power-operational-data
    gen_route = "electricity/electric-power-operational-data"
    gen_col = pick_generation_data_column(gen_route, api_key)

    generation_rows: List[Dict[str, Any]] = []
    gen_units = ""
    try:
        # Discover allowed facet values
        loc_payload = eia_facet(gen_route, "location", api_key)
        sec_payload = eia_facet(gen_route, "sectorid", api_key)
        fuel_payload = eia_facet(gen_route, "fueltypeid", api_key)

        loc_vals = (loc_payload.get("response", {}) or {}).get("facets", []) or []
        sec_vals = (sec_payload.get("response", {}) or {}).get("facets", []) or []
        fuel_vals = (fuel_payload.get("response", {}) or {}).get("facets", []) or []

        location_id = choose_facet_id(
            loc_vals,
            prefer_ids=["AZ", "US-AZ", "ARIZONA"],
            prefer_name_contains=["arizona", "(az)", " az "],
        )
        sector_id = choose_facet_id(
            sec_vals,
            prefer_ids=["ALL", "TOTAL", "TOT"],
            prefer_name_contains=["all", "total"],
        )
        fuel_id = choose_facet_id(
            fuel_vals,
            prefer_ids=["ALL", "TOTAL", "TOT"],
            prefer_name_contains=["all", "total"],
        )

        if not location_id:
            raise RuntimeError(
                "Could not resolve a 'location' facet value for Arizona. "
                "This dataset may not support state-level locations in the way expected."
            )
        if not sector_id:
            # If no ALL/TOTAL, pick the first sector to avoid invalid facet value
            sector_id = str(sec_vals[0].get("id")) if sec_vals else None
        if not fuel_id:
            fuel_id = str(fuel_vals[0].get("id")) if fuel_vals else None

        if not sector_id or not fuel_id:
            raise RuntimeError("Could not resolve sectorid/fueltypeid facet values for generation.")

        gen_params = {
            "frequency": "monthly",
            "data[]": [gen_col],
            "facets[location][]": [location_id],
            "facets[sectorid][]": [sector_id],
            "facets[fueltypeid][]": [fuel_id],
            "start": start,
            "end": end,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": 5000,
        }

        gen_res = eia_data(gen_route, api_key, gen_params)
        for r in gen_res.rows:
            generation_rows.append(
                {
                    "period": r.get("period"),
                    "generation": r.get(gen_col),
                }
            )

        gen_units = infer_units(gen_route, api_key, [gen_col]).get(gen_col, "")
        if not generation_rows:
            print(
                f"[warn] Generation query returned 0 rows. "
                f"route={gen_route} col={gen_col} location={location_id} sector={sector_id} fuel={fuel_id}",
                file=sys.stderr,
            )

    except Exception as e:
        print(
            f"[warn] Generation query failed for route '{gen_route}' using column '{gen_col}': {e}",
            file=sys.stderr,
        )
        generation_rows = []
        gen_units = ""

    combined = {
        "requested": {
            "stateid": state,
            "sectorid": sector,
            "frequency": "monthly",
            "start": start,
            "end": end,
        },
        "retail_sales": {
            "route": retail_route,
            "units": retail_units,
            "rows": retail.rows,
        },
        "generation": {
            "route": gen_route,
            "generation_column": gen_col,
            "units": {"generation": gen_units},
            "rows": generation_rows,
        },
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    site_dir = Path("site")
    build_dashboard(site_dir, combined)

    print("\nDone.")
    print(f"  Wrote: {site_dir / 'data.json'}")
    print(f"  Wrote: {site_dir / 'index.html'}")
    print("\nTo view:")
    print("  cd site")
    print("  python -m http.server 8000")
    print("  open http://localhost:8000")


if __name__ == "__main__":
    main()
