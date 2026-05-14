"""End-to-end smoke test for POST /analyze.

Assumes both servers are running:
    python -m mcp_server.server     (port 8001)
    uvicorn app.main:app            (port 8000)

Or simply run uvicorn alone with MCP_AUTOSTART=true (default), which boots
the MCP server as a subprocess.

Exit code 0 on success, 1 on any failure.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

API_URL = os.getenv("AGROLLAMA_API_URL", "http://127.0.0.1:8000/analyze")

EXPECTED_TOOL_KEYS = {
    "weather",
    "climate_normals",
    "season",
    "risk_analysis",
    "get_soil_by_coordinates",
    "crop_score",
}


def main() -> int:
    payload = {"latitude": 23.2599, "longitude": 77.4126}
    print(f"POST {API_URL} body={payload}")
    try:
        response = httpx.post(API_URL, json=payload, timeout=180.0)
    except httpx.HTTPError as exc:
        print(f"[FAIL] HTTP error: {exc}")
        return 1

    if response.status_code != 200:
        print(f"[FAIL] status={response.status_code} body={response.text[:500]}")
        return 1

    data = response.json()
    failures = 0

    location = data.get("location") or {}
    if abs(location.get("latitude", 0) - 23.2599) < 1e-4 and abs(location.get("longitude", 0) - 77.4126) < 1e-4:
        print("[PASS] location echoed back correctly")
    else:
        print(f"[FAIL] location mismatch: {location}")
        failures += 1

    order = data.get("tool_execution_order") or []
    print(f"  tool_execution_order ({len(order)}): {order}")
    if order:
        print("[PASS] tool_execution_order is non-empty")
    else:
        print("[FAIL] tool_execution_order is empty")
        failures += 1

    tools_output = data.get("tools_output") or {}
    actual_keys = set(tools_output.keys())
    print(f"  tools_output keys: {sorted(actual_keys)}")
    missing = EXPECTED_TOOL_KEYS - actual_keys
    if not missing:
        print("[PASS] all 6 expected tools_output keys present")
    else:
        print(f"[FAIL] missing tools_output keys: {sorted(missing)}")
        failures += 1

    final_message = data.get("llm_final_message") or ""
    if isinstance(final_message, str) and final_message.strip():
        print(f"[PASS] llm_final_message present ({len(final_message)} chars)")
        print("  preview:", final_message[:200].replace("\n", " "))
    else:
        print("[FAIL] llm_final_message empty")
        failures += 1

    print()
    if failures:
        print(f"RESULT: {failures} failure(s).")
        return 1
    print("RESULT: end-to-end /analyze check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
