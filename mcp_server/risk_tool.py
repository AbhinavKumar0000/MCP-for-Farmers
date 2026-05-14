"""
Risk assessment tool using Open-Meteo data.

- Drought: SPI-1 derived from 2019-2023 monthly precipitation distributions.
- Heatwave: consecutive hot-day thresholds.
- Flood: 7-day forecast rainfall plus antecedent wetness.
"""

import logging
import statistics
from collections import defaultdict
from typing import Any

from scipy.stats import gamma, norm

from .open_meteo_client import OpenMeteoError, archive_client, forecast_client

logger = logging.getLogger(__name__)

SPI_TO_DROUGHT_PROBABILITY = [
    (-2.0, 0.70),
    (-1.5, 0.55),
    (-1.0, 0.22),
]

HEATWAVE_DAYS_35C = 3
HEATWAVE_DAYS_38C = 2
FLOOD_7D_MM = [(200, 0.50), (150, 0.38), (100, 0.28), (50, 0.15), (20, 0.08)]
FLOOD_1DAY_MM = [(80, 0.35), (50, 0.22), (30, 0.12)]


def _fetch_forecast_with_past(
    latitude: float,
    longitude: float,
    past_days: int = 30,
    forecast_days: int = 7,
) -> dict[str, Any] | None:
    """Fetch daily forecast plus the requested number of trailing past days."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        "timezone": "auto",
        "past_days": past_days,
        "forecast_days": forecast_days,
    }
    try:
        return forecast_client.fetch(params)
    except OpenMeteoError as exc:
        logger.warning("Forecast request failed in risk_tool: %s", exc)
        return None


def _fetch_archive_years(
    latitude: float,
    longitude: float,
    start_year: int = 2019,
    end_year: int = 2023,
) -> dict[str, Any] | None:
    """Fetch archive precipitation used to build the SPI-1 reference distribution."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": f"{start_year}-01-01",
        "end_date": f"{end_year}-12-31",
        "daily": "precipitation_sum",
    }
    try:
        return archive_client.fetch(params)
    except OpenMeteoError as exc:
        logger.warning("Archive request failed in risk_tool: %s", exc)
        return None


def _build_monthly_precip_totals(archive_daily: dict[str, Any] | None) -> dict[int, list[float]]:
    """Aggregate archive daily rainfall into monthly totals keyed by calendar month."""
    if not archive_daily:
        return {}

    times = archive_daily.get("time") or []
    precip = archive_daily.get("precipitation_sum") or []
    if len(times) != len(precip):
        return {}

    totals_by_year_month: dict[tuple[int, int], float] = defaultdict(float)
    for index, timestamp in enumerate(times):
        try:
            date_text = str(timestamp).split("T")[0]
            year_text, month_text, _ = date_text.split("-")
            key = (int(year_text), int(month_text))
        except ValueError:
            continue
        totals_by_year_month[key] += precip[index] or 0.0

    monthly_totals: dict[int, list[float]] = defaultdict(list)
    for (_, month), total in sorted(totals_by_year_month.items()):
        monthly_totals[month].append(round(total, 3))
    return dict(monthly_totals)


def _reference_month(times: list[str], n_past: int) -> int | None:
    if not times or n_past <= 0:
        return None
    try:
        date_text = str(times[n_past - 1]).split("T")[0]
        return int(date_text.split("-")[1])
    except (IndexError, ValueError):
        return None


def _calculate_spi_1(
    actual_30d_mm: float,
    times: list[str],
    archive_daily: dict[str, Any] | None,
    n_past: int,
) -> tuple[float, float | None, float | None]:
    """
    Approximate SPI-1 from a 30-day observed precipitation total.

    The observed 30-day total is scored against the fitted gamma distribution
    for the current calendar month built from 2019-2023 monthly totals.
    """
    month = _reference_month(times, n_past)
    monthly_totals = _build_monthly_precip_totals(archive_daily)
    historical_values = monthly_totals.get(month or 0, [])
    if len(historical_values) < 2:
        return 0.0, None, None

    mean_value = statistics.mean(historical_values)
    std_value = statistics.stdev(historical_values) if len(historical_values) > 1 else None
    zero_probability = sum(1 for value in historical_values if value <= 0) / len(historical_values)
    positive_values = [value for value in historical_values if value > 0]

    if not positive_values:
        spi = -2.5 if actual_30d_mm <= 0 else 0.0
        return (
            float(round(spi, 2)),
            float(round(mean_value, 1)),
            float(round(std_value, 1)) if std_value is not None else None,
        )

    try:
        if len(set(round(value, 6) for value in positive_values)) < 2:
            raise ValueError("Not enough variability for gamma fit")
        shape, loc, scale = gamma.fit(positive_values, floc=0)
        gamma_cdf = gamma.cdf(max(actual_30d_mm, 0.0), shape, loc=loc, scale=scale)
        cdf = zero_probability + ((1 - zero_probability) * gamma_cdf)
    except Exception:
        rank = sum(1 for value in historical_values if value <= actual_30d_mm)
        cdf = rank / (len(historical_values) + 1)

    cdf = min(max(cdf, 1e-6), 1 - 1e-6)
    spi = float(norm.ppf(cdf))
    return (
        float(round(spi, 2)),
        float(round(mean_value, 1)),
        float(round(std_value, 1)) if std_value is not None else None,
    )


def _spi_to_drought_probability(spi: float) -> float:
    for threshold, probability in SPI_TO_DROUGHT_PROBABILITY:
        if spi < threshold:
            return probability
    return 0.08


