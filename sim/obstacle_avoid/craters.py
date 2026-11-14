"""Synthetic impact craters carved into the shared drive-corridor grid (M5).

Why carve instead of adding meshes: a crater is a *negative* obstacle. Carving it into the one
corridor ``Grid`` before M4 builds the collider and serves ``corridor.bin`` (C5) means the PyBullet
heightfield, the Three.js mesh and the lidar all see the same hole. The pre-carve copy of the grid
is kept by the service as the "orbital prior", which is what lets the lidar detect the drop-off.

Profile (simple bowl + ejecta rim): inside ``r ≤ R``: ``dz = h − (d + h)(1 − (r/R)²)``;
outside: ``dz = h·(R/r)³``. ``d`` = depth below the original surface, ``h`` = rim height.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geometry import Route
from .obstacles import site_positions


@dataclass
class Crater:
    id: str
    x: float
    y: float
    radius: float
    depth: float
    rim: float
    s_route: float

    def dz(self, r) -> np.ndarray:
        r = np.asarray(r, dtype=np.float64)
        R, d, h = self.radius, self.depth, self.rim
        inside = h - (d + h) * (1.0 - (r / R) ** 2)
        outside = h * (R / np.maximum(r, 1e-9)) ** 3
        return np.where(r <= R, inside, outside)

    def describe(self, frame=None) -> dict:
        """C5 CraterDesc."""
        lon = lat = None
        if frame is not None:
            lon, lat = frame.to_lonlat(self.x, self.y)
        return {"id": self.id, "x": round(self.x, 3), "y": round(self.y, 3),
                "radius": round(self.radius, 3), "depth": round(self.depth, 3), "rim": round(self.rim, 3),
                "s": round(self.s_route, 2), "lon": lon, "lat": lat}


def generate(path_xy, cfg: dict, seed: int) -> list[Crater]:
    """Craters on the route. Uses its own RNG stream (``seed + 7919``) so toggling craters does not
    reshuffle the boulders."""
    rng = np.random.default_rng(int(seed) + 7919)
    route = Route(path_xy)
    out: list[Crater] = []
    for s in site_positions(route.length, cfg, rng):
        R = float(rng.uniform(*cfg["radius_m"]))
        D = 2 * R
        p = route.point(s)
        hd = float(route.heading(s))
        off = rng.uniform(-cfg["lateral_jitter_m"], cfg["lateral_jitter_m"])
        out.append(Crater(id=f"c{len(out)}", x=float(p[0] - np.sin(hd) * off), y=float(p[1] + np.cos(hd) * off),
                          radius=R, depth=float(D * rng.uniform(*cfg["depth_ratio"])),
                          rim=float(D * rng.uniform(*cfg["rim_ratio"])), s_route=float(s)))
    return out


def apply(grid, craters: list[Crater], reach: float = 3.0) -> None:
    """Add each crater's ``dz`` to ``grid.h`` **in place**, within ``reach·R`` of its centre."""
    h = grid.h
    ny, nx = h.shape
    for c in craters:
        rr = reach * c.radius
        i0 = max(0, int(np.floor((c.x - rr - grid.x0) / grid.dx)))
        i1 = min(nx - 1, int(np.ceil((c.x + rr - grid.x0) / grid.dx)))
        j0 = max(0, int(np.floor((c.y - rr - grid.y0) / grid.dy)))
        j1 = min(ny - 1, int(np.ceil((c.y + rr - grid.y0) / grid.dy)))
        if i1 < i0 or j1 < j0:
            continue
        xs = grid.x0 + np.arange(i0, i1 + 1) * grid.dx
        ys = grid.y0 + np.arange(j0, j1 + 1) * grid.dy
        X, Y = np.meshgrid(xs, ys)
        r = np.hypot(X - c.x, Y - c.y)
        dz = np.where(r <= rr, c.dz(r), 0.0)
        h[j0:j1 + 1, i0:i1 + 1] += dz.astype(h.dtype)
