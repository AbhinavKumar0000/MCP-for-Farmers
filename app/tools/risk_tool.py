"""
Risk assessment tool: practical, accurate indices from real Open-Meteo data.

- Drought: SPI-like z-score (recent 30-day precip vs same-period climatology).
- Heatwave: WMO-style (consecutive days above local threshold; severity from max temp).
- Flood: combined 7-day forecast precip, max 1-day intensity, antecedent wetness.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# SPI-like thresholds: z-score of 30-day precip vs climatology -> drought probability
# SPI <= -2: exceptional drought; <= -1.5: extreme; <= -1: severe; <= -0.5: moderate
DROUGHT_Z_TO_PROB = [
    (-2.0, 0.70),
    (-1.5, 0.55),
    (-1.0, 0.40),
    (-0.5, 0.22),
    (0.0, 0.12),
]

# Heatwave: min consecutive days above threshold (WMO-style) and severity
HEATWAVE_DAYS_35C = 3  # ≥3 consecutive days ≥35°C
HEATWAVE_DAYS_38C = 2  # ≥2 consecutive days ≥38°C
HEATWAVE_SEVERE_40C = 40  # any day ≥40°C raises probability

# Flood: 7-day precip (mm) thresholds and max 1-day intensity
FLOOD_7D_MM = [(200, 0.50), (150, 0.38), (100, 0.28), (50, 0.15), (20, 0.08)]
FLOOD_1DAY_MM = [(80, 0.35), (50, 0.22), (30, 0.12)]  # single-day extreme


def _fetch_forecast_with_past(
    latitude: float, longitude: float, past_days: int = 30, forecast_days: int = 7
) -> dict | None:
    """Fetch forecast plus past days: daily max temp, precip, for 30d past + 7d future."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        "timezone": "auto",
        "past_days": past_days,
        "forecast_days": forecast_days,
    }
    try:
        r = requests.get(OPEN_METEO_FORECAST_URL, params=params, timeout=12)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        logger.warning("Forecast (with past) failed in risk_tool: %s", e)
        return None


