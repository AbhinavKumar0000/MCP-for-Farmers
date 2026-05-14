"""FastMCP server exposing AgroLLaMA agricultural intelligence tools.

This is a standards-compliant Model Context Protocol server. Any MCP
client (Claude Desktop, Cursor, our internal Gemini orchestrator, etc.)
can connect to the streamable-http endpoint at
``http://<host>:<port>/mcp`` and call the six registered tools.

All tool logic lives in sibling modules under ``mcp_server/`` and is
imported here unchanged. The functions decorated with ``@mcp.tool()``
are thin wrappers whose names, docstrings, and parameter signatures
form the public MCP contract.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server import climate_tool as _climate
from mcp_server import crop_score_tool as _crop_score
from mcp_server import risk_tool as _risk
from mcp_server import season_tool as _season
from mcp_server import soil_tool as _soil
from mcp_server import weather_tool as _weather

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8001"))


mcp = FastMCP(
    name="AgroLLaMA",
    instructions=(
        "Agricultural intelligence tools for Madhya Pradesh, India. "
        "All tools accept latitude/longitude in EPSG:4326 decimal degrees. "
        "Call weather_tool, climate_tool, season_tool, risk_tool, and "
        "get_soil_by_coordinates one-by-one, then call crop_score to obtain "
        "a deterministic ranked crop shortlist."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
)


@mcp.tool()
def weather_tool(latitude: float, longitude: float) -> dict[str, Any]:
    """Get weather forecast data for the coordinates: temperature, rainfall, humidity, and precipitation probability."""
    return _weather.run(latitude, longitude)


@mcp.tool()
def climate_tool(latitude: float, longitude: float) -> dict[str, Any]:
    """Get climate normals for the region: average rainfall, average temperature, and monthly normals."""
    return _climate.run(latitude, longitude)


@mcp.tool()
def season_tool(latitude: float, longitude: float) -> dict[str, Any]:
    """Get the current cropping season in IST: Kharif, Rabi, or Zaid."""
    return _season.run(latitude, longitude)


@mcp.tool()
def risk_tool(latitude: float, longitude: float) -> dict[str, Any]:
    """Get drought, flood, and heatwave risk analysis for the location."""
    return _risk.run(latitude, longitude)


@mcp.tool()
def get_soil_by_coordinates(latitude: float, longitude: float) -> dict[str, Any]:
    """Get district, dominant soil, soil characteristics, and agricultural suitability for a location in Madhya Pradesh."""
    return _soil.run(latitude, longitude)


@mcp.tool()
def crop_score(latitude: float, longitude: float) -> dict[str, Any]:
    """Rank the best candidate crops for the coordinates using deterministic scoring over weather, climate, soil, season, and risk evidence."""
    return _crop_score.run(latitude=latitude, longitude=longitude)


def main() -> None:
    """Run the FastMCP server with the streamable-http transport."""
    logger.info(
        "Starting AgroLLaMA MCP server on http://%s:%s/mcp (streamable-http)",
        MCP_HOST,
        MCP_PORT,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
