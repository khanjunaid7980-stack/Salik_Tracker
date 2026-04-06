"""
collector.py — Salik Traffic Data Collector
============================================
Polls TomTom Traffic Flow API for each of Salik's 8 Dubai toll gates,
estimates vehicle flow using the Greenshields speed-density model, applies
the Salik tariff schedule, and appends one row per gate to data/traffic_log.csv.

Free-tier usage: max 304 API calls/day (TomTom limit: 2,500/day).
Skips the toll-free window (Dubai 01:00–06:00) to preserve quota.
"""

import csv
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DUBAI_TZ = timezone(timedelta(hours=4))
TOMTOM_API_KEY = os.environ["TOMTOM_API_KEY"]
TOMTOM_URL = (
    "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
)

CSV_PATH = Path("data/traffic_log.csv")
CSV_COLUMNS = [
    "timestamp",
    "gate_name",
    "current_speed_kmph",
    "freeflow_speed_kmph",
    "tomtom_confidence",
    "estimated_flow_vph",
    "chargeable_vph",
    "tariff_aed",
    "revenue_run_rate_aed_hr",
    "congestion_ratio",
]

# ---------------------------------------------------------------------------
# Gate definitions
# Capacities: Sheikh Zayed Road corridor (Al Safa, Al Barsha, Jebel Ali)
#             → 3,600 veh/hr per RTA standards; all others → 2,400 veh/hr
# ---------------------------------------------------------------------------

GATES = [
    {"name": "Al Garhoud Bridge", "lat": 25.2285, "lon": 55.3544, "capacity": 2400},
    {"name": "Al Maktoum Bridge", "lat": 25.2255, "lon": 55.3001, "capacity": 2400},
    {"name": "Al Safa",           "lat": 25.1894, "lon": 55.2396, "capacity": 3600},
    {"name": "Al Barsha",         "lat": 25.1129, "lon": 55.1990, "capacity": 3600},
    {"name": "Al Mamzar North",   "lat": 25.2985, "lon": 55.3419, "capacity": 2400},
    {"name": "Al Mamzar South",   "lat": 25.2940, "lon": 55.3398, "capacity": 2400},
    {"name": "Airport Tunnel",    "lat": 25.2532, "lon": 55.3657, "capacity": 2400},
    {"name": "Jebel Ali",         "lat": 24.9857, "lon": 55.0722, "capacity": 3600},
]

# ---------------------------------------------------------------------------
# Ramadan stub — update dates each year
# During Ramadan the peak window shifts to 09:00–17:00; tariff remains AED 4.
# Set RAMADAN_ACTIVE = True and adjust START/END when Ramadan is in effect.
# ---------------------------------------------------------------------------

RAMADAN_ACTIVE = False
RAMADAN_START = date(2026, 2, 18)  # placeholder — adjust annually
RAMADAN_END = date(2026, 3, 19)    # placeholder — adjust annually

CHARGEABILITY = 0.75  # 75% of flow assumed to be chargeable (non-exempt)


# ---------------------------------------------------------------------------
# Tariff logic
# ---------------------------------------------------------------------------

def get_tariff(dt_dubai: datetime) -> float:
    """
    Return the applicable Salik tariff in AED for the given Dubai-localised
    datetime, following the official rate structure:
      - 01:00–06:00 daily       → AED 0 (toll-free)
      - Ramadan (stub)          → AED 4 flat (peak window differs but same rate)
      - Sunday all day          → AED 4 flat (except toll-free window above)
      - Weekday 06:00–10:00     → AED 6 peak
      - Weekday 16:00–20:00     → AED 6 peak
      - All other times         → AED 4 off-peak
    """
    hour = dt_dubai.hour
    weekday = dt_dubai.weekday()  # Monday=0 … Sunday=6

    # Toll-free window — applies every day
    if 1 <= hour < 6:
        return 0.0

    # Ramadan adjustment stub
    if RAMADAN_ACTIVE and RAMADAN_START <= dt_dubai.date() <= RAMADAN_END:
        # Peak window during Ramadan is 09:00–17:00 but tariff is still AED 4
        return 4.0

    # Sunday — flat off-peak all day (outside toll-free window)
    if weekday == 6:
        return 4.0

    # Weekday (Mon–Sat excluding Sunday) peak windows
    if weekday < 6 and ((6 <= hour < 10) or (16 <= hour < 20)):
        return 6.0

    # Off-peak
    return 4.0


