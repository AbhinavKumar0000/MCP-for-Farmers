"""
Weather tool: accurate real data from Open-Meteo Forecast API.

Uses current + 7-day daily + optional past 7 days for recent rainfall.
Returns clear periods (current vs forecast) and precipitation probability.
"""

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def run(latitude: float, longitude: float) -> dict[str, Any]:
    """
    Fetch real weather from Open-Meteo: current conditions, 7-day forecast,
    and past 7 days for recent rainfall. Returns structured JSON with clear
    data_period labels and precipitation probability where available.
    """
    logger.info("weather_tool.run(lat=%s, lon=%s)", latitude, longitude)
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weather_code",
        "timezone": "auto",
        "forecast_days": 7,
        "past_days": 7,
    }
    try:
        resp = requests.get(OPEN_METEO_FORECAST_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.exception("Open-Meteo request failed: %s", e)
        return {
            "tool": "weather",
            "avg_temp_c": None,
            "min_temp_c": None,
            "max_temp_c": None,
            "total_rainfall_mm": None,
            "avg_humidity_percent": None,
            "error": str(e),
        }

    current = data.get("current", {})
    daily = data.get("daily", {})

    temp = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    maxs = daily.get("temperature_2m_max") or []
    mins = daily.get("temperature_2m_min") or []
    precips = daily.get("precipitation_sum") or []
    precip_probs = daily.get("precipitation_probability_max") or []

    # With past_days=7, daily arrays are [7 past + 7 future] = 14; we use last 7 as forecast
    n_daily = len(precips)
    if n_daily >= 14:
        precips_forecast = precips[7:14]
        maxs_f = maxs[7:14]
        mins_f = mins[7:14]
        precips_past = precips[:7]
    else:
        precips_forecast = precips[-7:] if len(precips) >= 7 else precips
        maxs_f = maxs[-7:] if len(maxs) >= 7 else maxs
        mins_f = mins[-7:] if len(mins) >= 7 else mins
        precips_past = precips[:7] if len(precips) >= 7 else []

    maxs_f = [t for t in maxs_f if t is not None]
    mins_f = [t for t in mins_f if t is not None]
    min_temp = min(mins_f) if mins_f else None
    max_temp = max(maxs_f) if maxs_f else None
    total_rainfall_forecast_7d = sum(p for p in precips_forecast if p is not None)
    total_rainfall_past_7d = sum(p for p in precips_past if p is not None)
    total_rainfall_mm = total_rainfall_forecast_7d + total_rainfall_past_7d  # 14-day total for context; or report both

    if temp is None and maxs_f and mins_f:
        temp = (maxs_f[0] + mins_f[0]) / 2.0
    avg_temp = temp if temp is not None else ((min_temp + max_temp) / 2.0 if (min_temp is not None and max_temp is not None) else None)
    avg_precip_prob = None
    if precip_probs:
        probs_f = precip_probs[7:14] if len(precip_probs) >= 14 else (precip_probs[-7:] if len(precip_probs) >= 7 else precip_probs)
        valid = [x for x in probs_f if x is not None]
        avg_precip_prob = round(sum(valid) / len(valid), 0) if valid else None

    return {
        "tool": "weather",
        "avg_temp_c": round(avg_temp, 1) if avg_temp is not None else None,
        "min_temp_c": round(min_temp, 1) if min_temp is not None else None,
        "max_temp_c": round(max_temp, 1) if max_temp is not None else None,
        "total_rainfall_mm": round(total_rainfall_forecast_7d, 1),
        "rainfall_past_7d_mm": round(total_rainfall_past_7d, 1),
        "avg_humidity_percent": int(humidity) if humidity is not None else None,
        "precipitation_probability_max_7d_percent": int(avg_precip_prob) if avg_precip_prob is not None else None,
        "data_period": "current_plus_7d_forecast",
    }