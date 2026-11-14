"""[v1.1] Geology rasters and the obstacle / crater factors for the hazard model (C2-ext, C3-ext).

Why these proxies: no rock-abundance or crater map exists at rover scale for Jezero. The two missing
hazard factors are therefore built from what does exist, and labelled for what they are:
  * obstacle = per-unit score of the Mars 2020 Science Team photogeologic map (Stack et al. 2020,
    1:5 000), max'ed with a thermal-inertia prior (high TI = rock / bedrock). Tag `proxy_geologic_units`.
  * crater = closed depressions found in the 1 m HiRISE DTM itself (morphological fill minus DTM).
    The Robbins database is complete only for D >= 1 km, so it is written as context only. Tag `derived_dtm`.
Class rasters use majority / nearest resampling only; THEMIS (100 m) is upsampled bilinearly and its
source resolution is recorded so the UI never implies 5 m detail.

Every step is optional: a missing input prints a warning and that output is skipped (exit 0).
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    AOI,
    ANALYSIS,
    ANALYSIS_RES_M,
    DTM_PATH,
    GRID_HEIGHT,
    GRID_WIDTH,
    MARS_GEOG_PROJ4,
    MARS_RADIUS_M,
    METADATA,
    PROCESSED,
    ROOT,
    WEB,
    grid_bounds_proj,
    grid_crs,
    grid_transform,
    info,
    load_yaml,
    proj_to_deg,
    read_grid_tif,
    warn,
    write_grid_tif,
    write_json,
)

BLOCK = int(ANALYSIS_RES_M)
GEO_WEB = WEB / "geology"


# ---------------------------------------------------------------- helpers
def block_mode(codes: np.ndarray, n_codes: int, block: int = BLOCK) -> np.ndarray:
    """Majority code per block x block cell of an int array; 0 = no data -> NaN."""
    h, w = codes.shape[0] // block, codes.shape[1] // block
    blocks = codes[: h * block, : w * block].reshape(h, block, w, block)
    best = np.zeros((h, w), dtype=np.int32)
    best_n = np.zeros((h, w), dtype=np.int32)
    for c in range(1, n_codes + 1):
        n = (blocks == c).sum(axis=(1, 3), dtype=np.int32)
        better = n > best_n
        best[better], best_n[better] = c, n[better]
    nodata_n = (blocks == 0).sum(axis=(1, 3), dtype=np.int32)
    out = best.astype(np.float32)
    out[(best_n == 0) | (nodata_n > best_n)] = np.nan
    return out


def piecewise(x: np.ndarray, stops: list[list[float]]) -> np.ndarray:
    out = np.interp(x.astype(np.float64), stops[0], stops[1]).astype(np.float32)
    out[~np.isfinite(x)] = np.nan
    return out


def force_sphere(crs) -> Any:
    """Return `crs` unless its body is not the C1 sphere; then the C1 sphere equivalent (caveat §7)."""
    from pyproj import CRS
    from rasterio.crs import CRS as RCRS

    c = CRS.from_wkt(crs.to_wkt())
    e = c.ellipsoid
    a, b = e.semi_major_metre, e.semi_minor_metre
    if abs(a - MARS_RADIUS_M) < 1 and abs(b - MARS_RADIUS_M) < 1:
        return crs
    warn(f"source CRS body is an ellipsoid (a={a:.0f}, b={b:.0f}); forcing the C1 sphere R={MARS_RADIUS_M:.0f}")
    if c.is_geographic:
        return RCRS.from_proj4(MARS_GEOG_PROJ4)
    params = c.to_dict()
    params.pop("ellps", None)
    params.pop("rf", None)
    params.update({"a": MARS_RADIUS_M, "b": MARS_RADIUS_M})
    params.pop("datum", None)
    return RCRS.from_dict(params)


def to_grid(src_path: Path, resampling, band: int = 1) -> np.ndarray:
    """Reproject one band of a raster onto the C2 grid (NaN where no data)."""
    import rasterio
    from rasterio.warp import reproject

    dst = np.full((GRID_HEIGHT, GRID_WIDTH), np.nan, dtype=np.float32)
    with rasterio.open(src_path) as s:
        src = s.read(band).astype(np.float32)
        if s.nodata is not None:
            src[src == np.float32(s.nodata)] = np.nan
        src[~np.isfinite(src) | (src < -1e30)] = np.nan
        reproject(src, dst, src_transform=s.transform, src_crs=force_sphere(s.crs), src_nodata=np.nan,
                  dst_transform=grid_transform(), dst_crs=grid_crs(), dst_nodata=np.nan, resampling=resampling)
    return dst


def fc(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------- (1) Stack 2020 units
def process_units(env: dict[str, Any], risk_cfg: dict[str, Any]) -> tuple[np.ndarray | None, list[dict[str, Any]]]:
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import Affine
    from shapely.geometry import box, mapping

    src = env["stack2020_units"]
    shp = (ROOT / src["dest"]).parent / src["shapefile"]
    if not shp.exists():
        warn(f"Stack 2020 shapefile missing ({shp.relative_to(ROOT)}); geologic units skipped")
        return None, []
    gdf = gpd.read_file(shp)
    gdf = gdf.set_crs(force_sphere(_rio_crs(gdf.crs)).to_wkt(), allow_override=True).to_crs(grid_crs().to_wkt())
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["geometry"] = gdf.geometry.make_valid()
    aoi_box = box(*grid_bounds_proj())
    gdf = gdf[gdf.intersects(aoi_box)].copy()
    gdf["geometry"] = gdf.geometry.intersection(aoi_box)
    gdf = gdf[~gdf.geometry.is_empty]

    sf, bf = risk_cfg["surface_field"], risk_cfg["bedrock_field"]
    defer = set(risk_cfg.get("defer_to_bedrock") or [])
    mapping_ = risk_cfg["unit_groups"]
    groups = risk_cfg["groups"]
    group_order = list(groups)

    def name(v: Any) -> str:
        return "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip()

    def unit_of(row) -> str:
        s, b = name(row[sf]), name(row.get(bf))
        return b if s in defer and b else (s or b or "unknown")

    gdf["unit_id"] = gdf.apply(unit_of, axis=1)
    unmapped = sorted(set(gdf["unit_id"]) - set(mapping_))
    if unmapped:
        warn(f"geology_risk.yaml: unmapped unit(s) -> unknown: {', '.join(unmapped)}")
    gdf["group"] = gdf["unit_id"].map(lambda u: mapping_.get(u, "unknown"))
    units_sorted = sorted(set(gdf["unit_id"]), key=lambda u: (group_order.index(mapping_.get(u, "unknown")), u))
    code_of = {u: i + 1 for i, u in enumerate(units_sorted)}
    legend_units = []
    for u in units_sorted:
        g = mapping_.get(u, "unknown")
        legend_units.append({"code": code_of[u], "unit_id": u, "name": u, "group": g,
                             "risk_score": groups[g]["risk_score"], "color": groups[g]["color"]})
    gdf["unit_code"] = gdf["unit_id"].map(code_of)

    # rasterise at 1 m (the map is 1:5 000), majority per 5 m cell
    res = float(risk_cfg.get("rasterise_res_m", 1.0))
    factor = int(round(ANALYSIS_RES_M / res))
    x_w, _, _, y_n = grid_bounds_proj()
    shape = (GRID_HEIGHT * factor, GRID_WIDTH * factor)
    codes = rasterize(((g, int(c)) for g, c in zip(gdf.geometry, gdf["unit_code"])), out_shape=shape,
                      transform=Affine(res, 0, x_w, 0, -res, y_n), fill=0, dtype="int16")
    unit_raster = block_mode(codes, len(units_sorted), factor)
    write_grid_tif(PROCESSED / "geologic_units_5m.tif", unit_raster)
    info(f"wrote data/processed/geologic_units_5m.tif ({len(units_sorted)} units)")

    # web vectors: lon/lat on the sphere, light simplification (0.5 m), dissolved per unit + surface/bedrock
    geo = gdf.copy()
    geo["unit_name"] = geo.apply(lambda r: f"{name(r[sf])} over {name(r[bf])}" if name(r[bf]) else name(r[sf]), axis=1)
    geo = geo.dissolve(by=["unit_code", "unit_id", "unit_name", "group"], as_index=False)
    geo["geometry"] = geo.geometry.simplify(0.5, preserve_topology=True)
    geo = geo.to_crs(MARS_GEOG_PROJ4)
    feats = []
    for _, r in geo.iterrows():
        g = mapping(r.geometry)
        feats.append({"type": "Feature", "geometry": _round_geom(g, 7), "properties": {
            "unit_code": int(r["unit_code"]), "unit_id": r["unit_id"], "unit_name": r["unit_name"],
            "group": r["group"], "risk_score": groups[r["group"]]["risk_score"], "source": src["landing_page"]}})
    write_json(GEO_WEB / "geologic_units.geojson", fc(feats), compact=True)
    info(f"wrote frontend/public/data/geology/geologic_units.geojson ({len(feats)} features)")
    return unit_raster, legend_units


def _rio_crs(crs) -> Any:
    from rasterio.crs import CRS

    return CRS.from_wkt(crs.to_wkt())


def _round_geom(g: Any, nd: int) -> dict[str, Any]:
    def r(c: Any) -> Any:
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return [round(float(c[0]), nd), round(float(c[1]), nd)]
        return [r(x) for x in c]

    return {"type": g["type"], "coordinates": r(g["coordinates"])}


# ---------------------------------------------------------------- (2) THEMIS TI
def process_ti(env: dict[str, Any]) -> np.ndarray | None:
    from rasterio.enums import Resampling

    p = ROOT / env["themis_ti"]["dest"]
    if not p.exists():
        warn("THEMIS TI window missing (download_env_data.py --only themis_ti); thermal inertia skipped")
        return None
    ti = to_grid(p, Resampling.bilinear)
    ti[(ti <= 0) | (ti > 5000)] = np.nan
    write_grid_tif(PROCESSED / "thermal_inertia_5m.tif", ti)
    info("wrote data/processed/thermal_inertia_5m.tif (source 100 m, bilinear)")
    return ti


# ---------------------------------------------------------------- (3) NOAH-H
def read_class_raster_to_grid(path: Path) -> np.ndarray:
    """Majority-decimate a fine class raster onto the C2 grid without loading it whole.

    The NOAH-H mosaic is 25 cm (4.6 Gpx). It shares the C1 CRS, so the part overlapping the grid is
    read as one window straight into the matching block of 5 m cells with GDAL's `mode` resampling.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    out = np.full((GRID_HEIGHT, GRID_WIDTH), np.nan, dtype=np.float32)
    with rasterio.open(path) as s:
        if not check_same_crs(s.crs):
            warn(f"{path.name}: CRS differs from C1; using nearest reprojection")
            from rasterio.enums import Resampling as R

            return to_grid(path, R.nearest)
        x_w, y_s, x_e, y_n = grid_bounds_proj()
        b = s.bounds
        # overlap snapped to whole 5 m cells
        c0 = int(np.ceil(max(0.0, (b.left - x_w) / ANALYSIS_RES_M)))
        c1 = int(np.floor(min(GRID_WIDTH, (b.right - x_w) / ANALYSIS_RES_M)))
        r0 = int(np.ceil(max(0.0, (y_n - b.top) / ANALYSIS_RES_M)))
        r1 = int(np.floor(min(GRID_HEIGHT, (y_n - b.bottom) / ANALYSIS_RES_M)))
        if c1 <= c0 or r1 <= r0:
            return out
        win = from_bounds(x_w + c0 * ANALYSIS_RES_M, y_n - r1 * ANALYSIS_RES_M, x_w + c1 * ANALYSIS_RES_M,
                          y_n - r0 * ANALYSIS_RES_M, transform=s.transform)
        a = s.read(1, window=win, out_shape=(r1 - r0, c1 - c0), resampling=Resampling.mode).astype(np.float32)
        if s.nodata is not None:
            a[a == np.float32(s.nodata)] = np.nan
    out[r0:r1, c0:c1] = a
    return out


