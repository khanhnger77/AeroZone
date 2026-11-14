"""Terrain access for the sim: GeoTIFF window reads, the local ENU frame and height grids.

Why this module exists: the renderer, the PyBullet collider and M5's lidar must all see the *same*
heights ("what you see is what you hit"), so every consumer works from one `Grid` in one local frame.
The frame is a flat-plane ENU approximation (C1): curvature is ignored over a few kilometres, which
keeps the physics in plain metres while the raster stays in its equirectangular CRS.

Conventions (C1/C5/C8):
  * `Grid.h[j, i]`: row j runs south -> north, column i runs west -> east, metres relative to `z0`.
  * `x0, y0` are the local coordinates of the centre of post (0, 0), i.e. the south-west post.
  * `dx = res_x * cos(lat0)` because an east-west projected metre is `cos(lat)` ground metres.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MARS_RADIUS_M = 3_396_190.0  # IAU Mars 2000 sphere (C1)


def _scalar_or_array(a: np.ndarray):
    """Return a Python float for 0-d input, otherwise the ndarray, so callers can pass either."""
    return float(a) if np.ndim(a) == 0 else a


class Frame:
    """Local east-north-up frame centred on the drive corridor.

    `x = R*cos(lat0)*rad(lon - lon0)` (east), `y = R*rad(lat - lat0)` (north), `z = elevation - z0`.
    """

    def __init__(self, lon0: float, lat0: float, z0: float, radius_m: float = MARS_RADIUS_M) -> None:
        self.lon0 = float(lon0)
        self.lat0 = float(lat0)
        self.z0 = float(z0)
        self.radius_m = float(radius_m)
        self._kx = self.radius_m * math.cos(math.radians(self.lat0))  # m per radian, east
        self._ky = self.radius_m                                       # m per radian, north

    def to_local(self, lon, lat):
        """(lon, lat) in degrees -> local (x east, y north) in metres. Accepts scalars or arrays."""
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        x = self._kx * np.radians(lon - self.lon0)
        y = self._ky * np.radians(lat - self.lat0)
        return _scalar_or_array(x), _scalar_or_array(y)

    def to_lonlat(self, x, y):
        """Local (x, y) in metres -> (lon, lat) in degrees. Inverse of `to_local`."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        lon = self.lon0 + np.degrees(x / self._kx)
        lat = self.lat0 + np.degrees(y / self._ky)
        return _scalar_or_array(lon), _scalar_or_array(lat)

    def describe(self) -> dict:
        return {"lon0": self.lon0, "lat0": self.lat0, "z0": self.z0, "radius_m": self.radius_m}


@dataclass
class Grid:
    """Regular height grid in the local ENU frame (C8). Copy with `dataclasses.replace(g, h=g.h.copy())`."""

    h: np.ndarray            # float32 (ny, nx), metres relative to z0
    x0: float                # local x of the column-0 post centre
    y0: float                # local y of the row-0 post centre
    dx: float                # ground spacing east-west (m)
    dy: float                # ground spacing north-south (m)
    lon_w: float
    lon_e: float
    lat_s: float
    lat_n: float
    res_src_m: float         # native source resolution (m)
    meta: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.h.shape  # (ny, nx)

    @property
    def nx(self) -> int:
        return self.h.shape[1]

    @property
    def ny(self) -> int:
        return self.h.shape[0]

    def extent(self) -> tuple[float, float, float, float]:
        """(x_min, x_max, y_min, y_max) spanned by the post centres."""
        return (self.x0, self.x0 + (self.nx - 1) * self.dx,
                self.y0, self.y0 + (self.ny - 1) * self.dy)

    def height_at(self, x, y):
        """Bilinear height (m, relative to z0), clamped to the grid edge. Accepts scalars or arrays."""
        fx = np.clip((np.asarray(x, dtype=np.float64) - self.x0) / self.dx, 0.0, self.nx - 1.0)
        fy = np.clip((np.asarray(y, dtype=np.float64) - self.y0) / self.dy, 0.0, self.ny - 1.0)
        i0 = np.minimum(np.floor(fx).astype(np.intp), self.nx - 2)
        j0 = np.minimum(np.floor(fy).astype(np.intp), self.ny - 2)
        tx = fx - i0
        ty = fy - j0
        h = self.h
        z = ((1 - tx) * (1 - ty) * h[j0, i0] + tx * (1 - ty) * h[j0, i0 + 1]
             + (1 - tx) * ty * h[j0 + 1, i0] + tx * ty * h[j0 + 1, i0 + 1])
        return _scalar_or_array(z)

    def describe(self) -> dict:
        """The C5 `GridDesc` for this grid."""
        return {
            "nx": self.nx, "ny": self.ny,
            "x0": float(self.x0), "y0": float(self.y0),
            "dx": float(self.dx), "dy": float(self.dy),
            "lon_w": self.lon_w, "lon_e": self.lon_e, "lat_s": self.lat_s, "lat_n": self.lat_n,
            "h_min": float(self.h.min()), "h_max": float(self.h.max()),
            "res_src_m": float(self.res_src_m),
            "label": self.meta.get("label", ""),
            "resampled": bool(self.meta.get("resampled", False)),
        }


