"""Weather tool backed by Open-Meteo forecast data with in-memory caching."""

import copy
import logging
import time
from typing import Any

from .open_meteo_client import OpenMeteoError, forecast_client

logger = logging.getLogger(__name__)

WEATHER_CACHE_TTL_SECONDS = 60 * 60
_WEATHER_CACHE: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}


def _cache_key(latitude: float, longitude: float) -> tuple[float, float]:
    return (round(latitude, 1), round(longitude, 1))


def _get_cached_weather(key: tuple[float, float]) -> dict[str, Any] | None:
    cached = _WEATHER_CACHE.get(key)
    if not cached:
        return None
    cached_at, payload = cached
    if (time.time() - cached_at) > WEATHER_CACHE_TTL_SECONDS:
        _WEATHER_CACHE.pop(key, None)
        return None
    logger.debug("weather_tool cache hit for %s", key)
    return copy.deepcopy(payload)


def run(latitude: float, longitude: float) -> dict[str, Any]:
    """Fetch current conditions plus recent and forecast rainfall for the location."""
    logger.info("weather_tool.run(lat=%s, lon=%s)", latitude, longitude)
    key = _cache_key(latitude, longitude)
    cached = _get_cached_weather(key)
    if cached is not None:
        return cached

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
        data = forecast_client.fetch(params)
    except OpenMeteoError as exc:
        logger.exception("Open-Meteo forecast request failed: %s", exc)
        return {
            "tool": "weather",
            "avg_temp_c": None,
            "min_temp_c": None,
            "max_temp_c": None,
            "total_rainfall_mm": None,
            "avg_humidity_percent": None,
            "error": str(exc),
        }

    current = data.get("current", {})
    daily = data.get("daily", {})

    temperature = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    max_temps = daily.get("temperature_2m_max") or []
    min_temps = daily.get("temperature_2m_min") or []
    precipitation = daily.get("precipitation_sum") or []
    precipitation_probabilities = daily.get("precipitation_probability_max") or []

    if len(precipitation) >= 14:
        forecast_precip = precipitation[7:14]
        past_precip = precipitation[:7]
        forecast_max_temps = max_temps[7:14]
        forecast_min_temps = min_temps[7:14]
    else:
        forecast_precip = precipitation[-7:] if len(precipitation) >= 7 else precipitation
        past_precip = precipitation[:7] if len(precipitation) >= 7 else []
        forecast_max_temps = max_temps[-7:] if len(max_temps) >= 7 else max_temps
        forecast_min_temps = min_temps[-7:] if len(min_temps) >= 7 else min_temps

    valid_max_temps = [value for value in forecast_max_temps if value is not None]
    valid_min_temps = [value for value in forecast_min_temps if value is not None]
    min_temp = min(valid_min_temps) if valid_min_temps else None
    max_temp = max(valid_max_temps) if valid_max_temps else None
    total_rainfall_forecast = sum(value for value in forecast_precip if value is not None)
    total_rainfall_past = sum(value for value in past_precip if value is not None)

    if temperature is None and valid_max_temps and valid_min_temps:
        temperature = (valid_max_temps[0] + valid_min_temps[0]) / 2.0
    avg_temp = (
        temperature
        if temperature is not None
        else ((min_temp + max_temp) / 2.0 if min_temp is not None and max_temp is not None else None)
    )

    avg_precip_probability = None
    if precipitation_probabilities:
        forecast_probs = (
            precipitation_probabilities[7:14]
            if len(precipitation_probabilities) >= 14
            else (precipitation_probabilities[-7:] if len(precipitation_probabilities) >= 7 else precipitation_probabilities)
        )
        valid_probs = [value for value in forecast_probs if value is not None]
        if valid_probs:
            avg_precip_probability = round(sum(valid_probs) / len(valid_probs), 0)

    result = {
        "tool": "weather",
        "avg_temp_c": round(avg_temp, 1) if avg_temp is not None else None,
        "min_temp_c": round(min_temp, 1) if min_temp is not None else None,
        "max_temp_c": round(max_temp, 1) if max_temp is not None else None,
        "total_rainfall_mm": round(total_rainfall_forecast, 1),
        "rainfall_past_7d_mm": round(total_rainfall_past, 1),
        "avg_humidity_percent": int(humidity) if humidity is not None else None,
        "precipitation_probability_max_7d_percent": int(avg_precip_probability) if avg_precip_probability is not None else None,
        "data_period": "current_plus_7d_forecast",
    }
    _WEATHER_CACHE[key] = (time.time(), copy.deepcopy(result))
    return result
