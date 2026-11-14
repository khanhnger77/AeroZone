"""Terrain products from the HiRISE 1 m DTM over the AOI.

Outputs (GeoTIFF, source CRS, NaN = NoData) in data/processed/:
  elevation_5m.tif   mean of 1 m posts in each 5 m cell              [m above MOLA areoid]
  slope_5m.tif       slope from 5 m grid, central differences (10 m) [degrees]
  roughness_5m.tif   RMS of 1 m detrended relief in each 5 m cell    [m]
  hillshade_5m.tif   analytical hillshade (az 315, alt 35)           [0-255]

Method notes
  * The CRS is equirectangular with standard parallel 0 deg, so a 1 m "projected" step in x
    is only cos(lat) m on the ground at Jezero (~0.949). Slope uses a row-wise corrected dx.
  * Roughness: z - mean(z over 9x9 m) at 1 m removes regional slope, leaving metre-scale relief
    (blocks, ridges, ripples). RMS over each 5 m cell. HiRISE DTM vertical precision is
    ~0.1-0.3 m, so values below ~0.2 m are at the noise floor.
"""
from __future__ import annotations

import json

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from scipy import ndimage

from config import AOI, ANALYSIS_RES_M, METADATA, PROCESSED, RAW, deg_to_proj

DTM = RAW / "hirise_dtm" / "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif"
NULL = -1e30


def block_reduce(a: np.ndarray, f: int, fn=np.nanmean) -> np.ndarray:
    h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    return fn(a[:h, :w].reshape(h // f, f, w // f, f), axis=(1, 3))


def write(path, arr, profile):
    p = dict(profile, dtype="float32", count=1, nodata=np.nan, compress="deflate", tiled=True,
             blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **p) as dst:
        dst.write(arr.astype("float32"), 1)


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    f = int(ANALYSIS_RES_M)
    x0, y0 = deg_to_proj(AOI["lon_min"], AOI["lat_min"])
    x1, y1 = deg_to_proj(AOI["lon_max"], AOI["lat_max"])

    with rasterio.open(DTM) as ds:
        win = from_bounds(x0, y0, x1, y1, ds.transform).round_offsets().round_lengths()
        # snap window size to multiples of the aggregation factor
        win = rasterio.windows.Window(win.col_off, win.row_off, (win.width // f) * f, (win.height // f) * f)
        print(f"reading window {win} ...")
        z = ds.read(1, window=win).astype("float32")
        tr = ds.window_transform(win)
        crs = ds.crs

    z[z < NULL] = np.nan
    print(f"1 m grid {z.shape}, NaN fraction {np.isnan(z).mean():.4f}")

    # --- roughness at 1 m, then aggregate -------------------------------------------------
    zf = np.where(np.isnan(z), np.nanmean(z), z)
    resid = zf - ndimage.uniform_filter(zf, size=9, mode="nearest")
    resid[np.isnan(z)] = np.nan
    rough = np.sqrt(block_reduce(resid ** 2, f))
    del resid, zf

    # --- 5 m elevation ---------------------------------------------------------------------
    elev = block_reduce(z, f)
    del z
    tr5 = tr * rasterio.Affine.scale(f)
    profile = {"driver": "GTiff", "height": elev.shape[0], "width": elev.shape[1], "crs": crs, "transform": tr5}

    # --- slope with latitude-corrected dx ----------------------------------------------------
    rows = np.arange(elev.shape[0])
    lat_rows = np.degrees((tr5.f + (rows + 0.5) * tr5.e) / 3_396_190.0)
    dx = ANALYSIS_RES_M * np.cos(np.radians(lat_rows))[:, None]
    dzdy, dzdx = np.gradient(elev, ANALYSIS_RES_M, axis=0), np.gradient(elev, axis=1) / dx
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))

    # --- hillshade -------------------------------------------------------------------------
    az, alt = np.radians(315), np.radians(35)
    aspect = np.arctan2(-dzdx, dzdy)  # dzdy uses row order (north->south); sign handled below
    s = np.radians(slope)
    hs = np.sin(alt) * np.cos(s) + np.cos(alt) * np.sin(s) * np.cos(az - aspect)
    hill = np.clip(hs, 0, 1) * 255

    for name, arr in [("elevation_5m", elev), ("slope_5m", slope), ("roughness_5m", rough), ("hillshade_5m", hill)]:
        write(PROCESSED / f"{name}.tif", arr, profile)
        print(f"wrote {name}.tif")

    def stats(a):
        v = a[np.isfinite(a)]
        return {k: float(fn(v)) for k, fn in
                [("min", np.min), ("p05", lambda x: np.percentile(x, 5)), ("median", np.median),
                 ("mean", np.mean), ("p95", lambda x: np.percentile(x, 95)), ("max", np.max)]}

    meta = {
        "grid": {"res_m": ANALYSIS_RES_M, "shape": list(elev.shape), "crs_wkt": crs.to_wkt(),
                 "transform": list(tr5)[:6]},
        "elevation_m": stats(elev), "slope_deg": stats(slope), "roughness_m": stats(rough),
        "slope_fraction": {
            "lt_5": float((slope < 5).mean()), "5_15": float(((slope >= 5) & (slope < 15)).mean()),
            "15_25": float(((slope >= 15) & (slope < 25)).mean()), "ge_25": float((slope >= 25).mean())},
    }
    (METADATA / "terrain_stats.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps({k: meta[k] for k in ("elevation_m", "slope_deg", "roughness_m", "slope_fraction")}, indent=1))


if __name__ == "__main__":
    main()
