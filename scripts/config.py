"""Shared paths, constants and coordinate helpers for the M1 data pipeline (contract C1/C2).

Why one module: every script, and every other module that imports from here, must agree on the
Mars radius, the study area, the 5 m analysis grid and the projected <-> lon/lat conversion. A
single source of truth keeps the C2 rasters, the C3 web overlays and the planner on the same grid.

It also points PROJ and GDAL at the data files of the running interpreter's prefix, so a conda /
pixi environment works even when the shell has stale PROJ_LIB / GDAL_DATA variables.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np


def _set_geo_env() -> None:
    """Point PROJ_DATA / GDAL_DATA at the active environment (conda layout, all platforms)."""
    prefix = Path(sys.prefix)
    candidates = {
        "PROJ_DATA": [prefix / "share" / "proj", prefix / "Library" / "share" / "proj"],
        "GDAL_DATA": [prefix / "share" / "gdal", prefix / "Library" / "share" / "gdal"],
    }
    for var, paths in candidates.items():
        for p in paths:
            if p.is_dir():
                os.environ[var] = str(p)
                if var == "PROJ_DATA":
                    os.environ["PROJ_LIB"] = str(p)
                break


_set_geo_env()

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
ANALYSIS = DATA / "analysis"
METADATA = DATA / "metadata"
WEB = ROOT / "frontend" / "public" / "data"
CONFIG_DIR = Path(__file__).resolve().parent / "config"

DTM_DIR = RAW / "hirise_dtm"
DTM_FILENAME = "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif"
DTM_PATH = DTM_DIR / DTM_FILENAME
PERSEVERANCE_DIR = RAW / "perseverance"

# ---------------------------------------------------------------- C1 constants
MARS_RADIUS_M = 3_396_190.0
MARS_GRAVITY_M_S2 = 3.711
MARS_AIR_DENSITY_KG_M3 = 0.020

# Study area (AOI), east-positive lon, planetocentric lat, degrees.
AOI: dict[str, float] = {"lon_w": 77.29, "lon_e": 77.47, "lat_s": 18.42, "lat_n": 18.505}
AOI_CENTRE: tuple[float, float] = ((AOI["lon_w"] + AOI["lon_e"]) / 2, (AOI["lat_s"] + AOI["lat_n"]) / 2)

ANALYSIS_RES_M = 5.0

# The C2 grid, fixed by contract: every C2 / C2-ext raster has exactly this shape and transform.
GRID_WIDTH = 2133
GRID_HEIGHT = 1007
GRID_X0 = 4_581_341.0
GRID_Y0 = 1_096_879.0
GRID_TRANSFORM_GDAL = (ANALYSIS_RES_M, 0.0, GRID_X0, 0.0, -ANALYSIS_RES_M, GRID_Y0)

# ESRI:103885 = Mars 2000 sphere, equirectangular, standard parallel 0, central meridian 0.
MARS_EQC_PROJ4 = (
    f"+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 "
    f"+a={MARS_RADIUS_M:.0f} +b={MARS_RADIUS_M:.0f} +units=m +no_defs"
)
MARS_GEOG_PROJ4 = f"+proj=longlat +a={MARS_RADIUS_M:.0f} +b={MARS_RADIUS_M:.0f} +no_defs"


def grid_transform():
    """The C2 grid as a rasterio Affine."""
    from rasterio.transform import Affine

    return Affine(ANALYSIS_RES_M, 0.0, GRID_X0, 0.0, -ANALYSIS_RES_M, GRID_Y0)


# The exact CRS the HiRISE DTM carries; reused so every C2 raster has the same WKT as its source.
MARS_EQC_WKT = (
    'PROJCS["Equirectangular Mars 2000 Sphere IAU",GEOGCS["D_Mars_2000_Sphere",'
    'DATUM["Mars_2000_(Sphere)",SPHEROID["Mars_2000_Sphere_IAU",3396190,0],AUTHORITY["ESRI","106971"]],'
    'PRIMEM["Reference_Meridian",0],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]]],'
    'PROJECTION["Equirectangular"],PARAMETER["standard_parallel_1",0],PARAMETER["central_meridian",0],'
    'PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
)


def grid_crs():
    """The C1 raster CRS as a rasterio CRS (sphere, so no ellipsoid ambiguity)."""
    from rasterio.crs import CRS

    return CRS.from_wkt(MARS_EQC_WKT)


def grid_bounds_proj() -> tuple[float, float, float, float]:
    """(x_w, y_s, x_e, y_n) pixel-edge bounds of the C2 grid, projected metres."""
    return (
        GRID_X0,
        GRID_Y0 - GRID_HEIGHT * ANALYSIS_RES_M,
        GRID_X0 + GRID_WIDTH * ANALYSIS_RES_M,
        GRID_Y0,
    )


def grid_bounds_latlon() -> list[list[float]]:
    """[[lat_s, lon_w], [lat_n, lon_e]] of the C2 grid's pixel edges (C3 `bounds`)."""
    x_w, y_s, x_e, y_n = grid_bounds_proj()
    lon_w, lat_s = proj_to_deg(x_w, y_s)
    lon_e, lat_n = proj_to_deg(x_e, y_n)
    return [[round(float(lat_s), 8), round(float(lon_w), 8)], [round(float(lat_n), 8), round(float(lon_e), 8)]]