def _fetch_archive_years(
    latitude: float, longitude: float, start_year: int = 2019, end_year: int = 2023
) -> dict | None:
    """Fetch multi-year daily precipitation for drought climatology."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": f"{start_year}-01-01",
        "end_date": f"{end_year}-12-31",
        "daily": "precipitation_sum",
    }
    try:
        r = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        logger.warning("Archive (climatology) failed in risk_tool: %s", e)
        return None


def _drought_z_score(
    actual_30d_mm: float,
    times: list[str],
    archive_daily: dict | None,
    start_year: int = 2019,
    end_year: int = 2023,
) -> tuple[float, float | None, float | None]:
    """
    SPI-like z-score: actual 30-day precip vs same calendar-window climatology.
    Returns (z_score, climatology_mean_mm, climatology_std_mm).
    """
    import statistics
    if not times or len(times) < 30 or not archive_daily:
        return 0.0, None, None
    window_times = times[:30]  # past 30 days (same order as precip_30d)
    try:
        from datetime import datetime as dt
        # Build set of (month, day) for the 30-day window (ignore year)
        window_md = set()
        for t in window_times:
            s = t.split("T")[0]  # YYYY-MM-DD
            parts = s.split("-")
            if len(parts) == 3:
                window_md.add((int(parts[1]), int(parts[2])))
    except Exception:
        return 0.0, None, None
    arch_times = archive_daily.get("time") or []
    arch_precip = archive_daily.get("precipitation_sum") or []
    if len(arch_times) != len(arch_precip):
        return 0.0, None, None
    # Same calendar (month, day) window for each year
    same_period_sums = []
    for y in range(start_year, end_year + 1):
        year_sum = 0.0
        for i, t in enumerate(arch_times):
            try:
                s = (t.split("T")[0] if "T" in t else t).strip()
                parts = s.split("-")
                if len(parts) != 3:
                    continue
                yr, mo, day = int(parts[0]), int(parts[1]), int(parts[2])
                if yr != y or (mo, day) not in window_md:
                    continue
                p = arch_precip[i]
                year_sum += p if p is not None else 0.0
            except Exception:
                continue
        same_period_sums.append(year_sum)
    if len(same_period_sums) < 2:
        return 0.0, None, None
    mean_p = statistics.mean(same_period_sums)
    std_p = statistics.stdev(same_period_sums)
    if std_p and std_p > 0:
        z = (actual_30d_mm - mean_p) / std_p
        return round(z, 2), round(mean_p, 1), round(std_p, 1)
    if mean_p and mean_p > 0:
        pct = actual_30d_mm / mean_p
        if pct < 0.3:
            z = -2.0
        elif pct < 0.5:
            z = -1.5
        elif pct < 0.7:
            z = -1.0
        else:
            z = -0.5 if pct < 0.9 else 0.0
        return z, round(mean_p, 1), None
    return 0.0, round(mean_p, 1) if mean_p is not None else None, None


def _z_to_drought_probability(z: float) -> float:
    """Map SPI-like z-score to drought probability (0–1)."""
    for z_thresh, prob in DROUGHT_Z_TO_PROB:
        if z <= z_thresh:
            return prob
    return 0.08


def _heatwave_probability(max_temps_7d: list[float], min_temps_7d: list[float] | None) -> tuple[float, str]:
    """
    WMO-style: consecutive days above threshold. Returns (probability, severity).
    Uses 35°C and 38°C thresholds; 40°C+ increases severity.
    """
    if not max_temps_7d:
        return 0.08, "none"
    max_temps = [t for t in max_temps_7d if t is not None]
    if not max_temps:
        return 0.08, "none"
    abs_max = max(max_temps)
    # Consecutive days >= 35°C
    consec_35 = 0
    max_consec_35 = 0
    for t in max_temps:
        if t >= 35:
            consec_35 += 1
            max_consec_35 = max(max_consec_35, consec_35)
        else:
            consec_35 = 0
    consec_38 = sum(1 for t in max_temps if t >= 38)
    if abs_max >= 40 or (consec_38 >= HEATWAVE_DAYS_38C) or max_consec_35 >= 5:
        return 0.52, "high"
    if consec_38 >= 1 or max_consec_35 >= HEATWAVE_DAYS_35C:
        return 0.35, "moderate"
    if max_consec_35 >= 2 or any(t >= 35 for t in max_temps):
        return 0.18, "low"
    return 0.08, "none"


def _flood_probability(
    precip_7d: list[float],
    precip_30d: list[float],
) -> tuple[float, str]:
    """Combine 7-day total, max 1-day intensity, and antecedent 30-day wetness."""
    precips_7 = [p for p in precip_7d if p is not None]
    precips_30 = [p for p in precip_30d if p is not None]
    total_7d = sum(precips_7) if precips_7 else 0.0
    total_30d = sum(precips_30) if precips_30 else 0.0
    max_1day = max(precips_7) if precips_7 else 0.0
    prob = 0.05
    severity = "low"
    for thresh, p in FLOOD_7D_MM:
        if total_7d >= thresh:
            prob = p
            break
    for thresh, add in FLOOD_1DAY_MM:
        if max_1day >= thresh:
            prob = min(0.65, prob + add)
            severity = "high" if max_1day >= 50 else "moderate"
            break
    if total_30d > 100 and total_7d > 30:
        prob = min(0.70, prob + 0.12)
        severity = "high" if prob >= 0.35 else "moderate"
    if prob >= 0.40:
        severity = "high"
    elif prob >= 0.15:
        severity = "moderate"
    return round(prob, 2), severity


def run(latitude: float, longitude: float) -> dict[str, Any]:
    """
    Compute drought, flood, and heatwave risk from real data with practical indices.
    - Drought: SPI-like (30-day precip vs same-period climatology).
    - Heatwave: WMO-style consecutive days above 35/38°C.
    - Flood: 7-day precip + max 1-day intensity + antecedent wetness.
    """
    logger.info("risk_tool.run(lat=%s, lon=%s)", latitude, longitude)

    forecast = _fetch_forecast_with_past(latitude, longitude, past_days=30, forecast_days=7)
    archive = _fetch_archive_years(latitude, longitude, 2019, 2023)

    daily = (forecast or {}).get("daily") or {}
    times = daily.get("time") or []
    max_temps = daily.get("temperature_2m_max") or []
    min_temps = daily.get("temperature_2m_min") or []
    precips = daily.get("precipitation_sum") or []

    # API returns past_days first, then forecast_days: [past 30..., next 7]
    n_past = 30
    n_total = len(times)
    if n_total < 37:
        n_past = min(30, max(0, n_total - 7))
    precip_30d = precips[:n_past] if n_past else []
    precip_7d = precips[n_past : n_past + 7] if len(precips) > n_past else (precips[-7:] if len(precips) >= 7 else precips)
    max_temps_7d = max_temps[n_past : n_past + 7] if len(max_temps) > n_past else (max_temps[-7:] if len(max_temps) >= 7 else max_temps)
    min_temps_7d = min_temps[n_past : n_past + 7] if len(min_temps) > n_past else (min_temps[-7:] if len(min_temps) >= 7 else None)

    actual_30d_mm = sum(p for p in precip_30d if p is not None)
    arch_daily = archive.get("daily") if archive else None

    z_score, clim_mean, clim_std = _drought_z_score(
        actual_30d_mm, times, arch_daily, 2019, 2023
    )
    drought_prob = _z_to_drought_probability(z_score)
    heatwave_prob, heatwave_severity = _heatwave_probability(max_temps_7d, min_temps_7d)
    flood_prob, flood_severity = _flood_probability(precip_7d, precip_30d)

    return {
        "tool": "risk_analysis",
        "drought_probability": round(drought_prob, 2),
        "flood_probability": round(flood_prob, 2),
        "heatwave_probability": round(heatwave_prob, 2),
        "drought_severity": "moderate" if z_score <= -1 else ("severe" if z_score <= -1.5 else "low"),
        "drought_z_score_30d": z_score,
        "drought_climatology_30d_mm": clim_mean,
        "drought_climatology_std_mm": clim_std,
        "heatwave_severity": heatwave_severity,
        "flood_severity": flood_severity,
        "precipitation_30d_mm": round(actual_30d_mm, 1),
        "precipitation_forecast_7d_mm": round(sum(p for p in precip_7d if p is not None), 1),
        "data_period": "30d_past_7d_forecast",
    }