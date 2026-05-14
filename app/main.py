"""FastAPI entrypoint for AgroLLaMA.

On startup:
1.  Validates runtime configuration (API key, data assets, soil index).
2.  Launches the AgroLLaMA MCP server (``mcp_server/server.py``) as a
    subprocess on port 8001 — unless ``MCP_AUTOSTART=false``, in which case
    the server must already be running externally.
3.  Probes the MCP endpoint until it responds, then proceeds.

On shutdown the subprocess is terminated cleanly (CTRL_BREAK on Windows,
SIGTERM elsewhere).

Set ``MCP_AUTOSTART=false`` when running ``uvicorn --reload`` (to avoid
spawning a new MCP process on every code reload) and start the MCP server
manually in a second terminal:

    python -m mcp_server.server
"""

from __future__ import annotations

import atexit
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.orchestrator import run_analysis
from app.schemas.models import AnalyzeRequest, AnalyzeResponse
from app.tools import soil_tool

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
MP_GEOJSON_PATH = PROCESSED_DATA_DIR / "mp_districts.geojson"
SOIL_FIXED_PATH = PROCESSED_DATA_DIR / "soil_fixed.json"

# ---------------------------------------------------------------------------
# MCP subprocess configuration
# ---------------------------------------------------------------------------
MCP_AUTOSTART: bool = os.getenv("MCP_AUTOSTART", "true").lower() not in ("false", "0", "no")
MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8001/mcp")
MCP_PROBE_TIMEOUT: float = float(os.getenv("MCP_PROBE_TIMEOUT", "30"))

_mcp_proc: subprocess.Popen | None = None  # type: ignore[type-arg]


def _start_mcp_subprocess() -> "subprocess.Popen[bytes]":
    """Launch the MCP server as a child process."""
    kwargs: dict = {
        "cwd": str(PROJECT_ROOT),
    }
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP lets us send CTRL_BREAK_EVENT on Windows.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.server"],
        **kwargs,
    )
    logger.info("MCP server subprocess started (pid=%d).", proc.pid)
    return proc