def grid_cell_centres() -> tuple[np.ndarray, np.ndarray]:
    """Projected x of each column centre and y of each row centre."""
    xs = GRID_X0 + (np.arange(GRID_WIDTH) + 0.5) * ANALYSIS_RES_M
    ys = GRID_Y0 - (np.arange(GRID_HEIGHT) + 0.5) * ANALYSIS_RES_M
    return xs, ys


# ---------------------------------------------------------------- coordinates
def normalise_lon(lon: Any) -> Any:
    """Any east-positive longitude (0..360 or -180..180) to -180..180."""
    lon = np.asarray(lon, dtype=float)
    out = (lon + 180.0) % 360.0 - 180.0
    return out if out.shape else float(out)


def deg_to_proj(lon: Any, lat: Any) -> tuple[Any, Any]:
    """Lon/lat degrees -> projected metres on the C1 sphere (x = R*rad(lon), y = R*rad(lat))."""
    return MARS_RADIUS_M * np.radians(lon), MARS_RADIUS_M * np.radians(lat)


def proj_to_deg(x: Any, y: Any) -> tuple[Any, Any]:
    """Projected metres -> lon/lat degrees (inverse of deg_to_proj)."""
    return np.degrees(np.asarray(x, dtype=float) / MARS_RADIUS_M), np.degrees(np.asarray(y, dtype=float) / MARS_RADIUS_M)


def haversine_m(lon1: Any, lat1: Any, lon2: Any, lat2: Any) -> Any:
    """Great-circle distance on the Mars sphere, metres. Never use an Earth distance."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * MARS_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def in_aoi(lon: float, lat: float) -> bool:
    return AOI["lon_w"] <= lon <= AOI["lon_e"] and AOI["lat_s"] <= lat <= AOI["lat_n"]


# ---------------------------------------------------------------- IO helpers
def write_json(path: Path, obj: Any, compact: bool = False) -> None:
    """Write JSON with stable formatting; compact for web payloads, indented for metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    else:
        text = json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(name: str) -> Any:
    """Load scripts/config/<name>."""
    import yaml

    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def write_grid_tif(path: Path, arr: np.ndarray) -> None:
    """Write one C2 raster: single band float32, NaN NoData, C1 CRS, the fixed 5 m transform.

    Deterministic output (no timestamps, fixed compression) so the default hazard stays
    byte-identical across runs.
    """
    import rasterio

    if arr.shape != (GRID_HEIGHT, GRID_WIDTH):
        raise ValueError(f"{path.name}: shape {arr.shape} != C2 grid {(GRID_HEIGHT, GRID_WIDTH)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "width": GRID_WIDTH,
        "height": GRID_HEIGHT,
        "count": 1,
        "dtype": "float32",
        "nodata": float("nan"),
        "crs": grid_crs(),
        "transform": grid_transform(),
        "compress": "deflate",
        "predictor": 3,
        "tiled": False,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype(np.float32), 1)


def read_grid_tif(path: Path) -> np.ndarray:
    import rasterio

    with rasterio.open(path) as src:
        return src.read(1).astype(np.float32)


def warn(msg: str) -> None:
    """Yellow one-line warning on stderr (used for optional, degradable sources)."""
    sys.stderr.write(f"\033[33mWARNING: {msg}\033[0m\n")
    sys.stderr.flush()


def info(msg: str) -> None:
    print(msg, flush=True)