# ---------------------------------------------------------------------------
# Greenshields speed-density model
# ---------------------------------------------------------------------------

def greenshields_flow(capacity: int, current_speed: float, freeflow_speed: float) -> float:
    """
    Estimate vehicles per hour using the Greenshields parabolic speed-density model:
        Flow = Capacity × (v/vf) × (2 − v/vf)
    where v = current speed, vf = free-flow speed.
    The speed ratio is clamped to [0, 1] to avoid negative flow at v > vf.
    """
    if freeflow_speed <= 0:
        return 0.0
    ratio = min(current_speed / freeflow_speed, 1.0)
    return capacity * ratio * (2.0 - ratio)


# ---------------------------------------------------------------------------
# TomTom API call
# ---------------------------------------------------------------------------

def fetch_flow(lat: float, lon: float) -> dict:
    """
    Call TomTom flowSegmentData endpoint and return parsed fields:
      currentSpeed, freeFlowSpeed, confidence
    Raises requests.HTTPError on non-2xx responses.
    """
    params = {
        "point": f"{lat},{lon}",
        "unit": "KMPH",
        "key": TOMTOM_API_KEY,
    }
    resp = requests.get(TOMTOM_URL, params=params, timeout=15)
    resp.raise_for_status()
    segment = resp.json()["flowSegmentData"]
    return {
        "current_speed": segment["currentSpeed"],
        "freeflow_speed": segment["freeFlowSpeed"],
        "confidence": segment["confidence"],
    }


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def ensure_csv_header():
    """Create data/ directory and write CSV header if the file doesn't exist."""
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def append_row(row: dict):
    with CSV_PATH.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------

def collect():
    now_dubai = datetime.now(DUBAI_TZ)
    timestamp_str = now_dubai.strftime("%Y-%m-%d %H:%M:%S %z")

    # Belt-and-suspenders guard: exit during toll-free window to preserve quota.
    # The GitHub Actions cron (*/30 2-20 * * *) already avoids UTC 21:00–01:59
    # (= Dubai 01:00–05:59), but this catches manual workflow_dispatch triggers.
    if 1 <= now_dubai.hour < 6:
        print(
            f"[{timestamp_str}] Toll-free window active (01:00–06:00 Dubai). "
            "Skipping collection to preserve API quota."
        )
        sys.exit(0)

    tariff = get_tariff(now_dubai)
    ensure_csv_header()

    print(f"[{timestamp_str}] Starting collection. Tariff: AED {tariff:.0f}")

    for gate in GATES:
        name = gate["name"]
        row_base = {
            "timestamp": timestamp_str,
            "gate_name": name,
            "tariff_aed": tariff,
        }

        try:
            flow_data = fetch_flow(gate["lat"], gate["lon"])
            current_speed = flow_data["current_speed"]
            freeflow_speed = flow_data["freeflow_speed"]
            confidence = flow_data["confidence"]

            flow_vph = greenshields_flow(gate["capacity"], current_speed, freeflow_speed)
            chargeable_vph = flow_vph * CHARGEABILITY
            revenue_run_rate = chargeable_vph * tariff
            congestion_ratio = (
                round(current_speed / freeflow_speed, 4) if freeflow_speed > 0 else None
            )

            row = {
                **row_base,
                "current_speed_kmph": round(current_speed, 2),
                "freeflow_speed_kmph": round(freeflow_speed, 2),
                "tomtom_confidence": round(confidence, 4),
                "estimated_flow_vph": round(flow_vph, 1),
                "chargeable_vph": round(chargeable_vph, 1),
                "revenue_run_rate_aed_hr": round(revenue_run_rate, 2),
                "congestion_ratio": congestion_ratio,
            }
            print(
                f"  {name}: {current_speed} km/h (ff {freeflow_speed}), "
                f"flow {flow_vph:.0f} vph, chargeable {chargeable_vph:.0f} vph, "
                f"AED {revenue_run_rate:.0f}/hr"
            )

        except Exception as exc:
            print(f"  ERROR — {name}: {exc}")
            row = {
                **row_base,
                "current_speed_kmph": "",
                "freeflow_speed_kmph": "",
                "tomtom_confidence": "",
                "estimated_flow_vph": "",
                "chargeable_vph": "",
                "revenue_run_rate_aed_hr": "",
                "congestion_ratio": "",
            }

        append_row(row)

    print(f"[{timestamp_str}] Collection complete. CSV: {CSV_PATH}")


if __name__ == "__main__":
    collect()
