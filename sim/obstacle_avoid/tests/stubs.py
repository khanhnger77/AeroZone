"""C8 stand-ins so M5 can be built and tested before M4 exists.

* ``Grid`` / ``Pose``: the C8 dataclasses, rebuilt by hand with the same fields and rules.
* ``StubNavigator``: the C8 ``PurePursuitNavigator`` surface (``path, cum, s, xte, v, w, cruise, state,
  length, _point_at`` …) following M4's spec (monotone progress, lookahead ``4 + 3·v``, slow-down,
  turn in place over 60°).
* Two rover stand-ins: ``kinematic`` (0.5 s first-order lag, as M4's ``KinematicRover``) and
  ``wheeled`` (a *stand-in* for M4's PyBullet 6-wheel rover: slower lags plus acceleration limits,
  so the controller is exercised against a sluggish plant). The real wheeled model is exercised at
  integration time with M4.
* ``Harness``: runs the C8 50 Hz loop in the contract call order, at a given warp, and measures the
  true gap between the rover body (2.9 × 2.7 × 1.56 m box) and every boulder with PyBullet
  ``getClosestPoints``, plus the footprint-to-rim margin of every crater.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np

from sim.obstacle_avoid.geometry import heights, quat_from_yaw, wrap, yaw_of
from sim.obstacle_avoid.service import ObstacleAvoidance

try:
    import pybullet as p
    HAVE_PYBULLET = True
except ImportError:  # pragma: no cover
    p = None
    HAVE_PYBULLET = False

BODY_L, BODY_W, BODY_H = 2.9, 2.7, 1.56     # chassis 0.9 m + 0.66 m clearance: wheels to deck


# ------------------------------------------------------------------------------------------ C8 data
@dataclass
class Grid:
    h: np.ndarray
    x0: float
    y0: float
    dx: float
    dy: float
    lon_w: float = 77.29
    lon_e: float = 77.47
    lat_s: float = 18.42
    lat_n: float = 18.505
    res_src_m: float = 5.0
    meta: dict = field(default_factory=dict)

    def height_at(self, x, y) -> float:
        return float(heights(self, x, y))

    def extent(self):
        ny, nx = self.h.shape
        return self.x0, self.x0 + (nx - 1) * self.dx, self.y0, self.y0 + (ny - 1) * self.dy

    @property
    def shape(self):
        return self.h.shape

    def describe(self) -> dict:
        ny, nx = self.h.shape
        return {"nx": nx, "ny": ny, "x0": self.x0, "y0": self.y0, "dx": self.dx, "dy": self.dy,
                "lon_w": self.lon_w, "lon_e": self.lon_e, "lat_s": self.lat_s, "lat_n": self.lat_n,
                "h_min": float(self.h.min()), "h_max": float(self.h.max()), "res_src_m": self.res_src_m,
                "label": "stub", "resampled": False}


@dataclass
class Pose:
    x: float
    y: float
    z: float
    q: tuple = (0.0, 0.0, 0.0, 1.0)
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    wz: float = 0.0


def make_grid(fn, xmin, xmax, ymin, ymax, d: float = 5.0) -> Grid:
    xs = np.arange(xmin, xmax + 1e-9, d)
    ys = np.arange(ymin, ymax + 1e-9, d)
    X, Y = np.meshgrid(xs, ys)
    return Grid(h=np.asarray(fn(X, Y), dtype=np.float32), x0=float(xs[0]), y0=float(ys[0]), dx=d, dy=d)


def pose_on(grid, x, y, yaw, pitch_roll=True) -> Pose:
    """A pose standing on the grid; tilt from the terrain under the footprint."""
    z = grid.height_at(x, y)
    if not pitch_roll:
        return Pose(x, y, z, quat_from_yaw(yaw))
    c, s = np.cos(yaw), np.sin(yaw)
    hl, hw = BODY_L / 2, BODY_W / 2
    zf = grid.height_at(x + c * hl, y + s * hl)
    zb = grid.height_at(x - c * hl, y - s * hl)
    zl = grid.height_at(x - s * hw, y + c * hw)
    zr = grid.height_at(x + s * hw, y - c * hw)
    pitch = -np.arctan2(zf - zb, 2 * hl)            # nose up = negative pitch about +y (ENU, x fwd)
    roll = np.arctan2(zl - zr, 2 * hw)
    q = quat_from_euler(roll, pitch, yaw)
    return Pose(x, y, z, q)


def quat_from_euler(roll, pitch, yaw):
    cr, sr = np.cos(roll / 2), np.sin(roll / 2)
    cp, sp = np.cos(pitch / 2), np.sin(pitch / 2)
    cy, sy = np.cos(yaw / 2), np.sin(yaw / 2)
    return (sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy)


# ------------------------------------------------------------------------------------- navigator
class StubNavigator:
    """C8 ``PurePursuitNavigator`` surface, following M4's spec."""

    def __init__(self, max_yaw_rate=0.35, lookahead_m=4.0, lookahead_per_speed_s=3.0,
                 goal_tolerance_m=2.0, slow_radius_m=8.0) -> None:
        self.wmax = max_yaw_rate
        self.la, self.la_v = lookahead_m, lookahead_per_speed_s
        self.goal_tol, self.slow_r = goal_tolerance_m, slow_radius_m
        self.path = np.zeros((0, 2))
        self.cum = np.zeros(0)
        self.s = 0.0
        self.xte = 0.0
        self.v = self.w = 0.0
        self.cruise = 1.0
        self.state = "idle"
        self._i = 0
        self._pose = None

    @property
    def length(self) -> float:
        return float(self.cum[-1]) if len(self.cum) else 0.0

    def load_path(self, xy):
        self.path = np.asarray(xy, dtype=np.float64)
        self.cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(self.path, axis=0).T))])
        self.s, self._i, self.state = 0.0, 0, "idle"

    def set_cruise(self, v):
        self.cruise = float(v)

    def start(self):
        if self.state != "arrived":
            self.state = "driving"

    def pause(self):
        self.state = "paused"

    def _point_at(self, s) -> np.ndarray:
        s = float(np.clip(s, 0, self.length))
        return np.array([np.interp(s, self.cum, self.path[:, 0]), np.interp(s, self.cum, self.path[:, 1])])

    def observe(self, pose):
        self._pose = pose
        P = self.path
        lo, hi = self._i, min(len(P) - 1, self._i + 60)
        a, b = P[lo:hi], P[lo + 1:hi + 1]
        if not len(a):
            return
        ab = b - a
        L2 = np.maximum((ab ** 2).sum(1), 1e-12)
        q = np.array([pose.x, pose.y])
        t = np.clip(((q - a) * ab).sum(1) / L2, 0, 1)
        pr = a + ab * t[:, None]
        d = np.hypot(*(pr - q).T)
        k = int(np.argmin(d))
        self._i = lo + k
        s_new = self.cum[self._i] + t[k] * np.sqrt(L2[k])
        self.s = max(self.s, float(s_new))
        cross = ab[k, 0] * (q[1] - a[k, 1]) - ab[k, 1] * (q[0] - a[k, 0])
        self.xte = float(np.sign(cross) * d[k])
        end = P[-1]
        if self.state == "driving" and (np.hypot(*(q - end)) < self.goal_tol
                                        or self.length - self.s < 0.5 * self.goal_tol):
            self.state = "arrived"

    def command(self):
        if self.state != "driving" or self._pose is None:
            self.v = self.w = 0.0
            return 0.0, 0.0
        pose = self._pose
        yaw = yaw_of(pose.q)
        goal_d = self.length - self.s
        v = self.cruise * float(np.clip(goal_d / self.slow_r, 0.15, 1.0))
        ld = self.la + self.la_v * v
        tgt = self._point_at(self.s + ld)
        alpha = float(wrap(np.arctan2(tgt[1] - pose.y, tgt[0] - pose.x) - yaw))
        if abs(alpha) > np.deg2rad(60):
            self.v, self.w = 0.0, float(np.sign(alpha) * self.wmax)
        else:
            self.v = v
            self.w = float(np.clip(v * 2 * np.sin(alpha) / ld, -self.wmax, self.wmax))
        return self.v, self.w

    def status(self) -> dict:
        return {"state": self.state, "progress": self.s / max(self.length, 1e-9), "distance_done_m": self.s,
                "distance_remaining_m": self.length - self.s, "length_m": self.length,
                "cross_track_m": self.xte, "cmd_v": self.v, "cmd_w": self.w, "cruise_m_s": self.cruise}


