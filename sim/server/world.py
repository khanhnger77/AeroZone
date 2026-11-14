"""Multi-object world for the sim (path-traverse and world demos).

Every demo runs a `World`: a set of objects (rovers, astronauts) on the mission terrain. Exactly one
object is *selected*: the server drives it with the physics router (PyBullet wheeled rover, or the
astronaut walker), the pure-pursuit navigator and M5 obstacle avoidance. The other objects are
stepped here with a cheap terrain-following integrator, so tens of them cost almost nothing.

Object modes:
  route      follow the mission route (path-traverse demo; start/pause/reset as before)
  wander     walk to random nearby goals, forever
  goto       drive to a fixed point (path from the M2 hazard planner when it answers, else straight)
  intercept  chase another object, aiming at the predicted intercept point (re-planned periodically)
  manual     driven by the user (WASD / joystick) - selected object only
  stopped    met another object; waits until it is given a new mission
  idle       arrived / nothing to do

Speed over ground is the cruise speed scaled by a terrain factor from the local grade along the
heading and the local roughness, for every object; the intercept solution uses the same factor
averaged along the line of sight, so steep or rough ground ahead lengthens the predicted time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

AUTO_MODES = ("wander", "goto", "intercept")
MOVING_MODES = AUTO_MODES + ("route",)
KINDS = ("rover", "astronaut")
KIND_LABEL = {"rover": "Rover", "astronaut": "Astronaut"}
CRUISE_RANGE = {"rover": (0.4, 1.5), "astronaut": (0.5, 1.2)}   # m/s on flat ground
YAW_RATE = {"rover": 0.35, "astronaut": 1.2}                      # rad/s
MEET_RADIUS_M = 5.0          # two objects closer than this have "met": both stop
ARRIVE_RADIUS_M = 3.0
LOOKAHEAD_M = 4.0
WANDER_RANGE_M = (60.0, 350.0)
SPAWN_RADIUS_M = 600.0       # world demo objects start around the landing site
INTERCEPT_REPLAN_S = 6.0     # sim seconds between intercept re-plans
INTERCEPT_DRIFT_M = 10.0     # or sooner when the aim point moved this far
ROCK_LOOKAHEAD_M = 9.0
EDGE_MARGIN_M = 40.0
# Consumables (percent per sim hour). Rover battery: hotel load plus a drive term per m/s;
# astronaut suit oxygen: ~8 h at rest, faster when walking. Floored at 0 %. Demo values, not mission data.
DRAIN_PCT_H = {"rover": (3.0, 6.0), "astronaut": (11.0, 4.0)}   # (idle, extra per m/s)


def _wrap(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def intercept_point(p, speed: float, q, vq, max_tau: float = 3600.0):
    """Earliest point where a pursuer at ``p`` with ``speed`` can meet a target at ``q`` moving with
    constant velocity ``vq``: solve |q + vq·τ − p| = speed·τ for the smallest τ > 0.

    Returns ``(aim_xy, tau)``; ``tau`` is None (aim at the target itself) when no solution exists.
    """
    p, q, vq = (np.asarray(a, dtype=np.float64) for a in (p, q, vq))
    r = q - p
    a = float(vq @ vq) - speed * speed
    b = 2.0 * float(r @ vq)
    c = float(r @ r)
    taus: list[float] = []
    if abs(a) < 1e-9:
        if b < 0:
            taus = [-c / b]
    else:
        disc = b * b - 4.0 * a * c
        if disc >= 0:
            sq = math.sqrt(disc)
            taus = [t for t in ((-b - sq) / (2 * a), (-b + sq) / (2 * a)) if t > 0]
    taus = [t for t in taus if t <= max_tau]
    if not taus:
        return q, None
    tau = min(taus)
    return q + vq * tau, tau


@dataclass
class Agent:
    id: str
    name: str
    kind: str
    x: float
    y: float
    yaw: float
    cruise: float
    z: float = 0.0
    v: float = 0.0
    w: float = 0.0
    mode: str = "idle"
    goal: tuple[float, float] | None = None
    target_id: str | None = None
    path: np.ndarray | None = None
    path_idx: int = 0
    path_version: int = 0
    plan_t: float = -math.inf
    aim: tuple[float, float] | None = None
    home: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rng_seed: int = 0
    initial_mode: str = "wander"
    energy: float = 100.0              # rover battery / astronaut oxygen, percent
    route_pref: str | None = None      # planner profile for a goto ("cautious" for locations)

    def set_path(self, xy) -> None:
        """Path from the current position through ``xy`` (at least two distinct points)."""
        start = np.array([[self.x, self.y]])
        pts = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        if len(pts) == 0 or np.hypot(*(pts[0] - start[0])) > 1.0:
            pts = np.vstack([start, pts])
        if np.hypot(*(pts[-1] - pts[0])) < 0.5:
            pts = np.vstack([start, start + [[0.5 * math.cos(self.yaw), 0.5 * math.sin(self.yaw)]]])
        self.path = pts
        self.path_idx = 0
        self.path_version += 1


@dataclass
class World:
    grid: object                       # Grid the objects move on (the mission corridor)
    frame: object                      # Frame for lon/lat
    obstacles: list = field(default_factory=list)   # (x, y, radius) discs to steer around
    seed: int = 0
    agents: dict[str, Agent] = field(default_factory=dict)
    selected: str | None = None
    running: bool = False
    t: float = 0.0
    events: list[dict] = field(default_factory=list)
    _near: set = field(default_factory=set)
    _counts: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        x0, x1, y0, y1 = self.grid.extent()
        m = EDGE_MARGIN_M
        self.bounds = (x0 + m, x1 - m, y0 + m, y1 - m)

    # ------------------------------------------------------------------ objects
    def add(self, kind: str, x: float, y: float, yaw: float, name: str | None = None,
            cruise: float | None = None, mode: str = "wander") -> Agent:
        if kind not in KINDS:
            raise ValueError(f"unknown object type {kind!r}")
        n = self._counts.get(kind, 0) + 1
        self._counts[kind] = n
        lo, hi = CRUISE_RANGE[kind]
        a = Agent(id=f"{kind[0]}{n}", name=name or f"{KIND_LABEL[kind]}_{n}", kind=kind,
                  x=float(x), y=float(y), yaw=float(yaw),
                  cruise=float(cruise if cruise is not None else self.rng.uniform(lo, hi)),
                  home=(float(x), float(y), float(yaw)), rng_seed=int(self.rng.integers(2 ** 31)))
        a.z = self.h(a.x, a.y)
        a.initial_mode = mode
        self.agents[a.id] = a
        if self.selected is None:
            self.selected = a.id
        self.set_mode(a, mode)
        return a

    def spawn_random(self, kind: str, center: tuple[float, float], name: str | None = None) -> Agent:
        for _ in range(200):
            r = SPAWN_RADIUS_M * math.sqrt(self.rng.uniform())
            th = self.rng.uniform(0, 2 * math.pi)
            x, y = self.clamp(center[0] + r * math.cos(th), center[1] + r * math.sin(th))
            if self._clear(x, y, 10.0) and all(math.hypot(x - o.x, y - o.y) > 30 for o in self.agents.values()):
                break
        return self.add(kind, x, y, float(self.rng.uniform(-math.pi, math.pi)), name=name)

    def _clear(self, x: float, y: float, margin: float) -> bool:
        return all((x - ox) ** 2 + (y - oy) ** 2 > (r + margin) ** 2 for ox, oy, r in self.obstacles)

    def clamp(self, x: float, y: float) -> tuple[float, float]:
        x0, x1, y0, y1 = self.bounds
        return min(max(x, x0), x1), min(max(y, y0), y1)

    def h(self, x: float, y: float) -> float:
        return float(self.grid.height_at(x, y))

    @property
    def sel(self) -> Agent | None:
        return self.agents.get(self.selected) if self.selected else None

    def reset(self) -> None:
        for a in self.agents.values():
            a.x, a.y, a.yaw = a.home
            a.z, a.v, a.w = self.h(a.x, a.y), 0.0, 0.0
            a.energy = 100.0
            self.set_mode(a, a.initial_mode)
        self._near.clear()
        self.running = False

    # ------------------------------------------------------------------ missions
    def set_mode(self, a: Agent, mode: str, goal=None, target_id: str | None = None) -> None:
        a.mode, a.target_id, a.aim = mode, target_id, None
        a.goal = None if goal is None else (float(goal[0]), float(goal[1]))
        a.plan_t = -math.inf
        if mode == "wander":
            self._wander_goal(a)
        elif mode == "goto":
            a.set_path([a.goal])
        elif mode == "intercept":
            self._plan_intercept(a)
        elif mode != "route":
            a.path = None
            a.path_version += 1

    def assign_goto(self, agent_id: str, x: float, y: float, route: str | None = None) -> Agent:
        a = self.agents[agent_id]
        a.route_pref = route
        self.set_mode(a, "goto", goal=self.clamp(x, y))
        self._forget_meetings(a.id)
        return a

    def assign_intercept(self, agent_id: str, target_id: str) -> Agent:
        if target_id == agent_id or target_id not in self.agents:
            raise ValueError("intercept target must be another object")
        a = self.agents[agent_id]
        self.set_mode(a, "intercept", target_id=target_id)
        self._forget_meetings(a.id)
        return a

    def _forget_meetings(self, agent_id: str) -> None:
        # A new mission releases the "met" lock, so a pair that is still close does not re-stop at once.
        self._near = {p for p in self._near if agent_id not in p} | {
            frozenset((agent_id, o.id)) for o in self.agents.values()
            if o.id != agent_id and self._dist(self.agents[agent_id], o) < MEET_RADIUS_M}

    def _wander_goal(self, a: Agent) -> None:
        rng = np.random.default_rng(a.rng_seed + a.path_version)
        r = rng.uniform(*WANDER_RANGE_M)
        th = rng.uniform(-math.pi, math.pi)
        a.goal = self.clamp(a.x + r * math.cos(th), a.y + r * math.sin(th))
        a.set_path([a.goal])

    def _velocity(self, a: Agent) -> np.ndarray:
        return np.array([a.v * math.cos(a.yaw), a.v * math.sin(a.yaw)])

    def effective_speed(self, a: Agent, to_xy) -> float:
        """Cruise speed scaled by the mean terrain factor along the straight line to ``to_xy``."""
        d = math.hypot(to_xy[0] - a.x, to_xy[1] - a.y)
        n = int(min(30, max(2, d / 20.0)))
        hd = math.atan2(to_xy[1] - a.y, to_xy[0] - a.x)
        f = [self.terrain_factor(a.kind, a.x + (to_xy[0] - a.x) * k / n, a.y + (to_xy[1] - a.y) * k / n, hd)
             for k in range(n)]
        return max(0.05, a.cruise * float(np.mean(f)))

    def _plan_intercept(self, a: Agent) -> None:
        tgt = self.agents.get(a.target_id or "")
        if tgt is None:
            self.set_mode(a, "idle")
            return
        q = np.array([tgt.x, tgt.y])
        aim, _ = intercept_point((a.x, a.y), self.effective_speed(a, q), q, self._velocity(tgt))
        a.aim = self.clamp(float(aim[0]), float(aim[1]))
        a.goal = a.aim
        a.plan_t = self.t
        a.set_path([a.aim])

    def intercept_due(self, a: Agent) -> bool:
        if a.mode != "intercept" or a.target_id not in self.agents:
            return False
        if self.t - a.plan_t >= INTERCEPT_REPLAN_S:
            return True
        tgt = self.agents[a.target_id]
        aim, _ = intercept_point((a.x, a.y), max(a.cruise, 0.05), (tgt.x, tgt.y), self._velocity(tgt))
        return a.aim is None or math.hypot(aim[0] - a.aim[0], aim[1] - a.aim[1]) > INTERCEPT_DRIFT_M * 3

    def on_arrived(self, a: Agent) -> None:
        if a.mode == "wander":
            self._wander_goal(a)
        elif a.mode == "intercept":
            self._plan_intercept(a)
        elif a.mode == "goto":
            self.set_mode(a, "idle")

    # ------------------------------------------------------------------ terrain
    def terrain_factor(self, kind: str, x: float, y: float, heading: float) -> float:
        """Speed factor (0.15..1.1) from the grade along ``heading`` and the local roughness."""
        c, s = math.cos(heading), math.sin(heading)
        d = 3.0
        hs = self.grid.height_at(np.array([x, x + d * c, x - d * s, x + d * s, x - d * c]),
                                 np.array([y, y + d * s, y + d * c, y - d * c, y - d * s]))
        grade = (hs[1] - hs[0]) / d
        rough = float(np.std(hs - hs.mean()))
        if kind == "astronaut":
            f = math.exp(-3.5 * abs(grade + 0.05)) / math.exp(-3.5 * 0.05)        # Tobler
        else:
            f = 1.0 - min(abs(math.degrees(math.atan(grade))) / 30.0, 0.8)        # slows to 20% at 24°+
        f *= max(0.4, 1.0 - rough / 0.6)
        return float(min(max(f, 0.15), 1.1))

    def _avoid_heading(self, a: Agent, desired: float) -> float:
        """Bend ``desired`` away from obstacle discs inside a forward cone (reactive avoidance)."""
        push = 0.0
        for ox, oy, r in self.obstacles:
            dx, dy = ox - a.x, oy - a.y
            d = math.hypot(dx, dy) - r
            if d > ROCK_LOOKAHEAD_M or d < -r:
                continue
            bearing = _wrap(math.atan2(dy, dx) - desired)
            if abs(bearing) > 1.2:
                continue
            side = -1.0 if bearing >= 0 else 1.0          # obstacle on the left -> turn right
            push += side * (1.0 - max(d, 0.0) / ROCK_LOOKAHEAD_M) * 1.2
        return _wrap(desired + max(-1.4, min(1.4, push)))

    # ------------------------------------------------------------------ stepping
    def sync_selected(self, x: float, y: float, z: float, yaw: float, v: float) -> None:
        a = self.sel
        if a is not None:
            a.x, a.y, a.z, a.yaw, a.v = float(x), float(y), float(z), float(yaw), float(v)

    def step(self, dt: float) -> None:
        """Advance the unselected objects by ``dt`` sim seconds and apply the meeting rule."""
        if dt <= 0:
            return
        self.t += dt
        if self.running:
            for a in self.agents.values():             # consumables drain while time runs
                idle, per_ms = DRAIN_PCT_H[a.kind]
                a.energy = max(0.0, a.energy - (idle + per_ms * abs(a.v)) * dt / 3600.0)
            for a in self.agents.values():
                if a.id == self.selected:
                    if self.intercept_due(a):
                        self._plan_intercept(a)
                    continue
                self._step_agent(a, dt)
        self._check_meetings()

    def _step_agent(self, a: Agent, dt: float) -> None:
        if a.mode not in AUTO_MODES or a.path is None or len(a.path) < 2:
            a.v = 0.0
            return
        if self.intercept_due(a):
            self._plan_intercept(a)
        # Pure pursuit on the polyline: aim at the first vertex beyond the lookahead distance.
        while a.path_idx < len(a.path) - 1 and math.hypot(a.path[a.path_idx][0] - a.x,
                                                          a.path[a.path_idx][1] - a.y) < LOOKAHEAD_M:
            a.path_idx += 1
        gx, gy = a.path[-1]
        if math.hypot(gx - a.x, gy - a.y) < ARRIVE_RADIUS_M:
            a.v = 0.0
            self.on_arrived(a)
            return
        tx, ty = a.path[a.path_idx]
        desired = self._avoid_heading(a, math.atan2(ty - a.y, tx - a.x))
        err = _wrap(desired - a.yaw)
        rate = YAW_RATE[a.kind]
        a.w = max(-rate, min(rate, 2.0 * err))
        a.yaw = _wrap(a.yaw + a.w * dt)
        v = 0.0 if abs(err) > 1.0 else a.cruise * self.terrain_factor(a.kind, a.x, a.y, a.yaw) * math.cos(err)
        a.v = v
        nx, ny = self.clamp(a.x + v * math.cos(a.yaw) * dt, a.y + v * math.sin(a.yaw) * dt)
        if self._clear(nx, ny, 0.8):
            a.x, a.y = nx, ny
        a.z = self.h(a.x, a.y)

    @staticmethod
    def _dist(a: Agent, b: Agent) -> float:
        return math.hypot(a.x - b.x, a.y - b.y)

    def _check_meetings(self) -> None:
        ids = list(self.agents)
        near = set()
        for i, ia in enumerate(ids):
            for ib in ids[i + 1:]:
                a, b = self.agents[ia], self.agents[ib]
                if abs(a.x - b.x) < MEET_RADIUS_M and abs(a.y - b.y) < MEET_RADIUS_M \
                        and self._dist(a, b) < MEET_RADIUS_M:
                    near.add(frozenset((ia, ib)))
        for pair in near - self._near:
            a, b = (self.agents[i] for i in pair)
            if a.mode in MOVING_MODES or b.mode in MOVING_MODES:
                for o in (a, b):
                    if o.mode in AUTO_MODES:
                        self.set_mode(o, "stopped")
                        o.v = 0.0
                self.events.append({"type": "world_event", "event": "met",
                                    "detail": f"{a.name} met {b.name}", "agents": [a.id, b.id]})
        self._near = near

    def pop_events(self) -> list[dict]:
        ev, self.events = self.events, []
        return ev

    # ------------------------------------------------------------------ description
    def describe_agent(self, a: Agent) -> dict:
        lon, lat = self.frame.to_lonlat(a.x, a.y)
        d = {"id": a.id, "name": a.name, "kind": a.kind, "mode": a.mode,
             "lon": round(float(lon), 8), "lat": round(float(lat), 8),
             "x": round(a.x, 2), "y": round(a.y, 2), "z": round(a.z, 2),
             "yaw_deg": round(math.degrees(a.yaw), 1), "speed_m_s": round(abs(a.v), 3),
             "cruise_m_s": round(a.cruise, 3), "target": a.target_id, "goal": None,
             "energy": round(a.energy, 2)}
        if a.goal is not None and a.mode in ("goto", "intercept", "wander"):
            glon, glat = self.frame.to_lonlat(*a.goal)
            d["goal"] = {"lon": round(float(glon), 8), "lat": round(float(glat), 8)}
        d["path"] = self._remaining_path(a) if a.mode in ("goto", "intercept") else None
        return d

    def _remaining_path(self, a: Agent, max_pts: int = 60) -> list | None:
        """Mission path still ahead of a as [lon, lat] (for the dotted line on the map)."""
        if a.path is None or len(a.path) < 2:
            return None
        k = int(np.argmin(np.hypot(a.path[:, 0] - a.x, a.path[:, 1] - a.y)))
        pts = np.vstack([[a.x, a.y], a.path[min(k + 1, len(a.path) - 1):]])
        if len(pts) > max_pts:
            pts = np.vstack([pts[:-1][:: int(np.ceil((len(pts) - 1) / (max_pts - 1)))], pts[-1:]])
        lon, lat = self.frame.to_lonlat(pts[:, 0], pts[:, 1])
        return [[round(float(o), 7), round(float(t), 7)] for o, t in zip(np.atleast_1d(lon), np.atleast_1d(lat))]

    def describe(self) -> dict:
        return {"selected": self.selected, "running": self.running,
                "agents": [self.describe_agent(a) for a in self.agents.values()]}
