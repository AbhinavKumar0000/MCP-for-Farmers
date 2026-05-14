"""MCP orchestration: tools run ONLY when Gemini requests them via function_call."""

import json
import logging

from app.llm.gemini_client import GeminiClient
from app.schemas.models import AnalyzeResponse, LocationInfo
from app.tools import execute_tool

logger = logging.getLogger(__name__)


def _json_safe(obj):
    """Ensure object is JSON-serializable (native types only) to avoid 500 on response."""
    try:
        return json.loads(json.dumps(obj, default=_json_default))
    except (TypeError, ValueError):
        return obj


def _json_default(o):
    """Convert non-JSON-serializable types for json.dumps."""
    try:
        import numpy as np
        if isinstance(o, (np.integer, np.int64, np.int32)):
            return int(o)
        if isinstance(o, (np.floating, np.float64, np.float32)):
            return float(o)
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, (np.str_, np.bytes_)):
            return str(o)
    except ImportError:
        pass
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def run_analysis(latitude: float, longitude: float) -> AnalyzeResponse:
    """
    Run MCP orchestration: send lat/long to Gemini; Gemini returns function_calls;
    we execute only those tools and send results back; repeat until Gemini sends final message.
    """
    logger.info("mcp_server.run_analysis(lat=%s, lon=%s)", latitude, longitude)
    client = GeminiClient()
    result = client.run_tool_calling_loop(
        latitude=latitude,
        longitude=longitude,
        tool_executor=execute_tool,
    )
    tool_order = _json_safe(result["tool_execution_order"])
    tools_output = _json_safe(result["tools_output"])
    llm_message = result["llm_final_message"]
    if not isinstance(llm_message, str):
        llm_message = str(llm_message) if llm_message is not None else ""
    return AnalyzeResponse(
        location=LocationInfo(latitude=float(latitude), longitude=float(longitude)),
        tool_execution_order=tool_order,
        tools_output=tools_output,
        llm_final_message=llm_message,
    )
