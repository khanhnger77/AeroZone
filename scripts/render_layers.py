"""Render analysis rasters to colour PNG overlays + a layer manifest for the web app.

Because the source CRS is equirectangular (clat=0, clon=0) on the Mars sphere, raster pixel
edges map *linearly* to lon/lat. The PNGs are therefore placed with plain lon/lat bounds on
Leaflet's EPSG:4326-style grid (the same grid the NASA Trek tiles use) with no resampling.

Output: frontend/public/data/layers/*.png and frontend/public/data/layers.json
"""
from __future__ import annotations

import json

import numpy as np
import rasterio
from PIL import Image

from config import ANALYSIS, MARS_RADIUS_M, METADATA, PROCESSED, WEB

OUT = WEB / "layers"


def ramp(values: np.ndarray, stops: list[tuple[float, str]], alpha: int = 230) -> np.ndarray:
    xs = [s for s, _ in stops]
    cols = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for _, c in stops], dtype=float)
    out = np.zeros(values.shape + (4,), dtype=np.uint8)
    for ch in range(3):
        out[..., ch] = np.interp(values, xs, cols[:, ch])
    out[..., 3] = np.where(np.isfinite(values), alpha, 0)
    return out


def bounds_deg(ds) -> list[list[float]]:
    b = ds.bounds
    d = lambda v: float(np.degrees(v / MARS_RADIUS_M))
    return [[d(b.bottom), d(b.left)], [d(b.top), d(b.right)]]  # Leaflet [[S, W], [N, E]]


ELEV_STOPS = [(0, "#1d2b53"), (0.25, "#2f6f8f"), (0.5, "#7fb069"), (0.75, "#e6c36a"), (1.0, "#f3efe6")]
SLOPE_STOPS = [(0, "#0d1b2a"), (5, "#2a9d8f"), (15, "#e9c46a"), (25, "#e76f51"), (35, "#9b2226")]
ROUGH_STOPS = [(0, "#0b132b"), (0.05, "#3a506b"), (0.15, "#5bc0be"), (0.3, "#f4d35e"), (0.6, "#ee6c4d")]
HAZARD_STOPS = [(0, "#1a9850"), (20, "#91cf60"), (40, "#fee08b"), (60, "#fc8d59"), (80, "#d73027"), (100, "#7b0041")]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tstats = json.loads((METADATA / "terrain_stats.json").read_text())
    hmodel = json.loads((METADATA / "hazard_model.json").read_text())
    dtm_cite = "USGS Astrogeology / NASA MRO HiRISE - Mars 2020 TRN DTM mosaic, doi:10.5066/P9REJ9JN"

    with rasterio.open(PROCESSED / "hillshade_5m.tif") as ds:
        hill = ds.read(1) / 255.0
        bnds = bounds_deg(ds)
    with rasterio.open(PROCESSED / "elevation_5m.tif") as ds:
        elev = ds.read(1)
    lo, hi = tstats["elevation_m"]["p05"], tstats["elevation_m"]["p95"]
    rgba = ramp(np.clip((elev - lo) / (hi - lo), 0, 1), ELEV_STOPS, alpha=235)
    shade = (0.35 + 0.65 * hill)[..., None]
    rgba[..., :3] = (rgba[..., :3] * shade).clip(0, 255).astype(np.uint8)
    layers = []

    def save(id_, arr, **meta):
        Image.fromarray(arr, "RGBA").save(OUT / f"{id_}.png", optimize=True)
        layers.append({"id": id_, "url": f"data/layers/{id_}.png", "bounds": bnds, **meta})
        print(f"wrote {id_}.png")

    save("elevation", rgba, name="Elevation (shaded relief)", group="Topography",
         category="derived", units="m above MOLA areoid",
         legend={"type": "ramp", "stops": [[lo, ELEV_STOPS[0][1]], [hi, ELEV_STOPS[-1][1]]],
                 "colors": [c for _, c in ELEV_STOPS], "min": lo, "max": hi},
         source=dtm_cite, processing="1 m DTM averaged to 5 m; hillshade az 315 alt 35; colour stretch p05-p95",
         stats=tstats["elevation_m"])

    with rasterio.open(PROCESSED / "slope_5m.tif") as ds:
        save("slope", ramp(ds.read(1), SLOPE_STOPS), name="Slope", group="Topography", category="derived",
             units="degrees", legend={"type": "ramp", "colors": [c for _, c in SLOPE_STOPS], "min": 0, "max": 35},
             source=dtm_cite, processing="Central differences on 5 m grid (10 m baseline), cos(lat)-corrected dx",
             stats=tstats["slope_deg"])
    with rasterio.open(PROCESSED / "roughness_5m.tif") as ds:
        save("roughness", ramp(ds.read(1), ROUGH_STOPS), name="Terrain roughness", group="Topography",
             category="derived", units="m (RMS)",
             legend={"type": "ramp", "colors": [c for _, c in ROUGH_STOPS], "min": 0, "max": 0.6},
             source=dtm_cite, processing="RMS of (z - 9x9 m mean) at 1 m, per 5 m cell. Values < ~0.1 m near DTM noise.",
             stats=tstats["roughness_m"])
    with rasterio.open(ANALYSIS / "hazard_5m.tif") as ds:
        save("hazard", ramp(ds.read(1), HAZARD_STOPS, alpha=200), name="Terrain hazard (prototype model)",
             group="Safety", category="model", units="score 0-100",
             legend={"type": "classes", "classes": [
                 {"label": c["label"], "min": c["min"], "max": c["max"],
                  "color": HAZARD_STOPS[min(i + 1, 5)][1]} for i, c in enumerate(hmodel["classes"])]},
             source=dtm_cite, processing=hmodel["formula"] + "; obstacle & crater factors unavailable, re-normalised",
             model=hmodel)

    manifest = {"aoi_bounds": bnds, "raster_layers": layers}
    (WEB / "layers.json").write_text(json.dumps(manifest, indent=2))
    print(f"-> {WEB / 'layers.json'}")


if __name__ == "__main__":
    main()
