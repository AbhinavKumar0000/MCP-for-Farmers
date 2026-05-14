"""Deterministic crop scoring based on weather, climate, soil, season, and risk evidence."""

import csv
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

from . import climate_tool, risk_tool, season_tool, soil_tool, weather_tool

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_CROP_LOG_PATH = _ROOT / "crop_log.csv"
_CROP_ROWS_CACHE: list[dict[str, str]] | None = None

IST = pytz.timezone("Asia/Kolkata")
SEASON_MONTHS = {
    "Kharif": [6, 7, 8, 9, 10],
    "Rabi": [11, 12, 1, 2, 3],
    "Zaid": [4, 5],
}
SOIL_KEYWORDS = {
    "black": {"black", "cotton"},
    "alluvial": {"alluvial"},
    "red": {"red"},
    "yellow": {"yellow"},
    "loam": {"loam"},
    "clay": {"clay"},
    "sandy": {"sand", "sandy"},
    "laterite": {"laterite"},
}
RISK_SENSITIVITY_WEIGHTS = {
    "high": 1.0,
    "medium": 0.65,
    "low": 0.35,
}
RISK_FIELD_ALIASES = {
    "drought": "drought_probability",
    "flood": "flood_probability",
    "waterlogging": "flood_probability",
    "heat": "heatwave_probability",
    "heatwave": "heatwave_probability",
}


def _load_crop_rows() -> list[dict[str, str]]:
    global _CROP_ROWS_CACHE
    if _CROP_ROWS_CACHE is not None:
        return _CROP_ROWS_CACHE
    with _CROP_LOG_PATH.open(encoding="utf-8", newline="") as handle:
        _CROP_ROWS_CACHE = list(csv.DictReader(handle))
    return _CROP_ROWS_CACHE


def _parse_numeric_range(value: str) -> tuple[float, float] | None:
    matches = re.findall(r"\d+(?:\.\d+)?", value or "")
    if not matches:
        return None
    numbers = [float(match) for match in matches]
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return min(numbers[0], numbers[1]), max(numbers[0], numbers[1])


def _score_against_range(value: float | None, expected_range: tuple[float, float] | None) -> float:
    if value is None or expected_range is None:
        return 0.5
    lower, upper = expected_range
    if lower > upper:
        lower, upper = upper, lower
    if lower <= value <= upper:
        return 1.0
    span = max(upper - lower, 1.0)
    distance = lower - value if value < lower else value - upper
    return round(max(0.0, 1.0 - (distance / span)), 3)


def _split_seasons(value: str) -> list[str]:
    raw_parts = re.split(r"[\/,]", value or "")
    seasons = [part.strip() for part in raw_parts if part and part.strip()]
    return seasons or ["Annual"]


def _parse_week_tokens(value: str) -> list[int]:
    return [int(token) for token in re.findall(r"\d+", value or "")]


def _parse_sowing_windows(row: dict[str, str]) -> list[tuple[str, int, int]]:
    seasons = _split_seasons(row.get("season_supported", ""))
    starts = _parse_week_tokens(row.get("sowing_start_week", ""))
    ends = _parse_week_tokens(row.get("sowing_end_week", ""))

    if not starts or not ends:
        return []

    if len(starts) == len(seasons) and len(ends) == len(seasons):
        return [(season, starts[index], ends[index]) for index, season in enumerate(seasons)]

    start = starts[0]
    end = ends[0]
    return [(season, start, end) for season in seasons]


def _parse_peak_water_weeks(row: dict[str, str]) -> list[int]:
    return _parse_week_tokens(row.get("peak_water_week", ""))


def _week_distance(current_week: int, start_week: int, end_week: int) -> int:
    if start_week <= current_week <= end_week:
        return 0
    return min(abs(current_week - start_week), abs(current_week - end_week))


