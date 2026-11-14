"""Sanity-check the raw sources before anything is derived from them.

Why: a wrong CRS (ellipsoid instead of the Mars 2000 sphere), an AOI that falls off the DTM, or a
rover track in a different datum would silently corrupt every downstream layer. The checks here
are cheap and their results are written to data/metadata/validation_report.json, which is tracked
in git so reviewers can see what the pipeline saw.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    AOI,
    DTM_PATH,
    MARS_RADIUS_M,
    METADATA,
    PERSEVERANCE_DIR,
    ROOT,
    deg_to_proj,
    grid_bounds_proj,
    in_aoi,
    info,
    proj_to_deg,
    read_json,
    warn,
    write_json,
)


def check_crs(crs) -> bool:
    """True when the CRS is equirectangular on a sphere of radius R (C1)."""
    from pyproj import CRS

    c = CRS.from_wkt(crs.to_wkt())
    ell = c.ellipsoid
    sphere = ell is not None and abs(ell.semi_major_metre - MARS_RADIUS_M) < 1.0 and (
        ell.semi_minor_metre is None or abs(ell.semi_minor_metre - MARS_RADIUS_M) < 1.0
    )
    method = (c.coordinate_operation.method_name if c.coordinate_operation else "").lower()
    return bool(sphere and ("equirectangular" in method or "equidistant cylindrical" in method))


def main() -> int:
    import rasterio
    from rasterio.windows import from_bounds

    if not DTM_PATH.exists():
        warn(f"DTM missing: {DTM_PATH.relative_to(ROOT)} (run scripts/download_data.py)")
        return 1
    waypoints = read_json(PERSEVERANCE_DIR / "M20_waypoints.json")["features"]

    with rasterio.open(DTM_PATH) as src:
        crs_ok = check_crs(src.crs)
        res_m = float(abs(src.transform.a))
        b = src.bounds
        lon_w, lat_s = proj_to_deg(b.left, b.bottom)
        lon_e, lat_n = proj_to_deg(b.right, b.top)
        x_w, y_s, x_e, y_n = grid_bounds_proj()
        ax_w, ay_s = deg_to_proj(AOI["lon_w"], AOI["lat_s"])
        ax_e, ay_n = deg_to_proj(AOI["lon_e"], AOI["lat_n"])
        aoi_inside = bool(b.left <= ax_w and ax_e <= b.right and b.bottom <= ay_s and ay_n <= b.top)

        win = from_bounds(x_w, y_s, x_e, y_n, transform=src.transform).round_offsets().round_lengths()
        aoi = src.read(1, window=win, masked=True).astype(np.float64)
        valid = ~np.ma.getmaskarray(aoi) & np.isfinite(aoi.filled(np.nan)) & (aoi.filled(np.nan) > -1e30)
        nodata_fraction = float(1.0 - valid.mean())
        vals = aoi.filled(np.nan)[valid]
        elev_range = [round(float(vals.min()), 2), round(float(vals.max()), 2)] if vals.size else [None, None]

        lons = np.array([f["properties"]["lon"] for f in waypoints], dtype=float)
        lats = np.array([f["properties"]["lat"] for f in waypoints], dtype=float)
        elev_rover = np.array([f["properties"].get("elev_geoid", np.nan) for f in waypoints], dtype=float)
        xs, ys = deg_to_proj(lons, lats)
        inside_dtm = (xs >= b.left) & (xs < b.right) & (ys > b.bottom) & (ys <= b.top)
        inside_aoi = np.array([in_aoi(lo, la) for lo, la in zip(lons, lats)])

        dtm_at = np.full(len(waypoints), np.nan)
        idx = np.nonzero(inside_dtm)[0]
        if idx.size:
            sampled = np.array([v[0] for v in src.sample(zip(xs[idx], ys[idx]))], dtype=float)
            sampled[(sampled < -1e30) | (sampled == src.nodata)] = np.nan
            dtm_at[idx] = sampled
        diff = dtm_at - elev_rover
        ok = np.isfinite(diff) & (elev_rover > -9000)
        d = diff[ok]

    report = {
        "dtm_crs_ok": crs_ok,
        "dtm_res_m": res_m,
        "dtm_bounds_deg": [round(float(v), 6) for v in (lon_w, lat_s, lon_e, lat_n)],
        "aoi_inside_dtm": aoi_inside,
        "aoi_nodata_fraction": round(nodata_fraction, 6),
        "aoi_elevation_range_m": elev_range,
        "waypoints_total": int(len(waypoints)),
        "waypoints_inside_dtm": int(inside_dtm.sum()),
        "waypoints_inside_aoi": int(inside_aoi.sum()),
        "elev_check_n": int(d.size),
        "elev_diff_dtm_minus_rover_m": {
            "mean": round(float(d.mean()), 3) if d.size else None,
            "median": round(float(np.median(d)), 3) if d.size else None,
            "std": round(float(d.std()), 3) if d.size else None,
            "abs_p95": round(float(np.percentile(np.abs(d), 95)), 3) if d.size else None,
        },
    }
    write_json(METADATA / "validation_report.json", report)
    info(f"wrote {(METADATA / 'validation_report.json').relative_to(ROOT)}")
    if not crs_ok:
        warn("DTM CRS is not the Mars 2000 sphere equirectangular (C1)")
    if not aoi_inside:
        warn("AOI is not fully inside the DTM")
    return 0 if (crs_ok and aoi_inside) else 1


if __name__ == "__main__":
    sys.exit(main())
