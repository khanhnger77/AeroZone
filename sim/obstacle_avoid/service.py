"""``ObstacleAvoidance``: the only M5 object M4 talks to (contract C8).

Why a facade: M4's 50 Hz loop must stay simple and must not know about lidar, maps or planners.
It calls ``tick`` (scan + plan, when due), ``command`` (pass-through while tracking, override while
avoiding), ``status`` (the C6 ``avoid`` telemetry) and ``encode_frame`` (the C6 LDR1 binary frame),
in the C8 call order. Everything here is duck-typed against the frozen C8 names, so the module also
runs against the hand-built stubs in ``tests/stubs.py``.
"""
from __future__ import annotations

import dataclasses
import json
import struct
import time

import numpy as np

from . import craters as craters_mod
from . import obstacles as obstacles_mod
from .geometry import Route, load_config, yaw_of
from .lidar import Scan, VirtualLidar
from .planner import AvoidanceSupervisor, ObstacleMap, shadow_hazards

PLANNER_DESC = "A* 0.4 m on a lidar clearance map · pure pursuit · footprint safety check · watchdog"


class ObstacleAvoidance:
    def __init__(self, rover_cfg: dict | None = None, cfg: dict | None = None) -> None:
        self.cfg = cfg or load_config()
        self.rover_cfg = rover_cfg or {}
        lim = self.rover_cfg.get("limits", {}) if isinstance(self.rover_cfg, dict) else {}
        self.max_yaw_rate = float(lim.get("max_yaw_rate_rad_s", 0.35))
        self.lidar = VirtualLidar(self.cfg["lidar"])
        self.planner_name = f"{self.cfg['planner']['name']}: {PLANNER_DESC}"
        self.enabled = False
        self.seed: int | None = None
        self.rocks: list[obstacles_mod.Rock] = []
        self.craters: list[craters_mod.Crater] = []
        self.grid = None
        self.prior = None
        self.world = None
        self.path_xy = None
        self.brain = AvoidanceSupervisor(self.cfg["planner"], self.max_yaw_rate)
        self.reset()

    # ------------------------------------------------------------------------------------ mission
    def setup(self, enabled, seed, path_xy, grid, world, rocks=None, craters=None) -> None:
        """Generate and carve craters into ``grid`` (in place), generate boulders, spawn colliders."""
        obstacles_mod.despawn(self.rocks, self.world)
        self.rocks, self.craters = [], []
        self.enabled = bool(enabled)
        self.world = world
        self.grid = grid
        self.path_xy = None if path_xy is None else np.asarray(path_xy, dtype=np.float64)
        if not self.enabled or grid is None or self.path_xy is None or len(self.path_xy) < 2:
            self.enabled = False
            self.prior = None
            self.reset()
            return
        self.seed = int(seed) if seed is not None else int(np.random.SeedSequence().entropy % (2 ** 31))
        self.prior = dataclasses.replace(grid, h=grid.h.copy())       # orbital DTM prior (pre-carve)
        self.craters = list(craters) if craters is not None else craters_mod.generate(
            self.path_xy, self.cfg["craters"], self.seed)
        craters_mod.apply(grid, self.craters)
        clear = float(self.cfg["craters"]["boulder_clearance_m"])
        keep_away = [(c.x, c.y, c.radius + clear) for c in self.craters]
        if rocks is not None:
            self.rocks = list(rocks)
            for r in self.rocks:
                if r.samples is None:
                    r.samples = obstacles_mod.sample_surface(
                        r.v_world, r.f, self.cfg["obstacles"]["surface_density_per_m2"])
        else:
            self.rocks = obstacles_mod.generate(
                self.path_xy, grid.height_at, self.cfg["obstacles"], self.seed, keep_away)
        obstacles_mod.spawn(self.rocks, world)
        self.reset()

    def reset(self) -> None:
        """Rover respawned: clear the map and the supervisor; keep the obstacles."""
        self.map = ObstacleMap(self.cfg["planner"])
        self.brain.reset()
        self.last_scan_t: float | None = None
        self.last_scan_xy: np.ndarray | None = None
        self.last_scan: Scan | None = None
        self.last_flags = np.zeros(0, np.uint8)
        self.last_shadows = np.zeros((0, 2))
        self.scans = 0
        self.scan_ms = 0.0
        self.sim_time = 0.0
        self._last_stream = 0.0

    def describe(self, frame=None) -> dict:
        """C5 ``MissionDescription.obstacles``."""
        return {"enabled": self.enabled, "seed": self.seed if self.enabled else None,
                "count": len(self.rocks) + len(self.craters),
                "rocks": [r.describe(frame) for r in self.rocks],
                "craters": [c.describe(frame) for c in self.craters],
                "lidar": self.lidar.describe(), "planner": self.planner_name}

    # ------------------------------------------------------------------------------------ loop
    def tick(self, pose, nav, sim_time) -> dict | None:
        if not self.enabled:
            return None
        self.sim_time = float(sim_time)
        lc = self.cfg["lidar"]
        xy = np.array([pose.x, pose.y], dtype=np.float64)
        due = (self.last_scan_t is None
               or self.sim_time - self.last_scan_t >= 1.0 / float(lc["scan_hz_sim"])
               or np.hypot(*(xy - self.last_scan_xy)) >= float(lc["scan_every_m"]))
        if due:
            t0 = time.perf_counter()
            scan = self.lidar.scan(self.grid, self.rocks, pose, self.sim_time)
            shadows = shadow_hazards(scan, self.lidar.pixel_rays(), self.prior, self.lidar.range_m)
            self.last_flags = self.map.insert(scan.points_world, self.prior, shadows)
            pc = self.cfg["planner"]
            lm = self.map.local(pose.x, pose.y, float(pc["map_window_m"]), self.brain.inflate)
            self.brain.on_scan(pose, nav, lm, self.sim_time)
            ms = (time.perf_counter() - t0) * 1000.0
            self.scan_ms = ms if self.scans == 0 else 0.8 * self.scan_ms + 0.2 * ms
            self.scans += 1
            self.last_scan, self.last_shadows = scan, shadows
            self.last_scan_t, self.last_scan_xy = self.sim_time, xy
        return self.brain.pop_event()

    def command(self, nav_cmd, pose, nav):
        if not self.enabled:
            return nav_cmd
        v, w = self.brain.filter(nav_cmd, pose, nav, self.sim_time)
        return float(v), float(w)

    def status(self, nav=None) -> dict | None:
        if not self.enabled:
            return None
        st = self.brain.status()
        st.update({"obstacle_cells": len(self.map), "planner": self.planner_name,
                   "rocks": len(self.rocks), "craters": len(self.craters), "scans": self.scans,
                   "scan_ms": round(self.scan_ms, 2),
                   "points": 0 if self.last_scan is None else int(len(self.last_scan.points_world))})
        return st

    def stream_due(self) -> bool:
        """Rate-limit LDR1 frames to ``stream_hz`` of wall-clock time."""
        if not self.enabled:
            return False
        now = time.monotonic()
        if now - self._last_stream >= 1.0 / float(self.cfg["lidar"]["stream_hz"]):
            self._last_stream = now
            return True
        return False

    # ------------------------------------------------------------------------------------ LDR1
    def encode_frame(self, pose, nav) -> bytes:
        """C6 LDR1 binary frame."""
        scan = self.last_scan
        x, y, yaw = float(pose.x), float(pose.y), yaw_of(pose.q)
        c, s_ = np.cos(yaw), np.sin(yaw)

        def body(pts) -> list:
            pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
            if not len(pts):
                return []
            dx, dy = pts[:, 0] - x, pts[:, 1] - y
            bx, by = c * dx + s_ * dy, -s_ * dx + c * dy
            return [[round(float(a), 2), round(float(b), 2)] for a, b in zip(bx, by)]

        route_b: list = []
        if nav is not None and getattr(nav, "path", None) is not None and len(nav.path) >= 2:
            r = Route.of(nav)
            ss = np.arange(max(0.0, nav.s - 6.0), min(r.length, nav.s + 14.0) + 1e-9, 0.5)
            route_b = body(r.point(ss))
        cand = []
        P = self.brain.path
        if P is not None and self.brain.mode != "track":
            near = P[np.hypot(P[:, 0] - x, P[:, 1] - y) <= 14.0]
            if len(near) >= 2:
                cand = [{"xy": body(near), "ok": True, "best": True}]
        carrot = body(self.brain.carrot)[0] if self.brain.carrot is not None and self.brain.mode != "track" else None
        near_r = sum(1 for rk in self.rocks if np.hypot(rk.x - x, rk.y - y) <= 14.0)
        near_c = sum(1 for cr in self.craters if np.hypot(cr.x - x, cr.y - y) <= 14.0 + cr.radius)
        if scan is not None:
            pts = scan.points_sensor
            flags = self.last_flags if len(self.last_flags) == len(pts) else np.zeros(len(pts), np.uint8)
            depth = scan.depth
            st = scan.sim_time
        else:
            pts = np.zeros((0, 3))
            flags = np.zeros(0, np.uint8)
            depth = np.zeros((self.lidar.height, self.lidar.width), np.float32)
            st = self.sim_time
        header = {"n": int(len(pts)), "w": self.lidar.width, "h": self.lidar.height,
                  "range": self.lidar.range_m, "v_fov": list(self.lidar.v_fov), "sim_time": round(st, 3),
                  "mode": self.brain.mode, "route": route_b, "candidates": cand, "carrot": carrot,
                  "shadows": body(self.last_shadows[::3]), "rocks_near": near_r, "craters_near": near_c}
        hb = json.dumps(header, separators=(",", ":")).encode("utf-8")
        hb += b" " * ((-len(hb)) % 4)
        pcm = np.clip(np.round(pts * 100.0), -32768, 32767).astype("<i2")
        dcm = np.clip(np.round(depth * 100.0), 0, 65535).astype("<u2")
        return b"".join([b"LDR1", struct.pack("<I", len(hb)), hb, pcm.tobytes(),
                         flags.astype(np.uint8).tobytes(), dcm.tobytes()])


def decode_frame(buf: bytes) -> dict:
    """Reference LDR1 reader (used by the tests; M3 implements the same in TypeScript)."""
    assert buf[:4] == b"LDR1"
    (hl,) = struct.unpack("<I", buf[4:8])
    hdr = json.loads(buf[8:8 + hl].decode("utf-8"))
    o = 8 + hl
    n, w, h = hdr["n"], hdr["w"], hdr["h"]
    pts = np.frombuffer(bytes(buf[o:o + 6 * n]), dtype="<i2").reshape(n, 3) / 100.0
    o += 6 * n
    flags = np.frombuffer(bytes(buf[o:o + n]), dtype=np.uint8)
    o += n
    depth = np.frombuffer(bytes(buf[o:o + 2 * w * h]), dtype="<u2").reshape(h, w) / 100.0
    return {"header": hdr, "points": pts, "flags": flags, "depth": depth}