def _seasonal_rainfall_estimate(climate: dict[str, Any], season_name: str) -> float | None:
    monthly_rainfall = climate.get("monthly_avg_rainfall_mm") or {}
    months = SEASON_MONTHS.get(season_name)
    if not months:
        average = climate.get("avg_rainfall_normal_mm")
        return float(average) * 12 if average is not None else None

    values = []
    for month in months:
        value = monthly_rainfall.get(month)
        if value is None:
            value = monthly_rainfall.get(str(month))
        if value is not None:
            values.append(value)
    if not values:
        average = climate.get("avg_rainfall_normal_mm")
        return float(average) * len(months) if average is not None else None
    return round(sum(float(value) for value in values), 1)


def _current_temperature(weather: dict[str, Any], climate: dict[str, Any], current_month: int) -> float | None:
    if weather.get("avg_temp_c") is not None:
        return float(weather["avg_temp_c"])
    monthly_temps = climate.get("monthly_avg_temp_c") or {}
    monthly_temp = monthly_temps.get(current_month)
    if monthly_temp is None:
        monthly_temp = monthly_temps.get(str(current_month))
    if monthly_temp is not None:
        return float(monthly_temp)
    if climate.get("avg_temp_normal_c") is not None:
        return float(climate["avg_temp_normal_c"])
    return None


def _extract_soil_categories(text: str) -> set[str]:
    lowered = (text or "").lower()
    categories: set[str] = set()
    for category, keywords in SOIL_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            categories.add(category)
    return categories


def _score_soil_type_match(crop_soil_text: str, dominant_soil_text: str) -> float:
    crop_categories = _extract_soil_categories(crop_soil_text)
    soil_categories = _extract_soil_categories(dominant_soil_text)
    if not crop_categories or not soil_categories:
        return 0.5
    overlap = crop_categories & soil_categories
    if not overlap:
        return 0.1
    return round(min(1.0, 0.6 + (0.4 * len(overlap) / len(crop_categories))), 3)


def _season_alignment_score(row: dict[str, str], current_season: str, current_week: int) -> tuple[float, bool]:
    supported_seasons = _split_seasons(row.get("season_supported", ""))
    windows = _parse_sowing_windows(row)

    if "Annual" in supported_seasons:
        if not windows:
            return 0.6, True
        for _, start_week, end_week in windows:
            if start_week <= current_week <= end_week:
                return 0.85, True
        return 0.25, False

    if current_season not in supported_seasons:
        return 0.0, False

    relevant_windows = [window for window in windows if window[0] == current_season]
    if not relevant_windows:
        return 0.75, True

    for _, start_week, end_week in relevant_windows:
        if start_week <= current_week <= end_week:
            return 1.0, True

    nearest_distance = min(_week_distance(current_week, start_week, end_week) for _, start_week, end_week in relevant_windows)
    if nearest_distance <= 2:
        return 0.65, False
    return 0.35, False


def _risk_penalty_score(row: dict[str, str], risk: dict[str, Any], current_week: int) -> float:
    sensitivity_text = row.get("risk_sensitivity") or ""
    peak_weeks = _parse_peak_water_weeks(row)
    peak_multiplier = 1.15 if any(abs(current_week - peak_week) <= 2 for peak_week in peak_weeks) else 1.0

    if not sensitivity_text.strip():
        baseline_penalty = (
            float(risk.get("drought_probability") or 0)
            + float(risk.get("flood_probability") or 0)
            + float(risk.get("heatwave_probability") or 0)
        ) / 3
        return round(max(0.0, 1.0 - (baseline_penalty * 0.3 * peak_multiplier)), 3)

    total_penalty = 0.0
    for part in sensitivity_text.split(","):
        hazard_text, _, level_text = part.partition(":")
        hazard = hazard_text.strip().lower()
        level = level_text.strip().lower()
        if not hazard or not level:
            continue
        risk_field = RISK_FIELD_ALIASES.get(hazard)
        if not risk_field:
            continue
        probability = float(risk.get(risk_field) or 0.0)
        sensitivity_weight = RISK_SENSITIVITY_WEIGHTS.get(level, 0.5)
        total_penalty += probability * sensitivity_weight * peak_multiplier

    return round(max(0.0, 1.0 - min(total_penalty, 1.0)), 3)


