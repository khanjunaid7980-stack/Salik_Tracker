"""
generate_dashboard.py — Salik Traffic Dashboard Generator
==========================================================
Reads data/traffic_log.csv and writes docs/index.html — a self-contained
HTML dashboard with live gate cards, congestion indicators, charts, and
a summary table. Run automatically by GitHub Actions after each collection.
"""

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

CSV_PATH = Path("data/traffic_log.csv")
OUTPUT_PATH = Path("docs/index.html")
DUBAI_TZ = timezone(timedelta(hours=4))

GATES = [
    "Al Garhoud Bridge",
    "Al Maktoum Bridge",
    "Al Safa",
    "Al Barsha",
    "Al Mamzar North",
    "Al Mamzar South",
    "Airport Tunnel",
    "Jebel Ali",
]


def load_data():
    rows = []
    if not CSV_PATH.exists():
        return rows
    with CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            if row["current_speed_kmph"]:  # skip null rows
                row["current_speed_kmph"] = float(row["current_speed_kmph"])
                row["freeflow_speed_kmph"] = float(row["freeflow_speed_kmph"])
                row["estimated_flow_vph"] = float(row["estimated_flow_vph"])
                row["chargeable_vph"] = float(row["chargeable_vph"])
                row["tariff_aed"] = float(row["tariff_aed"])
                row["revenue_run_rate_aed_hr"] = float(row["revenue_run_rate_aed_hr"])
                row["congestion_ratio"] = float(row["congestion_ratio"])
                row["tomtom_confidence"] = float(row["tomtom_confidence"])
                rows.append(row)
    return rows


def latest_per_gate(rows):
    latest = {}
    for row in rows:
        latest[row["gate_name"]] = row
    return latest


def last_n_hours(rows, hours=24):
    cutoff = datetime.now(DUBAI_TZ) - timedelta(hours=hours)
    result = []
    for row in rows:
        try:
            ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S %z")
            if ts >= cutoff:
                result.append(row)
        except Exception:
            pass
    return result


def congestion_label(ratio):
    if ratio is None:
        return "unknown", "#888"
    if ratio >= 0.85:
        return "Free flow", "#22c55e"
    if ratio >= 0.60:
        return "Moderate", "#f59e0b"
    return "Congested", "#ef4444"


