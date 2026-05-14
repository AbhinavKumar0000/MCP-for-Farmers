"""FastAPI app: POST /analyze with latitude and longitude."""

import logging
from pathlib import Path
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.schemas.models import AnalyzeRequest, AnalyzeResponse
from app.mcp_server import run_analysis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

app = FastAPI(
    title="MCP Tool Orchestration API",
    description="Analyze location (lat/long) via Gemini-orchestrated tools: weather, climate, season, risk.",
    version="1.0.0",
)

# Allow all origins in development; restrict in production
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
    return {"status": "ok", "message": "POST /analyze with {\"latitude\": float, \"longitude\": float}"}


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
    geo_path = PROJECT_ROOT / "mp_districts.geojson"
    if not geo_path.exists():
        raise HTTPException(status_code=404, detail="GeoJSON not found.")
    try:
        with geo_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.exception("Failed to load mp_districts.geojson: %s", e)
        raise HTTPException(status_code=500, detail="Failed to read GeoJSON.")
    return JSONResponse(content=data)


def _run_analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    """Shared handler for POST /analyze and POST /api/analyze."""
    try:
        result = run_analysis(latitude=body.latitude, longitude=body.longitude)
        return result
    except ValueError as e:
        logger.exception("ValueError in analyze")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Error in analyze")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    """
    Analyze a location by latitude and longitude.
    Gemini orchestrates tool calls; all tool outputs are returned as structured JSON.
    """
    return _run_analyze(body)


@app.post("/api/analyze", response_model=AnalyzeResponse, tags=["api"])
def api_analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    """
    Alias of POST /analyze with an /api prefix, for frontend frameworks that prefer prefixed routes.
    """
    return _run_analyze(body)
