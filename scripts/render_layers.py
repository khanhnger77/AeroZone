"""Render the C2 rasters to RGBA PNG overlays and write the C3 `layers.json` manifest.

Why one PNG pixel per 5 m cell: the raster grid is equirectangular on the sphere, so a Leaflet image
overlay stretched over the lon/lat pixel-edge bounds places every cell exactly (C1) and no resampling
is needed. NaN cells are fully transparent. [v1.1] Geology and hazard-factor rasters from the
environment track go into the separate `overlay_layers` array, written only for rasters that exist,
so `raster_layers` stays exactly the four v1.0 layers.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    AOI,
    ANALYSIS,
    METADATA,
    PROCESSED,
    ROOT,
    WEB,
    grid_bounds_latlon,
    info,
    read_grid_tif,
    read_json,
    warn,
    write_json,
)

ELEV_COLORS = ["#1d2b53", "#2f6f8f", "#7fb069", "#e6c36a", "#f3efe6"]
SLOPE_STOPS = [(0, "#0d1b2a"), (5, "#2a9d8f"), (15, "#e9c46a"), (25, "#e76f51"), (35, "#9b2226")]
ROUGH_STOPS = [(0, "#0b132b"), (0.05, "#3a506b"), (0.15, "#5bc0be"), (0.3, "#f4d35e"), (0.6, "#ee6c4d")]
HAZARD_STOPS = [(0, "#1a9850"), (20, "#91cf60"), (40, "#fee08b"), (60, "#fc8d59"), (80, "#d73027"), (100, "#7b0041")]
TI_COLORS = ["#2b1d0e", "#7f5539", "#b08968", "#ddb892", "#e6ccb2", "#f8f4ef"]
TI_MIN, TI_MAX = 100.0, 800.0

ALPHA_HAZARD = 200
ALPHA_GEOLOGY = 170
ALPHA_TERRAIN = 255  # elevation / slope / roughness base layers

DTM_SOURCE = "USGS Astrogeology, HiRISE DTM mosaic of Jezero crater, 1 m (Mars 2020 TRN)"


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def ramp(values: np.ndarray, stops: list[tuple[float, str]]) -> np.ndarray:
    """Map values onto colour stops (linear in RGB). Returns float (H, W, 3) in 0..255."""
    xs = np.array([s[0] for s in stops], dtype=np.float64)
    cols = np.array([hex_rgb(s[1]) for s in stops], dtype=np.float64)
    v = np.nan_to_num(values.astype(np.float64), nan=xs[0])
    return np.stack([np.interp(v, xs, cols[:, c]) for c in range(3)], axis=-1)


def even_stops(vmin: float, vmax: float, colors: list[str]) -> list[tuple[float, str]]:
    n = len(colors)
    return [(vmin + (vmax - vmin) * i / (n - 1), c) for i, c in enumerate(colors)]


def to_png(rgb: np.ndarray, valid: np.ndarray, alpha: int, path: Path) -> None:
    rgba = np.zeros(rgb.shape[:2] + (4,), dtype=np.uint8)
    rgba[..., :3] = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    rgba[..., 3] = np.where(valid, alpha, 0).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)


def stats(a: np.ndarray, nd: int = 3) -> dict[str, float]:
    v = a[np.isfinite(a)]
    if not v.size:
        return {}
    p = np.percentile(v, [5, 50, 95])
    return {"min": round(float(v.min()), nd), "p05": round(float(p[0]), nd), "median": round(float(p[1]), nd),
            "mean": round(float(v.mean()), nd), "p95": round(float(p[2]), nd), "max": round(float(v.max()), nd)}


def ramp_legend(stops: list[tuple[float, str]], vmin: float | None = None, vmax: float | None = None) -> dict[str, Any]:
    return {"type": "ramp", "colors": [c for _, c in stops],
            "min": float(stops[0][0] if vmin is None else vmin), "max": float(stops[-1][0] if vmax is None else vmax),
            "stops": [[round(float(v), 4), c] for v, c in stops]}


def layer(lid: str, name: str, group: str, category: str, units: str, source: str, processing: str,
          legend: dict[str, Any], st: dict[str, float] | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": lid, "name": name, "url": f"data/layers/{lid}.png", "bounds": grid_bounds_latlon(),
                           "group": group, "category": category, "units": units, "source": source,
                           "processing": processing, "legend": legend}
    if st:
        out["stats"] = st
    out.update(extra)
    return out


# ---------------------------------------------------------------- v1.0 layers
def render_core(layers_dir: Path) -> list[dict[str, Any]]:
    elev = read_grid_tif(PROCESSED / "elevation_5m.tif")
    hill = read_grid_tif(PROCESSED / "hillshade_5m.tif")
    slope = read_grid_tif(PROCESSED / "slope_5m.tif")
    rough = read_grid_tif(PROCESSED / "roughness_5m.tif")
    hazard = read_grid_tif(ANALYSIS / "hazard_5m.tif")
    model = read_json(METADATA / "hazard_model.json")

    ev = elev[np.isfinite(elev)]
    p05, p95 = (float(np.percentile(ev, 5)), float(np.percentile(ev, 95))) if ev.size else (0.0, 1.0)
    e_stops = even_stops(p05, p95, ELEV_COLORS)
    shade = 0.45 + 0.55 * np.nan_to_num(hill, nan=180.0)[..., None] / 255.0
    to_png(ramp(elev, e_stops) * shade, np.isfinite(elev), ALPHA_TERRAIN, layers_dir / "elevation.png")
    to_png(ramp(slope, SLOPE_STOPS), np.isfinite(slope), ALPHA_TERRAIN, layers_dir / "slope.png")
    to_png(ramp(rough, ROUGH_STOPS), np.isfinite(rough), ALPHA_TERRAIN, layers_dir / "roughness.png")
    to_png(ramp(hazard, HAZARD_STOPS), np.isfinite(hazard), ALPHA_HAZARD, layers_dir / "hazard.png")

    return [
        layer("elevation", "Elevation", "terrain", "observation", "m above areoid", DTM_SOURCE,
              "mean of 1 m posts per 5 m cell; colour p05-p95 stretch, hillshade-modulated (az 315, alt 35)",
              ramp_legend(e_stops, p05, p95), stats(elev, 2)),
        layer("slope", "Slope", "terrain", "derived", "deg", DTM_SOURCE,
              "central differences on the 5 m grid, cos(lat)-corrected east-west spacing",
              ramp_legend(SLOPE_STOPS), stats(slope, 2)),
        layer("roughness", "Roughness", "terrain", "derived", "m", DTM_SOURCE,
              "RMS of (1 m z - 9 x 9 m moving mean) per 5 m cell",
              ramp_legend(ROUGH_STOPS), stats(rough, 4)),
        layer("hazard", "Hazard (prototype)", "hazard", "model", "0-100", "derived from the HiRISE DTM layers",
              model["formula"], ramp_legend(HAZARD_STOPS), stats(hazard, 2), model=model),
    ]


# ---------------------------------------------------------------- [v1.1] overlays
def _classes_png(codes: np.ndarray, code_color: dict[int, str], path: Path, alpha: int) -> None:
    rgb = np.zeros(codes.shape + (3,), dtype=np.float64)
    valid = np.isfinite(codes)
    ci = np.where(valid, codes, -1).astype(np.int64)
    known = np.zeros(codes.shape, dtype=bool)
    for code, col in code_color.items():
        m = ci == code
        rgb[m] = hex_rgb(col)
        known |= m
    to_png(rgb, valid & known, alpha, path)


def render_overlays(layers_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    legend_path = METADATA / "geology_legend.json"
    legend = read_json(legend_path) if legend_path.exists() else {}

    p = PROCESSED / "geologic_units_5m.tif"
    if p.exists() and legend.get("units"):
        codes = read_grid_tif(p)
        units = legend["units"]
        groups: dict[str, dict[str, Any]] = {}
        for u in units:
            g = groups.setdefault(u["group"], {"label": u["group"], "min": u["code"], "max": u["code"],
                                               "color": legend.get("group_colors", {}).get(u["group"], u["color"])})
            g["min"], g["max"] = min(g["min"], u["code"]), max(g["max"], u["code"])
        _classes_png(codes, {u["code"]: groups[u["group"]]["color"] for u in units},
                     layers_dir / "geologic_units.png", ALPHA_GEOLOGY)
        out.append(layer("geologic_units", "Geologic units (Mars 2020 Science Team)", "geology", "derived", "class",
                         "Stack et al. 2020, ESM shapefile", "rasterised, majority -> 5 m",
                         {"type": "classes", "classes": list(groups.values())}))

    p = PROCESSED / "terrain_class_5m.tif"
    if p.exists() and legend.get("terrain_classes"):
        codes = read_grid_tif(p)
        tcs = legend["terrain_classes"]
        _classes_png(codes, {t["code"]: t["color"] for t in tcs}, layers_dir / "terrain_class.png", ALPHA_GEOLOGY)
        res = float(legend.get("terrain_source_res_m") or 0.0)
        st = {"source_res_m": res} if res > 5.0 else None  # only coarser-than-grid sources are flagged
        out.append(layer("terrain_class", "Terrain class (NOAH-H, deep learning)", "geology", "model", "class",
                         "Wright et al. 2022", "mode/nearest -> 5 m",
                         {"type": "classes", "classes": [{"label": t["name"], "min": t["code"], "max": t["code"],
                                                          "color": t["color"]} for t in tcs]}, st))

    p = PROCESSED / "thermal_inertia_5m.tif"
    if p.exists():
        ti = read_grid_tif(p)
        stops = even_stops(TI_MIN, TI_MAX, TI_COLORS)
        to_png(ramp(ti, stops), np.isfinite(ti), ALPHA_GEOLOGY, layers_dir / "thermal_inertia.png")
        out.append(layer("thermal_inertia", "Thermal inertia (THEMIS)", "geology", "observation", "J m⁻² K⁻¹ s⁻½",
                         "USGS THEMIS TI quantitative mosaic, 100 m", "reprojected, bilinear -> 5 m",
                         ramp_legend(stops), {**stats(ti, 1), "source_res_m": 100.0}))

    p = ANALYSIS / "obstacle_risk_5m.tif"
    if p.exists():
        a = read_grid_tif(p)
        to_png(ramp(a, HAZARD_STOPS), np.isfinite(a), ALPHA_HAZARD, layers_dir / "obstacle_risk.png")
        out.append(layer("obstacle_risk", "Obstacle factor (proxy)", "hazard", "model", "0-100",
                         "geologic_units x geology_risk.yaml (+ TI prior)", "per-unit score, max with TI prior",
                         ramp_legend(HAZARD_STOPS), stats(a, 2)))

    p = ANALYSIS / "crater_risk_5m.tif"
    if p.exists():
        a = read_grid_tif(p)
        to_png(ramp(a, HAZARD_STOPS), np.isfinite(a), ALPHA_HAZARD, layers_dir / "crater_risk.png")
        out.append(layer("crater_risk", "Crater factor", "hazard", "derived", "0-100", "HiRISE DTM 1 m",
                         "depression detection (fill - DTM), max per 5 m cell", ramp_legend(HAZARD_STOPS), stats(a, 2)))
    return out


def main() -> int:
    needed = [PROCESSED / f"{n}_5m.tif" for n in ("elevation", "hillshade", "slope", "roughness")]
    needed += [ANALYSIS / "hazard_5m.tif", METADATA / "hazard_model.json"]
    missing = [p for p in needed if not p.exists()]
    if missing:
        warn("missing inputs: " + ", ".join(str(p.relative_to(ROOT)) for p in missing))
        return 1
    layers_dir = WEB / "layers"
    manifest: dict[str, Any] = {
        "aoi_bounds": [[AOI["lat_s"], AOI["lon_w"]], [AOI["lat_n"], AOI["lon_e"]]],
        "raster_layers": render_core(layers_dir),
    }
    overlays = render_overlays(layers_dir)
    if overlays:
        manifest["overlay_layers"] = overlays
    write_json(WEB / "layers.json", manifest)
    info(f"wrote {(WEB / 'layers.json').relative_to(ROOT)} ({len(manifest['raster_layers'])} raster layers, "
         f"{len(overlays)} overlay layers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
