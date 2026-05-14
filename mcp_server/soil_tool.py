"""
Soil intelligence tool for Madhya Pradesh.

District boundaries are loaded once, normalized against the processed soil
records, and indexed with GeoPandas' spatial index for fast point lookups.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DATA_DIR = _ROOT / "data" / "processed"
GEOJSON_PATH = PROCESSED_DATA_DIR / "mp_districts.geojson"
SOIL_DATA_PATH = PROCESSED_DATA_DIR / "soil_fixed.json"

KNOWN_DISTRICT_ALIASES = {
    "east nimar": "khandwa",
    "west nimar": "khargone",
    "narsimhapur": "narsinghpur",
}

_gdf_cache: Any = None
_spatial_index: Any = None
_soil_by_district: dict[str, dict[str, Any]] | None = None
_district_match_summary: dict[str, Any] | None = None
_initialization_error: Exception | None = None


def normalize_district_name(name: str) -> str:
    """Normalize district names across boundary and soil sources."""
    cleaned = re.sub(r"[^\w\s]", " ", name or "").lower()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return KNOWN_DISTRICT_ALIASES.get(cleaned, cleaned)


def _extract_geojson_names(gdf: Any) -> set[str]:
    if "NAME_2" not in gdf.columns:
        raise KeyError("GeoJSON is missing NAME_2 district names")
    return {
        str(name).strip()
        for name in gdf["NAME_2"]
        if name is not None and str(name).strip() and str(name).strip().lower() != "nan"
    }


def _build_match_summary(geojson_names: set[str], soil_names: set[str]) -> dict[str, Any]:
    normalized_geojson = {normalize_district_name(name): name for name in geojson_names}
    normalized_soil = {normalize_district_name(name): name for name in soil_names}
    unmatched_geojson = sorted(
        original for normalized, original in normalized_geojson.items() if normalized not in normalized_soil
    )
    unmatched_soil = sorted(
        original for normalized, original in normalized_soil.items() if normalized not in normalized_geojson
    )
    return {
        "geojson_district_count": len(geojson_names),
        "soil_record_count": len(soil_names),
        "matched_count": len(normalized_geojson) - len(unmatched_geojson),
        "unmatched_geojson_districts": unmatched_geojson,
        "unmatched_soil_districts": unmatched_soil,
    }


def initialize_soil_data() -> None:
    """Load boundaries, build the spatial index, and normalize soil metadata once."""
    global _gdf_cache, _spatial_index, _soil_by_district, _district_match_summary, _initialization_error
    if _gdf_cache is not None and _spatial_index is not None and _soil_by_district is not None:
        return

    try:
        import geopandas as gpd
    except ImportError as exc:
        _initialization_error = exc
        logger.exception("geopandas is required for soil tool: %s", exc)
        return

    try:
        if not GEOJSON_PATH.exists():
            raise FileNotFoundError(f"District boundaries not found: {GEOJSON_PATH}")
        if not SOIL_DATA_PATH.exists():
            raise FileNotFoundError(f"Soil data not found: {SOIL_DATA_PATH}")

        gdf = gpd.read_file(GEOJSON_PATH)
        if gdf.crs is None:
            gdf = gdf.set_crs(4326, allow_override=True)
        else:
            gdf = gdf.to_crs(4326)

        with SOIL_DATA_PATH.open(encoding="utf-8") as handle:
            rows = json.load(handle)

        soil_lookup: dict[str, dict[str, Any]] = {}
        soil_names: set[str] = set()
        for row in rows:
            district = (row.get("district") or "").strip()
            if not district:
                continue
            soil_names.add(district)
            agricultural_suitability = row.get("agricultural_suitability")
            if agricultural_suitability in (None, ""):
                agricultural_suitability = row.get("suitability") or ""
            soil_lookup[normalize_district_name(district)] = {
                "district": district,
                "dominant_soil": row.get("dominant_soil") or "",
                "soil_characteristics": row.get("soil_characteristics") or "",
                "agricultural_suitability": agricultural_suitability,
            }

        geojson_names = _extract_geojson_names(gdf)
        match_summary = _build_match_summary(geojson_names, soil_names)

        _gdf_cache = gdf
        _spatial_index = gdf.sindex
        _soil_by_district = soil_lookup
        _district_match_summary = match_summary
        _initialization_error = None

        logger.info(
            "Loaded %s MP districts, %s soil records, matched=%s",
            len(gdf),
            len(soil_lookup),
            match_summary["matched_count"],
        )
        if match_summary["unmatched_geojson_districts"]:
            logger.warning(
                "GeoJSON districts without soil matches after normalization: %s",
                ", ".join(match_summary["unmatched_geojson_districts"]),
            )
        if match_summary["unmatched_soil_districts"]:
            logger.warning(
                "Soil records without GeoJSON matches after normalization: %s",
                ", ".join(match_summary["unmatched_soil_districts"]),
            )
    except Exception as exc:
        _initialization_error = exc
        logger.exception("Soil tool initialization failed: %s", exc)


def get_district_match_summary() -> dict[str, Any]:
    """Expose district normalization stats for startup checks and diagnostics."""
    initialize_soil_data()
    return dict(_district_match_summary or {})


def get_boundary_district_count() -> int:
    """Return the number of loaded district polygons."""
    initialize_soil_data()
    return int(len(_gdf_cache)) if _gdf_cache is not None else 0


def get_soil_record_count() -> int:
    """Return the number of normalized soil records."""
    initialize_soil_data()
    return int(len(_soil_by_district or {}))


def _candidate_rows(point: Any) -> Any:
    candidate_indices = list(_spatial_index.query(point))
    if not candidate_indices:
        return _gdf_cache.iloc[0:0]
    candidates = _gdf_cache.iloc[candidate_indices]
    return candidates[candidates.geometry.apply(lambda geometry: geometry is not None and geometry.covers(point))]


def run(latitude: float, longitude: float) -> dict[str, Any]:
    """Return soil information for a point in Madhya Pradesh."""
    logger.info("soil_tool.run(lat=%s, lon=%s)", latitude, longitude)
    initialize_soil_data()
    if _initialization_error is not None:
        return {"error": f"Soil data unavailable: {_initialization_error}"}

    try:
        from shapely.geometry import Point
    except ImportError:
        return {"error": "Geospatial library unavailable (shapely)"}

    point = Point(longitude, latitude)
    if not point.is_valid:
        return {"error": "Invalid coordinates"}

    matches = _candidate_rows(point)
    if matches.empty:
        return {"error": "Outside Madhya Pradesh"}

    row = matches.iloc[0]
    district_name = str(row.get("NAME_2") or "").strip()
    if not district_name:
        return {"error": "District name not found for location"}

    soil = (_soil_by_district or {}).get(normalize_district_name(district_name))
    if not soil:
        return {
            "district": district_name,
            "dominant_soil": "",
            "soil_characteristics": "",
            "agricultural_suitability": "",
            "note": "District in MP but soil metadata not in database",
        }

    return {
        "district": soil["district"],
        "dominant_soil": soil["dominant_soil"],
        "soil_characteristics": soil["soil_characteristics"],
        "agricultural_suitability": soil["agricultural_suitability"],
    }


initialize_soil_data()