def check_same_crs(crs) -> bool:
    from pyproj import CRS

    return CRS.from_wkt(crs.to_wkt()).equals(CRS.from_wkt(grid_crs().to_wkt()), ignore_axis_order=True) or (
        "Equirectangular" in crs.to_wkt() and "3396190" in crs.to_wkt() and '"central_meridian",0' in crs.to_wkt())


def process_noah(env: dict[str, Any], risk_cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], float | None]:
    root = (ROOT / env["noah_h_jezero"]["dest"]).parent
    path = root / risk_cfg["noah_raster"]
    if not path.exists():
        warn("NOAH-H mosaic missing (download_env_data.py --only noah_h_jezero); terrain_class skipped")
        return [], None
    info(f"NOAH-H: majority-decimating {path.name} (25 cm) to 5 m ...")
    tc = read_class_raster_to_grid(path)
    tc[tc <= 0] = np.nan
    known = {int(k): v for k, v in (risk_cfg.get("terrain_classes") or {}).items()}
    codes = sorted(int(c) for c in np.unique(tc[np.isfinite(tc)]))
    unknown = [c for c in codes if c not in known]
    if unknown:
        warn(f"NOAH-H: class code(s) {unknown} not in geology_risk.yaml; set to NaN")
        tc[np.isin(tc, unknown)] = np.nan
    classes = [{"code": c, "name": v["name"], "risk_score": v["risk_score"], "color": v["color"]}
               for c, v in sorted(known.items())]
    write_grid_tif(PROCESSED / "terrain_class_5m.tif", tc)
    cov = float(np.isfinite(tc).mean())
    info(f"wrote data/processed/terrain_class_5m.tif ({len(codes)} classes present, {100 * cov:.1f} % of the grid)")
    return classes, float(risk_cfg.get("noah_source_res_m", 0.25))


