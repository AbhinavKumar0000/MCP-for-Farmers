"""Gemini API client: true function-calling orchestration. Tools run ONLY when Gemini requests them."""

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _to_native(obj: Any) -> Any:
    """Convert numpy/pandas types to native Python for JSON and protos; avoid 'bad argument type' errors."""
    if obj is None:
        return None
    try:
        import numpy as np
        if isinstance(obj, (np.floating, np.integer, np.int64, np.int32, np.float64, np.float32)):
            return float(obj) if isinstance(obj, (np.floating, np.float64, np.float32)) else int(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, (np.str_, np.bytes_)):
            return str(obj)
        if isinstance(obj, np.ndarray):
            return [_to_native(x) for x in obj.tolist()]
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {str(k): _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)):
        return obj
    return obj


def _to_proto_safe(obj: Any) -> Any:
    """Round-trip through JSON so Gemini protos.FunctionResponse gets only primitives."""
    import json
    try:
        return json.loads(json.dumps(obj, default=_json_fallback))
    except (TypeError, ValueError):
        return _to_native(obj)


def _json_fallback(o: Any) -> Any:
    """For json.dumps: convert numpy and other non-JSON types."""
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
    return str(o)

import google.generativeai as genai

load_dotenv()
from google.generativeai import protos
from google.generativeai.types import content_types

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_CROP_LOG_PATH = _ROOT / "crop_log.csv"


def _load_crop_log_context() -> str:
    """
    Load crop reference information from crop_log.csv as a compact
    text table that Gemini can use when recommending crops.
    """
    if not _CROP_LOG_PATH.exists():
        logger.warning("crop_log.csv not found at %s", _CROP_LOG_PATH)
        return ""
    try:
        import csv
    except ImportError:
        logger.warning("csv module unavailable while reading crop_log.csv")
        return ""

    rows: list[str] = []
    try:
        with _CROP_LOG_PATH.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                crop = (row.get("Crop") or "").strip()
                zone = (row.get("Agro-Climatic Zone") or "").strip()
                soil = (row.get("Soil Type") or "").strip()
                rainfall = (row.get("Rainfall") or "").strip()
                temp = (row.get("Tempreture") or "").strip()
                humidity = (row.get("humidity_range_percent") or "").strip()
                seasons = (row.get("season_supported") or "").strip()
                risk = (row.get("risk_sensitivity") or "").strip()
                duration = (row.get("duration_days") or "").strip()
                if not crop:
                    continue
                rows.append(
                    f"- Crop: {crop}; Zone: {zone}; Soil: {soil}; "
                    f"Rainfall(mm): {rainfall}; Temp(°C): {temp}; "
                    f"Humidity(%): {humidity}; Seasons: {seasons}; "
                    f"Risk: {risk}; Duration(days): {duration}"
                )
    except Exception as e:
        logger.warning("Failed to read crop_log.csv: %s", e)
        return ""

    if not rows:
        return ""
    header = (
        "Crop reference table for Madhya Pradesh agriculture. "
        "Use this when recommending crops so that rainfall, temperature, "
        "humidity, season, soil type and risk sensitivity are realistic:\n"
    )
    return header + "\n".join(rows)

# Strict system prompt: Gemini must use function calling, not assume results.
# Anti-hallucination: soil data must come only from get_soil_by_coordinates.
SYSTEM_PROMPT = """You are an MCP orchestrator and crop-planning assistant.
You must call tools one by one.
Do not assume tool results.
Do not generate final output until all required tools are called.
You must explicitly call tools using function calling.

Soil data (district, dominant soil, soil characteristics, agricultural suitability):
- You are strictly forbidden from guessing or fabricating soil type or crop suitability.
- If soil or location-based soil information is needed, you MUST call get_soil_by_coordinates with the given latitude and longitude.
- If get_soil_by_coordinates returns an error (e.g. "Outside Madhya Pradesh"), state that exactly; do not invent soil data.
- All spatial soil data is from official district boundaries (GADM Level-2, EPSG:4326) and polygon containment only.

Crop recommendations:
- When recommending crops, you must base your reasoning on: (a) tool outputs (weather, climate, risk, season, soil) and (b) the crop reference table provided in the user message.
- Do not invent crop properties (rainfall, season, temperature, risk). Use the crop table, or clearly say when information is not available.
- Always explain briefly why each recommended crop is suitable for the current weather, season, soil and risk profile.

When you have received results from all tools you need, respond with a short final message summarizing that analysis is complete."""

