"""Derive the four C2 terrain rasters (elevation, slope, roughness, hillshade) on the 5 m grid.

Why 5 m: the 1 m DTM is too heavy for an interactive planner and for web overlays, while 5 m still
resolves rover-scale slopes. The 5 m grid is aligned to whole DTM pixels, so elevation is an exact
mean of 5 x 5 posts (no resampling kernel). Roughness keeps the sub-cell information the mean
throws away: the RMS residual of the 1 m surface about its 9 x 9 m moving mean. Slope uses
central differences with a per-row cos(lat)-corrected east-west spacing, because one projected
metre east-west is only cos(lat) ground metres on the equirectangular grid (C1).
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    ANALYSIS_RES_M,
    DTM_PATH,
    GRID_HEIGHT,
    GRID_WIDTH,
    GRID_X0,
    GRID_Y0,
    METADATA,
    PROCESSED,
    ROOT,
    grid_cell_centres,
    info,
    proj_to_deg,
    warn,
    write_grid_tif,
    write_json,
)

BLOCK = int(ANALYSIS_RES_M)  # 1 m posts per 5 m cell edge
ROUGH_WIN = 9                # 9 x 9 m moving mean
HALO = ROUGH_WIN // 2


def read_aoi_1m(dtm_path: Path = DTM_PATH, halo: int = HALO) -> np.ndarray:
    """Read the C2 grid footprint (plus `halo` posts each side) from the 1 m DTM, NaN NoData."""
    import rasterio
    from rasterio.windows import Window

    with rasterio.open(dtm_path) as src:
        res = abs(src.transform.a)
        if abs(res - 1.0) > 1e-9:
            raise ValueError(f"expected a 1 m DTM, got {res} m")
        col0 = (GRID_X0 - src.transform.c) / res
        row0 = (src.transform.f - GRID_Y0) / res
        if abs(col0 - round(col0)) > 1e-6 or abs(row0 - round(row0)) > 1e-6:
            raise ValueError("C2 grid is not aligned to whole DTM posts")
        win = Window(int(round(col0)) - halo, int(round(row0)) - halo,
                     GRID_WIDTH * BLOCK + 2 * halo, GRID_HEIGHT * BLOCK + 2 * halo)
        z = src.read(1, window=win, boundless=True, fill_value=np.nan).astype(np.float32)
        nd = src.nodata
    bad = ~np.isfinite(z) | (z < -1e30)
    if nd is not None and np.isfinite(nd):
        bad |= z == np.float32(nd)
    z[bad] = np.nan
    return z


def block_view(a: np.ndarray) -> np.ndarray:
    """(H*5, W*5) -> (H, W, 25) view of 5 x 5 blocks."""
    h, w = a.shape[0] // BLOCK, a.shape[1] // BLOCK
    return a[: h * BLOCK, : w * BLOCK].reshape(h, BLOCK, w, BLOCK).swapaxes(1, 2).reshape(h, w, BLOCK * BLOCK)


def nan_moving_mean(z: np.ndarray, size: int) -> np.ndarray:
    """Moving mean that ignores NaN (normalised convolution)."""
    valid = np.isfinite(z).astype(np.float32)
    s = uniform_filter(np.where(valid > 0, z, 0.0).astype(np.float32), size=size, mode="nearest")
    n = uniform_filter(valid, size=size, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = s / n
    out[n < 1e-6] = np.nan
    return out


def compute(z1: np.ndarray, halo: int = HALO) -> dict[str, np.ndarray]:
    """All four rasters from the padded 1 m read."""
    core = z1[halo: z1.shape[0] - halo, halo: z1.shape[1] - halo]
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        elev = np.nanmean(block_view(core), axis=2).astype(np.float32)

        resid = z1 - nan_moving_mean(z1, ROUGH_WIN)
        resid_core = resid[halo: z1.shape[0] - halo, halo: z1.shape[1] - halo]
        rough = np.sqrt(np.nanmean(block_view(resid_core) ** 2, axis=2)).astype(np.float32)
    rough[np.isnan(elev)] = np.nan

    slope, aspect_dx, aspect_dy = slope_deg(elev)
    hill = hillshade(aspect_dx, aspect_dy)
    hill[np.isnan(elev)] = np.nan
    return {"elevation": elev, "slope": slope, "roughness": rough, "hillshade": hill}


def _gradients(elev: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """dz/dx_east and dz/dy_north in ground metres (rows run north -> south)."""
    _, ys = grid_cell_centres()
    _, lat = proj_to_deg(0.0, ys)
    dx_ground = ANALYSIS_RES_M * np.cos(np.radians(lat))[:, None]
    dz_dcol = np.gradient(elev, axis=1)          # per column step
    dz_drow = np.gradient(elev, axis=0)          # per row step (southward)
    return dz_dcol / dx_ground, -dz_drow / ANALYSIS_RES_M


def slope_deg(elev: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    gx, gy = _gradients(elev)
    slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)
    return slope, gx, gy


def hillshade(gx: np.ndarray, gy: np.ndarray, azimuth_deg: float = 315.0, altitude_deg: float = 35.0) -> np.ndarray:
    """Lambertian hillshade 0-255 (illumination from azimuth, measured clockwise from north)."""
    az = np.radians(azimuth_deg)
    alt = np.radians(altitude_deg)
    # surface normal n = (-gx, -gy, 1)/|.| ; sun vector s = (sin az cos alt, cos az cos alt, sin alt)
    norm = np.sqrt(gx * gx + gy * gy + 1.0)
    shade = (-gx * np.sin(az) * np.cos(alt) - gy * np.cos(az) * np.cos(alt) + np.sin(alt)) / norm
    return (255.0 * np.clip(shade, 0.0, 1.0)).astype(np.float32)


def _stats(a: np.ndarray, nd: int = 3) -> dict[str, float]:
    v = a[np.isfinite(a)]
    if not v.size:
        return {}
    p = np.percentile(v, [5, 50, 95])
    return {
        "min": round(float(v.min()), nd), "p05": round(float(p[0]), nd), "median": round(float(p[1]), nd),
        "mean": round(float(v.mean()), nd), "p95": round(float(p[2]), nd), "max": round(float(v.max()), nd),
    }


def slope_fraction(slope: np.ndarray) -> dict[str, float]:
    v = slope[np.isfinite(slope)]
    edges = [(0, 5), (5, 15), (15, 25), (25, 30), (30, 90)]
    out = {}
    for lo, hi in edges:
        key = f"{lo}-{hi}" if hi < 90 else f">={lo}"
        out[key] = round(float(((v >= lo) & (v < hi)).mean()), 6) if v.size else 0.0
    return out


def main() -> int:
    if not DTM_PATH.exists():
        warn(f"DTM missing: {DTM_PATH.relative_to(ROOT)} (run scripts/download_data.py)")
        return 1
    info("reading the AOI from the 1 m DTM ...")
    z1 = read_aoi_1m()
    out = compute(z1)
    for name, arr in out.items():
        path = PROCESSED / f"{name}_5m.tif"
        write_grid_tif(path, arr)
        info(f"wrote {path.relative_to(ROOT)}")
    stats = {
        "grid": {
            "width": GRID_WIDTH, "height": GRID_HEIGHT, "res_m": ANALYSIS_RES_M,
            "transform": [ANALYSIS_RES_M, 0.0, GRID_X0, 0.0, -ANALYSIS_RES_M, GRID_Y0],
            "crs": "Mars 2000 sphere equirectangular (ESRI:103885)",
            "valid_fraction": round(float(np.isfinite(out["elevation"]).mean()), 6),
        },
        "elevation_m": _stats(out["elevation"], 2),
        "slope_deg": _stats(out["slope"], 2),
        "roughness_m": _stats(out["roughness"], 4),
        "slope_fraction": slope_fraction(out["slope"]),
    }
    write_json(METADATA / "terrain_stats.json", stats)
    info(f"wrote {(METADATA / 'terrain_stats.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