def _build_evidence(latitude: float | None, longitude: float | None, bundle: dict[str, Any]) -> dict[str, Any]:
    evidence = dict(bundle)
    if all(evidence.get(key) is not None for key in ("weather", "climate", "soil", "risk", "season")):
        return evidence
    if latitude is None or longitude is None:
        raise ValueError("crop_score requires either a full evidence bundle or latitude/longitude")

    if evidence.get("weather") is None:
        evidence["weather"] = weather_tool.run(latitude, longitude)
    if evidence.get("climate") is None:
        evidence["climate"] = climate_tool.run(latitude, longitude)
    if evidence.get("soil") is None:
        evidence["soil"] = soil_tool.run(latitude, longitude)
    if evidence.get("risk") is None:
        evidence["risk"] = risk_tool.run(latitude, longitude)
    if evidence.get("season") is None:
        evidence["season"] = season_tool.run(latitude, longitude)
    return evidence


def run(
    latitude: float | None = None,
    longitude: float | None = None,
    weather: dict[str, Any] | None = None,
    climate: dict[str, Any] | None = None,
    soil: dict[str, Any] | None = None,
    risk: dict[str, Any] | None = None,
    season: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rank crops from crop_log.csv using a simple deterministic scoring model."""
    logger.info("crop_score_tool.run(lat=%s, lon=%s)", latitude, longitude)
    evidence = _build_evidence(
        latitude,
        longitude,
        {
            "weather": weather,
            "climate": climate,
            "soil": soil,
            "risk": risk,
            "season": season,
        },
    )

    now = datetime.now(IST)
    current_week = now.isocalendar().week
    current_month = int(evidence["season"].get("current_month") or now.month)
    current_season = evidence["season"].get("current_season") or "Unknown"
    dominant_soil = evidence["soil"].get("dominant_soil", "") if isinstance(evidence["soil"], dict) else ""

    ranked_crops: list[dict[str, Any]] = []
    for row in _load_crop_rows():
        rainfall_requirement = _parse_numeric_range(row.get("Rainfall", ""))
        temperature_requirement = _parse_numeric_range(row.get("Tempreture", ""))

        rainfall_match = _score_against_range(
            _seasonal_rainfall_estimate(evidence["climate"], current_season),
            rainfall_requirement,
        )
        temperature_match = _score_against_range(
            _current_temperature(evidence["weather"], evidence["climate"], current_month),
            temperature_requirement,
        )
        soil_type_match = _score_soil_type_match(row.get("Soil Type", ""), dominant_soil)
        season_alignment, plantable_now = _season_alignment_score(row, current_season, current_week)
        risk_penalty = _risk_penalty_score(row, evidence["risk"], current_week)

        total_score = round(
            (0.20 * rainfall_match)
            + (0.15 * temperature_match)
            + (0.20 * soil_type_match)
            + (0.30 * season_alignment)
            + (0.15 * risk_penalty),
            3,
        )

        ranked_crops.append(
            {
                "crop": row.get("Crop", ""),
                "season_supported": row.get("season_supported", ""),
                "plantable_now": plantable_now,
                "total_score": total_score,
                "scores": {
                    "rainfall_match": round(rainfall_match, 3),
                    "temperature_match": round(temperature_match, 3),
                    "soil_type_match": round(soil_type_match, 3),
                    "season_alignment": round(season_alignment, 3),
                    "risk_penalty": round(risk_penalty, 3),
                },
                "sowing_window": {
                    "start_week": row.get("sowing_start_week", ""),
                    "end_week": row.get("sowing_end_week", ""),
                },
                "days_to_harvest": row.get("days_to_harvest", ""),
                "peak_water_week": row.get("peak_water_week", ""),
            }
        )

    ranked_crops.sort(key=lambda item: (-item["total_score"], item["crop"]))
    top_crops = ranked_crops[:6]

    return {
        "tool": "crop_score",
        "evaluation_date_ist": now.date().isoformat(),
        "current_week": current_week,
        "current_season": current_season,
        "district": evidence["soil"].get("district") if isinstance(evidence["soil"], dict) else None,
        "ranked_crops": top_crops,
    }
