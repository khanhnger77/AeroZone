"""Loads the 5 m analysis grids once and offers lon/lat <-> grid helpers."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
ANALYSIS = ROOT / "data" / "analysis"
MARS_RADIUS_M = 3_396_190.0


@dataclass
class Terrain:
    elevation: np.ndarray
    slope: np.ndarray
    roughness: np.ndarray
    hazard: np.ndarray
    transform: rasterio.Affine
    res_m: float

    @property
    def shape(self) -> tuple[int, int]:
        return self.elevation.shape

    def lonlat_to_rc(self, lon: float, lat: float) -> tuple[int, int]:
        x, y = MARS_RADIUS_M * math.radians(lon), MARS_RADIUS_M * math.radians(lat)
        t = self.transform
        return int((y - t.f) / t.e), int((x - t.c) / t.a)

    def rc_to_lonlat(self, row: float, col: float) -> tuple[float, float]:
        t = self.transform  # north-up, no rotation
        x, y = t.c + (col + 0.5) * t.a, t.f + (row + 0.5) * t.e
        return math.degrees(x / MARS_RADIUS_M), math.degrees(y / MARS_RADIUS_M)

    def inside(self, row: int, col: int) -> bool:
        return 0 <= row < self.shape[0] and 0 <= col < self.shape[1]

    def ground_dx(self, row: float) -> float:
        """East-west ground size of a cell at this row (equirectangular, clat=0)."""
        _, lat = self.rc_to_lonlat(row, 0)
        return self.res_m * math.cos(math.radians(lat))


def _read(path: Path) -> tuple[np.ndarray, rasterio.Affine]:
    with rasterio.open(path) as ds:
        return ds.read(1), ds.transform


@lru_cache(maxsize=1)
def get_terrain() -> Terrain:
    elev, tr = _read(PROCESSED / "elevation_5m.tif")
    slope, _ = _read(PROCESSED / "slope_5m.tif")
    rough, _ = _read(PROCESSED / "roughness_5m.tif")
    hazard, _ = _read(ANALYSIS / "hazard_5m.tif")
    return Terrain(elev, slope, rough, hazard, tr, abs(tr.a))


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * MARS_RADIUS_M * math.asin(math.sqrt(a))
