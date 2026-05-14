"""Season tool: uses real system date to determine current cropping season (Kharif/Rabi/Zaid)."""

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Rules: Jun–Oct → Kharif, Nov–Mar → Rabi, Apr–May → Zaid (no API; real date only)
MONTH_TO_SEASON = {
    1: "Rabi",
    2: "Rabi",
    3: "Rabi",
    4: "Zaid",
    5: "Zaid",
    6: "Kharif",
    7: "Kharif",
    8: "Kharif",
    9: "Kharif",
    10: "Kharif",
    11: "Rabi",
    12: "Rabi",
}


def run(latitude: float, longitude: float) -> dict[str, Any]:
    """
    Return current month and corresponding cropping season.
    Jun–Oct → Kharif, Nov–Mar → Rabi, Apr–May → Zaid.
    """
    logger.info("season_tool.run(lat=%s, lon=%s)", latitude, longitude)
    now = datetime.utcnow()
    current_month = now.month
    current_season = MONTH_TO_SEASON.get(current_month, "Unknown")
    return {
        "tool": "season",
        "current_month": current_month,
        "current_season": current_season,
    }