# ----------------------------------------------------------------------------------------- rovers
class StubRover:
    """Unicycle rover on the grid. ``kinematic``: 0.5 s lag (M4 KinematicRover). ``wheeled``: a
    sluggish stand-in for M4's PyBullet rover (0.8 s / 0.6 s lags, 0.6 m/s² and 0.5 rad/s² limits)."""

    MODELS = {"kinematic": dict(tau_v=0.5, tau_w=0.5, acc=None, wacc=None),
              "wheeled": dict(tau_v=0.8, tau_w=0.6, acc=0.6, wacc=0.5)}

    def __init__(self, grid, model="kinematic", max_yaw_rate=0.35) -> None:
        self.grid = grid
        self.model = model
        self.p = self.MODELS[model]
        self.wmax = max_yaw_rate
        self.x = self.y = self.yaw = 0.0
        self.v = self.w = 0.0
        self.cmd = (0.0, 0.0)
        self.sim_time = 0.0

    def spawn(self, x, y, yaw):
        self.x, self.y, self.yaw, self.v, self.w = float(x), float(y), float(yaw), 0.0, 0.0

    def set_inputs(self, v, w):
        self.cmd = (float(v), float(np.clip(w, -self.wmax, self.wmax)))

    def update(self, elapsed, warp=1.0, dt=0.01):
        T = elapsed * warp
        n = max(1, int(round(T / dt)))
        h = T / n
        for _ in range(n):
            dv = (self.cmd[0] - self.v) * min(1.0, h / self.p["tau_v"])
            dw = (self.cmd[1] - self.w) * min(1.0, h / self.p["tau_w"])
            if self.p["acc"]:
                dv = float(np.clip(dv, -self.p["acc"] * h, self.p["acc"] * h))
                dw = float(np.clip(dw, -self.p["wacc"] * h, self.p["wacc"] * h))
            self.v += dv
            self.w += dw
            self.yaw += self.w * h
            self.x += self.v * np.cos(self.yaw) * h
            self.y += self.v * np.sin(self.yaw) * h
        self.sim_time += T
        return n

    def pose(self) -> Pose:
        return pose_on(self.grid, self.x, self.y, self.yaw)


