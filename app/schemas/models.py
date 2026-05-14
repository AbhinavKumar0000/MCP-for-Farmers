"""Pydantic schemas for request/response and tool outputs."""

from typing import Any

from pydantic import BaseModel, Field


# --- Request ---
class AnalyzeRequest(BaseModel):
    """Request body for POST /analyze."""

    latitude: float = Field(..., ge=-90, le=90, description="Latitude")
    longitude: float = Field(..., ge=-180, le=180, description="Longitude")


# --- Tool output schemas (for typing; tools return dicts) ---
class WeatherOutput(BaseModel):
    tool: str = "weather"
    avg_temp_c: float
    min_temp_c: float
    max_temp_c: float
    total_rainfall_mm: float
    avg_humidity_percent: float


class ClimateNormalsOutput(BaseModel):
    tool: str = "climate_normals"
    avg_rainfall_normal_mm: float
    avg_temp_normal_c: float


class SeasonOutput(BaseModel):
    tool: str = "season"
    current_month: int
    current_season: str


class RiskAnalysisOutput(BaseModel):
    tool: str = "risk_analysis"
    drought_probability: float
    flood_probability: float
    heatwave_probability: float


# --- Final response ---
class LocationInfo(BaseModel):
    latitude: float
    longitude: float


class AnalyzeResponse(BaseModel):
    """Combined response from MCP orchestration."""

    location: LocationInfo
    tool_execution_order: list[str] = Field(
        default_factory=list,
        description="Order in which Gemini requested each tool (by name).",
    )
    tools_output: dict[str, Any] = Field(
        default_factory=dict,
        description="Outputs from each tool keyed by tool name.",
    )
    llm_final_message: str = Field(
        default="",
        description="Final message from Gemini after all tool calls.",
    )
