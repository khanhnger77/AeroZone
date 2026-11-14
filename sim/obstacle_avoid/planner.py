"""Hazard map, footprint model and the avoidance supervisor (M5).

Why this design:

* **Prior-relative classification.** The orbital DTM (the corridor grid *before* craters are carved)
  is the rover's prior. A lidar return is a hazard when it departs from that prior: a *hole*
  (more than ``hole_depth_m`` below it) or a *step* (a local rise of more than ``obstacle_height_m``
  within a 1.5 m neighbourhood). Working on the residual means plain slopes, however steep, never
  produce false hazards.
* **Range shadows.** A drop-off is often invisible: the ray simply goes further than the prior
  predicts, or returns nothing. Tracing pixel rays against the prior and flagging rays that come back
  long turns that absence of data into negative hazards at the crater lip.
* **Clearance field, not a binary map.** Hazard cells are kept in world-fixed 0.2 m keys; each scan
  rasterises an 80 m window and takes a distance transform. The rover footprint (2.9 × 2.7 m) is three
  circles along the heading, so a pose is safe when the minimum clearance at the three centres is
  above the circle radius plus margin. The same field drives A* costs, goal selection, rejoin
  checks and the per-tick safety check, so all decisions agree.
* **Supervisor with a watchdog.** Track the route until it is blocked, detour on an A* path to a
  clear route point beyond the obstacle, rejoin when the route ahead is clear, back up along the trail
  and penalise the failed path when progress stalls, and only give up (``blocked``) after
  ``max_recoveries`` attempts.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .geometry import Route, heights, wrap, yaw_of

FAR = 1.0e3            # clearance reported outside the local window / with an empty map
FLOOR_EXTRA_M = 0.15   # floor = footprint_r + 0.15 = 1.60 m: never accept less than this from a move
TOLERANCE_M = 0.02
# Smoothness (M5 internals, not part of the C10 config contract):
SIDE_SWITCH_COST = 3.0     # extra cost per cell for passing on the other side of the route than the detour chose
LEAD_IN_M = 1.2            # detours start along the current heading for this far when it is clear
ACCEL_M_S2 = 0.4           # speed-up ramp while avoiding (braking is never limited)
YAW_ACCEL_RAD_S2 = 0.6     # max change of the turn-rate command while avoiding


# --------------------------------------------------------------------------------------------- keys
def encode_keys(i: np.ndarray, j: np.ndarray) -> np.ndarray:
    i = np.asarray(i, dtype=np.int64)
    j = np.asarray(j, dtype=np.int64)
    return (i << 32) ^ (j & 0xFFFFFFFF)


def decode_keys(k: np.ndarray):
    k = np.asarray(k, dtype=np.int64)
    i = k >> 32
    j = ((k & 0xFFFFFFFF) ^ 0x80000000) - 0x80000000
    return i, j


# ---------------------------------------------------------------------------------- classification
def classify_parts(pts: np.ndarray, prior, cfg: dict):
    """Return boolean masks ``(hole, step)`` for world points ``pts (n, 3)``."""
    n = len(pts)
    if n == 0:
        return np.zeros(0, bool), np.zeros(0, bool)
    resid = pts[:, 2] - heights(prior, pts[:, 0], pts[:, 1])
    hole = resid < -float(cfg["hole_depth_m"])
    cell = float(cfg["step_cell_m"])
    ci = np.floor(pts[:, 0] / cell).astype(np.int64)
    cj = np.floor(pts[:, 1] / cell).astype(np.int64)
    i0, j0 = ci.min() - 1, cj.min() - 1
    ii, jj = ci - i0, cj - j0
    shape = (jj.max() + 2, ii.max() + 2)
    cmax = np.full(shape, -np.inf)
    cmin = np.full(shape, np.inf)
    np.maximum.at(cmax, (jj, ii), resid)
    np.minimum.at(cmin, (jj, ii), resid)
    nbmin = ndimage.minimum_filter(cmin, size=3, mode="constant", cval=np.inf)
    hob = float(cfg["obstacle_height_m"])
    step_cell = cmax > nbmin + hob
    base = nbmin[jj, ii]
    step = step_cell[jj, ii] & (resid > base + hob)
    return hole, step


def shadow_hazards(scan, rays: np.ndarray, prior, rmax: float, step_m: float = 0.3) -> np.ndarray:
    """Drop-off hazards from range shadows: (m, 2) world xy.

    Downward pixel rays within ±110° of forward (every second column) are traced against the prior in
    ``step_m`` steps. Where the prior predicts a hit at ``r_exp`` but the lidar returned nothing, or
    returned further than ``r_exp + 0.4 + 0.05·r_exp``, the predicted hit point is a hazard.
    """
    H, W, _ = rays.shape
    az = -180.0 + (np.arange(W) + 0.5) * 360.0 / W
    cols = np.nonzero(np.abs(az) <= 110.0)[0][::2]
    down = rays[:, 0, 2] < 0
    rows = np.nonzero(down)[0]
    if len(rows) == 0 or len(cols) == 0:
        return np.zeros((0, 2))
    R_, C_ = np.meshgrid(rows, cols, indexing="ij")
    R_, C_ = R_.ravel(), C_.ravel()
    d = rays[R_, C_] @ scan.rot.T                      # world directions
    steps = np.arange(step_m, rmax + 1e-9, step_m)
    P = scan.sensor[None, None, :] + steps[None, :, None] * d[:, None, :]   # (k, s, 3)
    hz = heights(prior, P[..., 0], P[..., 1])
    below = P[..., 2] <= hz
    hit = below.any(axis=1)
    first = np.argmax(below, axis=1)
    k = np.nonzero(hit & (first > 0))[0]
    if len(k) == 0:
        return np.zeros((0, 2))
    f = first[k]
    # Linear refinement between the last step above and the first below.
    za = P[k, f - 1, 2] - hz[k, f - 1]
    zb = P[k, f, 2] - hz[k, f]
    t = za / np.maximum(za - zb, 1e-9)
    r_exp = steps[f - 1] + t * step_m
    keep = r_exp <= rmax - 1.0
    k, r_exp = k[keep], r_exp[keep]
    meas = scan.depth[R_[k], C_[k]]
    bad = (meas <= 0) | (meas > r_exp + 0.4 + 0.05 * r_exp)
    pts = scan.sensor[None, :2] + r_exp[bad, None] * d[k[bad], :2]
    return pts


# --------------------------------------------------------------------------------------- local map
@dataclass
class LocalMap:
    x0: float
    y0: float
    res: float
    keepout: np.ndarray          # bool (n, n): clearance ≤ inflate
    clearance: np.ndarray        # float32 (n, n), metres to the nearest hazard
    inflate: float
    empty: bool = False

    @property
    def size(self) -> int:
        return self.clearance.shape[0]

    @property
    def center(self) -> tuple[float, float]:
        n = self.size
        return self.x0 + 0.5 * n * self.res, self.y0 + 0.5 * n * self.res

    def clear_at(self, x, y) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if self.empty:
            return np.full(np.broadcast(x, y).shape, FAR)
        n = self.size
        i = np.floor((x - self.x0) / self.res).astype(np.int64)
        j = np.floor((y - self.y0) / self.res).astype(np.int64)
        inside = (i >= 0) & (i < n) & (j >= 0) & (j < n)
        out = np.full(np.broadcast(i, j).shape, FAR)
        out[inside] = self.clearance[j[inside], i[inside]]
        return out

    def blocked(self, x, y) -> np.ndarray:
        return self.clear_at(x, y) <= self.inflate


class ObstacleMap:
    """World-fixed hazard cells in two sets (positive = steps/rocks, negative = holes/drop-offs).

    Keys are ``(i << 32) ^ (j & 0xffffffff)`` of 0.2 m cells. The sets are stored as sorted unique
    int64 arrays (set semantics, fast vectorised union and window queries).
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.res = float(cfg["map_resolution_m"])
        self.pos = np.zeros(0, dtype=np.int64)
        self.neg = np.zeros(0, dtype=np.int64)

    def __len__(self) -> int:
        return int(len(self.pos) + len(self.neg))

    def _keys(self, xy) -> np.ndarray:
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        return encode_keys(np.floor(xy[:, 0] / self.res), np.floor(xy[:, 1] / self.res))

    def insert(self, points: np.ndarray, prior, shadows=None) -> np.ndarray:
        """Classify and insert one scan; returns the per-point hazard flag (uint8)."""
        hole, step = classify_parts(points, prior, self.cfg)
        if step.any():
            self.pos = np.union1d(self.pos, self._keys(points[step, :2]))
        neg = [points[hole, :2]]
        if shadows is not None and len(shadows):
            neg.append(np.asarray(shadows)[:, :2])
        negxy = np.concatenate(neg, axis=0)
        if len(negxy):
            self.neg = np.union1d(self.neg, self._keys(negxy))
        return (hole | step).astype(np.uint8)

    def _raster(self, keys, ic0, jc0, n) -> np.ndarray:
        m = np.zeros((n, n), dtype=bool)
        if len(keys) == 0:
            return m
        i, j = decode_keys(keys)
        i = i - ic0
        j = j - jc0
        ok = (i >= 0) & (i < n) & (j >= 0) & (j < n)
        m[j[ok], i[ok]] = True
        return m

    def local(self, cx: float, cy: float, window: float, inflate: float) -> LocalMap:
        res = self.res
        n = int(round(window / res))
        n += n % 2
        ic0 = int(np.floor(cx / res)) - n // 2
        jc0 = int(np.floor(cy / res)) - n // 2
        x0, y0 = ic0 * res, jc0 * res
        pos = self._raster(self.pos, ic0, jc0, n)
        neg = self._raster(self.neg, ic0, jc0, n)
        if not pos.any() and not neg.any():
            return LocalMap(x0, y0, res, np.zeros((n, n), bool), np.full((n, n), FAR, np.float32),
                            inflate, empty=True)
        if neg.any():
            neg = ndimage.distance_transform_edt(~neg) * res <= float(self.cfg["negative_margin_m"])
        obst = pos | neg
        clr = (ndimage.distance_transform_edt(~obst) * res).astype(np.float32)
        return LocalMap(x0, y0, res, clr <= inflate, clr, inflate)


