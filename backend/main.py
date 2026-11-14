"""MARS ODYSSEY API.

Run from the repo root:
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import json
from functools import lru_cache

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .services.routing import RouteError, plan_routes
from .services.terrain import ROOT, get_terrain

WEB_DATA = ROOT / "frontend" / "public" / "data"
METADATA = ROOT / "data" / "metadata"

app = FastAPI(title="MARS ODYSSEY API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@lru_cache(maxsize=1)
def samples() -> list[dict]:
    return json.loads((WEB_DATA / "samples.geojson").read_text())["features"]


class LonLat(BaseModel):
    lon: float = Field(..., ge=-180, le=180)
    lat: float = Field(..., ge=-90, le=90)


class RouteRequest(BaseModel):
    start: LonLat
    end: LonLat


@app.get("/api/health")
def health():
    t = get_terrain()
    return {"status": "ok", "grid_shape": t.shape, "res_m": t.res_m}


@app.get("/api/metadata")
def metadata():
    out = {}
    for name in ("sources", "validation_report", "terrain_stats", "hazard_model"):
        p = METADATA / f"{name}.json"
        if p.exists():
            out[name] = json.loads(p.read_text())
    return out


@app.get("/api/point")
def point(lon: float, lat: float):
    """Terrain values under a map location (for the cursor readout / click inspector)."""
    t = get_terrain()
    r, c = t.lonlat_to_rc(lon, lat)
    if not t.inside(r, c):
        return {"inside": False}
    f = lambda a: None if a[r, c] != a[r, c] else round(float(a[r, c]), 2)  # NaN -> None
    return {"inside": True, "elevation_m": f(t.elevation), "slope_deg": f(t.slope),
            "roughness_m": f(t.roughness), "hazard": f(t.hazard)}


RAW_FEED = "https://mars.nasa.gov/rss/api/"
_PREF = ["MCZ", "SHERLOC", "WATSON", "NAVCAM", "FRONT_HAZCAM", "SUPERCAM"]


@lru_cache(maxsize=256)
def _raw_images(sol: int) -> list[dict]:
    r = requests.get(RAW_FEED, params={"feed": "raw_images", "category": "mars2020", "feedtype": "json",
                                       "sol": sol, "num": 100}, timeout=30,
                     headers={"User-Agent": "mars-odyssey-hackathon/0.1"})
    r.raise_for_status()
    imgs = r.json().get("images", [])

    def rank(i):
        inst = i.get("camera", {}).get("instrument", "").upper()
        return next((k for k, p in enumerate(_PREF) if p in inst), 99)

    keep = ("imageid", "sol", "caption", "date_taken_utc", "camera", "image_files", "credit")
    return [{k: i.get(k) for k in keep} for i in sorted(imgs, key=rank)]


@app.get("/api/raw-images")
def raw_images(sol: int, n: int = 9):
    """Proxy for the NASA Mars 2020 raw-image feed (browser CORS is blocked). Matched by sol only."""
    try:
        return {"sol": sol, "images": _raw_images(sol)[:n], "source": "NASA/JPL-Caltech Mars 2020 raw images"}
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"NASA raw-image feed unavailable: {e}")


@app.post("/api/route")
def route(req: RouteRequest):
    try:
        return plan_routes(get_terrain(), (req.start.lon, req.start.lat), (req.end.lon, req.end.lat), samples())
    except RouteError as e:
        raise HTTPException(status_code=422, detail=str(e))