# Tool declarations for Gemini (names must match TOOL_REGISTRY in app.tools)
GEMINI_TOOL_DECLARATIONS = [
    {
        "name": "weather_tool",
        "description": "Get weather forecast data for given coordinates: average/min/max temperature (C), total rainfall (mm), average humidity (%).",
        "parameters": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude of the location."},
                "longitude": {"type": "number", "description": "Longitude of the location."},
            },
            "required": ["latitude", "longitude"],
        },
    },
    {
        "name": "climate_tool",
        "description": "Get climate normals for the region: average rainfall (mm) and average temperature (C).",
        "parameters": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude of the location."},
                "longitude": {"type": "number", "description": "Longitude of the location."},
            },
            "required": ["latitude", "longitude"],
        },
    },
    {
        "name": "season_tool",
        "description": "Get current cropping season for the location: Kharif (Jun-Oct), Rabi (Nov-Mar), or Zaid (Apr-May).",
        "parameters": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude of the location."},
                "longitude": {"type": "number", "description": "Longitude of the location."},
            },
            "required": ["latitude", "longitude"],
        },
    },
    {
        "name": "risk_tool",
        "description": "Get risk analysis: drought, flood, and heatwave probabilities (0-1) for the location.",
        "parameters": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude of the location."},
                "longitude": {"type": "number", "description": "Longitude of the location."},
            },
            "required": ["latitude", "longitude"],
        },
    },
    {
        "name": "get_soil_by_coordinates",
        "description": "Returns soil information for a location in Madhya Pradesh using latitude and longitude. Use this whenever the user asks about soil type, dominant soil, soil characteristics, or crop suitability for a location. Returns district, dominant_soil, soil_characteristics, agricultural_suitability. Returns error if outside Madhya Pradesh.",
        "parameters": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude in decimal degrees (EPSG:4326)."},
                "longitude": {"type": "number", "description": "Longitude in decimal degrees (EPSG:4326)."},
            },
            "required": ["latitude", "longitude"],
        },
    },
]


def get_gemini_tool_declarations() -> list[content_types.FunctionDeclaration]:
    """Return list of FunctionDeclaration for Gemini (no callables; backend executes)."""
    return [
        content_types.FunctionDeclaration(
            name=d["name"],
            description=d["description"],
            parameters=d["parameters"],
        )
        for d in GEMINI_TOOL_DECLARATIONS
    ]


def _get_function_calls_from_response(response: Any) -> list[protos.FunctionCall]:
    """Extract all function calls from a generate_content response. Empty if model returned text only."""
    if not response.candidates:
        return []
    parts = response.candidates[0].content.parts
    return [
        part.function_call
        for part in parts
        if part and getattr(part, "function_call", None)
    ]


def _get_text_from_response(response: Any) -> str:
    """Extract text content from response if present."""
    if not response.candidates or not response.candidates[0].content.parts:
        return ""
    text_parts = [
        part.text
        for part in response.candidates[0].content.parts
        if part and getattr(part, "text", None) and part.text
    ]
    return " ".join(text_parts).strip() if text_parts else ""


