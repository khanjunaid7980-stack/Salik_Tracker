# Salik Traffic Data Collector

An independent traffic data collection system for equity research on **Salik Company PJSC** (DFM: SALIK), Dubai's listed toll road operator. The system polls real-time traffic conditions at all 8 Salik toll gates every 30 minutes and estimates chargeable trip volumes, allowing cross-validation against management guidance in a financial model.

---

## Project Structure

```
Salik_Tracker/
├── collector.py                   # Main data collection script
├── requirements.txt               # Python dependencies (requests only)
├── .github/
│   └── workflows/
│       └── collect.yml            # GitHub Actions cron scheduler
├── .gitignore                     # Excludes .env and secrets
├── data/
│   ├── .gitkeep                   # Ensures data/ is tracked before first run
│   └── traffic_log.csv            # Appended by each collection run (auto-created)
└── README.md
```

---

## Methodology

### 1. Data Source: TomTom Traffic Flow API

Each gate is represented by a GPS coordinate. The collector calls the TomTom
[flowSegmentData](https://developer.tomtom.com/traffic-api/documentation/traffic-flow/flow-segment-data)
endpoint (free tier, 2,500 calls/day, no credit card required) for each gate:

```
GET https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json
    ?point={lat},{lon}&unit=KMPH&key={API_KEY}
```

Fields extracted: `currentSpeed` (km/h), `freeFlowSpeed` (km/h), `confidence` (0–1).

### 2. Flow Estimation: Greenshields Speed-Density Model

The [Greenshields model](https://en.wikipedia.org/wiki/Greenshields%27_model) is a
classical traffic flow parabola that relates speed to density:

```
Flow (veh/hr) = Capacity × (v / v_f) × (2 − v / v_f)
```

Where:
- `v` = current speed from TomTom
- `v_f` = free-flow speed from TomTom
- `Capacity` = lane capacity per RTA standards (see table below)
- Speed ratio is clamped to `[0, 1]` (handles v > v_f edge cases)

At free-flow (v = v_f), ratio = 1 → flow = capacity.  
At full stop (v = 0), ratio = 0 → flow = 0.  
Maximum flow occurs at v = v_f / 2 (50% of free-flow speed).

#### Lane Capacities

| Gate | Road | Capacity (veh/hr) |
|---|---|---|
| Al Garhoud Bridge | Al Rebat Road | 2,400 |
| Al Maktoum Bridge | Al Maktoum Road | 2,400 |
| Al Safa | Sheikh Zayed Road (E11) | 3,600 |
| Al Barsha | Sheikh Zayed Road (E11) | 3,600 |
| Al Mamzar North | Al Ittihad Road | 2,400 |
| Al Mamzar South | Al Ittihad Road | 2,400 |
| Airport Tunnel | Airport Tunnel | 2,400 |
| Jebel Ali | Sheikh Zayed Road (E11) | 3,600 |

Sheikh Zayed Road gates use 3,600 veh/hr (higher-capacity multi-lane highway);
all others use 2,400 veh/hr, consistent with RTA Dubai design standards.

### 3. Chargeability Assumption

Not all vehicles passing a Salik gate are charged. Exempt categories include
emergency vehicles, military, RTA buses, and certain diplomatic vehicles.
This model applies a **75% chargeability factor**:

```
Chargeable vph = Estimated flow vph × 0.75
```

This is a conservative assumption. Salik's disclosed chargeability rate has
historically been in the 75–80% range, but 75% is used for conservatism.

### 4. Tariff Schedule

Revenue estimates use Dubai local time (UTC+4):

| Window | Days | Tariff |
|---|---|---|
| 01:00 AM – 06:00 AM | All days | AED 0 (toll-free) |
| 06:00 AM – 10:00 AM | Mon – Sat | AED 6 (peak) |
| 04:00 PM – 08:00 PM | Mon – Sat | AED 6 (peak) |
| All other hours | Mon – Sat | AED 4 (off-peak) |
| All day (excl. toll-free) | Sunday | AED 4 (flat) |

**Ramadan adjustment (stub)**: During Ramadan the peak window shifts to
09:00–17:00, but the tariff remains AED 4 throughout. Update `RAMADAN_START`,
`RAMADAN_END`, and set `RAMADAN_ACTIVE = True` in `collector.py` annually.

### 5. Revenue Run Rate

```
Revenue run rate (AED/hr) = Chargeable vph × Tariff (AED)
```

Summing across all 8 gates and extrapolating to an annual figure provides an
independent cross-check against Salik's disclosed trip volumes and revenue.

---

## Gate Coordinates

| Gate | Latitude | Longitude |
|---|---|---|
| Al Garhoud Bridge | 25.2285 | 55.3544 |
| Al Maktoum Bridge | 25.2255 | 55.3001 |
| Al Safa | 25.1894 | 55.2396 |
| Al Barsha | 25.1129 | 55.1990 |
| Al Mamzar North | 25.2985 | 55.3419 |
| Al Mamzar South | 25.2940 | 55.3398 |
| Airport Tunnel | 25.2532 | 55.3657 |
| Jebel Ali | 24.9857 | 55.0722 |

---

## CSV Output: `data/traffic_log.csv`

One row is appended per gate per poll cycle. On API failure, a null row is
written to preserve the timestamp in the log.

| Column | Description |
|---|---|
| `timestamp` | Dubai local time (UTC+4) of the poll |
| `gate_name` | Salik gate name |
| `current_speed_kmph` | TomTom current speed (km/h) |
| `freeflow_speed_kmph` | TomTom free-flow speed (km/h) |
| `tomtom_confidence` | TomTom data confidence score (0–1) |
| `estimated_flow_vph` | Greenshields-estimated flow (vehicles/hr) |
| `chargeable_vph` | Estimated chargeable flow (flow × 75%) |
| `tariff_aed` | Applicable Salik tariff in AED |
| `revenue_run_rate_aed_hr` | Estimated revenue run rate (AED/hr) |
| `congestion_ratio` | current_speed / freeflow_speed (1.0 = free-flow) |

---

## Free-Tier API Usage

| Metric | Value |
|---|---|
| TomTom free tier limit | 2,500 calls/day |
| Collection window | Dubai 06:00–01:00 (19 hrs/day) |
| Poll frequency | Every 30 minutes |
| Gates per poll | 8 |
| **Daily usage** | **304 calls/day (~12% of quota)** |

The scheduler (`*/30 2-20 * * *` UTC) and an in-script guard both skip the
Salik toll-free window (01:00–06:00 Dubai), eliminating zero-revenue data
points and conserving free-tier quota for the hours that matter.

---

## Setup

### 1. Get a TomTom API Key (free)

1. Register at [developer.tomtom.com](https://developer.tomtom.com)
2. Create an app — the free tier includes 2,500 Traffic API calls/day, no credit card required
3. Copy your API key

### 2. Add the API Key as a GitHub Secret

1. Go to your repository → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `TOMTOM_API_KEY`
4. Value: paste your TomTom API key
5. Click **Add secret**

### 3. Enable GitHub Actions

GitHub Actions is enabled by default on public repositories. For private repos,
go to **Settings** → **Actions** → **General** → select **Allow all actions**.

The workflow will start collecting automatically on the cron schedule
(`*/30 2-20 * * *` UTC). To trigger a test run immediately:
**Actions** → **Collect Traffic Data** → **Run workflow**.

### 4. Run Locally

```bash
git clone https://github.com/<your-org>/Salik_Tracker.git
cd Salik_Tracker
pip install -r requirements.txt
export TOMTOM_API_KEY=your_api_key_here
python collector.py
```

Output CSV will be written to `data/traffic_log.csv`.

---

## Limitations and Caveats

- **Proxy for actual gate traffic**: TomTom measures road segments near gate coordinates, not gantry-level vehicle counts. Treat estimates as directional proxies.
- **Greenshields model**: A simplified parabolic model; actual flow-density curves may differ, especially for multi-lane highways.
- **Chargeability assumption**: The 75% factor is an estimate. Actual exempt-vehicle proportions vary by gate location and time of day.
- **Coordinate approximations**: GPS coordinates are approximate gate locations; TomTom may snap to the nearest road segment.
- **For research purposes only**: Not investment advice.
