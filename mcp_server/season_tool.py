"""Season tool: uses IST to determine the current cropping season."""

import logging
from datetime import datetime
from typing import Any

import pytz

logger = logging.getLogger(__name__)

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

IST = pytz.timezone("Asia/Kolkata")


def run(latitude: float, longitude: float) -> dict[str, Any]:
    """Return the current month and corresponding cropping season in IST."""
    logger.info("season_tool.run(lat=%s, lon=%s)", latitude, longitude)
    now = datetime.now(IST)
    current_month = now.month
    return {
        "tool": "season",
        "current_month": current_month,
        "current_season": MONTH_TO_SEASON.get(current_month, "Unknown"),
    }
