"""Terrain grid access for the route planner (contracts C1 and C2).

Why this module exists: every endpoint needs the same four co-registered 5 m
rasters and the same lon/lat <-> pixel arithmetic. Loading them once (cached)
and keeping the projection maths in one place guarantees the API, the router
and the tests all agree with M1's grid to the cell.

The raster CRS is the equirectangular Mars sphere (``ESRI:103885``), so the
projection is closed-form (``x = R·rad(lon)``, ``y = R·rad(lat)``) and needs
no PROJ call. A projected east-west metre is only ``cos(lat)`` ground metres,
which is why :meth:`Terrain.ground_dx` exists.

``ROOT`` is the repo root. Setting ``MARS_ODYSSEY_ROOT`` points it at another
tree with the same layout (used by the stub data before M1's pipeline runs).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio

# ---------------------------------------------------------------- constants (C1)
MARS_RADIUS_M = 3_396_190.0

ROOT = Path(os.environ.get("MARS_ODYSSEY_ROOT", Path(__file__).resolve().parents[2]))

RASTER_PATHS = {
    "elevation": "data/processed/elevation_5m.tif",
    "slope": "data/processed/slope_5m.tif",
    "roughness": "data/processed/roughness_5m.tif",
    "hazard": "data/analysis/hazard_5m.tif",
}


@dataclass(frozen=True)
class Terrain:
    """The four C2 rasters plus the affine transform ``(a, b, c, d, e, f)``."""

    elevation: np.ndarray
    slope: np.ndarray
    roughness: np.ndarray
    hazard: np.ndarray
    transform: tuple[float, float, float, float, float, float]
    res_m: float

    @property
    def shape(self) -> tuple[int, int]:
        return self.elevation.shape  # (rows, cols)

    def lonlat_to_rc(self, lon: float, lat: float) -> tuple[int, int]:
        """Cell containing ``(lon, lat)``; may lie outside the grid (see :meth:`inside`)."""
        a, _, c, _, e, f = self.transform
        x = MARS_RADIUS_M * np.radians(lon)
        y = MARS_RADIUS_M * np.radians(lat)
        return int(np.floor((y - f) / e)), int(np.floor((x - c) / a))

    def rc_to_lonlat(self, row, col):
        """Lon/lat of cell centres. Accepts scalars or arrays."""
        a, _, c, _, e, f = self.transform
        x = c + (np.asarray(col, dtype=float) + 0.5) * a
        y = f + (np.asarray(row, dtype=float) + 0.5) * e
        return np.degrees(x / MARS_RADIUS_M), np.degrees(y / MARS_RADIUS_M)

    def inside(self, r: int, c: int) -> bool:
        rows, cols = self.shape
        return 0 <= r < rows and 0 <= c < cols

    def ground_dx(self, row: float) -> float:
        """Ground length in metres of one east-west cell at ``row``."""
        _, lat = self.rc_to_lonlat(row, 0)
        return float(self.res_m * np.cos(np.radians(lat)))


def _read_band(path: Path) -> tuple[np.ndarray, tuple[float, ...]]:
    with rasterio.open(path) as ds:
        arr = ds.read(1).astype(np.float32)
        if ds.nodata is not None and not np.isnan(ds.nodata):
            arr[arr == ds.nodata] = np.nan
        t = ds.transform
        return arr, (t.a, t.b, t.c, t.d, t.e, t.f)


@lru_cache(maxsize=1)
def get_terrain() -> Terrain:
    """Read the four rasters once. Raises ``FileNotFoundError`` if M1's data is missing."""
    arrays: dict[str, np.ndarray] = {}
    transform: tuple[float, ...] | None = None
    for name, rel in RASTER_PATHS.items():
        path = ROOT / rel
        if not path.exists():
            raise FileNotFoundError(f"missing raster {path} (run `pixi run data`)")
        arrays[name], t = _read_band(path)
        if transform is None:
            transform = t
        elif not np.allclose(t, transform):
            raise ValueError(f"{path} is not on the shared C2 grid")
    assert transform is not None
    return Terrain(**arrays, transform=transform, res_m=float(transform[0]))


def haversine_m(lon1, lat1, lon2, lat2):
    """Great-circle distance on the Mars sphere. Vectorised over NumPy arrays."""
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    h = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * MARS_RADIUS_M * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))
