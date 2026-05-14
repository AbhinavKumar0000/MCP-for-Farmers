"""
Soil intelligence tool for Madhya Pradesh, India.

Performs spatial containment (point-in-polygon) on GADM Level-2 district boundaries
(EPSG:4326 / WGS84) and returns soil metadata from official district-level data.
No heuristic approximations; tool must be called for any soil query—never guess.
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Project root: app/tools/soil_tool.py -> app/tools -> app -> project root
_ROOT = Path(__file__).resolve().parent.parent.parent
GEOJSON_PATH = _ROOT / "mp_districts.geojson"
SOIL_DATA_PATH = _ROOT / "soil_fixed.json"

_gdf_cache: Any = None
_soil_by_district: dict[str, dict[str, Any]] | None = None


def _load_district_boundaries():
    """Load MP district polygons once (GeoPandas)."""
    global _gdf_cache
    if _gdf_cache is not None:
        return _gdf_cache
    try:
        import geopandas as gpd
    except ImportError:
        raise ImportError("geopandas is required for soil tool. Install with: pip install geopandas")
    if not GEOJSON_PATH.exists():
        raise FileNotFoundError(f"District boundaries not found: {GEOJSON_PATH}")
    _gdf_cache = gpd.read_file(GEOJSON_PATH)
    _gdf_cache.set_crs(4326, allow_override=True)
    logger.info("Loaded MP district boundaries: %s districts", len(_gdf_cache))
    return _gdf_cache


def _load_soil_lookup() -> dict[str, dict[str, Any]]:
    """Load district -> soil metadata lookup (from soil_fixed.json)."""
    global _soil_by_district
    if _soil_by_district is not None:
        return _soil_by_district
    if not SOIL_DATA_PATH.exists():
        raise FileNotFoundError(f"Soil data not found: {SOIL_DATA_PATH}")
    with open(SOIL_DATA_PATH, encoding="utf-8") as f:
        rows = json.load(f)
    _soil_by_district = {}
    for row in rows:
        district = (row.get("district") or "").strip()
        if district:
            _soil_by_district[district.lower()] = {
                "district": district,
                "dominant_soil": row.get("dominant_soil") or "",
                "soil_characteristics": row.get("soil_characteristics") or "",
                "agricultural_suitability": row.get("agricultural_suitability") or "",
            }
    logger.info("Loaded soil data for %s districts", len(_soil_by_district))
    return _soil_by_district


def run(latitude: float, longitude: float) -> dict[str, Any]:
    """
    Return soil information for a location in Madhya Pradesh using latitude and longitude.

    Uses spatial containment on official district boundary polygons (EPSG:4326).
    Returns structured JSON: district, dominant_soil, soil_characteristics, agricultural_suitability.
    If the point is outside MP, returns {"error": "Outside Madhya Pradesh"}.
    """
    logger.info("soil_tool.run(lat=%s, lon=%s)", latitude, longitude)
    try:
        gdf = _load_district_boundaries()
        soil_lookup = _load_soil_lookup()
    except Exception as e:
        logger.exception("Soil tool init failed: %s", e)
        return {"error": f"Soil data unavailable: {e}"}

    try:
        from shapely.geometry import Point
    except ImportError:
        return {"error": "Geospatial library unavailable (shapely)"}

    # GeoJSON / WGS84: (longitude, latitude)
    point = Point(longitude, latitude)
    if not point.is_valid:
        return {"error": "Invalid coordinates"}

    # Point-in-polygon: which district contains this point?
    mask = gdf.contains(point)
    if not mask.any():
        return {"error": "Outside Madhya Pradesh"}

    row = gdf.loc[mask].iloc[0]
    raw_name = row.get("NAME_2")
    district_name = str(raw_name).strip() if raw_name is not None and str(raw_name) != "nan" else ""
    if not district_name:
        return {"error": "District name not found for location"}

    # Match soil data (case-insensitive; allow exact and normalized)
    key = district_name.lower()
    soil = soil_lookup.get(key)
    if not soil:
        # Try matching without extra spaces
        for k, v in soil_lookup.items():
            if k.replace(" ", "") == key.replace(" ", ""):
                soil = v
                break
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
        # "agricultural_suitability": soil["agricultural_suitability"],
    }