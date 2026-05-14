"""
Climate normals tool: accurate long-term averages from Open-Meteo Archive.

Uses 10-year window (2014-2023) for stable normals; returns monthly averages
and variability (std dev) for practical comparison.
"""

import logging
import statistics
from typing import Any

import requests

logger = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# 10-year window for more stable normals (WMO uses 30-year; 10 is a practical compromise)
NORMALS_START = "2014-01-01"
NORMALS_END = "2023-12-31"


def run(latitude: float, longitude: float) -> dict[str, Any]:
    """
    Fetch real climate normals from Open-Meteo Archive (reanalysis).
    Returns average temperature and rainfall (monthly normals), plus
    standard deviation for variability.
    """
    logger.info("climate_tool.run(lat=%s, lon=%s)", latitude, longitude)
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": NORMALS_START,
        "end_date": NORMALS_END,
        "daily": "temperature_2m_mean,precipitation_sum",
    }
    try:
        resp = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.exception("Open-Meteo archive request failed: %s", e)
        return {
            "tool": "climate_normals",
            "avg_rainfall_normal_mm": None,
            "avg_temp_normal_c": None,
            "error": str(e),
        }

    daily = data.get("daily", {})
    temps_raw = daily.get("temperature_2m_mean") or []
    precips_raw = daily.get("precipitation_sum") or []
    times = daily.get("time") or []

    temps = [t for t in temps_raw if t is not None]
    precips = [p for p in precips_raw if p is not None]

    avg_temp = sum(temps) / len(temps) if temps else None
    total_precip = sum(precips)
    n_days = len(precips_raw)
    n_months = n_days / 30.44 if n_days else 0
    avg_rainfall_per_month = total_precip / n_months if n_months > 0 else None

    # Monthly normals: average temp and precip by month (1-12); indices match times
    monthly_temp: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    monthly_precip: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for i, t in enumerate(times):
        try:
            s = (t.split("T")[0] if "T" in str(t) else str(t)).strip()
            parts = s.split("-")
            if len(parts) != 3:
                continue
            month = int(parts[1])
            if 1 <= month <= 12 and i < len(temps_raw) and i < len(precips_raw):
                if temps_raw[i] is not None:
                    monthly_temp[month].append(temps_raw[i])
                if precips_raw[i] is not None:
                    monthly_precip[month].append(precips_raw[i])
        except Exception:
            continue
    monthly_avg_temp = {m: round(statistics.mean(v), 1) if v else None for m, v in monthly_temp.items()}
    # Monthly precip: sum of daily then average across years gives mean daily; * 30.44 = monthly mm
    monthly_avg_precip = {}
    for m, v in monthly_precip.items():
        if v:
            mean_daily = statistics.mean(v)
            monthly_avg_precip[m] = round(mean_daily * 30.44, 1)
        else:
            monthly_avg_precip[m] = None

    temp_std = round(statistics.stdev(temps), 1) if len(temps) > 1 else None
    precip_std = round(statistics.stdev(precips), 1) if len(precips) > 1 else None

    return {
        "tool": "climate_normals",
        "avg_rainfall_normal_mm": round(avg_rainfall_per_month, 1) if avg_rainfall_per_month is not None else None,
        "avg_temp_normal_c": round(avg_temp, 1) if avg_temp is not None else None,
        "temp_std_dev_c": temp_std,
        "precipitation_std_dev_mm": precip_std,
        "monthly_avg_temp_c": monthly_avg_temp,
        "monthly_avg_rainfall_mm": monthly_avg_precip,
        "data_period_years": "2014-2023",
    }