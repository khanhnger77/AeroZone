"""Validate raw datasets before any integration.

Checks
  1. DTM CRS is the expected Mars 2000 sphere equirectangular (not Earth WGS84).
  2. AOI lies fully inside the DTM extent.
  3. NoData fraction inside the AOI.
  4. Fraction of Perseverance waypoints inside the DTM / AOI.
  5. Independent elevation cross-check: DTM height vs rover-reported elevation (elev_geoid)
     at each waypoint. Both are heights above the MOLA areoid, so the difference should be
     small; a large bias would mean a datum or georeferencing problem.

Writes data/metadata/validation_report.json.
"""
from __future__ import annotations

import json

import numpy as np
import rasterio

from config import AOI, MARS_RADIUS_M, METADATA, RAW, deg_to_proj

DTM = RAW / "hirise_dtm" / "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif"
WAYPOINTS = RAW / "perseverance" / "M20_waypoints.json"
ISIS_NULL_THRESHOLD = -1e30  # ISIS special pixels are ~-3.4e38


def main() -> None:
    report: dict = {}
    with rasterio.open(DTM) as ds:
        crs = ds.crs.to_wkt()
        report["dtm_crs_ok"] = ("3396190" in crs) and ("Equidistant Cylindrical" in crs or "Equirectangular" in crs)
        report["dtm_res_m"] = ds.res
        b = ds.bounds
        report["dtm_bounds_deg"] = [np.degrees(v / MARS_RADIUS_M) for v in (b.left, b.bottom, b.right, b.top)]

        x0, y0 = deg_to_proj(AOI["lon_min"], AOI["lat_min"])
        x1, y1 = deg_to_proj(AOI["lon_max"], AOI["lat_max"])
        report["aoi_inside_dtm"] = bool(b.left <= x0 and x1 <= b.right and b.bottom <= y0 and y1 <= b.top)

        # NoData fraction in AOI (decimated read for speed).
        win = rasterio.windows.from_bounds(x0, y0, x1, y1, ds.transform)
        arr = ds.read(1, window=win, out_shape=(int(win.height // 10), int(win.width // 10)))
        report["aoi_nodata_fraction"] = float((arr < ISIS_NULL_THRESHOLD).mean())
        report["aoi_elevation_range_m"] = [float(arr[arr > ISIS_NULL_THRESHOLD].min()),
                                           float(arr[arr > ISIS_NULL_THRESHOLD].max())]

        # Rover waypoints vs DTM.
        feats = json.loads(WAYPOINTS.read_text())["features"]
        pts = [(f["properties"]["lon"], f["properties"]["lat"], f["properties"]["elev_geoid"]) for f in feats]
        xy = [deg_to_proj(lon, lat) for lon, lat, _ in pts]
        inside_dtm = [b.left <= x <= b.right and b.bottom <= y <= b.top for x, y in xy]
        inside_aoi = [AOI["lon_min"] <= lon <= AOI["lon_max"] and AOI["lat_min"] <= lat <= AOI["lat_max"]
                      for lon, lat, _ in pts]
        report["waypoints_total"] = len(pts)
        report["waypoints_inside_dtm"] = int(sum(inside_dtm))
        report["waypoints_inside_aoi"] = int(sum(inside_aoi))

        sample_xy = [p for p, ok in zip(xy, inside_dtm) if ok]
        rover_z = np.array([p[2] for p, ok in zip(pts, inside_dtm) if ok])
        dtm_z = np.array([v[0] for v in ds.sample(sample_xy)])
        valid = dtm_z > ISIS_NULL_THRESHOLD
        diff = dtm_z[valid] - rover_z[valid]
        report["elev_check_n"] = int(valid.sum())
        report["elev_diff_dtm_minus_rover_m"] = {
            "median": float(np.median(diff)),
            "mean": float(diff.mean()),
            "p05": float(np.percentile(diff, 5)),
            "p95": float(np.percentile(diff, 95)),
            "abs_median": float(np.median(np.abs(diff))),
        }

    METADATA.mkdir(parents=True, exist_ok=True)
    out = METADATA / "validation_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