# ------------------------------------------------------------------------------------------- A*
@dataclass
class AStarResult:
    path: list | None                 # [(j, i), ...] start → goal, or None
    reached: tuple | None             # goal cell reached
    g: dict                           # flat index → cost-so-far of every settled cell
    parent: dict                      # flat index → flat index of its predecessor
    ncols: int

    def trace(self, cell) -> list:
        k = cell[0] * self.ncols + cell[1]
        out = []
        while k is not None:
            out.append(divmod(k, self.ncols))
            k = self.parent.get(k)
        return out[::-1]


_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def astar(cost: np.ndarray, start: tuple, goal_mask: np.ndarray, res: float) -> AStarResult:
    """A* on an 8-connected grid to the nearest of several goal cells.

    Edge cost = mean of the two cell costs × step length (m), as in a geometric cost-distance; non-finite
    cells are impassable. Heuristic = Euclidean distance (m) to the nearest goal, from one distance
    transform. Every cell costs ≥ 1 per metre, so the heuristic is admissible and consistent: the path is
    optimal and each cell is settled once. When no goal is reachable the search exhausts the reachable
    region; ``g`` then holds all of it for the caller's fallback.
    """
    import heapq

    ny, nx = cost.shape
    if not goal_mask.any():
        return AStarResult(None, None, {}, {}, nx)
    h = (ndimage.distance_transform_edt(~goal_mask) * res).ravel()
    c = cost.ravel()
    goal = goal_mask.ravel()
    s = start[0] * nx + start[1]
    g = {s: 0.0}
    parent: dict = {s: None}
    closed = set()
    heap = [(h[s], h[s], s)]                     # (f, h, cell): ties go to the cell nearer the goal
    steps = [(dj * nx + di, dj, di, res * (1.4142135623730951 if dj and di else 1.0)) for dj, di in _NB]
    inf = float("inf")
    while heap:
        _, _, u = heapq.heappop(heap)
        if u in closed:
            continue
        closed.add(u)
        if goal[u]:
            path = []
            k = u
            while k is not None:
                path.append(divmod(k, nx))
                k = parent[k]
            return AStarResult(path[::-1], divmod(u, nx), g, parent, nx)
        uj, ui = divmod(u, nx)
        gu = g[u]
        cu = c[u]
        for off, dj, di, length in steps:
            vj, vi = uj + dj, ui + di
            if vj < 0 or vj >= ny or vi < 0 or vi >= nx:
                continue
            v = u + off
            cv = c[v]
            if cv == inf or v in closed:
                continue
            gv = gu + 0.5 * (cu + cv) * length
            if gv < g.get(v, inf):
                g[v] = gv
                parent[v] = u
                heapq.heappush(heap, (gv + h[v], h[v], v))
    return AStarResult(None, None, {k: g[k] for k in closed}, parent, nx)


