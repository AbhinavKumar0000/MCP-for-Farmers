"""Backward-compatibility shim for app/tools/.

All runtime tool logic has moved to ``mcp_server/`` and is served over the
Model Context Protocol via FastMCP (see ``mcp_server/server.py``).

This module re-exports ``soil_tool`` only because ``app/main.py`` imports it
directly for startup health checks (``initialize_soil_data``,
``get_boundary_district_count``, ``get_soil_record_count``,
``get_district_match_summary``).  Those checks still run in-process at boot
time so they remain unaffected by the MCP transport.

Do NOT add new tools here. New tools belong in ``mcp_server/``.
"""

from mcp_server import soil_tool  # noqa: F401 — re-exported for app/main.py

__all__ = ["soil_tool"]
