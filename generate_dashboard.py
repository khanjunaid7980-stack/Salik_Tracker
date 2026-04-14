"""
generate_dashboard.py — Salik Traffic Dashboard Generator
==========================================================
Reads data/traffic_log.csv and writes docs/index.html — a self-contained
HTML dashboard with live gate cards, congestion indicators, charts,
cumulative revenue, and annual breakdown vs Salik reported figures.
Run automatically by GitHub Actions after each collection.
"""

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

CSV_PATH  = Path("data/traffic_log.csv")
OUTPUT    = Path("docs/index.html")
DUBAI_TZ  = timezone(timedelta(hours=4))

GATES = [
    "Al Garhoud Bridge", "Al Maktoum Bridge", "Al Safa", "Al Barsha",
    "Al Mamzar North",   "Al Mamzar South",   "Airport Tunnel", "Jebel Ali",
]

GATE_COLORS = [
    "#6366f1","#22c55e","#f59e0b","#ef4444",
    "#3b82f6","#ec4899","#14b8a6","#f97316",
]

# ── Salik reported annual revenue (AED million) ───────────────────────────────
# Source: Salik Company PJSC Annual Reports / DFM disclosures.
# Update each year when the Annual Report is published.
SALIK_REPORTED = {
    # "2022": 1_847,   # partial year after IPO — update from AR
    # "2023": 2_156,   # update from AR
    # "2024": 2_340,   # update from AR
    # "2025": None,    # update from AR when published
    # "2026": None,    # current year — in progress
}

SALIK_FY_END_MONTH = 12   # December 31 each year


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def load_data():
    rows = []
    if not CSV_PATH.exists():
        return rows
    with CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            if row["current_speed_kmph"]:
                row["current_speed_kmph"]      = float(row["current_speed_kmph"])
                row["freeflow_speed_kmph"]     = float(row["freeflow_speed_kmph"])
                row["estimated_flow_vph"]      = float(row["estimated_flow_vph"])
                row["chargeable_vph"]          = float(row["chargeable_vph"])
                row["tariff_aed"]              = float(row["tariff_aed"])
                row["revenue_run_rate_aed_hr"] = float(row["revenue_run_rate_aed_hr"])
                row["congestion_ratio"]        = float(row["congestion_ratio"])
                row["tomtom_confidence"]       = float(row["tomtom_confidence"])
                try:
                    row["_dt"] = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S %z")
                except Exception:
                    row["_dt"] = None
                rows.append(row)
    return rows


def latest_per_gate(rows):
    latest = {}
    for row in rows:
        latest[row["gate_name"]] = row
    return latest


def last_n_hours(rows, hours=24):
    cutoff = datetime.now(DUBAI_TZ) - timedelta(hours=hours)
    return [r for r in rows if r["_dt"] and r["_dt"] >= cutoff]


# ─────────────────────────────────────────────────────────────────────────────
# Revenue accounting
# Each 30-min poll cycle: network_revenue_aed = Σ(rate_per_gate) × 0.5 hr
# ─────────────────────────────────────────────────────────────────────────────

def build_revenue_timeseries(rows):
    """
    Returns dict: {timestamp_str: network_revenue_aed_for_30min_interval}
    where the value = sum(revenue_run_rate_aed_hr across gates) × 0.5
    """
    rate_by_ts = defaultdict(float)   # total AED/hr across all gates per timestamp
    for row in rows:
        rate_by_ts[row["timestamp"]] += row["revenue_run_rate_aed_hr"]
    return {ts: rate * 0.5 for ts, rate in rate_by_ts.items()}  # × 0.5 hr