def _drought_severity_from_spi(spi: float) -> str:
    if spi < -2.0:
        return "severe"
    if spi < -1.5:
        return "moderate"
    if spi < -1.0:
        return "mild"
    return "normal"


def _heatwave_probability(
    max_temps_7d: list[float],
    min_temps_7d: list[float] | None,
) -> tuple[float, str]:
    """Score heatwave likelihood from consecutive hot days."""
    del min_temps_7d
    if not max_temps_7d:
        return 0.08, "none"

    max_temps = [temp for temp in max_temps_7d if temp is not None]
    if not max_temps:
        return 0.08, "none"

    absolute_max = max(max_temps)
    consecutive_35 = 0
    max_consecutive_35 = 0
    for temp in max_temps:
        if temp >= 35:
            consecutive_35 += 1
            max_consecutive_35 = max(max_consecutive_35, consecutive_35)
        else:
            consecutive_35 = 0
    consecutive_38 = sum(1 for temp in max_temps if temp >= 38)

    if absolute_max >= 40 or consecutive_38 >= HEATWAVE_DAYS_38C or max_consecutive_35 >= 5:
        return 0.52, "high"
    if consecutive_38 >= 1 or max_consecutive_35 >= HEATWAVE_DAYS_35C:
        return 0.35, "moderate"
    if max_consecutive_35 >= 2 or any(temp >= 35 for temp in max_temps):
        return 0.18, "low"
    return 0.08, "none"


def _flood_probability(
    precip_7d: list[float],
    precip_30d: list[float],
) -> tuple[float, str]:
    """Combine 7-day rainfall, single-day intensity, and antecedent wetness."""
    forecast_precip = [value for value in precip_7d if value is not None]
    recent_precip = [value for value in precip_30d if value is not None]
    total_7d = sum(forecast_precip) if forecast_precip else 0.0
    total_30d = sum(recent_precip) if recent_precip else 0.0
    max_1day = max(forecast_precip) if forecast_precip else 0.0

    probability = 0.05
    severity = "low"
    for threshold, base_probability in FLOOD_7D_MM:
        if total_7d >= threshold:
            probability = base_probability
            break

    for threshold, increment in FLOOD_1DAY_MM:
        if max_1day >= threshold:
            probability = min(0.65, probability + increment)
            severity = "high" if max_1day >= 50 else "moderate"
            break

    if total_30d > 100 and total_7d > 30:
        probability = min(0.70, probability + 0.12)
        severity = "high" if probability >= 0.35 else "moderate"

    if probability >= 0.40:
        severity = "high"
    elif probability >= 0.15:
        severity = "moderate"

    return round(probability, 2), severity


def run(latitude: float, longitude: float) -> dict[str, Any]:
    """
    Compute drought, flood, and heatwave risks from Open-Meteo data.

    The response schema is unchanged, but drought probability and severity now
    come from an SPI-1 calculation instead of the old heuristic.
    """
    logger.info("risk_tool.run(lat=%s, lon=%s)", latitude, longitude)

    forecast = _fetch_forecast_with_past(latitude, longitude, past_days=30, forecast_days=7)
    archive = _fetch_archive_years(latitude, longitude, 2019, 2023)

    daily = (forecast or {}).get("daily") or {}
    times = daily.get("time") or []
    max_temps = daily.get("temperature_2m_max") or []
    min_temps = daily.get("temperature_2m_min") or []
    precipitation = daily.get("precipitation_sum") or []

    n_total = len(times)
    n_past = 30
    if n_total < 37:
        n_past = min(30, max(0, n_total - 7))

    precip_30d = precipitation[:n_past] if n_past else []
    precip_7d = precipitation[n_past : n_past + 7] if len(precipitation) > n_past else (
        precipitation[-7:] if len(precipitation) >= 7 else precipitation
    )
    max_temps_7d = max_temps[n_past : n_past + 7] if len(max_temps) > n_past else (
        max_temps[-7:] if len(max_temps) >= 7 else max_temps
    )
    min_temps_7d = min_temps[n_past : n_past + 7] if len(min_temps) > n_past else (
        min_temps[-7:] if len(min_temps) >= 7 else None
    )

    actual_30d_mm = sum(value for value in precip_30d if value is not None)
    archive_daily = archive.get("daily") if archive else None
    spi_score, climatology_mean, climatology_std = _calculate_spi_1(
        actual_30d_mm,
        times,
        archive_daily,
        n_past,
    )

    drought_probability = _spi_to_drought_probability(spi_score)
    drought_severity = _drought_severity_from_spi(spi_score)
    heatwave_probability, heatwave_severity = _heatwave_probability(max_temps_7d, min_temps_7d)
    flood_probability, flood_severity = _flood_probability(precip_7d, precip_30d)

    return {
        "tool": "risk_analysis",
        "drought_probability": round(drought_probability, 2),
        "flood_probability": round(flood_probability, 2),
        "heatwave_probability": round(heatwave_probability, 2),
        "drought_severity": drought_severity,
        "drought_z_score_30d": spi_score,
        "drought_climatology_30d_mm": climatology_mean,
        "drought_climatology_std_mm": climatology_std,
        "heatwave_severity": heatwave_severity,
        "flood_severity": flood_severity,
        "precipitation_30d_mm": round(actual_30d_mm, 1),
        "precipitation_forecast_7d_mm": round(sum(value for value in precip_7d if value is not None), 1),
        "data_period": "30d_past_7d_forecast",
    }