# ---------------------------------------------------------------------------------------- harness
class BodyProbe:
    """PyBullet DIRECT world holding the rock colliders and a ghost box for the rover body."""

    def __init__(self) -> None:
        self.client = p.connect(p.DIRECT)
        p.setGravity(0, 0, -3.711, physicsClientId=self.client)
        self.cfg = {"collision": {"lateral_friction": 0.8, "restitution": 0.05}}
        shape = p.createCollisionShape(p.GEOM_BOX, halfExtents=[BODY_L / 2, BODY_W / 2, BODY_H / 2],
                                       physicsClientId=self.client)
        self.body = p.createMultiBody(0, shape, -1, [0, 0, -100], physicsClientId=self.client)

    def gap(self, pose: Pose, rocks, reach=6.0) -> float:
        up = np.array([0, 0, BODY_H / 2])
        from sim.obstacle_avoid.geometry import quat_to_matrix
        c = np.array([pose.x, pose.y, pose.z]) + quat_to_matrix(pose.q) @ up
        p.resetBasePositionAndOrientation(self.body, c.tolist(), list(pose.q), physicsClientId=self.client)
        best = np.inf
        for r in rocks:
            if r.body is None or np.hypot(r.x - pose.x, r.y - pose.y) > reach + r.radius:
                continue
            cps = p.getClosestPoints(self.body, r.body, reach, physicsClientId=self.client)
            for cp in cps:
                best = min(best, cp[8])
        return best

    def close(self):
        p.disconnect(self.client)


@dataclass
class RunResult:
    arrived: bool
    final_mode: str
    min_gap_m: float
    contacts: int
    min_rim_margin_m: float
    detours: int
    recoveries: int
    blocked: bool
    final_xte_m: float
    sim_time_s: float
    max_xte_m: float
    modes: list

    @property
    def ok(self) -> bool:
        return (self.arrived and not self.blocked and self.contacts == 0 and self.final_mode == "track"
                and self.min_rim_margin_m > 0.0)