def generate(rows):
    latest = latest_per_gate(rows)
    recent = last_n_hours(rows, 24)

    now_dubai = datetime.now(DUBAI_TZ).strftime("%d %b %Y %H:%M Dubai time")

    # ── Gate cards data ──────────────────────────────────────────────────────
    cards_html = ""
    total_revenue = 0
    for gate in GATES:
        data = latest.get(gate)
        if not data:
            cards_html += f"""
            <div class="card empty">
                <div class="gate-name">{gate}</div>
                <div class="no-data">No data yet</div>
            </div>"""
            continue

        ratio = data["congestion_ratio"]
        label, color = congestion_label(ratio)
        pct = int(ratio * 100)
        rev = data["revenue_run_rate_aed_hr"]
        total_revenue += rev
        tariff = int(data["tariff_aed"])
        speed = int(data["current_speed_kmph"])
        ff = int(data["freeflow_speed_kmph"])
        flow = int(data["estimated_flow_vph"])
        chargeable = int(data["chargeable_vph"])

        cards_html += f"""
        <div class="card">
            <div class="gate-name">{gate}</div>
            <div class="congestion-bar-wrap">
                <div class="congestion-bar" style="width:{pct}%;background:{color}"></div>
            </div>
            <div class="congestion-label" style="color:{color}">{label} — {pct}% of free flow</div>
            <div class="metrics">
                <div class="metric"><span class="mval">{speed}</span><span class="mlbl">km/h now</span></div>
                <div class="metric"><span class="mval">{ff}</span><span class="mlbl">km/h free flow</span></div>
                <div class="metric"><span class="mval">{flow:,}</span><span class="mlbl">veh/hr est.</span></div>
                <div class="metric"><span class="mval">{chargeable:,}</span><span class="mlbl">chargeable/hr</span></div>
            </div>
            <div class="revenue">AED {rev:,.0f} / hr &nbsp;<span class="tariff-badge">@ AED {tariff}</span></div>
        </div>"""

    # ── Chart data: congestion ratio per gate over last 24h ─────────────────
    chart_labels = []
    chart_datasets = []
    gate_colors = [
        "#6366f1","#22c55e","#f59e0b","#ef4444",
        "#3b82f6","#ec4899","#14b8a6","#f97316"
    ]
    timestamps_set = sorted({r["timestamp"] for r in recent})
    # Use hour:minute labels only
    chart_labels = [ts[11:16] for ts in timestamps_set]

    for i, gate in enumerate(GATES):
        gate_rows = {r["timestamp"]: r for r in recent if r["gate_name"] == gate}
        data_pts = [
            round(gate_rows[ts]["congestion_ratio"] * 100, 1) if ts in gate_rows else None
            for ts in timestamps_set
        ]
        chart_datasets.append({
            "label": gate,
            "data": data_pts,
            "borderColor": gate_colors[i],
            "backgroundColor": gate_colors[i] + "22",
            "tension": 0.3,
            "spanGaps": True,
            "pointRadius": 2,
        })

    # ── Revenue bar chart (latest per gate) ──────────────────────────────────
    rev_labels = [g.replace(" ", "\n") for g in GATES]
    rev_data = [round(latest[g]["revenue_run_rate_aed_hr"], 0) if g in latest else 0 for g in GATES]

    # ── Recent readings table (last 16 rows = 2 full cycles) ─────────────────
    table_rows = ""
    for row in rows[-16:]:
        ratio = row["congestion_ratio"]
        _, color = congestion_label(ratio)
        table_rows += f"""
        <tr>
            <td>{row['timestamp'][11:16]}</td>
            <td>{row['gate_name']}</td>
            <td>{int(row['current_speed_kmph'])}</td>
            <td>{int(row['freeflow_speed_kmph'])}</td>
            <td style="color:{color};font-weight:600">{int(ratio*100)}%</td>
            <td>{int(row['estimated_flow_vph']):,}</td>
            <td>AED {int(row['tariff_aed'])}</td>
            <td>AED {row['revenue_run_rate_aed_hr']:,.0f}</td>
        </tr>"""

    # ── Total stats ──────────────────────────────────────────────────────────
    active_gates = len(latest)
    avg_congestion = (
        round(sum(latest[g]["congestion_ratio"] for g in latest) / active_gates * 100)
        if active_gates else 0
    )
    total_rows = len(rows)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Salik Traffic Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #f1f5f9; --muted: #94a3b8; --accent: #6366f1;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; padding: 24px; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }}
  .stat-row {{ display: flex; gap: 16px; margin-bottom: 28px; flex-wrap: wrap; }}
  .stat-box {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
               padding: 16px 24px; flex: 1; min-width: 160px; }}
  .stat-box .val {{ font-size: 1.8rem; font-weight: 700; color: var(--accent); }}
  .stat-box .lbl {{ font-size: 0.78rem; color: var(--muted); margin-top: 4px; }}
  .section-title {{ font-size: 1rem; font-weight: 600; margin-bottom: 14px; color: var(--muted);
                    text-transform: uppercase; letter-spacing: .05em; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px,1fr)); gap: 16px; margin-bottom: 32px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 18px; }}
  .card.empty {{ opacity: .5; }}
  .gate-name {{ font-weight: 700; font-size: 1rem; margin-bottom: 12px; }}
  .congestion-bar-wrap {{ background: var(--border); border-radius: 99px; height: 8px; margin-bottom: 6px; overflow: hidden; }}
  .congestion-bar {{ height: 100%; border-radius: 99px; transition: width .5s; }}
  .congestion-label {{ font-size: 0.78rem; margin-bottom: 12px; }}
  .metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px; }}
  .metric {{ background: var(--bg); border-radius: 8px; padding: 8px 10px; }}
  .mval {{ display: block; font-size: 1.25rem; font-weight: 700; }}
  .mlbl {{ font-size: 0.7rem; color: var(--muted); }}
  .revenue {{ font-size: 1rem; font-weight: 700; color: #22c55e; }}
  .tariff-badge {{ font-size: 0.72rem; color: var(--muted); font-weight: 400; }}
  .no-data {{ color: var(--muted); font-size: 0.85rem; }}
  .charts {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 32px; }}
  @media(max-width:768px) {{ .charts {{ grid-template-columns: 1fr; }} }}
  .chart-box {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ color: var(--muted); font-weight: 500; text-align: left; padding: 8px 12px;
        border-bottom: 1px solid var(--border); font-size: 0.75rem; text-transform: uppercase; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: rgba(255,255,255,.03); }}
  .table-wrap {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
                 padding: 20px; overflow-x: auto; }}
  .refresh-note {{ color: var(--muted); font-size: 0.75rem; margin-top: 20px; text-align: center; }}
