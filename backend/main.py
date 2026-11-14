"""MARS ODYSSEY route-planner API: the C4 contract over HTTP on port 8000.

Why a thin app module: all terrain and routing logic lives in
``backend.services`` so it can be tested and reused (M4 calls ``/api/route``
for its demo mission). This file only validates input, maps service errors to
the status codes C4 promises (422 for unroutable requests, 502 when the NASA
feed is down) and caches the slow, read-only inputs.

Run with ``pixi run backend`` (``uvicorn backend.main:app --port 8000``).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .services.routing import RouteError, plan_routes
from .services.terrain import ROOT, get_terrain

# ---------------------------------------------------------------- configuration
SAMPLES_PATH = ROOT / "frontend/public/data/samples.geojson"
METADATA_DIR = ROOT / "data/metadata"
METADATA_KEYS = ("sources", "validation_report", "terrain_stats", "hazard_model")

RAW_FEED_URL = ("https://mars.nasa.gov/rss/api/?feed=raw_images&category=mars2020"
                "&feedtype=json&sol={sol}&num=100")
RAW_USER_AGENT = "mars-odyssey-hackathon/0.1"
RAW_TIMEOUT_S = 15.0
RAW_CACHE_SOLS = 256
CAMERA_RANK = ("MCZ", "SHERLOC", "WATSON", "NAVCAM", "FRONT_HAZCAM", "SUPERCAM")
RAW_IMAGE_FIELDS = ("imageid", "sol", "caption", "date_taken_utc", "camera", "image_files", "credit")
IMAGE_FILE_KEYS = ("small", "medium", "large", "full_res")

app = FastAPI(title="MARS ODYSSEY API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """C4 promises ``{detail: "<reason>"}`` as a string, not FastAPI's error list."""
    reasons = "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in exc.errors())
    return JSONResponse(status_code=422, content={"detail": reasons})


class LonLat(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class RouteRequest(BaseModel):
    start: LonLat
    end: LonLat


# ---------------------------------------------------------------- cached inputs
@lru_cache(maxsize=1)
def samples() -> list[dict[str, Any]]:
    """Sample features from C3 ``samples.geojson``; empty if M1 has not produced it."""
    try:
        return json.loads(SAMPLES_PATH.read_text()).get("features", [])
    except (OSError, ValueError):
        return []


def _terrain():
    try:
        return get_terrain()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _round_or_none(v: float) -> float | None:
    return round(float(v), 2) if np.isfinite(v) else None


# ---------------------------------------------------------------- endpoints
@app.get("/api/health")
def health() -> dict[str, Any]:
    t = _terrain()
    return {"status": "ok", "grid_shape": list(t.shape), "res_m": t.res_m}


@app.get("/api/metadata")
def metadata() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in METADATA_KEYS:
        try:
            out[key] = json.loads((METADATA_DIR / f"{key}.json").read_text())
        except (OSError, ValueError):
            out[key] = None
    return out


@app.get("/api/point")
def point(lon: float = Query(...), lat: float = Query(...)) -> dict[str, Any]:
    t = _terrain()
    r, c = t.lonlat_to_rc(lon, lat)
    if not t.inside(r, c):
        return {"inside": False}
    return {
        "inside": True,
        "elevation_m": _round_or_none(t.elevation[r, c]),
        "slope_deg": _round_or_none(t.slope[r, c]),
        "roughness_m": _round_or_none(t.roughness[r, c]),
        "hazard": _round_or_none(t.hazard[r, c]),
    }


@lru_cache(maxsize=RAW_CACHE_SOLS)
def _fetch_raw_feed(sol: int) -> tuple[dict[str, Any], ...]:
    """One NASA feed call per sol. Failures raise and are therefore not cached."""
    resp = httpx.get(RAW_FEED_URL.format(sol=sol), headers={"User-Agent": RAW_USER_AGENT},
                     timeout=RAW_TIMEOUT_S, follow_redirects=True)
    resp.raise_for_status()
    images = resp.json().get("images", [])
    return tuple(_raw_image(im) for im in images)


def _raw_image(im: dict[str, Any]) -> dict[str, Any]:
    out = {k: im.get(k) for k in RAW_IMAGE_FIELDS}
    out["camera"] = im.get("camera") or {}
    files = im.get("image_files") or {}
    out["image_files"] = {k: files.get(k) for k in IMAGE_FILE_KEYS}
    return out


def _camera_rank(im: dict[str, Any]) -> int:
    instrument = str(im["camera"].get("instrument", "")).upper()
    return next((i for i, name in enumerate(CAMERA_RANK) if name in instrument), len(CAMERA_RANK))


@app.get("/api/raw-images")
def raw_images(sol: int = Query(..., ge=0), n: int = Query(9, ge=1, le=100)) -> dict[str, Any]:
    try:
        images = _fetch_raw_feed(sol)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"NASA raw-image feed unreachable: {exc}") from exc
    ranked = sorted(images, key=_camera_rank)[:n]
    return {"sol": sol, "images": ranked, "source": RAW_FEED_URL.format(sol=sol)}


@app.post("/api/route")
def route(req: RouteRequest) -> dict[str, Any]:
    t = _terrain()
    try:
        return plan_routes(t, (req.start.lon, req.start.lat), (req.end.lon, req.end.lat), samples())
    except RouteError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