def annual_revenue_table(revenue_ts, rows):
    """
    Returns list of dicts, one per year, with:
      - year, first_date, last_date, fy_end_date
      - collected_aed   : sum of 30-min intervals actually measured
      - poll_days       : distinct calendar days in our data for that year
      - daily_avg_aed   : collected_aed / poll_days
      - fy_days         : total days in that FY (Jan 1 – Dec 31)
      - days_remaining  : FY days still to run from today (0 if year complete)
      - extrapolated_aed: daily_avg × fy_days (full FY, not partial)
      - salik_reported  : from SALIK_REPORTED dict (AED million → AED)
    """
    today = datetime.now(DUBAI_TZ).date()

    # Bucket revenue into years
    by_year = defaultdict(float)
    dates_by_year = defaultdict(set)
    for ts, rev in revenue_ts.items():
        year = ts[:4]
        by_year[year] += rev
        dates_by_year[year].add(ts[:10])   # YYYY-MM-DD

    result = []
    for year in sorted(by_year.keys()):
        poll_days  = len(dates_by_year[year])
        collected  = by_year[year]
        daily_avg  = collected / poll_days if poll_days else 0
        fy_start   = datetime(int(year), 1,  1).date()
        fy_end     = datetime(int(year), 12, 31).date()
        fy_days    = (fy_end - fy_start).days + 1
        # Days remaining in FY from today (0 if the year has already closed)
        days_remaining = max((fy_end - today).days + 1, 0)
        extrap     = daily_avg * fy_days   # full FY estimate regardless of coverage

        # Date range string
        year_rows  = [r for r in rows if r["timestamp"][:4] == year]
        first_date = min(r["timestamp"][:10] for r in year_rows)
        last_date  = max(r["timestamp"][:10] for r in year_rows)

        reported_m = SALIK_REPORTED.get(year)   # AED million or None
        reported   = reported_m * 1_000_000 if reported_m else None
        variance   = ((extrap / reported) - 1) * 100 if reported else None

        result.append({
            "year":            year,
            "first_date":      first_date,
            "fy_end_date":     fy_end.strftime("%d %b %Y"),
            "days_remaining":  days_remaining,
            "last_date": last_date,
            "poll_days":        poll_days,
            "collected_aed":    collected,
            "daily_avg_aed":    daily_avg,
            "fy_days":          fy_days,
            "extrapolated_aed": extrap,
            "salik_reported":   reported,
            "variance_pct":     variance,
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# HTML helpers
# ─────────────────────────────────────────────────────────────────────────────

def fmt_aed(v, suffix=""):
    if v is None:
        return "—"
    if v >= 1_000_000_000:
        return f"AED {v/1_000_000_000:.2f}B{suffix}"
    if v >= 1_000_000:
        return f"AED {v/1_000_000:.1f}M{suffix}"
    return f"AED {v:,.0f}{suffix}"


def congestion_label(ratio):
    if ratio is None:
        return "Unknown", "#888"
    if ratio >= 0.85:
        return "Free flow",  "#22c55e"
    if ratio >= 0.60:
        return "Moderate",   "#f59e0b"
    return     "Congested",  "#ef4444"


# ─────────────────────────────────────────────────────────────────────────────
# HTML sections
# ─────────────────────────────────────────────────────────────────────────────

def gate_cards(latest):
    html = ""
    total_rev = 0
    for gate in GATES:
        d = latest.get(gate)
        if not d:
            html += f'<div class="card empty"><div class="gate-name">{gate}</div><div class="no-data">No data yet</div></div>'
            continue
        ratio = d["congestion_ratio"]
        label, color = congestion_label(ratio)
        pct   = int(ratio * 100)
        rev   = d["revenue_run_rate_aed_hr"]
        total_rev += rev
        html += f"""
        <div class="card">
          <div class="gate-name">{gate}</div>
          <div class="cbar-wrap"><div class="cbar" style="width:{pct}%;background:{color}"></div></div>
          <div class="clabel" style="color:{color}">{label} — {pct}% of free flow</div>
          <div class="metrics">
            <div class="m"><span class="mv">{int(d['current_speed_kmph'])}</span><span class="ml">km/h now</span></div>
            <div class="m"><span class="mv">{int(d['freeflow_speed_kmph'])}</span><span class="ml">km/h free-flow</span></div>
            <div class="m"><span class="mv">{int(d['estimated_flow_vph']):,}</span><span class="ml">veh/hr est.</span></div>
            <div class="m"><span class="mv">{int(d['chargeable_vph']):,}</span><span class="ml">chargeable/hr</span></div>
          </div>
          <div class="rev">AED {rev:,.0f}/hr <span class="tbadge">@ AED {int(d['tariff_aed'])}</span></div>
        </div>"""
    return html, total_rev


def annual_table_html(annual):
    now_year = str(datetime.now(DUBAI_TZ).year)
    rows_html = ""
    for y in annual:
        yr       = y["year"]
        is_live  = yr == now_year
        status   = '<span style="color:#f59e0b;font-size:.75rem"> ● In progress</span>' if is_live \
                   else '<span style="color:#22c55e;font-size:.75rem"> ✓ Complete</span>'
        fy_end   = y["fy_end_date"]   # "31 Dec 2026"
        rem      = f'<span class="muted">{y["days_remaining"]} days left in FY</span>' \
                   if is_live and y["days_remaining"] > 0 else ""
        coverage = f'{y["poll_days"]} days observed'
        rep      = fmt_aed(y["salik_reported"]) if y["salik_reported"] \
                   else '<span class="muted">Update from Annual Report</span>'
        var      = f'<span style="color:{"#22c55e" if (y["variance_pct"] or 0) >= 0 else "#ef4444"}">' \
                   f'{y["variance_pct"]:+.1f}%</span>' \
                   if y["variance_pct"] is not None else '<span class="muted">—</span>'
        rows_html += f"""
        <tr>
          <td><b>{yr}</b>{status}</td>
          <td class="muted">{y['first_date']} → {y['last_date']}</td>
          <td>31 Dec {yr} &nbsp;{rem}</td>
          <td class="muted">{coverage}</td>
          <td>{fmt_aed(y['collected_aed'])}</td>
          <td>{fmt_aed(y['daily_avg_aed'])}/day</td>
          <td><b>{fmt_aed(y['extrapolated_aed'])}</b><br><span class="muted" style="font-size:.72rem">daily avg × {y['fy_days']} FY days</span></td>
          <td>{rep}</td>
          <td>{var}</td>
        </tr>"""
    return rows_html


def daily_chart_data(revenue_ts):
    daily = defaultdict(float)
    for ts, rev in revenue_ts.items():
        daily[ts[:10]] += rev
    dates = sorted(daily.keys())[-60:]   # last 60 days
    return dates, [round(daily[d], 0) for d in dates]


def trend_chart_data(rows_24h):
    timestamps_set = sorted({r["timestamp"] for r in rows_24h})
    labels = [ts[11:16] for ts in timestamps_set]
    datasets = []
    for i, gate in enumerate(GATES):
        gmap = {r["timestamp"]: r for r in rows_24h if r["gate_name"] == gate}
        pts  = [round(gmap[ts]["congestion_ratio"]*100, 1) if ts in gmap else None
                for ts in timestamps_set]
        datasets.append({
            "label": gate, "data": pts,
            "borderColor": GATE_COLORS[i],
            "backgroundColor": GATE_COLORS[i] + "22",
            "tension": 0.3, "spanGaps": True, "pointRadius": 2,
        })
    return labels, datasets


def recent_table(rows):
    html = ""
    for row in rows[-16:]:
        _, color = congestion_label(row["congestion_ratio"])
        html += f"""<tr>
          <td>{row['timestamp'][11:16]}</td>
          <td>{row['gate_name']}</td>
          <td>{int(row['current_speed_kmph'])}</td>
          <td>{int(row['freeflow_speed_kmph'])}</td>
          <td style="color:{color};font-weight:600">{int(row['congestion_ratio']*100)}%</td>
          <td>{int(row['estimated_flow_vph']):,}</td>
          <td>AED {int(row['tariff_aed'])}</td>
          <td>AED {row['revenue_run_rate_aed_hr']:,.0f}</td>
        </tr>"""
    return html


# ─────────────────────────────────────────────────────────────────────────────
# Main generator
# ─────────────────────────────────────────────────────────────────────────────

def generate(rows):
    latest   = latest_per_gate(rows)
    rows_24h = last_n_hours(rows, 24)
    rev_ts   = build_revenue_timeseries(rows)
    annual   = annual_revenue_table(rev_ts, rows)

    total_cumulative = sum(rev_ts.values())
    total_poll_days  = len({ts[:10] for ts in rev_ts})
    daily_avg        = total_cumulative / total_poll_days if total_poll_days else 0
    annual_run_rate  = daily_avg * 365

    cards_html, network_rev_hr = gate_cards(latest)
    avg_cong = round(sum(latest[g]["congestion_ratio"] for g in latest) / len(latest) * 100) if latest else 0
    now_dubai = datetime.now(DUBAI_TZ).strftime("%d %b %Y %H:%M Dubai time")

    d_labels, d_data   = daily_chart_data(rev_ts)
    t_labels, t_datasets = trend_chart_data(rows_24h)
    ann_rows_html       = annual_table_html(annual)

    rev_bar_data = [
        round(latest[g]["revenue_run_rate_aed_hr"], 0) if g in latest else 0
        for g in GATES
    ]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Salik Traffic Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root{{--bg:#0f172a;--sur:#1e293b;--bor:#334155;--txt:#f1f5f9;--mut:#94a3b8;--acc:#6366f1}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--txt);font-family:system-ui,sans-serif;padding:24px}}
  h1{{font-size:1.5rem;margin-bottom:4px}}
  .sub{{color:var(--mut);font-size:.85rem;margin-bottom:24px}}
  .sec{{font-size:.8rem;font-weight:600;color:var(--mut);text-transform:uppercase;letter-spacing:.06em;margin:28px 0 14px}}
  /* Stat row */
  .stats{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px}}
  .sbox{{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:16px 22px;flex:1;min-width:160px}}
  .sbox .v{{font-size:1.7rem;font-weight:700;color:var(--acc)}}
  .sbox .l{{font-size:.75rem;color:var(--mut);margin-top:4px}}
  /* Gate cards */
  .cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px;margin-bottom:8px}}
  .card{{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:18px}}
  .card.empty{{opacity:.5}}
  .gate-name{{font-weight:700;margin-bottom:10px}}
  .cbar-wrap{{background:var(--bor);border-radius:99px;height:7px;margin-bottom:5px;overflow:hidden}}
  .cbar{{height:100%;border-radius:99px}}
  .clabel{{font-size:.75rem;margin-bottom:10px}}
  .metrics{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px}}
  .m{{background:var(--bg);border-radius:8px;padding:7px 10px}}
  .mv{{display:block;font-size:1.2rem;font-weight:700}}
  .ml{{font-size:.68rem;color:var(--mut)}}
  .rev{{font-size:.95rem;font-weight:700;color:#22c55e}}
  .tbadge{{font-size:.7rem;color:var(--mut);font-weight:400}}
  .no-data{{color:var(--mut);font-size:.85rem}}
  /* Charts */
  .charts2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:8px}}
  .charts1{{margin-bottom:8px}}
  @media(max-width:760px){{.charts2{{grid-template-columns:1fr}}}}
  .cbox{{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:18px}}
  /* Annual table */
  .twrap{{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:20px;overflow-x:auto;margin-bottom:8px}}
  table{{width:100%;border-collapse:collapse;font-size:.82rem}}
  th{{color:var(--mut);font-weight:500;text-align:left;padding:8px 10px;border-bottom:1px solid var(--bor);font-size:.72rem;text-transform:uppercase;white-space:nowrap}}
  td{{padding:8px 10px;border-bottom:1px solid var(--bor);white-space:nowrap}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:rgba(255,255,255,.03)}}
  .muted{{color:var(--mut)}}
  .note{{background:#1e293b;border:1px solid #334155;border-left:3px solid #f59e0b;border-radius:8px;padding:14px 18px;font-size:.82rem;color:#94a3b8;margin-bottom:8px;line-height:1.6}}
  .note b{{color:#f1f5f9}}
  .foot{{color:var(--mut);font-size:.73rem;margin-top:20px;text-align:center}}
</style>
</head>
<body>

<h1>Salik Traffic Monitor</h1>
<p class="sub">Updated: {now_dubai} &nbsp;·&nbsp; Auto-refreshes every 30 min &nbsp;·&nbsp; All 8 toll gates &nbsp;·&nbsp; {len(rows):,} readings collected</p>

<!-- ── Top stats ─────────────────────────────────────────────────────────── -->
<p class="sec">Network Snapshot (Current)</p>
<div class="stats">
  <div class="sbox"><div class="v">AED {network_rev_hr:,.0f}</div><div class="l">Estimated network revenue / hr right now</div></div>
  <div class="sbox"><div class="v">{avg_cong}%</div><div class="l">Average network speed vs free-flow</div></div>
  <div class="sbox"><div class="v">{fmt_aed(daily_avg)}</div><div class="l">Average estimated revenue / day (observed days)</div></div>
  <div class="sbox"><div class="v">{fmt_aed(annual_run_rate)}</div><div class="l">Annualised run rate (daily avg × 365)</div></div>
</div>

<!-- ── Gate cards ────────────────────────────────────────────────────────── -->
<p class="sec">Live Gate Status</p>
<div class="cards">{cards_html}</div>

<!-- ── Annual revenue table ──────────────────────────────────────────────── -->
<p class="sec">Annual Revenue Estimate vs Salik Reported (AED)</p>
<div class="note">
  <b>How this works:</b> For each 30-minute poll cycle, we sum the estimated revenue across all 8 gates
  and multiply by 0.5 hr to get the revenue for that window. Summing all windows gives the
  <b>Collected Period</b> figure. We then divide by observed days and multiply by 365 to get the
  <b>Full-Year Extrapolation</b>. The <b>Salik Reported</b> column shows actual annual toll revenue
  from their Annual Report — add those figures by updating <code>SALIK_REPORTED</code> in
  <code>generate_dashboard.py</code> each year.
</div>
<div class="twrap">
  <table>
    <thead><tr>
      <th>FY</th><th>Our Data Window</th><th>FY End Date</th><th>Coverage</th>
      <th>Est. Revenue (Observed)</th><th>Daily Avg</th>
      <th>Full-Year Extrapolation</th><th>Salik Reported Revenue</th><th>Variance</th>
    </tr></thead>
    <tbody>{ann_rows_html}</tbody>
  </table>
</div>

<!-- ── Cumulative stats ──────────────────────────────────────────────────── -->
<p class="sec">Cumulative Since Collection Began</p>
<div class="stats">
  <div class="sbox"><div class="v">{fmt_aed(total_cumulative)}</div><div class="l">Total estimated revenue — all observed windows</div></div>
  <div class="sbox"><div class="v">{total_poll_days}</div><div class="l">Calendar days with live data</div></div>
  <div class="sbox"><div class="v">{len(rev_ts):,}</div><div class="l">30-min poll cycles with real readings</div></div>
  <div class="sbox"><div class="v">{len(rows):,}</div><div class="l">Individual gate readings stored</div></div>
  <div class="sbox"><div class="v" style="color:#22c55e">304 / 2,500</div><div class="l">TomTom API calls per day (12% of free tier · resets midnight UTC)</div></div>
</div>

<!-- ── Daily revenue chart ───────────────────────────────────────────────── -->
<p class="sec">Daily Estimated Revenue — Last 60 Days (AED)</p>
<div class="charts1"><div class="cbox" style="height:220px">
  <canvas id="dailyChart"></canvas>
</div></div>

<!-- ── 24h congestion + current revenue ─────────────────────────────────── -->
<p class="sec">Last 24 Hours — Congestion Ratio per Gate (%)</p>
<div class="charts2">
  <div class="cbox"><canvas id="trendChart" height="220"></canvas></div>
  <div class="cbox"><canvas id="revChart"   height="220"></canvas></div>
</div>

<!-- ── Latest readings table ────────────────────────────────────────────── -->
<p class="sec">Latest Readings</p>
<div class="twrap">
  <table>
    <thead><tr>
      <th>Time (Dubai)</th><th>Gate</th><th>Speed km/h</th><th>Free-flow</th>
      <th>Congestion</th><th>Flow vph</th><th>Tariff</th><th>Revenue/hr</th>
    </tr></thead>
    <tbody>{recent_table(rows)}</tbody>
  </table>
</div>

<p class="foot">
  Collection runs every 30 min (Dubai 06:00–01:00) via GitHub Actions · TomTom Traffic Flow API ·
  Greenshields model · 75% chargeability assumption · For research purposes only
</p>

<script>
const chtDefaults = {{
  responsive:true, maintainAspectRatio:false,
  plugins:{{legend:{{labels:{{color:'#94a3b8',boxWidth:11,font:{{size:10}}}}}}}},
  scales:{{
    x:{{ticks:{{color:'#94a3b8',maxTicksLimit:10,font:{{size:10}}}},grid:{{color:'#1e293b'}}}},
    y:{{ticks:{{color:'#94a3b8',font:{{size:10}}}},grid:{{color:'#334155'}}}}
  }}
}};

// Daily revenue bar chart
new Chart(document.getElementById('dailyChart').getContext('2d'),{{
  type:'bar',
  data:{{
    labels:{json.dumps(d_labels)},
    datasets:[{{
      label:'Est. daily revenue (AED)',
      data:{json.dumps(d_data)},
      backgroundColor:'#6366f133',
      borderColor:'#6366f1',
      borderWidth:1,
    }}]
  }},
  options:{{...chtDefaults,
    scales:{{
      x:{{ticks:{{color:'#94a3b8',maxTicksLimit:15,font:{{size:9}}}},grid:{{color:'#1e293b'}}}},
      y:{{ticks:{{color:'#94a3b8',font:{{size:10}},callback:v=>'AED '+v.toLocaleString()}},grid:{{color:'#334155'}}}}
    }}
  }}
}});

// 24h congestion trend
new Chart(document.getElementById('trendChart').getContext('2d'),{{
  type:'line',
  data:{{
    labels:{json.dumps(t_labels)},
    datasets:{json.dumps(t_datasets)}
  }},
  options:{{...chtDefaults,
    scales:{{
      x:{{ticks:{{color:'#94a3b8',maxTicksLimit:12,font:{{size:10}}}},grid:{{color:'#1e293b'}}}},
      y:{{min:0,max:100,ticks:{{color:'#94a3b8',callback:v=>v+'%'}},grid:{{color:'#334155'}}}}
    }}
  }}
}});

// Current revenue per gate
new Chart(document.getElementById('revChart').getContext('2d'),{{
  type:'bar',
  data:{{
    labels:{json.dumps([g.split()[-1] for g in GATES])},
    datasets:[{{
      label:'AED/hr',
      data:{json.dumps(rev_bar_data)},
      backgroundColor:{json.dumps(GATE_COLORS)},
    }}]
  }},
  options:{{...chtDefaults,
    plugins:{{legend:{{display:false}}}},
    scales:{{
      x:{{ticks:{{color:'#94a3b8',font:{{size:10}}}},grid:{{color:'#1e293b'}}}},
      y:{{ticks:{{color:'#94a3b8',callback:v=>'AED '+v.toLocaleString()}},grid:{{color:'#334155'}}}}
    }}
  }}
}});
</script>
</body>
</html>"""

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"Dashboard written → {OUTPUT}  ({len(rows):,} rows · {total_poll_days} days · cumulative {fmt_aed(total_cumulative)})")


if __name__ == "__main__":
    generate(load_data())
