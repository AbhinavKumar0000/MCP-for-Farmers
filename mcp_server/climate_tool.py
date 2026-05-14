"""Climate normals tool backed by Open-Meteo archive data with caching."""

import copy
import logging
import statistics
import time
from typing import Any

from .open_meteo_client import OpenMeteoError, archive_client

logger = logging.getLogger(__name__)

NORMALS_START = "2014-01-01"
NORMALS_END = "2023-12-31"
CLIMATE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_CLIMATE_CACHE: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}


def _cache_key(latitude: float, longitude: float) -> tuple[float, float]:
    return (round(latitude, 1), round(longitude, 1))


def _get_cached_climate(key: tuple[float, float]) -> dict[str, Any] | None:
    cached = _CLIMATE_CACHE.get(key)
    if not cached:
        return None
    cached_at, payload = cached
    if (time.time() - cached_at) > CLIMATE_CACHE_TTL_SECONDS:
        _CLIMATE_CACHE.pop(key, None)
        return None
    logger.debug("climate_tool cache hit for %s", key)
    return copy.deepcopy(payload)


def run(latitude: float, longitude: float) -> dict[str, Any]:
    """Fetch long-term climate normals and monthly aggregates for the location."""
    logger.info("climate_tool.run(lat=%s, lon=%s)", latitude, longitude)
    key = _cache_key(latitude, longitude)
    cached = _get_cached_climate(key)
    if cached is not None:
        return cached

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": NORMALS_START,
        "end_date": NORMALS_END,
        "daily": "temperature_2m_mean,precipitation_sum",
    }

    try:
        data = archive_client.fetch(params)
    except OpenMeteoError as exc:
        logger.exception("Open-Meteo archive request failed: %s", exc)
        return {
            "tool": "climate_normals",
            "avg_rainfall_normal_mm": None,
            "avg_temp_normal_c": None,
            "error": str(exc),
        }

    daily = data.get("daily", {})
    temps_raw = daily.get("temperature_2m_mean") or []
    precipitation_raw = daily.get("precipitation_sum") or []
    times = daily.get("time") or []

    temps = [value for value in temps_raw if value is not None]
    precipitation = [value for value in precipitation_raw if value is not None]

    avg_temp = sum(temps) / len(temps) if temps else None
    total_precipitation = sum(precipitation)
    n_days = len(precipitation_raw)
    n_months = n_days / 30.44 if n_days else 0
    avg_rainfall_per_month = total_precipitation / n_months if n_months > 0 else None

    monthly_temp: dict[int, list[float]] = {month: [] for month in range(1, 13)}
    monthly_precip: dict[int, list[float]] = {month: [] for month in range(1, 13)}

    for index, timestamp in enumerate(times):
        try:
            date_text = str(timestamp).split("T")[0]
            _, month_text, _ = date_text.split("-")
            month = int(month_text)
        except ValueError:
            continue
        if not 1 <= month <= 12:
            continue
        if index < len(temps_raw) and temps_raw[index] is not None:
            monthly_temp[month].append(temps_raw[index])
        if index < len(precipitation_raw) and precipitation_raw[index] is not None:
            monthly_precip[month].append(precipitation_raw[index])

    monthly_avg_temp = {
        month: round(statistics.mean(values), 1) if values else None
        for month, values in monthly_temp.items()
    }
    monthly_avg_precip = {}
    for month, values in monthly_precip.items():
        if values:
            monthly_avg_precip[month] = round(statistics.mean(values) * 30.44, 1)
        else:
            monthly_avg_precip[month] = None

    result = {
        "tool": "climate_normals",
        "avg_rainfall_normal_mm": round(avg_rainfall_per_month, 1) if avg_rainfall_per_month is not None else None,
        "avg_temp_normal_c": round(avg_temp, 1) if avg_temp is not None else None,
        "temp_std_dev_c": round(statistics.stdev(temps), 1) if len(temps) > 1 else None,
        "precipitation_std_dev_mm": round(statistics.stdev(precipitation), 1) if len(precipitation) > 1 else None,
        "monthly_avg_temp_c": monthly_avg_temp,
        "monthly_avg_rainfall_mm": monthly_avg_precip,
        "data_period_years": "2014-2023",
    }
    _CLIMATE_CACHE[key] = (time.time(), copy.deepcopy(result))
    return result