def _probe_mcp_ready(timeout: float = MCP_PROBE_TIMEOUT) -> bool:
    """Return True once the MCP endpoint responds (any HTTP status is fine)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            # FastMCP's streamable-http endpoint replies to a bare GET with
            # 405 (Method Not Allowed) — that's enough to confirm liveness.
            r = httpx.get(MCP_SERVER_URL, timeout=1.5)
            if r.status_code in (200, 400, 405, 406, 422):
                return True
        except httpx.HTTPError:
            pass
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _terminate_mcp() -> None:
    """Terminate the MCP subprocess cleanly, with a fallback to kill."""
    global _mcp_proc
    proc = _mcp_proc
    if proc is None or proc.poll() is not None:
        _mcp_proc = None
        return
    logger.info("Terminating MCP server subprocess (pid=%d)…", proc.pid)
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception as exc:
        logger.warning("Non-fatal error while stopping MCP server: %s", exc)
    finally:
        _mcp_proc = None
        logger.info("MCP server subprocess stopped.")


# ---------------------------------------------------------------------------
# Startup health checks (data assets + soil index)
# ---------------------------------------------------------------------------

def _startup_health_check() -> None:
    """Validate API key, data files, and soil spatial index before serving requests."""
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing or empty. Refusing to start.")

    if not MP_GEOJSON_PATH.exists():
        raise RuntimeError(f"Processed GeoJSON not found: {MP_GEOJSON_PATH}")
    if not SOIL_FIXED_PATH.exists():
        raise RuntimeError(f"Processed soil file not found: {SOIL_FIXED_PATH}")

    try:
        with MP_GEOJSON_PATH.open(encoding="utf-8") as fh:
            geojson = json.load(fh)
        with SOIL_FIXED_PATH.open(encoding="utf-8") as fh:
            soil_rows = json.load(fh)
    except Exception as exc:
        raise RuntimeError(f"Failed to read processed soil assets: {exc}") from exc

    district_count = len(geojson.get("features") or [])
    soil_record_count = len(soil_rows) if isinstance(soil_rows, list) else 0
    if district_count <= 40:
        raise RuntimeError(f"District boundary count is too low: {district_count}")

    soil_tool.initialize_soil_data()
    loaded_district_count = soil_tool.get_boundary_district_count()
    loaded_soil_record_count = soil_tool.get_soil_record_count()
    summary = soil_tool.get_district_match_summary()

    if loaded_district_count <= 40:
        raise RuntimeError(
            f"Soil spatial index failed to load enough districts: {loaded_district_count}"
        )
    if loaded_soil_record_count == 0:
        raise RuntimeError("No soil records were loaded into the soil lookup")

    logger.info(
        "Startup soil summary: file_districts=%d file_soil=%d "
        "loaded_districts=%d loaded_soil=%d matched=%s "
        "unmatched_geojson=%s unmatched_soil=%s",
        district_count,
        soil_record_count,
        loaded_district_count,
        loaded_soil_record_count,
        summary.get("matched_count"),
        summary.get("unmatched_geojson_districts", []),
        summary.get("unmatched_soil_districts", []),
    )


# ---------------------------------------------------------------------------
# FastAPI lifespan (replaces deprecated @app.on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _mcp_proc

    # --- 1. Data asset + soil health checks ---
    _startup_health_check()

    # --- 2. MCP server lifecycle ---
    # _probe_mcp_ready blocks on time.sleep; run it in a thread so we don't
    # stall uvicorn's event loop during lifespan startup.
    loop = asyncio.get_event_loop()
    if MCP_AUTOSTART:
        _mcp_proc = _start_mcp_subprocess()
        # Register a safety-net so the child is cleaned up on unexpected exits.
        atexit.register(_terminate_mcp)
        ready = await loop.run_in_executor(None, _probe_mcp_ready, MCP_PROBE_TIMEOUT)
        if not ready:
            _terminate_mcp()
            raise RuntimeError(
                f"MCP server did not become ready within {MCP_PROBE_TIMEOUT}s "
                f"at {MCP_SERVER_URL}. "
                "Check that port 8001 is free and mcp_server.server can be imported."
            )
        logger.info("MCP server ready at %s (pid=%d).", MCP_SERVER_URL, _mcp_proc.pid)
    else:
        logger.info(
            "MCP_AUTOSTART=false — expecting an external MCP server at %s.", MCP_SERVER_URL
        )
        ready = await loop.run_in_executor(None, _probe_mcp_ready, 3.0)
        if not ready:
            raise RuntimeError(
                f"MCP_AUTOSTART=false but no MCP server is reachable at {MCP_SERVER_URL}. "
                "Run `python -m mcp_server.server` first."
            )
        logger.info("External MCP server confirmed at %s.", MCP_SERVER_URL)

    try:
        yield  # Application runs here
    finally:
        _terminate_mcp()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AgroLLaMA — Agricultural Intelligence API",
    description=(
        "Crop advisory for Madhya Pradesh powered by a FastMCP tool server "
        "and Gemini orchestration. POST /analyze with a latitude/longitude "
        "to receive weather, climate, soil, season, risk, and crop-score evidence "
        "plus a Gemini-synthesised crop recommendation."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    """Serve the main web UI at the root path."""
    index_path = BASE_DIR / "static" / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI not built yet.")
    return FileResponse(index_path, media_type="text/html")


@app.get("/health")
def health() -> dict[str, str]:
    """Simple health endpoint."""
    return {
        "status": "ok",
        "message": 'POST /analyze with {"latitude": float, "longitude": float}',
        "mcp_server": MCP_SERVER_URL,
    }


@app.get("/ui", include_in_schema=False)
def ui() -> FileResponse:
    """Serve the main web UI."""
    index_path = BASE_DIR / "static" / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI not built yet.")
    return FileResponse(index_path, media_type="text/html")


@app.get("/mp-geojson", include_in_schema=False)
def mp_geojson() -> JSONResponse:
    """Serve Madhya Pradesh district GeoJSON for the map UI."""
    if not MP_GEOJSON_PATH.exists():
        raise HTTPException(status_code=404, detail="GeoJSON not found.")
    try:
        with MP_GEOJSON_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.exception("Failed to load mp_districts.geojson: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read GeoJSON.") from exc
    return JSONResponse(content=data)


def _run_analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    """Shared handler for POST /analyze and POST /api/analyze."""
    try:
        return run_analysis(latitude=body.latitude, longitude=body.longitude)
    except ValueError as exc:
        logger.exception("ValueError in analyze")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Error in analyze")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    """Analyze a location by latitude and longitude."""
    return _run_analyze(body)


@app.post("/api/analyze", response_model=AnalyzeResponse, tags=["api"])
def api_analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    """Alias of POST /analyze with an /api prefix."""
    return _run_analyze(body)