class TerrainSource:
    """Windowed reader for the C2 elevation GeoTIFF (equirectangular, sphere radius R, north-up)."""

    def __init__(self, path: str | Path) -> None:
        import rasterio  # imported lazily so the module (Frame/Grid) loads without GDAL

        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"terrain raster not found: {self.path}")
        with rasterio.open(self.path) as ds:
            t = ds.transform
            self.width, self.height = ds.width, ds.height
            self.res_x = float(t.a)
            self.res_y = float(-t.e)
            self.x_west = float(t.c)   # projected x of the raster's west edge
            self.y_north = float(t.f)  # projected y of the raster's north edge

    # projected metres <-> degrees on the C1 sphere (x = R*rad(lon), y = R*rad(lat))
    @staticmethod
    def _px_to_lon(px: float) -> float:
        return math.degrees(px / MARS_RADIUS_M)

    @staticmethod
    def _lon_to_px(lon: float) -> float:
        return MARS_RADIUS_M * math.radians(lon)

    def aoi_lonlat(self) -> tuple[float, float, float, float]:
        """(lon_w, lon_e, lat_s, lat_n) of the whole raster."""
        x_east = self.x_west + self.width * self.res_x
        y_south = self.y_north - self.height * self.res_y
        return (self._px_to_lon(self.x_west), self._px_to_lon(x_east),
                self._px_to_lon(y_south), self._px_to_lon(self.y_north))

    def read(self, lon_w: float, lon_e: float, lat_s: float, lat_n: float, frame: Frame,
             max_points: int, target_res_m: float | None = None, label: str = "") -> Grid:
        """Read a window as a `Grid` in `frame`.

        The window is snapped outwards to whole source pixels and clipped to the raster. When the
        target resolution is coarser than the source, or the window exceeds `max_points` per side,
        GDAL block-averages (`Resampling.average`) instead of decimating, which keeps relief honest.
        NaN holes are filled with the nearest valid value so the collider has no gaps.
        """
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.windows import Window
        from scipy import ndimage

        col0 = max(0, int(math.floor((self._lon_to_px(lon_w) - self.x_west) / self.res_x)))
        col1 = min(self.width, int(math.ceil((self._lon_to_px(lon_e) - self.x_west) / self.res_x)))
        row0 = max(0, int(math.floor((self.y_north - self._lon_to_px(lat_n)) / self.res_y)))
        row1 = min(self.height, int(math.ceil((self.y_north - self._lon_to_px(lat_s)) / self.res_y)))
        w, hgt = col1 - col0, row1 - row0
        if w < 2 or hgt < 2:
            raise ValueError("requested window lies outside the terrain raster")

        scale = max(1.0, float(max(w, hgt)) / float(max_points))
        if target_res_m:
            scale = max(scale, float(target_res_m) / self.res_x)
        nx = max(2, int(w // scale))
        ny = max(2, int(hgt // scale))

        with rasterio.open(self.path) as ds:
            data = ds.read(1, window=Window(col0, row0, w, hgt), out_shape=(ny, nx),
                           resampling=Resampling.average).astype(np.float32)

        invalid = ~np.isfinite(data)
        if invalid.all():
            raise ValueError("terrain window contains no valid elevation data")
        if invalid.any():
            idx = ndimage.distance_transform_edt(invalid, return_distances=False, return_indices=True)
            data = data[tuple(idx)]

        data = np.ascontiguousarray(data[::-1] - np.float32(frame.z0), dtype=np.float32)  # south -> north

        # Edges of the snapped window (projected metres) and the output post pitch.
        px_w = self.x_west + col0 * self.res_x
        px_e = self.x_west + col1 * self.res_x
        py_n = self.y_north - row0 * self.res_y
        py_s = self.y_north - row1 * self.res_y
        res_x_out = (px_e - px_w) / nx
        res_y_out = (py_n - py_s) / ny
        cos_lat0 = math.cos(math.radians(frame.lat0))

        # Local coordinates of the centre of the first (south-west) post.
        x0 = cos_lat0 * (px_w + 0.5 * res_x_out - self._lon_to_px(frame.lon0))
        y0 = (py_s + 0.5 * res_y_out) - self._lon_to_px(frame.lat0)

        return Grid(
            h=data, x0=float(x0), y0=float(y0),
            dx=float(res_x_out * cos_lat0), dy=float(res_y_out),
            lon_w=self._px_to_lon(px_w), lon_e=self._px_to_lon(px_e),
            lat_s=self._px_to_lon(py_s), lat_n=self._px_to_lon(py_n),
            res_src_m=self.res_x,
            meta={"label": label, "resampled": (nx != w) or (ny != hgt)},
        )


def build_mission_terrain(src: TerrainSource, coords, cfg: dict) -> tuple[Frame, Grid, Grid]:
    """Build the local frame plus the drive-corridor and context grids for a route.

    * Corridor: route bounding box + `corridor_margin_m`, clipped to the AOI, at native resolution
      (down-averaged only if a side would exceed `max_grid_points`). It is both collider and mesh.
    * Context: the whole AOI at `context_res_m`, visual only.
    * The frame origin is the corridor centre; `z0` is the corridor mean elevation rounded to 0.1 m.
    `cfg` is the parsed physics.yaml (or its `terrain` section). `coords` is a list of [lon, lat].
    """
    t = cfg.get("terrain", cfg)
    pts = np.asarray(coords, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[0] < 2 or pts.shape[1] != 2:
        raise ValueError("route needs at least two [lon, lat] points")

    aoi_w, aoi_e, aoi_s, aoi_n = src.aoi_lonlat()
    lon_min, lat_min = pts.min(axis=0)
    lon_max, lat_max = pts.max(axis=0)
    if lon_max < aoi_w or lon_min > aoi_e or lat_max < aoi_s or lat_min > aoi_n:
        raise ValueError("route lies outside the terrain AOI")

    margin_m = float(t["corridor_margin_m"])
    lat_mid = 0.5 * (lat_min + lat_max)
    dlat = math.degrees(margin_m / MARS_RADIUS_M)
    dlon = math.degrees(margin_m / (MARS_RADIUS_M * math.cos(math.radians(lat_mid))))
    c_w, c_e = max(aoi_w, lon_min - dlon), min(aoi_e, lon_max + dlon)
    c_s, c_n = max(aoi_s, lat_min - dlat), min(aoi_n, lat_max + dlat)

    lon0, lat0 = 0.5 * (c_w + c_e), 0.5 * (c_s + c_n)
    provisional = Frame(lon0, lat0, 0.0)
    corridor = src.read(c_w, c_e, c_s, c_n, provisional, int(t["max_grid_points"]), label="corridor")

    z0 = round(float(corridor.h.mean()), 1)   # read with z0 = 0, so h is absolute elevation
    corridor.h -= np.float32(z0)
    frame = Frame(lon0, lat0, z0)

    context = src.read(aoi_w, aoi_e, aoi_s, aoi_n, frame, int(t["max_grid_points"]),
                       target_res_m=float(t["context_res_m"]), label="context")
    return frame, corridor, context
