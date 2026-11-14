"""Small geometry helpers shared by the M5 modules.

Why a separate module: M5 must work before M4 exists (C8 stubs) and must never import M4 internals
beyond the frozen C8 names. Everything here is duck-typed against those names: a grid is anything
with ``h, x0, y0, dx, dy``; a pose anything with ``x, y, z, q``; a route follower anything with
``path`` (and optionally ``cum``).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

CONFIG_PATH = Path(__file__).with_name("config.yaml")


def load_config(path: str | Path | None = None) -> dict:
    """Read ``config.yaml`` (C10). Values are part of the contract; never duplicate them in code."""
    with open(path or CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def heights(grid, xs, ys) -> np.ndarray:
    """Vectorised bilinear height lookup, clamped to the grid edge.

    Same rule as M4's ``Grid.height_at``: posts at ``x0 + i*dx``, ``y0 + j*dy``, ``h[j, i]``.
    """
    h = grid.h
    ny, nx = h.shape
    fx = np.array(xs, dtype=np.float64, ndmin=1) - grid.x0
    fx *= 1.0 / grid.dx
    np.clip(fx, 0.0, nx - 1.000001, out=fx)
    fy = np.array(ys, dtype=np.float64, ndmin=1) - grid.y0
    fy *= 1.0 / grid.dy
    np.clip(fy, 0.0, ny - 1.000001, out=fy)
    scalar = np.ndim(xs) == 0 and np.ndim(ys) == 0
    i = fx.astype(np.intp)          # fx >= 0, so truncation == floor
    j = fy.astype(np.intp)
    tx = fx - i
    ty = fy - j
    flat = h.ravel()
    k = j * nx + i
    h00 = flat[k]
    h10 = flat[k + 1]
    h01 = flat[k + nx]
    h11 = flat[k + nx + 1]
    a = h00 + (h10 - h00) * tx
    b = h01 + (h11 - h01) * tx
    out = a + (b - a) * ty
    return out[0] if scalar else out


def quat_to_matrix(q) -> np.ndarray:
    """Rotation matrix (body → world) from an ENU quaternion ``(qx, qy, qz, qw)``."""
    x, y, z, w = (float(v) for v in q)
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    return np.array([
        [1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
        [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
        [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)],
    ])


def yaw_of(q) -> float:
    """Heading (rad, 0 = east, CCW positive) of an ENU quaternion."""
    x, y, z, w = (float(v) for v in q)
    return float(np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))


def quat_from_yaw(yaw: float) -> tuple[float, float, float, float]:
    return (0.0, 0.0, float(np.sin(yaw / 2)), float(np.cos(yaw / 2)))


def wrap(a):
    """Wrap angle(s) to (-pi, pi]."""
    return (np.asarray(a) + np.pi) % (2 * np.pi) - np.pi


class Route:
    """Arc-length parametrisation of a polyline route (local ENU, metres)."""

    def __init__(self, xy) -> None:
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        keep = np.ones(len(xy), dtype=bool)
        if len(xy) > 1:
            keep[1:] = np.hypot(*np.diff(xy, axis=0).T) > 1e-9
        self.xy = xy[keep]
        seg = np.hypot(*np.diff(self.xy, axis=0).T) if len(self.xy) > 1 else np.zeros(0)
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])
        self.length = float(self.cum[-1])

    @classmethod
    def of(cls, nav) -> "Route":
        """Build (and cache on the follower) the route of a C8 ``PurePursuitNavigator``."""
        cached = getattr(nav, "_m5_route", None)
        path = nav.path
        if cached is not None and cached[0] is path:
            return cached[1]
        r = cls(path)
        try:
            nav._m5_route = (path, r)
        except AttributeError:  # slots or frozen: just do not cache
            pass
        return r

    def point(self, s) -> np.ndarray:
        s = np.clip(np.asarray(s, dtype=np.float64), 0.0, self.length)
        return np.stack([np.interp(s, self.cum, self.xy[:, 0]),
                         np.interp(s, self.cum, self.xy[:, 1])], axis=-1)

    def heading(self, s, half: float = 0.5) -> np.ndarray:
        s = np.asarray(s, dtype=np.float64)
        a = self.point(s - half)
        b = self.point(s + half)
        d = b - a
        return np.arctan2(d[..., 1], d[..., 0])

    def project(self, x: float, y: float) -> float:
        """Arc length of the closest route point (global search; used off the hot path)."""
        a = self.xy[:-1]
        b = self.xy[1:]
        ab = b - a
        L2 = np.maximum((ab ** 2).sum(1), 1e-12)
        t = np.clip(((np.array([x, y]) - a) * ab).sum(1) / L2, 0, 1)
        p = a + ab * t[:, None]
        d = np.hypot(p[:, 0] - x, p[:, 1] - y)
        k = int(np.argmin(d))
        return float(self.cum[k] + t[k] * np.sqrt(L2[k]))