def smooth_path(cells: list, cost: np.ndarray) -> list:
    """Any-angle shortcutting of an 8-connected grid path (string pulling).

    Grid A* has many equal-cost paths and may return "straight, then diagonal at the end", which never
    converges onto a goal that keeps moving ahead. From each kept cell we jump to the farthest later cell
    whose straight segment only crosses cells no costlier than the costliest cell it replaces, so a
    shortcut never brings the rover closer to an obstacle than the grid path did.
    """
    if len(cells) <= 2:
        return cells
    P = np.asarray(cells, dtype=np.int64)
    cvals = cost[P[:, 0], P[:, 1]]
    out = [cells[0]]
    a = 0
    n = len(cells)
    while a < n - 1:
        b = n - 1
        while b > a + 1:
            k = int(max(abs(P[b, 0] - P[a, 0]), abs(P[b, 1] - P[a, 1]))) * 2 + 1
            jj = np.rint(np.linspace(P[a, 0], P[b, 0], k)).astype(np.int64)
            ii = np.rint(np.linspace(P[a, 1], P[b, 1], k)).astype(np.int64)
            seg = cost[jj, ii]
            if np.isfinite(seg).all() and seg.max() <= cvals[a:b + 1].max() + 1e-9:
                break
            b -= 1
        out.append(cells[b])
        a = b
    return out