def run_closed_loop(grid, path_xy, *, model="kinematic", speed=1.0, warp=1.0, seed=1, enabled=True,
                    rocks=None, craters=None, spawn_xy=None, spawn_yaw=None, max_sim_s=None,
                    rover_cfg=None) -> tuple[RunResult, ObstacleAvoidance]:
    """Drive the route in the C8 call order; returns metrics and the facade for inspection."""
    probe = BodyProbe()
    avoid = ObstacleAvoidance(rover_cfg or {"limits": {"max_yaw_rate_rad_s": 0.35}})
    path_xy = np.asarray(path_xy, dtype=np.float64)
    # Mission build order (C8): terrain -> avoid.setup (carves) -> router.set_terrain -> load_path -> spawn -> reset
    avoid.setup(enabled, seed, path_xy, grid, probe, rocks=rocks, craters=craters)
    rover = StubRover(grid, model)
    nav = StubNavigator()
    nav.load_path(path_xy)
    nav.set_cruise(speed)
    sx, sy = spawn_xy if spawn_xy is not None else path_xy[0]
    yaw0 = spawn_yaw if spawn_yaw is not None else float(np.arctan2(*(path_xy[min(3, len(path_xy) - 1)] - path_xy[0])[::-1]))
    rover.spawn(sx, sy, yaw0)
    avoid.reset()
    nav.start()
    length = nav.length
    max_sim_s = max_sim_s or (length / max(speed, 0.05) * 3.0 + 300.0)
    loop_dt = 0.02
    min_gap, contacts, rim, max_xte = np.inf, 0, np.inf, 0.0
    modes = ["track"]
    fx = np.array([-0.97, 0.0, 0.97])
    while rover.sim_time < max_sim_s and nav.state != "arrived":
        pose = rover.pose()
        ev = avoid.tick(pose, nav, rover.sim_time)
        if ev is not None:
            modes.append(ev["mode"])
        nav.observe(pose)
        rover.set_inputs(*avoid.command(nav.command(), pose, nav))
        rover.update(min(loop_dt, 0.25), warp)
        pose = rover.pose()
        if avoid.rocks:
            g = probe.gap(pose, avoid.rocks)
            min_gap = min(min_gap, g)
            contacts += int(g <= 0.0)
        for c in avoid.craters:
            yaw = yaw_of(pose.q)
            cx = pose.x + np.cos(yaw) * fx
            cy = pose.y + np.sin(yaw) * fx
            rim = min(rim, float((np.hypot(cx - c.x, cy - c.y) - c.radius - 1.45).min()))
        if avoid.brain.mode == "track":
            max_xte = max(max_xte, abs(nav.xte)) if avoid.enabled else max(max_xte, abs(nav.xte))
        if avoid.brain.mode == "blocked":
            break
    probe.close()
    res = RunResult(arrived=nav.state == "arrived", final_mode=avoid.brain.mode, min_gap_m=float(min_gap),
                    contacts=contacts, min_rim_margin_m=float(rim), detours=avoid.brain.detours,
                    recoveries=avoid.brain.recoveries, blocked=avoid.brain.mode == "blocked",
                    final_xte_m=float(abs(nav.xte)), sim_time_s=float(rover.sim_time), max_xte_m=float(max_xte),
                    modes=modes)
    return res, avoid


def straight_route(length=120.0, step=1.0, yaw=0.0):
    s = np.arange(0.0, length + 1e-9, step)
    return np.stack([s * np.cos(yaw), s * np.sin(yaw)], axis=1)


def rock_at(x, y, r, h, rid="r0", grid=None, seed=0):
    """A deterministic test boulder at (x, y)."""
    from sim.obstacle_avoid import obstacles as om
    rng = np.random.default_rng(seed)
    v, f = om.make_rock_shape(r, h, rng)
    z = (grid.height_at(x, y) if grid is not None else 0.0) - 0.2 * h
    return om.Rock(id=rid, x=float(x), y=float(y), z=float(z), yaw=0.0,
                   radius=float(np.hypot(v[:, 0], v[:, 1]).max()), height=float(h), s=float(x), v_local=v, f=f)


def flat_grid(extent=(-60, 220, -80, 80), slope_deg=0.0, d=5.0):
    k = np.tan(np.deg2rad(slope_deg))
    return make_grid(lambda X, Y: k * Y + 0 * X, *extent, d=d)


__all__ = ["Grid", "Pose", "StubNavigator", "StubRover", "run_closed_loop", "straight_route", "rock_at",
           "flat_grid", "make_grid", "pose_on", "HAVE_PYBULLET", "dataclasses", "SimpleNamespace"]
