"""Virtual 360° lidar, 10 m range (M5).

Why a point-splat z-buffer instead of ray casting: the world is a height grid plus a handful of
convex rocks. Splatting a dense polar stencil of terrain points and the rock surface samples into
the sensor's (elevation × azimuth) image, keeping the nearest return per pixel, gives correct
occlusion (rocks hide the ground behind them, hills hide what is beyond) in a few milliseconds of
vectorised NumPy — no per-ray marching and no PyBullet ray batch, so it also runs without PyBullet.

Image layout (C6 LDR1): ``depth[row, col]``, row 0 = lowest elevation, column c = azimuth
``−180° + (c + 0.5)·360°/W``; sensor frame x forward, y left, z up.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geometry import heights, quat_to_matrix, yaw_of


@dataclass
class Scan:
    sim_time: float
    sensor: np.ndarray          # (3,) world position of the sensor
    yaw: float
    rot: np.ndarray             # (3, 3) sensor → world
    depth: np.ndarray           # (H, W) float32 metres, 0 = no return
    points_world: np.ndarray    # (n, 3) one return per lit pixel
    points_sensor: np.ndarray   # (n, 3)
    pixels: np.ndarray          # (n, 2) int (row, col) of each return


class VirtualLidar:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.range_m = float(cfg["range_m"])
        self.mount_height_m = float(cfg["mount_height_m"])
        self.h_res_deg = float(cfg["h_res_deg"])
        self.v_res_deg = float(cfg["v_res_deg"])
        self.v_fov = (float(cfg["v_fov_deg"][0]), float(cfg["v_fov_deg"][1]))
        self.width = int(round(360.0 / self.h_res_deg))                                  # 240
        self.height = int(round((self.v_fov[1] - self.v_fov[0]) / self.v_res_deg))       # 32
        az = np.deg2rad(np.arange(-180.0, 180.0, float(cfg["terrain_az_step_deg"])))
        rs = np.arange(float(cfg["terrain_r_step_m"]), self.range_m + 1e-9, float(cfg["terrain_r_step_m"]))
        A, Rr = np.meshgrid(az, rs)
        # Polar terrain stencil in the sensor's horizontal frame (heading-relative).
        self._sten_r = Rr.ravel()
        self._sten_cos = np.cos(A.ravel())
        self._sten_sin = np.sin(A.ravel())
        self._rays = None

    def describe(self) -> dict:
        """C5 LidarDesc."""
        return {"range_m": self.range_m, "width": self.width, "height": self.height,
                "mount_height_m": self.mount_height_m, "h_res_deg": self.h_res_deg,
                "v_fov_deg": [self.v_fov[0], self.v_fov[1]]}

    def pixel_rays(self) -> np.ndarray:
        """(H, W, 3) unit pixel-centre directions in the sensor frame."""
        if self._rays is None:
            az = np.deg2rad(-180.0 + (np.arange(self.width) + 0.5) * self.h_res_deg)
            el = np.deg2rad(self.v_fov[0] + (np.arange(self.height) + 0.5) * self.v_res_deg)
            E, A = np.meshgrid(el, az, indexing="ij")
            self._rays = np.stack([np.cos(E) * np.cos(A), np.cos(E) * np.sin(A), np.sin(E)], axis=-1)
        return self._rays

    def sensor_pose(self, pose):
        rot = quat_to_matrix(pose.q)
        sensor = np.array([pose.x, pose.y, pose.z], dtype=np.float64) + rot @ np.array([0, 0, self.mount_height_m])
        return sensor, rot

    def scan(self, grid, rocks, pose, t: float) -> Scan:
        sensor, rot = self.sensor_pose(pose)
        yaw = yaw_of(pose.q)
        c, s = np.cos(yaw), np.sin(yaw)
        # Terrain stencil, rotated to the heading so the pattern is stable in the sensor frame.
        cx = c * self._sten_cos - s * self._sten_sin
        cy = s * self._sten_cos + c * self._sten_sin
        tx = sensor[0] + self._sten_r * cx
        ty = sensor[1] + self._sten_r * cy
        parts = [np.stack([tx, ty, heights(grid, tx, ty)], axis=1)]
        n_terrain = len(parts[0])
        reach2 = (self.range_m + 4.0) ** 2
        for rock in rocks or ():
            if (rock.x - sensor[0]) ** 2 + (rock.y - sensor[1]) ** 2 > reach2 or rock.samples is None:
                continue
            smp = rock.samples
            above = smp[:, 2] > heights(grid, smp[:, 0], smp[:, 1])
            parts.append(smp[above])
        pw = np.concatenate(parts, axis=0)
        is_rock = np.arange(len(pw)) >= n_terrain
        ps = (pw - sensor) @ rot                       # world → sensor (rot is orthonormal)
        rng_ = np.linalg.norm(ps, axis=1)
        ok = (rng_ > 0.2) & (rng_ <= self.range_m)
        el = np.degrees(np.arcsin(np.clip(ps[:, 2] / np.maximum(rng_, 1e-9), -1, 1)))
        ok &= (el >= self.v_fov[0]) & (el < self.v_fov[1])
        pw, ps, rng_, el, is_rock = pw[ok], ps[ok], rng_[ok], el[ok], is_rock[ok]
        az = np.degrees(np.arctan2(ps[:, 1], ps[:, 0]))
        colf = (az + 180.0) / self.h_res_deg
        rowf = (el - self.v_fov[0]) / self.v_res_deg
        if is_rock.any():
            # Rock samples are sparser than the pixel grid at short range: splat each one over the
            # pixels its surface patch subtends (patch ≈ 1/sqrt(density)), so rocks are solid occluders.
            k = np.nonzero(is_rock)[0]
            patch = 1.0 / np.sqrt(float(self.cfg.get("rock_density_per_m2", 25.0)))
            half = np.degrees(0.5 * patch / np.maximum(rng_[k], 0.2))
            hc = np.clip(np.ceil(half / self.h_res_deg), 0, 3).astype(np.int64)
            hr = np.clip(np.ceil(half / self.v_res_deg), 0, 3).astype(np.int64)
            extra_idx, extra_dc, extra_dr = [], [], []
            for dc in range(-3, 4):
                for dr in range(-3, 4):
                    if dc == 0 and dr == 0:
                        continue
                    sel = (np.abs(dc) <= hc) & (np.abs(dr) <= hr)
                    if sel.any():
                        extra_idx.append(k[sel])
                        extra_dc.append(np.full(sel.sum(), dc))
                        extra_dr.append(np.full(sel.sum(), dr))
            if extra_idx:
                ei = np.concatenate(extra_idx)
                pw = np.concatenate([pw, pw[ei]])
                ps = np.concatenate([ps, ps[ei]])
                rng_ = np.concatenate([rng_, rng_[ei]])
                colf = np.concatenate([colf, colf[ei] + np.concatenate(extra_dc)])
                rowf = np.concatenate([rowf, rowf[ei] + np.concatenate(extra_dr)])
        col = np.mod(np.floor(colf).astype(np.int64), self.width)          # azimuth wraps
        row = np.floor(rowf).astype(np.int64)
        inside = (row >= 0) & (row < self.height)
        pw, ps, rng_, col, row = pw[inside], ps[inside], rng_[inside], col[inside], row[inside]
        lin = row * self.width + col
        # z-buffer: nearest return per pixel (ties broken by the first candidate).
        zbuf = np.full(self.height * self.width, np.inf)
        np.minimum.at(zbuf, lin, rng_)
        cand = np.nonzero(rng_ <= zbuf[lin])[0]
        _, first = np.unique(lin[cand], return_index=True)
        win = cand[first]
        depth = np.zeros((self.height, self.width), dtype=np.float32)
        depth[row[win], col[win]] = rng_[win]
        return Scan(sim_time=float(t), sensor=sensor, yaw=yaw, rot=rot, depth=depth,
                    points_world=pw[win], points_sensor=ps[win],
                    pixels=np.stack([row[win], col[win]], axis=1))
