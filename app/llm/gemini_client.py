"""Gemini API client with MCP-backed tool execution.

Tool declarations are fetched live from the AgroLLaMA MCP server at startup
via ``list_tools()`` and converted to Gemini FunctionDeclaration objects.
Each tool call Gemini emits is forwarded to the MCP server via
``call_tool()``, and the structured response is injected back as a
FunctionResponse — preserving the exact same tool_execution_order and
tools_output shape as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import google.generativeai as genai
import pytz
from dotenv import load_dotenv
from google.generativeai import protos
from google.generativeai.types import content_types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_CROP_LOG_PATH = _ROOT / "crop_log.csv"
_IST = pytz.timezone("Asia/Kolkata")

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8001/mcp")

# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

def _to_native(obj: Any) -> Any:
    """Recursively convert numpy/exotic scalars to plain Python primitives."""
    if obj is None:
        return None
    try:
        import numpy as np
        if isinstance(obj, (np.floating, np.float64, np.float32)):
            return float(obj)
        if isinstance(obj, (np.integer, np.int64, np.int32)):
            return int(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, (np.str_, np.bytes_)):
            return str(obj)
        if isinstance(obj, np.ndarray):
            return [_to_native(item) for item in obj.tolist()]
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {str(k): _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(item) for item in obj]
    return obj


def _json_fallback(obj: Any) -> Any:
    try:
        import numpy as np
        if isinstance(obj, (np.integer, np.int64, np.int32)):
            return int(obj)
        if isinstance(obj, (np.floating, np.float64, np.float32)):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, (np.str_, np.bytes_)):
            return str(obj)
    except ImportError:
        pass
    return str(obj)


def _to_proto_safe(obj: Any) -> Any:
    """Round-trip through JSON so Gemini function responses contain only primitives."""
    try:
        return json.loads(json.dumps(obj, default=_json_fallback))
    except (TypeError, ValueError):
        return _to_native(obj)


# ---------------------------------------------------------------------------
# MCP client helpers (async, called via asyncio.run from the sync loop)
# ---------------------------------------------------------------------------

async def _mcp_list_tools_async() -> list[dict[str, Any]]:
    """Open a short-lived MCP session and return the raw tool list."""
    async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [t.model_dump() for t in result.tools]


async def _mcp_call_tool_async(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Open a short-lived MCP session, call one tool, and return a plain dict."""
    async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args)

            # FastMCP returns structured_content for dict-returning tools.
            structured = getattr(result, "structuredContent", None)
            if isinstance(structured, dict):
                # Unwrap {"result": <payload>} wrapper that FastMCP adds for
                # non-BaseModel returns.
                if set(structured.keys()) == {"result"} and isinstance(structured["result"], dict):
                    return structured["result"]
                return structured

            # Fallback: parse the first text content block.
            for block in getattr(result, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    return {"result": text, "tool": name}

            return {"error": "Empty MCP result", "tool": name}


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from synchronous code."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Gemini ← MCP schema conversion
# ---------------------------------------------------------------------------

def _strip_gemini_unsupported(schema: Any) -> Any:
    """Recursively remove keys that the Gemini FunctionDeclaration schema rejects.

    The Gemini API rejects ``title`` and ``$schema`` anywhere in the schema
    object tree (top-level and inside ``properties`` values).  FastMCP auto-
    generates these from Pydantic, so we must strip them before conversion.
    """
    _UNSUPPORTED = {"title", "$schema", "default"}
    if isinstance(schema, dict):
        return {
            k: _strip_gemini_unsupported(v)
            for k, v in schema.items()
            if k not in _UNSUPPORTED
        }
    if isinstance(schema, list):
        return [_strip_gemini_unsupported(item) for item in schema]
    return schema


def _mcp_tool_to_gemini_declaration(spec: dict[str, Any]) -> content_types.FunctionDeclaration:
    """Convert one MCP ToolInfo dict to a Gemini FunctionDeclaration."""
    input_schema = spec.get("inputSchema") or {}
    clean_schema = _strip_gemini_unsupported(input_schema)
    return content_types.FunctionDeclaration(
        name=spec["name"],
        description=spec.get("description") or "",
        parameters=clean_schema or {"type": "object", "properties": {}},
    )


def get_gemini_tool_declarations() -> list[content_types.FunctionDeclaration]:
    """Fetch live tool declarations from the MCP server and convert to Gemini format."""
    tools = _run_async(_mcp_list_tools_async())
    return [_mcp_tool_to_gemini_declaration(t) for t in tools]


# ---------------------------------------------------------------------------
# Crop context loader
# ---------------------------------------------------------------------------

def _load_crop_log_context() -> str:
    """Load crop reference rows as a compact text table for the prompt."""
    if not _CROP_LOG_PATH.exists():
        logger.warning("crop_log.csv not found at %s", _CROP_LOG_PATH)
        return ""

    import csv

    rows: list[str] = []
    try:
        with _CROP_LOG_PATH.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                crop = (row.get("Crop") or "").strip()
                if not crop:
                    continue
                rows.append(
                    f"- Crop: {crop}; Zone: {(row.get('Agro-Climatic Zone') or '').strip()}; "
                    f"Soil: {(row.get('Soil Type') or '').strip()}; "
                    f"Rainfall(mm): {(row.get('Rainfall') or '').strip()}; "
                    f"Temp(C): {(row.get('Tempreture') or '').strip()}; "
                    f"Humidity(%): {(row.get('humidity_range_percent') or '').strip()}; "
                    f"Seasons: {(row.get('season_supported') or '').strip()}; "
                    f"Risk: {(row.get('risk_sensitivity') or '').strip()}; "
                    f"Duration(days): {(row.get('duration_days') or '').strip()}; "
                    f"SowingStartWeek: {(row.get('sowing_start_week') or '').strip()}; "
                    f"SowingEndWeek: {(row.get('sowing_end_week') or '').strip()}; "
                    f"DaysToHarvest: {(row.get('days_to_harvest') or '').strip()}; "
                    f"PeakWaterWeek: {(row.get('peak_water_week') or '').strip()}"
                )
    except Exception as exc:
        logger.warning("Failed to read crop_log.csv: %s", exc)
        return ""

    if not rows:
        return ""

    return (
        "Crop reference table for Madhya Pradesh agriculture. Use it to ground crop recommendations in "
        "soil, rainfall, temperature, season, sowing window, harvest timing, and risk sensitivity:\n"
        + "\n".join(rows)
    )


# ---------------------------------------------------------------------------
# Gemini response helpers
# ---------------------------------------------------------------------------

def _get_function_calls_from_response(response: Any) -> list[protos.FunctionCall]:
    if not response.candidates:
        return []
    return [
        part.function_call
        for part in response.candidates[0].content.parts
        if part and getattr(part, "function_call", None)
    ]


def _get_text_from_response(response: Any) -> str:
    if not response.candidates or not response.candidates[0].content.parts:
        return ""
    text_parts = [
        part.text
        for part in response.candidates[0].content.parts
        if part and getattr(part, "text", None) and part.text
    ]
    return " ".join(text_parts).strip() if text_parts else ""


# ---------------------------------------------------------------------------
# System prompt (unchanged from original)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an MCP orchestrator and crop-planning assistant.
You must call tools one by one.
Do not assume tool results.
Do not generate final output until all required tools are called.
You must explicitly call tools using function calling.

Soil data:
- You are forbidden from guessing or fabricating soil type or crop suitability.
- If soil or location-based soil information is needed, you must call get_soil_by_coordinates with the given latitude and longitude.
- If get_soil_by_coordinates returns an error, state that exactly and do not invent soil data.

Crop recommendations:
- Base recommendations on tool outputs plus the crop reference table provided in the user message.
- Use sowing_start_week and sowing_end_week to determine whether a crop is currently plantable for today's date.
- Do not invent crop properties. Use the crop table, or say when information is unavailable.
- After weather, climate, season, risk, and soil are available, call crop_score to obtain a deterministic ranked shortlist before writing recommendations.
- Explain briefly why each recommended crop fits the current weather, season, soil, and risk profile.

When you have received all needed tool results, respond with a concise final message."""


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

class GeminiClient:
    """Gemini client that dispatches tool calls through the AgroLLaMA MCP server."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY not set. Add it to .env or pass api_key.")
        genai.configure(api_key=key)
        self._model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=SYSTEM_PROMPT,
        )
        # Fetch live declarations once per client instance (per request today;
        # a future optimisation could cache these at process scope).
        self._gemini_tools = [
            content_types.Tool(function_declarations=get_gemini_tool_declarations())
        ]
        logger.info(
            "GeminiClient initialised — %d tool(s) fetched from MCP server at %s.",
            len(self._gemini_tools[0].function_declarations),
            MCP_SERVER_URL,
        )

    def run_tool_calling_loop(
        self,
        latitude: float,
        longitude: float,
        max_rounds: int = 15,
    ) -> dict[str, Any]:
        """Run the Gemini tool-calling loop, dispatching every tool call via MCP."""
        now_ist = datetime.now(_IST)
        crop_context = _load_crop_log_context()

        user_prompt_parts = [
            f"Analyze the location with latitude {latitude} and longitude {longitude} in Madhya Pradesh.",
            f"Today's IST date is {now_ist.date().isoformat()} and the current ISO week is {now_ist.isocalendar().week}.",
            "Call weather_tool, climate_tool, season_tool, risk_tool, and get_soil_by_coordinates with the same latitude and longitude.",
            "After those results are available, call crop_score with the same latitude and longitude to rank the best crops.",
            "Do not guess soil, weather, climate, season, risk, or crop suitability without tool output.",
        ]
        if crop_context:
            user_prompt_parts.append(
                "When recommending crops, use the following crop reference table and sowing windows "
                "to determine whether each crop is currently plantable:\n"
            )
            user_prompt_parts.append(crop_context)
        user_prompt_parts.append(
            "In the final answer, provide a concise summary plus a list of 3-6 recommended crops "
            "with their suitability reasons, plantability timing, and risk warnings."
        )

        contents: list[protos.Content] = [
            protos.Content(role="user", parts=[protos.Part(text="\n\n".join(user_prompt_parts))])
        ]
        tools_output: dict[str, Any] = {}
        tool_execution_order: list[str] = []
        llm_final_message = ""

        for round_number in range(1, max_rounds + 1):
            logger.info(
                "Tool-calling loop round %d, messages=%d", round_number, len(contents)
            )
            response = self._model.generate_content(
                contents=contents, tools=self._gemini_tools
            )
            function_calls = _get_function_calls_from_response(response)

            if function_calls:
                contents.append(
                    protos.Content(
                        role="model",
                        parts=list(response.candidates[0].content.parts),
                    )
                )
                function_response_parts: list[protos.Part] = []

                for fc in function_calls:
                    name = fc.name
                    args = dict(fc.args) if fc.args else {}
                    lat = float(args.get("latitude", latitude))
                    lon = float(args.get("longitude", longitude))
                    logger.info("Gemini → MCP call: %s(%s, %s)", name, lat, lon)

                    tool_execution_order.append(name)

                    # Dispatch through MCP server instead of direct Python call.
                    raw = _run_async(
                        _mcp_call_tool_async(name, {"latitude": lat, "longitude": lon})
                    )
                    safe = _to_native(raw) if isinstance(raw, dict) else _to_native({"result": raw})

                    # Key tools_output by the "tool" field (e.g. "weather",
                    # "climate_normals") or fall back to the function name —
                    # identical to the original behaviour.
                    output_key = safe.get("tool", name) if isinstance(safe, dict) else name
                    tools_output[output_key] = safe

                    function_response_parts.append(
                        protos.Part(
                            function_response=protos.FunctionResponse(
                                name=str(name),
                                response=_to_proto_safe(safe),
                            )
                        )
                    )

                contents.append(
                    protos.Content(role="user", parts=function_response_parts)
                )
                continue

            llm_final_message = _get_text_from_response(response)
            logger.info(
                "Gemini returned final message. length=%d", len(llm_final_message)
            )
            break

        return {
            "tool_execution_order": _to_native(tool_execution_order),
            "tools_output": _to_native(tools_output),
            "llm_final_message": str(llm_final_message or "(No final message from model)"),
        }