# --------------------------------------------------------------------------------- footprint/arcs
class ArcPlanner:
    """Footprint model (three circles along the heading), arc prediction and the legacy arc set."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.fx = np.asarray(cfg["footprint_x_m"], dtype=np.float64)
        self.r = float(cfg["footprint_r_m"])
        self.inflate = self.r + float(cfg["safety_margin_m"])
        self.candidates = self._legacy_candidates()

    def footprint_clear(self, lm: LocalMap, xy, heading) -> np.ndarray:
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        hd = np.broadcast_to(np.asarray(heading, dtype=np.float64), (len(xy),))
        cx = xy[:, 0:1] + np.cos(hd)[:, None] * self.fx[None, :]
        cy = xy[:, 1:2] + np.sin(hd)[:, None] * self.fx[None, :]
        return lm.clear_at(cx, cy).min(axis=1)

    @staticmethod
    def predict(x: float, y: float, yaw: float, v: float, w: float, L: float, step: float = 0.25) -> np.ndarray:
        """Poses ``(n+1, 3)`` [x, y, yaw] along the constant-(v, ω) arc for path length ``L``."""
        if abs(v) < 1e-3:
            return np.array([[x, y, yaw]])
        n = max(1, int(np.ceil(L / step)))
        sgn = np.sign(v)
        k = w / v                                   # signed curvature per metre of travel
        s = np.linspace(0.0, L, n + 1) * sgn
        th = yaw + k * s
        if abs(k) < 1e-6:
            px = x + s * np.cos(yaw)
            py = y + s * np.sin(yaw)
        else:
            px = x + (np.sin(th) - np.sin(yaw)) / k
            py = y - (np.cos(th) - np.cos(yaw)) / k
        return np.stack([px, py, th], axis=1)

    def _legacy_candidates(self) -> list[dict]:
        """The original fixed arc set (kept for parity with the reference repo; not used live)."""
        c = self.cfg
        kmax = 1.0 / float(c["min_turn_radius_m"])
        out = [{"kind": "arc", "k": float(k), "L": float(c["arc_length_m"])}
               for k in np.linspace(-kmax, kmax, int(c["n_arcs"]))]
        for a in c["point_turns_deg"]:
            out += [{"kind": "turn", "dyaw_deg": float(a)}, {"kind": "turn", "dyaw_deg": -float(a)}]
        out.append({"kind": "reverse", "L": float(c["reverse_length_m"])})
        return out


# -------------------------------------------------------------------------------------- supervisor
class AvoidanceSupervisor:
    """Modes ``track → avoid → (recover) → track``, or ``blocked`` when recovery is exhausted."""

    def __init__(self, cfg: dict, max_yaw_rate: float = 0.35) -> None:
        self.cfg = cfg
        self.arcs = ArcPlanner(cfg)
        self.inflate = self.arcs.inflate
        self.floor = self.arcs.r + FLOOR_EXTRA_M
        self.wmax = float(max_yaw_rate)
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        self.mode = "track"
        self.detours = 0
        self.recoveries = 0
        self.trail: deque = deque(maxlen=400)
        self.path: np.ndarray | None = None
        self.path_idx = 0
        self.goal_s: float | None = None
        self.lm: LocalMap | None = None
        self.blocked_at: float | None = None
        self.choice: str | None = None
        self.carrot: np.ndarray | None = None
        self.attempts = 0
        self.penalties: list[np.ndarray] = []
        self.best_prog = -np.inf
        self.last_prog_t = 0.0
        self.rec_start: np.ndarray | None = None
        self.rec_t = 0.0
        self.here = FAR
        self.t = 0.0
        self._events: list[dict] = []
        self.side = 0                      # +1 / -1: side of the route the current detour passes on
        self._last_cmd = (0.0, 0.0)        # for the smooth-command limiter
        self._last_cmd_t: float | None = None

    def _set_mode(self, mode: str, detail: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self._events.append({"type": "avoid_event", "mode": mode, "detail": detail})

    def pop_event(self) -> dict | None:
        if not self._events:
            return None
        ev = self._events[-1]
        if len(self._events) > 1:
            ev = dict(ev, detail="; ".join(e["detail"] for e in self._events))
        self._events.clear()
        return ev

    # ------------------------------------------------------------------ helpers
    def _fc(self, xy, hd) -> np.ndarray:
        return self.arcs.footprint_clear(self.lm, xy, hd)

    def _req(self) -> float:
        if self.here < self.floor:
            return self.here - 0.05
        return max(min(self.inflate, self.here - 0.1), self.floor) - TOLERANCE_M

    def _route_blocked(self, route: Route, s: float) -> float | None:
        ds = np.arange(0.0, float(self.cfg["check_ahead_m"]) + 1e-9, 0.5)
        ss = s + ds
        ok = ss <= route.length
        if not ok.any():
            return None
        p = route.point(ss[ok])
        b = self.lm.blocked(p[:, 0], p[:, 1])
        return float(ds[ok][np.argmax(b)]) if b.any() else None

    def _arc_blocked(self, x, y, yaw, nav, L=6.0) -> bool:
        v = float(getattr(nav, "v", 0.0) or 0.0)
        w = float(getattr(nav, "w", 0.0) or 0.0)
        if abs(v) < 0.05:
            v = max(float(getattr(nav, "cruise", 0.5) or 0.5), 0.1)
        P = self.arcs.predict(x, y, yaw, v, w, L)
        return bool((self._fc(P[1:, :2], P[1:, 2]) <= self.inflate).any())

    # ------------------------------------------------------------------ per scan
    def on_scan(self, pose, nav, lm: LocalMap, t: float) -> None:
        self.lm, self.t = lm, float(t)
        x, y, yaw = float(pose.x), float(pose.y), yaw_of(pose.q)
        self.here = float(self._fc([[x, y]], yaw)[0])
        route = Route.of(nav)
        s = float(nav.s)
        if self.mode in ("track", "avoid"):
            if not self.trail or np.hypot(x - self.trail[-1][0], y - self.trail[-1][1]) >= 0.25:
                self.trail.append((x, y))
        self.blocked_at = None if lm.empty else self._route_blocked(route, s)
        driving = getattr(nav, "state", "driving") == "driving"
        if lm.empty and self.mode == "track":
            return
        if self.mode == "track":
            if driving and (self.blocked_at is not None or self._arc_blocked(x, y, yaw, nav)):
                why = (f"obstacle {self.blocked_at:.1f} m ahead" if self.blocked_at is not None
                       else "obstacle on the follower's arc")
                self._enter_avoid(pose, nav, why)
        elif self.mode == "avoid":
            if self._can_rejoin(x, y, yaw, nav, route, s):
                self._set_mode("track", "route clear, rejoined")
                self.path = None
                self.attempts = 0
            elif driving:
                self._plan(x, y, yaw, nav, route, s)
                self._watchdog(x, y)
        elif self.mode == "recover":
            self._check_recover_done(x, y, yaw, nav, route, s)

    def _enter_avoid(self, pose, nav, why: str) -> None:
        self._set_mode("avoid", why)
        self.detours += 1
        self.side = 0
        self.penalties = []
        self.attempts = 0
        self.best_prog = -np.inf
        self.last_prog_t = self.t
        route = Route.of(nav)
        self._plan(float(pose.x), float(pose.y), yaw_of(pose.q), nav, route, float(nav.s))

    def _can_rejoin(self, x, y, yaw, nav, route: Route, s: float) -> bool:
        if self.blocked_at is not None or self.here <= self.inflate:
            return False
        ss = np.arange(max(0.0, s - 3.0), min(route.length, s + 12.0) + 1e-9, 0.5)
        if len(ss):
            if (self._fc(route.point(ss), route.heading(ss)) <= self.inflate).any():
                return False
        if self._arc_blocked(x, y, yaw, nav):
            return False
        if abs(float(getattr(nav, "xte", 0.0))) >= float(self.cfg["rejoin_xte_m"]):
            return False
        hd = float(route.heading(s, half=3.0))
        return abs(float(wrap(yaw - hd))) < np.deg2rad(float(self.cfg["rejoin_heading_deg"]))

    def _goals(self, route: Route, s: float):
        c = self.cfg
        s0 = s + float(c["carrot_m"])
        if self.blocked_at is not None:
            s0 = max(s0, s + self.blocked_at + 2.0)
        s1 = min(s + 45.0, route.length)
        ss = np.arange(s0, s1 + 1e-9, 1.0) if s0 <= s1 else np.array([route.length])
        p = route.point(ss)
        cx, cy = self.lm.center
        half = 0.5 * self.lm.size * self.lm.res - 2.0
        inside = (np.abs(p[:, 0] - cx) < half) & (np.abs(p[:, 1] - cy) < half)
        good = inside & (self._fc(p, route.heading(ss)) > self.inflate + 0.3)
        if good.any():
            return p[good], ss[good]
        # Nothing qualifies: the farthest route point still inside the window.
        sw = np.arange(s, route.length + 1e-9, 1.0)
        pw = route.point(sw)
        inw = (np.abs(pw[:, 0] - cx) < half) & (np.abs(pw[:, 1] - cy) < half)
        if not inw.any():
            return p[-1:], ss[-1:]
        k = np.nonzero(inw)[0]
        k = k[np.argmax(sw[k])]
        return pw[k:k + 1], sw[k:k + 1]

    def _plan(self, x, y, yaw, nav, route: Route, s: float) -> None:
        lm = self.lm
        c = self.cfg
        n2 = lm.size // 2
        res2 = lm.res * 2
        clr = lm.clearance[:2 * n2, :2 * n2].reshape(n2, 2, n2, 2).min(axis=(1, 3)).astype(np.float64)
        ref = float(c["path_clear_ref_m"])
        cost = 1.0 + float(c["path_w_clear"]) * np.clip(ref - clr, 0, None) / ref
        imp = clr <= self.inflate
        ci = np.arange(n2)
        cxs = lm.x0 + (ci + 0.5) * res2
        cys = lm.y0 + (ci + 0.5) * res2
        ri = int(np.clip((x - lm.x0) // res2, 0, n2 - 1))
        rj = int(np.clip((y - lm.y0) // res2, 0, n2 - 1))
        if self.here <= self.inflate:              # escape: wedged inside the margin
            D = np.hypot(cxs[None, :] - x, cys[:, None] - y)
            esc = (D <= 4.0) & imp & (clr > self.floor - 0.1)
            cost[esc] = 25.0
            imp[esc] = False
        if self.penalties:
            pen = np.zeros_like(imp)
            for pth in self.penalties:
                pi = ((pth[:, 0] - lm.x0) // res2).astype(int)
                pj = ((pth[:, 1] - lm.y0) // res2).astype(int)
                ok = (pi >= 0) & (pi < n2) & (pj >= 0) & (pj < n2)
                pen[pj[ok], pi[ok]] = True
            if pen.any():
                pen = ndimage.distance_transform_edt(~pen) * res2 <= 2.0
                cost[pen] += 30.0
        # Side commitment: once a detour passes on one side, switching sides costs extra, so a partly-seen
        # obstacle does not make the plan (and the rover) wiggle left-right between scans.
        p0 = route.point(s)
        hd0 = float(route.heading(s, half=3.0))
        lat = (-(cxs[None, :] - p0[0]) * np.sin(hd0) + (cys[:, None] - p0[1]) * np.cos(hd0))   # left = +
        lon = ((cxs[None, :] - p0[0]) * np.cos(hd0) + (cys[:, None] - p0[1]) * np.sin(hd0))
        if self.side:
            cost[(lat * self.side < -0.6) & (lon > 0)] += SIDE_SWITCH_COST
        cost[imp] = np.inf
        cost[rj, ri] = 1.0
        gxy, gs = self._goals(route, s)
        gi = ((gxy[:, 0] - lm.x0) // res2).astype(int)
        gj = ((gxy[:, 1] - lm.y0) // res2).astype(int)
        ok = (gi >= 0) & (gi < n2) & (gj >= 0) & (gj < n2)
        gi, gj, gs, gxy = gi[ok], gj[ok], gs[ok], gxy[ok]
        cells = None
        goal_s = None
        remaining_extra = 0.0
        if len(gi):
            goal_mask = np.zeros(cost.shape, bool)
            goal_mask[gj, gi] = True
            goal_mask &= np.isfinite(cost)
            res = astar(cost, (rj, ri), goal_mask, res2)
            if res.reached is not None:
                cells = res.path
                end = res.reached
                k = int(np.argmin((gj - end[0]) ** 2 + (gi - end[1]) ** 2))
                goal_s = float(gs[k])
            elif res.g:
                # No goal reachable: go to the explored cell nearest the goals (dist + 0.05·cost).
                keys = np.fromiter(res.g.keys(), dtype=np.int64)
                gvals = np.fromiter(res.g.values(), dtype=np.float64)
                jj, ii = np.divmod(keys, n2)
                keep = keys != rj * n2 + ri
                if keep.any():
                    jj, ii, gvals = jj[keep], ii[keep], gvals[keep]
                    d = np.hypot(cxs[ii][:, None] - gxy[None, :, 0], cys[jj][:, None] - gxy[None, :, 1])
                    dmin = d.min(axis=1)
                    b = int(np.argmin(dmin + 0.05 * gvals))
                    cells = res.trace((int(jj[b]), int(ii[b])))
                    goal_s = float(gs[int(np.argmin(d[b]))])
                    remaining_extra = float(dmin[b])
        if cells is None or len(cells) < 2:
            self.path = None
            return
        cells = np.asarray(smooth_path([tuple(cc) for cc in cells], cost))
        xy = np.stack([cxs[cells[:, 1]], cys[cells[:, 0]]], axis=1)
        xy[0] = (x, y)
        xy = self._round_corners(xy, yaw)
        xy = Route(xy).point(np.arange(0.0, Route(xy).length + 1e-9, res2)) if len(xy) > 1 else xy  # dense for pursuit
        cells = xy
        lat_path = -(xy[:, 0] - p0[0]) * np.sin(hd0) + (xy[:, 1] - p0[1]) * np.cos(hd0)
        k_far = int(np.argmax(np.abs(lat_path)))
        if abs(lat_path[k_far]) > 0.8:
            self.side = int(np.sign(lat_path[k_far]))
        if remaining_extra == 0.0 and goal_s is not None:
            ext = np.arange(goal_s + 1.0, min(goal_s + 10.0, route.length) + 1e-9, 1.0)
            if len(ext):
                xy = np.vstack([xy, route.point(ext)])
        self.path = xy
        self.path_idx = 0
        self.goal_s = goal_s
        seg = np.hypot(*np.diff(xy[: len(cells)], axis=0).T).sum()
        prog = goal_s - (seg + remaining_extra)
        if prog >= self.best_prog + 0.5:
            self.best_prog = prog
            self.last_prog_t = self.t

    def _path_clear(self, pts: np.ndarray) -> np.ndarray:
        """Per-point footprint clearance check along a polyline (heading from the neighbours)."""
        d = np.gradient(pts, axis=0) if len(pts) > 1 else np.zeros_like(pts)
        hd = np.arctan2(d[:, 1], d[:, 0])
        return self._fc(pts, hd) > self.inflate + TOLERANCE_M

    def _round_corners(self, xy: np.ndarray, yaw: float, iters: int = 3) -> np.ndarray:
        """Chaikin corner cutting with a clearance guard, starting tangent to the rover's heading.

        A corner is cut only if both replacement points keep the full footprint clearance, so rounding
        never brings the path closer to an obstacle than the safety margin allows.
        """
        if len(xy) < 2:
            return xy
        # Leave along the current heading when that is clear: the rover curves into the detour
        # instead of stopping to spin towards the first waypoint.
        first = xy[1] - xy[0]
        if np.hypot(*first) > 1.0 and abs(float(wrap(np.arctan2(first[1], first[0]) - yaw))) > np.deg2rad(15):
            lead = xy[0] + LEAD_IN_M * np.array([np.cos(yaw), np.sin(yaw)])
            if self._fc(lead[None, :], yaw)[0] > self.inflate + TOLERANCE_M:
                xy = np.vstack([xy[:1], lead, xy[1:]])
        for _ in range(iters):
            if len(xy) < 3:
                break
            out = [xy[0]]
            for i in range(1, len(xy) - 1):
                a, v, b = xy[i - 1], xy[i], xy[i + 1]
                cand = np.array([0.25 * a + 0.75 * v, 0.75 * v + 0.25 * b])
                if self._path_clear(np.vstack([a, cand, b]))[1:3].all():
                    out.extend(cand)
                else:
                    out.append(v)
            out.append(xy[-1])
            xy = np.asarray(out)
        return xy

    def _smooth_cmd(self, raw, x, y, yaw):
        """Limit acceleration and turn-rate changes while avoiding. Braking and holds are never delayed,
        and a limited command is used only if it passes the same safety check as the raw one."""
        v_raw, w_raw = float(raw[0]), float(raw[1])
        lv, lw = self._last_cmd
        dt = None if self._last_cmd_t is None else self.t - self._last_cmd_t
        self._last_cmd_t = self.t
        if dt is None or dt <= 0 or dt > 0.5 or (v_raw == 0.0 and w_raw == 0.0):
            self._last_cmd = (v_raw, w_raw)
            return v_raw, w_raw
        v = v_raw
        if np.sign(v_raw) == np.sign(lv) and abs(v_raw) > abs(lv):            # speeding up: ramp
            v = lv + np.clip(v_raw - lv, -ACCEL_M_S2 * dt, ACCEL_M_S2 * dt)
        w = lw + float(np.clip(w_raw - lw, -YAW_ACCEL_RAD_S2 * dt, YAW_ACCEL_RAD_S2 * dt))
        if (v, w) != (v_raw, w_raw) and not self._safe(x, y, yaw, v, w):
            v, w = v_raw, w_raw
        self._last_cmd = (float(v), float(w))
        return float(v), float(w)

    def _watchdog(self, x, y) -> None:
        stalled = self.t - self.last_prog_t > float(self.cfg["watchdog_s"])
        if self.path is not None and not stalled:
            return
        why = "no path" if self.path is None else "no progress"
        if self.attempts < int(self.cfg["max_recoveries"]):
            self.attempts += 1
            self.recoveries += 1
            if self.path is not None:
                self.penalties.append(self.path.copy())
            self.rec_start = np.array([x, y])
            self.rec_t = self.t
            self.path = None
            self._set_mode("recover", f"{why}: backing up (attempt {self.attempts})")
        else:
            self.path = None
            self._set_mode("blocked", f"{why} after {self.attempts} recoveries")

    def _check_recover_done(self, x, y, yaw, nav, route, s) -> bool:
        backed = self.rec_start is not None and np.hypot(x - self.rec_start[0], y - self.rec_start[1]) >= float(
            self.cfg["recover_back_m"])
        timeout = self.t - self.rec_t > float(self.cfg["recover_timeout_s"])
        if backed or not self.trail or timeout:
            self._set_mode("avoid", "recovery done, replanning")
            self.best_prog = -np.inf
            self.last_prog_t = self.t
            self._plan(x, y, yaw, nav, route, s)
            return True
        return False

    # ------------------------------------------------------------------ per tick
    def _safe(self, x, y, yaw, v, w) -> bool:
        if abs(v) < 1e-3:
            return True
        P = self.arcs.predict(x, y, yaw, v, w, 2.0)
        return bool(self._fc(P[1:, :2], P[1:, 2]).min() >= self._req())

    def _swing_clear(self, x, y, yaw, dyaw) -> bool:
        n = max(1, int(np.ceil(abs(np.degrees(dyaw)) / 5.0)))
        hs = yaw + np.linspace(0, dyaw, n + 1)[1:]
        return bool(self._fc(np.repeat([[x, y]], n, axis=0), hs).min() >= self._req())

    def _fallback(self, x, y, yaw, alpha: float):
        if abs(alpha) > 1e-3 and self._swing_clear(x, y, yaw, alpha):
            self.choice = "turn"
            return 0.0, float(np.sign(alpha) * self.wmax)
        if self._safe(x, y, yaw, -0.3, 0.0):
            self.choice = "reverse"
            return -0.3, 0.0
        self.choice = "hold"
        return 0.0, 0.0

    def filter(self, nav_cmd, pose, nav, t: float | None = None):
        if t is not None:
            self.t = float(t)
        if getattr(nav, "state", "driving") != "driving" or self.lm is None:
            self.choice = None
            return nav_cmd
        x, y, yaw = float(pose.x), float(pose.y), yaw_of(pose.q)
        if self.mode == "track":
            v, w = (float(nav_cmd[0]), float(nav_cmd[1]))
            if self.lm.empty or self._safe(x, y, yaw, v, w):
                self.choice = None
                self._last_cmd, self._last_cmd_t = (v, w), self.t     # the limiter starts from here
                return nav_cmd
            self._enter_avoid(pose, nav, "safety stop")
        if self.mode == "blocked":
            self.choice = "hold"
            return 0.0, 0.0
        if self.mode == "recover":
            return self._smooth_cmd(self._reverse_on_trail(x, y, yaw, nav), x, y, yaw)
        return self._smooth_cmd(self._pursue(x, y, yaw, nav), x, y, yaw)

    def _pursue(self, x, y, yaw, nav):
        if self.path is None or len(self.path) < 2:
            self.choice = "hold"
            self.carrot = None
            return 0.0, 0.0
        P = self.path
        lo = self.path_idx
        hi = min(len(P), lo + 40)
        d = np.hypot(P[lo:hi, 0] - x, P[lo:hi, 1] - y)
        self.path_idx = lo + int(np.argmin(d))
        ld = float(self.cfg["path_lookahead_m"])
        seg = np.hypot(*np.diff(P[self.path_idx:], axis=0).T)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        k = self.path_idx + int(min(np.searchsorted(cum, ld), len(cum) - 1))
        tgt = P[k]
        self.carrot = tgt
        dx, dy = tgt[0] - x, tgt[1] - y
        alpha = float(wrap(np.arctan2(dy, dx) - yaw))
        dist = max(float(np.hypot(dx, dy)), 0.5)
        if abs(alpha) > np.deg2rad(45.0):
            return self._fallback(x, y, yaw, alpha)
        cruise = float(getattr(nav, "cruise", 0.5) or 0.5)
        v = min(cruise, float(self.cfg["avoid_speed_m_s"]))
        if self.here < self.inflate + 0.5:
            v = min(v, 0.3)
        w = float(np.clip(v * 2.0 * np.sin(alpha) / dist, -self.wmax, self.wmax))
        if self._safe(x, y, yaw, v, w):
            self.choice = "path"
            return v, w
        return self._fallback(x, y, yaw, alpha)

    def _reverse_on_trail(self, x, y, yaw, nav):
        if self.rec_start is not None and self._check_recover_done(x, y, yaw, nav, Route.of(nav), float(nav.s)):
            return self._pursue(x, y, yaw, nav)
        while self.trail and np.hypot(self.trail[-1][0] - x, self.trail[-1][1] - y) < 0.6:
            self.trail.pop()
        if not self.trail:
            self.choice = "hold"
            return 0.0, 0.0
        # Target ~1.5 m back along the trail (time order: newest first).
        acc, prev, tgt = 0.0, (x, y), self.trail[-1]
        for pt in reversed(self.trail):
            acc += np.hypot(pt[0] - prev[0], pt[1] - prev[1])
            prev, tgt = pt, pt
            if acc >= 1.5:
                break
        self.carrot = np.asarray(tgt)
        bx = np.cos(yaw) * (tgt[0] - x) + np.sin(yaw) * (tgt[1] - y)
        by = -np.sin(yaw) * (tgt[0] - x) + np.cos(yaw) * (tgt[1] - y)
        # Angle of the target off the rear axis (positive = behind-left). Bringing the rear onto it needs
        # a yaw change of -a_rear, and while reversing the same sign of yaw rate steers the rear there.
        a_rear = float(np.arctan2(by, -bx))
        if abs(a_rear) > np.deg2rad(60.0):
            dyaw = -a_rear
            if self._swing_clear(x, y, yaw, dyaw):
                self.choice = "turn"
                return 0.0, float(np.sign(dyaw) * self.wmax)
            self.choice = "hold"
            return 0.0, 0.0
        dist = max(float(np.hypot(bx, by)), 0.5)
        w = float(np.clip(-0.3 * 2.0 * np.sin(a_rear) / dist, -self.wmax, self.wmax))
        if self._safe(x, y, yaw, -0.3, w):
            self.choice = "reverse"
            return -0.3, w
        self.choice = "hold"
        return 0.0, 0.0

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        arc = None
        if self.path is not None and self.mode != "track":
            arc = [[round(float(a), 2), round(float(b), 2)] for a, b in self.path[::3]]
        return {"mode": self.mode, "detours": self.detours, "recoveries": self.recoveries,
                "blocked_ahead_m": None if self.blocked_at is None else round(self.blocked_at, 2),
                "choice": self.choice, "arc": arc}