# ---------------------------------------------------------------- (4) Robbins
def process_robbins(env: dict[str, Any]) -> None:
    from _http import get_json

    src = env["robbins_craters"]
    bbox = f"{AOI['lon_w']},{AOI['lat_s']},{AOI['lon_e']},{AOI['lat_n']}"
    try:
        data = get_json(src["url"], params={"bbox": bbox, "f": "json", "limit": 1000})
    except Exception as exc:  # noqa: BLE001
        warn(f"Robbins crater API unavailable ({exc}); craters_robbins.geojson not refreshed")
        return
    feats = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        lon = float(p.get("lon_e", f["geometry"]["coordinates"][0]))
        lon = (lon + 180.0) % 360.0 - 180.0
        lat = float(p.get("lat", f["geometry"]["coordinates"][1]))
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                      "properties": {"crater_id": p.get("craterid"), "diam_km": p.get("diamkm"), "lon": lon, "lat": lat,
                                     "source": src["landing_page"]}})
    write_json(GEO_WEB / "craters_robbins.geojson", fc(feats), compact=True)
    info(f"wrote frontend/public/data/geology/craters_robbins.geojson ({len(feats)} features; complete for D >= 1 km)")


# ---------------------------------------------------------------- (5) crater factor
def detect_tile(z: np.ndarray, cfg: dict[str, Any]) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Crater score (1 m) and crater list for one DTM tile (local pixel coordinates)."""
    from scipy import ndimage as ndi
    from skimage.measure import perimeter
    from skimage.morphology import reconstruction

    valid = np.isfinite(z)
    score = np.zeros(z.shape, dtype=np.float32)
    if valid.sum() < 100:
        return score, []
    zf = np.where(valid, z, np.nanmin(z) - 1.0).astype(np.float64)
    seed = np.full_like(zf, zf.max())
    seed[0, :], seed[-1, :], seed[:, 0], seed[:, -1] = zf[0, :], zf[-1, :], zf[:, 0], zf[:, -1]
    seed[~valid] = zf[~valid]  # nodata acts as an outlet, never as a wall
    filled = reconstruction(seed, zf, method="erosion")
    depth = (filled - zf).astype(np.float32)
    depth[~valid] = 0.0

    labels, n = ndi.label(depth > 0.05)
    if n == 0:
        return score, []
    idx = np.arange(1, n + 1)
    area = ndi.sum_labels(np.ones_like(depth), labels, idx)
    dmax = ndi.maximum(depth, labels, idx)
    diam = np.sqrt(4.0 * area / np.pi)
    keep = (dmax >= cfg["min_depth_m"]) & (diam >= cfg["min_diameter_m"]) & (diam <= cfg["max_diameter_m"])
    objs = ndi.find_objects(labels)
    craters: list[dict[str, Any]] = []
    h, w = z.shape
    for k in np.nonzero(keep)[0]:
        lab = k + 1
        sl = objs[k]
        reg = labels[sl] == lab
        per = perimeter(reg)
        circ = 4.0 * np.pi * area[k] / max(per * per, 1e-9)
        if circ < cfg["min_circularity"]:
            continue
        r = diam[k] / 2.0
        pad = int(np.ceil(r)) + 2
        y0, y1 = max(sl[0].start - pad, 0), min(sl[0].stop + pad, h)
        x0, x1 = max(sl[1].start - pad, 0), min(sl[1].stop + pad, w)
        inside = labels[y0:y1, x0:x1] == lab
        dist = ndi.distance_transform_edt(~inside)
        s = (100.0 * np.clip(1.0 - dist / r, 0.0, 1.0)).astype(np.float32)
        np.maximum(score[y0:y1, x0:x1], s, out=score[y0:y1, x0:x1])
        cy, cx = ndi.center_of_mass(reg)
        craters.append({"row": sl[0].start + cy, "col": sl[1].start + cx, "diam_m": float(diam[k]),
                        "depth_m": float(dmax[k])})
    score[~valid] = np.nan
    return score, craters


def process_craters(risk_cfg: dict[str, Any]) -> bool:
    from process_terrain import read_aoi_1m

    if not DTM_PATH.exists():
        warn("HiRISE DTM missing; crater factor skipped")
        return False
    cfg = risk_cfg["crater"]
    info("crater factor: reading the AOI from the 1 m DTM ...")
    z = read_aoi_1m(halo=0)
    H, W = z.shape
    tile = max(BLOCK, (int(cfg["tile_px"]) // BLOCK) * BLOCK)
    ov = (int(cfg["overlap_px"]) // BLOCK) * BLOCK
    out = np.full((GRID_HEIGHT, GRID_WIDTH), np.nan, dtype=np.float32)
    craters: list[dict[str, Any]] = []
    x_w, _, _, y_n = grid_bounds_proj()
    n_tiles = ((H + tile - 1) // tile) * ((W + tile - 1) // tile)
    t = 0
    for r0 in range(0, H, tile):
        for c0 in range(0, W, tile):
            t += 1
            r1, c1 = min(r0 + tile, H), min(c0 + tile, W)
            er0, ec0, er1, ec1 = max(r0 - ov, 0), max(c0 - ov, 0), min(r1 + ov, H), min(c1 + ov, W)
            score, found = detect_tile(z[er0:er1, ec0:ec1], cfg)
            core = score[r0 - er0: r1 - er0, c0 - ec0: c1 - ec0]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks -> NaN
                hb, wb = core.shape[0] // BLOCK, core.shape[1] // BLOCK
                blk = np.nanmax(core[: hb * BLOCK, : wb * BLOCK].reshape(hb, BLOCK, wb, BLOCK), axis=(1, 3))
            out[r0 // BLOCK: r0 // BLOCK + hb, c0 // BLOCK: c0 // BLOCK + wb] = blk
            for c in found:
                gr, gc = c["row"] + er0, c["col"] + ec0
                if r0 <= gr < r1 and c0 <= gc < c1:  # count each crater once (its centre's tile)
                    craters.append({**c, "row": gr, "col": gc})
            sys.stdout.write(f"\r  tile {t}/{n_tiles}: {len(craters)} craters")
            sys.stdout.flush()
    sys.stdout.write("\n")
    write_grid_tif(ANALYSIS / "crater_risk_5m.tif", out)

    feats = []
    for i, c in enumerate(sorted(craters, key=lambda c: -c["diam_m"])):
        lon, lat = proj_to_deg(x_w + c["col"] + 0.5, y_n - c["row"] - 0.5)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(lon), 7), round(float(lat), 7)]},
                      "properties": {"id": i + 1, "diam_m": round(c["diam_m"], 1), "depth_m": round(c["depth_m"], 2),
                                     "lon": round(float(lon), 7), "lat": round(float(lat), 7), "risk_peak": 100}})
    write_json(GEO_WEB / "craters_dtm.geojson", fc(feats), compact=True)
    info(f"wrote data/analysis/crater_risk_5m.tif and geology/craters_dtm.geojson ({len(feats)} craters)")
    return True


# ---------------------------------------------------------------- (6) obstacle factor
def process_obstacle(units: np.ndarray | None, legend_units: list[dict[str, Any]], ti: np.ndarray | None,
                     risk_cfg: dict[str, Any]) -> None:
    if units is None:
        warn("geologic units unavailable; obstacle factor skipped")
        return
    lut = np.full(max([u["code"] for u in legend_units] + [0]) + 1, np.nan, dtype=np.float32)
    for u in legend_units:
        lut[u["code"]] = np.nan if u["risk_score"] is None else float(u["risk_score"])
    unit_risk = np.full(units.shape, np.nan, dtype=np.float32)
    ok = np.isfinite(units)
    unit_risk[ok] = lut[units[ok].astype(np.int64)]
    obstacle = unit_risk
    if ti is not None:
        obstacle = np.fmax(unit_risk, piecewise(ti, risk_cfg["ti_prior"]))
        obstacle[~ok] = np.nan  # the TI prior refines the unit score; it never stands in for it
    write_grid_tif(ANALYSIS / "obstacle_risk_5m.tif", obstacle.astype(np.float32))
    info("wrote data/analysis/obstacle_risk_5m.tif" + (" (units + TI prior)" if ti is not None else " (units only)"))


def main() -> int:
    env = load_yaml("env_sources.yaml")
    risk_cfg = load_yaml("geology_risk.yaml")
    GEO_WEB.mkdir(parents=True, exist_ok=True)

    units, legend_units = process_units(env, risk_cfg)
    ti = process_ti(env)
    terrain_classes, terrain_res = process_noah(env, risk_cfg)
    process_robbins(env)
    process_craters(risk_cfg)
    process_obstacle(units, legend_units, ti, risk_cfg)

    legend = {
        "units": legend_units,
        "terrain_classes": terrain_classes,
        "group_colors": {g: v["color"] for g, v in risk_cfg["groups"].items()},
        "group_risk": {g: v["risk_score"] for g, v in risk_cfg["groups"].items()},
        "source": {"units": env["stack2020_units"]["citation"], "terrain_classes": env["noah_h_jezero"]["citation"]},
    }
    if terrain_res is not None:
        legend["terrain_source_res_m"] = round(terrain_res, 3)
    write_json(METADATA / "geology_legend.json", legend)
    info("wrote data/metadata/geology_legend.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
