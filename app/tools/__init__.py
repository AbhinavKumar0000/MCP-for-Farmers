"""Tools package: registry and dispatcher for MCP tools."""

from typing import Any, Callable

from app.tools import climate_tool, risk_tool, season_tool, soil_tool, weather_tool

# Map tool name (as used by Gemini function_call) to Python tool run function
TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "weather_tool": weather_tool.run,
    "climate_tool": climate_tool.run,
    "season_tool": season_tool.run,
    "risk_tool": risk_tool.run,
    "get_soil_by_coordinates": soil_tool.run,
}


def execute_tool(name: str, latitude: float, longitude: float) -> dict[str, Any]:
    """Execute a tool by name with lat/long; return its JSON output."""
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        raise ValueError(f"Unknown tool: {name}")
    return fn(latitude, longitude)
