"""Per-tool smoke test against a running AgroLLaMA MCP server.

Usage:
    python -m mcp_server.server         # in one terminal
    python scripts/test_mcp_tools.py    # in another

The script connects to the streamable-http endpoint, lists tools, calls each
of the six tools with a Bhopal coordinate, and additionally exercises the
``get_soil_by_coordinates`` tool with a non-MP coordinate (Delhi) to verify
that the soil tool correctly reports an out-of-region error.

Exit code 0 on success, 1 on any failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8001/mcp")

BHOPAL = (23.2599, 77.4126)
DELHI = (28.6139, 77.2090)

EXPECTED_TOOLS = {
    "weather_tool",
    "climate_tool",
    "season_tool",
    "risk_tool",
    "get_soil_by_coordinates",
    "crop_score",
}


def _ok(message: str) -> None:
    print(f"  [PASS] {message}")


def _fail(message: str) -> None:
    print(f"  [FAIL] {message}")


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _parse_tool_result(result: Any) -> dict[str, Any]:
    """Extract a dict payload from an MCP CallToolResult."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP wraps non-BaseModel return values under {"result": <value>}
        if set(structured.keys()) == {"result"} and isinstance(structured["result"], dict):
            return structured["result"]
        return structured

    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"raw_text": text}

    return {}


async def run() -> int:
    failures = 0
    print(f"Connecting to MCP server: {MCP_URL}")

    try:
        async with streamablehttp_client(MCP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                _section("list_tools()")
                tools_result = await session.list_tools()
                tool_names = {tool.name for tool in tools_result.tools}
                print(f"  Discovered: {sorted(tool_names)}")
                if EXPECTED_TOOLS.issubset(tool_names):
                    _ok(f"All {len(EXPECTED_TOOLS)} expected tools present")
                else:
                    _fail(f"Missing tools: {EXPECTED_TOOLS - tool_names}")
                    failures += 1

                lat, lon = BHOPAL
                bhopal_args = {"latitude": lat, "longitude": lon}

                _section(f"weather_tool {bhopal_args}")
                result = _parse_tool_result(await session.call_tool("weather_tool", bhopal_args))
                print(f"  -> tool={result.get('tool')!r} keys={sorted(result.keys())}")
                if result.get("tool") == "weather" and "avg_temp_c" in result and "total_rainfall_mm" in result:
                    _ok("weather_tool returned expected shape")
                else:
                    _fail(f"weather_tool unexpected: {result}")
                    failures += 1

                _section(f"climate_tool {bhopal_args}")
                result = _parse_tool_result(await session.call_tool("climate_tool", bhopal_args))
                print(f"  -> tool={result.get('tool')!r} avg_temp_normal_c={result.get('avg_temp_normal_c')}")
                if result.get("tool") == "climate_normals" and "avg_temp_normal_c" in result:
                    _ok("climate_tool returned expected shape")
                else:
                    _fail(f"climate_tool unexpected: {result}")
                    failures += 1

                _section(f"season_tool {bhopal_args}")
                result = _parse_tool_result(await session.call_tool("season_tool", bhopal_args))
                print(f"  -> {result}")
                if result.get("tool") == "season" and result.get("current_season") in {"Kharif", "Rabi", "Zaid"}:
                    _ok("season_tool returned a valid season")
                else:
                    _fail(f"season_tool unexpected: {result}")
                    failures += 1

                _section(f"risk_tool {bhopal_args}")
                result = _parse_tool_result(await session.call_tool("risk_tool", bhopal_args))
                print(
                    f"  -> drought={result.get('drought_probability')} "
                    f"flood={result.get('flood_probability')} "
                    f"heatwave={result.get('heatwave_probability')}"
                )
                if (
                    result.get("tool") == "risk_analysis"
                    and "drought_probability" in result
                    and "flood_probability" in result
                    and "heatwave_probability" in result
                ):
                    _ok("risk_tool returned expected shape")
                else:
                    _fail(f"risk_tool unexpected: {result}")
                    failures += 1

                _section(f"get_soil_by_coordinates {bhopal_args} (in MP)")
                result = _parse_tool_result(await session.call_tool("get_soil_by_coordinates", bhopal_args))
                print(f"  -> district={result.get('district')!r} soil={result.get('dominant_soil')!r}")
                if result.get("district") and result.get("dominant_soil") is not None:
                    _ok("soil tool returned a district + soil")
                else:
                    _fail(f"soil tool unexpected: {result}")
                    failures += 1

                _section(f"crop_score {bhopal_args}")
                result = _parse_tool_result(await session.call_tool("crop_score", bhopal_args))
                ranked = result.get("ranked_crops") or []
                print(f"  -> {len(ranked)} ranked crops; top={ranked[0].get('crop') if ranked else None}")
                if result.get("tool") == "crop_score" and ranked and "total_score" in ranked[0]:
                    _ok("crop_score returned a ranked shortlist")
                else:
                    _fail(f"crop_score unexpected: {result}")
                    failures += 1

                delhi_args = {"latitude": DELHI[0], "longitude": DELHI[1]}
                _section(f"get_soil_by_coordinates {delhi_args} (outside MP)")
                result = _parse_tool_result(await session.call_tool("get_soil_by_coordinates", delhi_args))
                print(f"  -> {result}")
                error_text = (result.get("error") or "").lower()
                if "madhya pradesh" in error_text or "outside" in error_text:
                    _ok("soil tool correctly reports out-of-region error")
                else:
                    _fail(f"soil tool did not flag out-of-MP location: {result}")
                    failures += 1

    except Exception:
        print("\nUnhandled exception while talking to MCP server:")
        traceback.print_exc()
        return 1

    print()
    if failures:
        print(f"RESULT: {failures} failure(s).")
        return 1
    print("RESULT: all tool checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