class GeminiClient:
    """
    Client for Gemini API. Tools are executed ONLY when Gemini returns a function_call.
    No automatic or direct tool execution; strict tool-calling loop.
    """

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY not set. Add it to .env or pass api_key.")
        genai.configure(api_key=key)
        self._model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=SYSTEM_PROMPT,
        )
        logger.info(
            "GeminiClient initialized (tools: %s). Tools run only when Gemini requests them.",
            [d["name"] for d in GEMINI_TOOL_DECLARATIONS],
        )

    def run_tool_calling_loop(
        self,
        latitude: float,
        longitude: float,
        tool_executor: Any,
        max_rounds: int = 15,
    ) -> dict[str, Any]:
        """
        Run the proper tool-calling loop:
        User -> Gemini -> [function_call] -> Backend executes tool -> send result back -> Gemini -> ...
        Stops when Gemini returns a response with no function_call; that text is llm_final_message.
        """
        crop_context = _load_crop_log_context()
        user_prompt_parts = [
            f"Analyze the location with latitude {latitude} and longitude {longitude} in Madhya Pradesh.",
            "Use the available tools as needed: weather, climate normals, season, risk analysis, "
            "and soil (get_soil_by_coordinates for district, dominant soil and soil characteristics).",
            "Call each tool with the given latitude and longitude. For any soil or crop-suitability "
            "question, you must call get_soil_by_coordinates; do not guess.",
        ]
        if crop_context:
            user_prompt_parts.append(
                "When you recommend crops, you must use the following crop reference table; "
                "prefer crops whose rainfall, temperature, humidity, soil type, supported season and "
                "risk sensitivity match the current tool outputs:\n"
            )
            user_prompt_parts.append(crop_context)
        user_prompt_parts.append(
            "In your final answer, provide a concise summary plus a clearly formatted list of 3–6 "
            "recommended crops, each with: suitability reason, season, rainfall/temperature fit, "
            "and any important risk warnings."
        )
        user_prompt = "\n\n".join(user_prompt_parts)
        contents: list[protos.Content] = [
            protos.Content(role="user", parts=[protos.Part(text=user_prompt)])
        ]
        tools_output: dict[str, Any] = {}
        tool_execution_order: list[str] = []
        llm_final_message = ""
        gemini_tools = [content_types.Tool(function_declarations=get_gemini_tool_declarations())]

        for round_num in range(1, max_rounds + 1):
            logger.info("Tool-calling loop round %s, messages count=%s", round_num, len(contents))
            response = self._model.generate_content(
                contents=contents,
                tools=gemini_tools,
            )
            function_calls = _get_function_calls_from_response(response)

            if function_calls:
                # Gemini requested tool(s) — we execute ONLY these, no automatic execution
                response_parts = list(response.candidates[0].content.parts)
                contents.append(protos.Content(role="model", parts=response_parts))

                function_response_parts: list[protos.Part] = []
                for fc in function_calls:
                    name = fc.name
                    args = dict(fc.args) if fc.args else {}
                    lat = float(args.get("latitude", latitude))
                    lon = float(args.get("longitude", longitude))
                    logger.info("Gemini requested tool: %s", name)
                    logger.info("Arguments: %s", args)
                    tool_execution_order.append(name)
                    result = tool_executor(name, lat, lon)
                    safe = _to_native(result) if isinstance(result, dict) else _to_native({"result": result})
                    if isinstance(result, dict) and "tool" in result:
                        tools_output[result["tool"]] = safe
                    else:
                        tools_output[name] = safe
                    proto_payload = _to_proto_safe(safe)
                    function_response_parts.append(
                        protos.Part(
                            function_response=protos.FunctionResponse(
                                name=str(name),
                                response=proto_payload,
                            )
                        )
                    )
                contents.append(
                    protos.Content(role="user", parts=function_response_parts)
                )
                continue

            # No function_call — Gemini sent final message
            llm_final_message = _get_text_from_response(response)
            logger.info("Gemini returned final message (no more tool calls). Length=%s", len(llm_final_message))
            break

        return {
            "tool_execution_order": _to_native(tool_execution_order),
            "tools_output": _to_native(tools_output),
            "llm_final_message": str(llm_final_message or "(No final message from model)"),
        }