</style>
</head>
<body>

<h1>Salik Traffic Monitor</h1>
<p class="subtitle">Updated: {now_dubai} &nbsp;·&nbsp; Auto-refreshes every 30 minutes &nbsp;·&nbsp; All 8 toll gates</p>

<div class="stat-row">
  <div class="stat-box">
    <div class="val">AED {total_revenue:,.0f}</div>
    <div class="lbl">Estimated network revenue / hr</div>
  </div>
  <div class="stat-box">
    <div class="val">{avg_congestion}%</div>
    <div class="lbl">Average network speed vs free-flow</div>
  </div>
  <div class="stat-box">
    <div class="val">{active_gates} / 8</div>
    <div class="lbl">Gates with live data</div>
  </div>
  <div class="stat-box">
    <div class="val">{total_rows:,}</div>
    <div class="lbl">Total readings collected</div>
  </div>
</div>

<p class="section-title">Live Gate Status</p>
<div class="cards">{cards_html}</div>

<p class="section-title">Last 24 Hours — Congestion Ratio per Gate (%)</p>
<div class="charts">
  <div class="chart-box">
    <canvas id="trendChart" height="200"></canvas>
  </div>
  <div class="chart-box">
    <canvas id="revChart" height="200"></canvas>
  </div>
</div>

<p class="section-title">Latest Readings</p>
<div class="table-wrap">
  <table>
    <thead><tr>
      <th>Time (Dubai)</th><th>Gate</th><th>Speed km/h</th><th>Free-flow</th>
      <th>Congestion</th><th>Flow vph</th><th>Tariff</th><th>Revenue / hr</th>
    </tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
</div>

<p class="refresh-note">
  This page is regenerated automatically after every data collection run (~every 30 min).
  Hard-refresh your browser (Ctrl+Shift+R) to see the latest update.
</p>

<script>
const trendCtx = document.getElementById('trendChart').getContext('2d');
new Chart(trendCtx, {{
  type: 'line',
  data: {{
    labels: {json.dumps(chart_labels)},
    datasets: {json.dumps(chart_datasets)}
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8', boxWidth: 12, font: {{ size: 11 }} }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8', maxTicksLimit: 12, font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }} }},
      y: {{ min: 0, max: 100, ticks: {{ color: '#94a3b8', callback: v => v+'%' }}, grid: {{ color: '#334155' }} }}
    }}
  }}
}});

const revCtx = document.getElementById('revChart').getContext('2d');
new Chart(revCtx, {{
  type: 'bar',
  data: {{
    labels: {json.dumps([g.split()[-1] for g in GATES])},
    datasets: [{{
      label: 'AED / hr',
      data: {json.dumps(rev_data)},
      backgroundColor: ['#6366f1','#22c55e','#f59e0b','#ef4444','#3b82f6','#ec4899','#14b8a6','#f97316'],
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }} }},
      y: {{ ticks: {{ color: '#94a3b8', callback: v => 'AED '+v.toLocaleString() }}, grid: {{ color: '#334155' }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard written to {OUTPUT_PATH}  ({len(rows)} data rows, {active_gates} gates live)")


if __name__ == "__main__":
    rows = load_data()
    generate(rows)
