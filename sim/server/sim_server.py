"""aiohttp sim server: HTTP API (C5), WebSocket telemetry (C6) and the fixed-rate sim loop (C8).

Why this module exists: it is the single authority for the rover's state (principle 1). One process
owns one PyBullet world. The browser only renders what this server streams.

Threading model. PyBullet is not thread-safe, so every physics call runs on the event-loop thread:
the 50 Hz loop, mission (re)building after the terrain read, and the commands. Only the slow,
physics-free work runs in a thread pool: the GeoTIFF read (rasterio) and the texture mosaics.

Loop (C8 call order, principle 8): sleep 1/loop_hz; skip unless there is both a mission and a
connected client; then avoid.tick -> nav.observe -> avoid.command(nav.command()) -> router.update ->
optional LDR1 frame -> telemetry.

Run: `python -m sim.server.sim_server --host 127.0.0.1 --port 8100`.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import math
import os
import secrets
import signal
import time
from pathlib import Path

import numpy as np
import yaml
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from sim.backend.navigator import PurePursuitNavigator
from sim.server.physics import RoverModelRouter, euler_from_quat
from sim.server.terrain import TerrainSource, build_mission_terrain
from sim.server.textures import TextureService, TextureUnavailable
from sim.server.world import AUTO_MODES, KINDS, World

try:  # M5 owns this facade; until it exists the sim runs with obstacle avoidance disabled.
    from sim.obstacle_avoid.service import ObstacleAvoidance
except ImportError:  # pragma: no cover - depends on M5 being present
    ObstacleAvoidance = None

log = logging.getLogger("sim.server")

SIM_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SIM_DIR.parent
PROTOCOL = "mo-sim/1"
DEFAULT_TOPICS = frozenset({"telemetry", "events"})
KNOWN_TOPICS = frozenset({"telemetry", "events", "lidar"})
WARP_RANGE = (0.1, 50.0)
TILT_EVENT_PERIOD_S = 10.0
DEMO_START = {"lon": 77.45088572, "lat": 18.44462715}   # Perseverance landing site
DEMO_END = {"lon": 77.3052, "lat": 18.4975}            # Cheyava Falls / sample 25
WORLD_GRID_POINTS = 1200      # world demo: the whole AOI is the corridor, ~8 m posts
WORLD_MAX_AGENTS = 40
WORLD_KM2_PER_ROCK = 0.5      # world demo: random boulders, ~2 per km2 (sparse)
WORLD_KM2_PER_CRATER = 2.5    # and a crater per 2.5 km2
WORLD_POST_HZ = 5.0
DRIVE_TIMEOUT_S = 0.6         # manual drive input expires (released key, lost socket)

# Static trees under /sim (C5). Explicit MIME types: Windows registries often map .js to text/plain,
# which browsers refuse for ES modules.
STATIC_ROOTS = {
    "js": SIM_DIR / "web" / "js",
    "vendor": SIM_DIR / "web" / "vendor",
    "rover/model": SIM_DIR / "rover" / "model",
    "obstacle_avoid": SIM_DIR / "obstacle_avoid" / "web",
}
MIME = {".js": "text/javascript", ".mjs": "text/javascript", ".wasm": "application/wasm",
        ".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".json": "application/json",
        ".css": "text/css", ".html": "text/html", ".png": "image/png", ".jpg": "image/jpeg",
        ".svg": "image/svg+xml"}


class TerrainMissing(RuntimeError):
    """The C2 elevation raster is not available (HTTP 503)."""


class PlannerUnavailable(RuntimeError):
    """The C4 route planner could not supply the demo route (HTTP 503)."""


class _NullAvoidance:
    """Stand-in for M5's facade with the same interface; obstacle avoidance stays disabled."""

    def __init__(self, rover_cfg: dict) -> None:
        self.enabled = False

    def setup(self, enabled, seed, path_xy, grid, world, rocks=None, craters=None) -> None:
        self.enabled = False

    def reset(self) -> None:
        pass

    def describe(self, frame=None) -> dict:
        return {"enabled": False, "rocks": [], "craters": []}

    def tick(self, pose, nav, sim_time):
        return None

    def command(self, nav_cmd, pose, nav):
        return nav_cmd

    def status(self, nav=None):
        return None

    def stream_due(self) -> bool:
        return False

    def encode_frame(self, pose, nav) -> bytes:
        return b""


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def dumps(obj) -> str:
    return json.dumps(obj, default=_json_default, separators=(",", ":"), allow_nan=False)


def _r(v: float, n: int = 3) -> float:
    return round(float(v), n)


def _xy_list(xy: np.ndarray) -> list[list[float]]:
    return np.round(np.asarray(xy, dtype=np.float64), 3).tolist()


class SimServer:
    """Owns the configs, the physics router, the navigator, the M5 facade and the mission."""

    def __init__(self, physics_cfg: dict | None = None, rover_cfg: dict | None = None,
                 terrain_path: str | Path | None = None, cache_dir: str | Path | None = None) -> None:
        self.physics_cfg = physics_cfg or load_yaml(SIM_DIR / "configs" / "physics.yaml")
        self.rover_cfg = rover_cfg or load_yaml(SIM_DIR / "configs" / "rover.yaml")
        src = Path(terrain_path or self.physics_cfg["terrain"]["source"])
        self.terrain_path = src if src.is_absolute() else REPO_ROOT / src
        self.loop_hz = float(self.physics_cfg.get("loop_hz", 50))
        self.planner_url = str(self.physics_cfg["backend"]["planner_url"]).rstrip("/")

        self.router = RoverModelRouter(self.physics_cfg, self.rover_cfg)
        self.nav = PurePursuitNavigator(self.rover_cfg)
        avoid_cls = ObstacleAvoidance or _NullAvoidance
        self.avoid = avoid_cls(self.rover_cfg)
        self.textures = TextureService(self.physics_cfg["textures"], cache_dir or SIM_DIR / "cache")

        self.clients: dict[web.WebSocketResponse, set[str]] = {}
        self.mission: dict | None = None        # the current MissionDescription
        self.frame = None
        self.grids: dict = {}                   # level -> Grid
        self.terrain_bytes: dict[str, bytes] = {}
        self.path_xy: np.ndarray | None = None
        self.warp = 1.0
        self._source: TerrainSource | None = None
        self._build_lock = asyncio.Lock()
        self._demo_lock = asyncio.Lock()
        self._loop_task: asyncio.Task | None = None
        self._last_tilt_event = -math.inf
        self._background: set[asyncio.Task] = set()
        self.world: World | None = None         # the objects of the current demo (C5 extension)
        self._nav_version = -1                  # selected object's path version loaded into nav
        self._drive = (0.0, 0.0, -math.inf)     # manual (v, w) and its monotonic time
        self._last_world_post = -math.inf

    # ------------------------------------------------------------------------------------ config
    @property
    def mission_id(self) -> str | None:
        return self.mission["id"] if self.mission else None

    def _lidar_desc(self) -> dict | None:
        lidar = getattr(self.avoid, "lidar", None)
        return lidar.describe() if lidar is not None and hasattr(lidar, "describe") else None

    def _planner_name(self) -> str:
        return str(getattr(self.avoid, "planner_name", getattr(self.avoid, "planner", "")) or "")

    def config(self) -> dict:
        """The C5 discovery document."""
        pc, d, lim = self.physics_cfg, self.rover_cfg["dynamics"], self.rover_cfg["limits"]
        return {
            "protocol": PROTOCOL, "ws": "ws", "api": "api", "backend": "pure_pursuit",
            "physics": {
                "active": self.router.describe(),
                "available": list(self.router.available),
                "gravity_m_s2": float(pc["gravity_m_s2"]),
                "timestep_s": float(pc["timestep_s"]),
                "air_density_kg_m3": float(pc["atmosphere"]["air_density_kg_m3"]),
            },
            "loop_hz": self.loop_hz,
            "rover": {
                "name": self.rover_cfg["name"],
                "model_url": "rover/model/perseverance.glb",
                "chassis_size_m": d["chassis_size_m"],
                "wheel_radius_m": d["wheel_radius_m"],
                "wheel_positions_m": d["wheel_positions_m"],
            },
            # Additive (proposed C5 extension): the on-foot "astronaut" model, when available.
            "astronaut": self.router.astronaut.describe() if self.router.astronaut is not None else None,
            "controls": {
                "speeds_m_s": lim["demo_speeds_m_s"],
                "real_max_speed_m_s": lim["real_max_speed_m_s"],
                "default_speed_m_s": lim["default_speed_m_s"],
                "warps": pc["warps"],
                "models": list(self.router.available),
            },
            "obstacle_avoid": {"available": True, "lidar": self._lidar_desc(),
                               "planner": self._planner_name()},
            "mission": self.mission_id,
        }

    # ------------------------------------------------------------------------------------ mission
    def _terrain_source(self) -> TerrainSource:
        if self._source is None:
            if not self.terrain_path.exists():
                raise TerrainMissing(f"terrain raster missing: {self.terrain_path} "
                                     "(run the M1 data pipeline)")
            self._source = TerrainSource(self.terrain_path)
        return self._source

    @staticmethod
    def _parse_route(r, what: str) -> dict:
        if not isinstance(r, dict):
            raise ValueError(f"{what} must be an object")
        coords = r.get("coordinates")
        try:
            arr = np.asarray(coords, dtype=np.float64)
        except (TypeError, ValueError):
            raise ValueError(f"{what}.coordinates must be [lon, lat][]") from None
        if arr.ndim != 2 or arr.shape[1] != 2 or arr.shape[0] < 2 or not np.isfinite(arr).all():
            raise ValueError(f"{what}.coordinates must hold at least two finite [lon, lat] points")
        return {"id": str(r.get("id", "route")), "label": str(r.get("label", r.get("id", "route"))),
                "color": str(r.get("color", "#34d399")), "length_m": r.get("length_m"),
                "coordinates": arr}

    @staticmethod
    def _parse_point(p, fallback: np.ndarray, what: str) -> dict:
        if p is None:
            return {"lon": float(fallback[0]), "lat": float(fallback[1])}
        try:
            lon, lat = float(p["lon"]), float(p["lat"])
        except (TypeError, KeyError, ValueError):
            raise ValueError(f"{what} must be {{lon, lat}}") from None
        if not (math.isfinite(lon) and math.isfinite(lat)):
            raise ValueError(f"{what} must be finite")
        return {"lon": lon, "lat": lat}

    def parse_request(self, req) -> dict:
        """Validate a C5 MissionRequest; raises ValueError (HTTP 422)."""
        if not isinstance(req, dict):
            raise ValueError("mission request must be a JSON object")
        route = self._parse_route(req.get("route"), "route")
        alts = req.get("alternatives") or []
        if not isinstance(alts, list):
            raise ValueError("alternatives must be a list")
        alternatives = [self._parse_route(a, f"alternatives[{i}]") for i, a in enumerate(alts)]
        obst = req.get("obstacles") or {}
        if not isinstance(obst, dict):
            raise ValueError("obstacles must be an object")
        seed = obst.get("seed")
        try:
            seed = int(seed) if seed is not None else secrets.randbelow(2 ** 31)
        except (TypeError, ValueError):
            raise ValueError("obstacles.seed must be an integer") from None
        coords = route["coordinates"]
        obj = req.get("object")
        if obj is not None and obj not in KINDS:
            raise ValueError(f"object must be one of {list(KINDS)}")
        return {"route": route, "alternatives": alternatives, "object": obj,
                "start": self._parse_point(req.get("start"), coords[0], "start"),
                "end": self._parse_point(req.get("end"), coords[-1], "end"),
                "obstacles": {"enabled": bool(obst.get("enabled", False)), "seed": seed}}

    async def build_mission(self, raw_req) -> dict:
        """Build terrain + obstacles + rover for a MissionRequest (C8 mission build order)."""
        req = self.parse_request(raw_req)
        async with self._build_lock:
            src = self._terrain_source()
            loop = asyncio.get_running_loop()
            frame, corridor, context = await loop.run_in_executor(
                None, build_mission_terrain, src, req["route"]["coordinates"].tolist(),
                self.physics_cfg)
            # From here on: loop thread only, no awaits, so the sim loop never sees a half-built mission.
            return self._install_mission(req, frame, corridor, context)

    def _install_mission(self, req: dict, frame, corridor, context) -> dict:
        route = req["route"]
        x, y = frame.to_local(route["coordinates"][:, 0], route["coordinates"][:, 1])
        path_xy = np.column_stack([x, y])
        obst = req["obstacles"]

        self.world = None
        self.avoid.setup(obst["enabled"], obst["seed"], path_xy, corridor, self.router.world)
        self.router.set_terrain(corridor)          # after carving, so collider == served mesh
        if req.get("object"):                      # chosen before the run; fixed for the mission
            self.router.set_model(self._model_for(req["object"]))
        self.frame, self.path_xy = frame, path_xy
        self.grids = {"corridor": corridor, "context": context}
        self.terrain_bytes = {k: g.h.astype("<f4", copy=False).tobytes() for k, g in self.grids.items()}
        self.nav.load_path(path_xy)
        self.warp = 1.0
        self._spawn()                               # spawn + avoid.reset()
        # The path-traverse demo is a one-object world: the map shows it, and it can be sent elsewhere.
        kind = req.get("object") or ("astronaut" if self.router.active.name == "astronaut" else "rover")
        self.world = World(corridor, frame, obstacles=self._obstacle_discs(), seed=obst["seed"])
        ahead = path_xy[min(3, len(path_xy) - 1)] - path_xy[0]
        agent = self.world.add(kind, path_xy[0, 0], path_xy[0, 1], math.atan2(ahead[1], ahead[0]),
                               name=self.rover_cfg["name"] if kind == "rover" else "Astronaut_1",
                               cruise=self.nav.cruise, mode="route")
        agent.path = path_xy
        self._nav_version = agent.path_version

        seg = np.hypot(*np.diff(path_xy, axis=0).T)
        length = route["length_m"] if route["length_m"] is not None else float(seg.sum())
        alternatives = []
        for a in req["alternatives"]:
            ax, ay = frame.to_local(a["coordinates"][:, 0], a["coordinates"][:, 1])
            alternatives.append({"id": a["id"], "color": a["color"], "label": a["label"],
                                 "xy": _xy_list(np.column_stack([ax, ay]))})

        obstacles = {"enabled": False, "seed": obst["seed"], "count": 0, "rocks": [], "craters": [],
                     "lidar": self._lidar_desc(), "planner": self._planner_name()}
        obstacles.update(self.avoid.describe(frame) or {})
        obstacles["enabled"] = bool(obstacles.get("enabled", False))
        obstacles.setdefault("seed", obst["seed"])
        if not obstacles.get("count"):
            obstacles["count"] = len(obstacles.get("rocks", [])) + len(obstacles.get("craters", []))

        self.mission = {
            "id": secrets.token_hex(5),
            "frame": frame.describe(),
            "route": {"id": route["id"], "label": route["label"], "color": route["color"],
                      "length_m": float(length), "xy": _xy_list(path_xy)},
            "alternatives": alternatives,
            "start": req["start"], "end": req["end"],
            "terrain": {k: g.describe() for k, g in self.grids.items()},
            "obstacles": obstacles,
            "physics": self.router.describe(),     # active model, collider self-test, degradation
            "world": self.world.describe(),
        }
        col = self.router.collider
        log.info("mission %s: route %s %.0f m, corridor %dx%d, obstacles %s, model %s%s",
                 self.mission["id"], route["id"], length, corridor.nx, corridor.ny,
                 obstacles["enabled"], self.router.active.name,
                 f", collider {col.tiles} tiles x {col.tile_posts} posts "
                 f"{'verified' if col.ok else 'FAILED'} ({col.probes} probes)" if col else "")
        self._spawn_background(self._warm_textures(self.mission["id"]))
        self._spawn_background(self.broadcast({"type": "mission_loaded",
                                               "mission": self.mission["id"]}, "events"))
        return self.mission

    def _model_for(self, kind: str) -> str:
        """Router model that carries an object type: the astronaut walker, or the rover."""
        avail = self.router.available
        if kind == "astronaut" and "astronaut" in avail:
            return "astronaut"
        return "wheeled" if "wheeled" in avail else "kinematic"

    def _obstacle_discs(self) -> list[tuple[float, float, float]]:
        return ([(r.x, r.y, r.radius) for r in getattr(self.avoid, "rocks", [])]
                + [(c.x, c.y, c.radius) for c in getattr(self.avoid, "craters", [])])

    # ------------------------------------------------------------------------------------ world demo
    def parse_world(self, req) -> dict:
        """Validate a world-demo request: ``{agents: [{kind, name?}], obstacles: {enabled, seed}}``."""
        if not isinstance(req, dict):
            raise ValueError("world request must be a JSON object")
        agents = req.get("agents")
        if not isinstance(agents, list) or not 1 <= len(agents) <= WORLD_MAX_AGENTS:
            raise ValueError(f"agents must be a list of 1..{WORLD_MAX_AGENTS} objects")
        out = []
        for i, a in enumerate(agents):
            kind = a.get("kind") if isinstance(a, dict) else None
            if kind not in KINDS:
                raise ValueError(f"agents[{i}].kind must be one of {list(KINDS)}")
            name = a.get("name")
            out.append({"kind": kind, "name": str(name)[:40] if name else None})
        obst = req.get("obstacles") or {}
        if not isinstance(obst, dict):
            raise ValueError("obstacles must be an object")
        try:
            seed = int(obst["seed"]) if obst.get("seed") is not None else secrets.randbelow(2 ** 31)
        except (TypeError, ValueError):
            raise ValueError("obstacles.seed must be an integer") from None
        return {"agents": out, "obstacles": {"enabled": bool(obst.get("enabled", False)), "seed": seed}}

    async def build_world(self, raw_req) -> dict:
        """World demo: the whole AOI as the drive grid, sparse map-wide obstacles, many objects."""
        req = self.parse_world(raw_req)
        async with self._build_lock:
            src = self._terrain_source()
            w, e, s, n = src.aoi_lonlat()
            inset = 1e-4
            coords = [[w + inset, s + inset], [e - inset, n - inset]]
            cfg = copy.deepcopy(self.physics_cfg)
            cfg["terrain"]["max_grid_points"] = min(int(cfg["terrain"]["max_grid_points"]), WORLD_GRID_POINTS)
            cfg["terrain"]["corridor_margin_m"] = 0.0
            loop = asyncio.get_running_loop()
            frame, corridor, context = await loop.run_in_executor(
                None, build_mission_terrain, src, coords, cfg)
            return self._install_world(req, frame, corridor, context)

    def _random_obstacles(self, corridor, seed: int):
        """World demo obstacles: boulders and craters scattered uniformly at random over the AOI
        (not along any path), sparse, and never stacked on one another."""
        from sim.obstacle_avoid import craters as craters_mod, obstacles as obstacles_mod
        cfg = self.avoid.cfg
        oc, cc = cfg["obstacles"], cfg["craters"]
        rng = np.random.default_rng(seed)
        x0, x1, y0, y1 = corridor.extent()
        area_km2 = (x1 - x0) * (y1 - y0) / 1e6
        m = 100.0
        craters = []
        for k in range(int(area_km2 / WORLD_KM2_PER_CRATER)):
            r = float(rng.uniform(*cc["radius_m"]))
            x, y = float(rng.uniform(x0 + m, x1 - m)), float(rng.uniform(y0 + m, y1 - m))
            if any(math.hypot(x - c.x, y - c.y) < 4 * (r + c.radius) for c in craters):
                continue
            craters.append(craters_mod.Crater(id=f"c{k}", x=x, y=y, radius=r,
                                              depth=r * float(rng.uniform(*cc["depth_ratio"])),
                                              rim=r * float(rng.uniform(*cc["rim_ratio"])), s_route=0.0))
        rocks = []
        for k in range(int(area_km2 / WORLD_KM2_PER_ROCK)):
            r = float(rng.uniform(*oc["radius_m"]))
            h = r * float(rng.uniform(*oc["height_ratio"]))
            v, f = obstacles_mod.make_rock_shape(r, h, rng)
            x, y = float(rng.uniform(x0 + m, x1 - m)), float(rng.uniform(y0 + m, y1 - m))
            if any(math.hypot(x - c.x, y - c.y) < 3 * c.radius + 5 for c in craters) \
                    or any(math.hypot(x - o.x, y - o.y) < 40 for o in rocks):
                continue
            rocks.append(obstacles_mod.Rock(
                id=f"r{len(rocks)}", x=x, y=y, z=float(corridor.height_at(x, y)) - oc["bury_fraction"] * h,
                yaw=float(rng.uniform(0, 2 * math.pi)), radius=float(np.hypot(v[:, 0], v[:, 1]).max()),
                height=h, s=0.0, v_local=v, f=f))
        return rocks, craters

    def _install_world(self, req: dict, frame, corridor, context) -> dict:
        x0, x1, y0, y1 = corridor.extent()
        track = np.array([[x0, y0], [x1, y1]], dtype=np.float64)   # M5 needs a path; unused here
        obst = req["obstacles"]

        self.world = None
        if obst["enabled"] and isinstance(getattr(self.avoid, "cfg", None), dict):
            rocks, craters = self._random_obstacles(corridor, obst["seed"])
        else:
            rocks, craters = [], []      # no obstacles, but the lidar still scans the terrain
        self.avoid.setup(ObstacleAvoidance is not None, obst["seed"], track, corridor,
                         self.router.world, rocks=rocks, craters=craters)
        self.router.set_terrain(corridor)
        self.frame, self.path_xy = frame, None
        self.grids = {"corridor": corridor, "context": context}
        self.terrain_bytes = {k: g.h.astype("<f4", copy=False).tobytes() for k, g in self.grids.items()}
        self.warp = 1.0

        world = World(corridor, frame, obstacles=self._obstacle_discs(), seed=obst["seed"])
        cx, cy = frame.to_local(DEMO_START["lon"], DEMO_START["lat"])
        for spec in req["agents"]:
            world.spawn_random(spec["kind"], (float(cx), float(cy)), name=spec["name"])
        self.world = world
        self._bind_selected(world.selected)
        self._spawn_world_selected()

        obstacles = {"enabled": False, "seed": obst["seed"], "count": 0, "rocks": [], "craters": [],
                     "lidar": self._lidar_desc(), "planner": self._planner_name()}
        obstacles.update(self.avoid.describe(frame) or {})
        obstacles["enabled"] = bool(obstacles.get("enabled", False))
        land = {"lon": DEMO_START["lon"], "lat": DEMO_START["lat"]}
        self.mission = {
            "id": secrets.token_hex(5),
            "kind": "world",
            "frame": frame.describe(),
            "route": {"id": "world", "label": "World demo", "color": "#94a3b8", "length_m": 0.0, "xy": []},
            "alternatives": [], "start": land, "end": land,
            "terrain": {k: g.describe() for k, g in self.grids.items()},
            "obstacles": obstacles,
            "physics": self.router.describe(),
            "world": world.describe(),
        }
        log.info("world %s: %d objects, grid %dx%d, %d rocks, %d craters", self.mission["id"],
                 len(world.agents), corridor.nx, corridor.ny, len(obstacles["rocks"]),
                 len(obstacles["craters"]))
        self._spawn_background(self._warm_textures(self.mission["id"]))
        self._spawn_background(self.broadcast({"type": "mission_loaded",
                                               "mission": self.mission["id"]}, "events"))
        return self.mission

    def _bind_selected(self, agent_id: str) -> None:
        """Put the physics router on ``agent_id`` (its model and pose); the old object goes kinematic."""
        w = self.world
        a = w.agents[agent_id]
        old = w.sel
        if old is not None and old.id != agent_id and old.mode == "manual":
            w.set_mode(old, "idle")
        w.selected = agent_id
        self.router.set_model(self._model_for(a.kind))
        self.router.spawn(a.x, a.y, a.yaw)
        self.nav.set_cruise(a.cruise)               # its own speed (object speed control)
        self._nav_version = -1                      # reload nav from the new object's path
        self.avoid.reset()

    def _spawn_world_selected(self) -> None:
        a = self.world.sel
        if a is not None and a.path is not None:
            self.nav.load_path(a.path)
            self._nav_version = a.path_version

    def _world_mode(self) -> bool:
        return bool(self.mission and self.mission.get("kind") == "world")

    async def _plan_agent(self, agent_id: str, version: int) -> None:
        """Refine a goto with the M2 hazard-aware planner (slope, roughness, hazard); keep the
        straight line if the planner is unavailable."""
        w, mission = self.world, self.mission_id
        a = w.agents.get(agent_id) if w else None
        if a is None or a.goal is None:
            return
        slon, slat = self.frame.to_lonlat(a.x, a.y)
        elon, elat = self.frame.to_lonlat(*a.goal)
        body = {"start": {"lon": float(slon), "lat": float(slat)}, "end": {"lon": float(elon), "lat": float(elat)}}
        try:
            async with ClientSession(timeout=ClientTimeout(total=90)) as http:
                async with http.post(f"{self.planner_url}/api/route", json=body) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json(content_type=None)
        except Exception as exc:                    # planner offline: straight line stays
            log.info("goto %s: planner unavailable (%s); driving straight", agent_id, exc)
            return
        routes = data.get("routes") or []
        want = a.route_pref or data.get("recommended")      # "cautious" for a location goal
        chosen = next((r for r in routes if r.get("id") == want), None) \
            or next((r for r in routes if r.get("id") == data.get("recommended")), routes[0] if routes else None)
        if (chosen is None or self.mission_id != mission or self.world is not w
                or a.path_version != version or a.mode != "goto"):
            return
        pts = np.asarray(chosen.get("coordinates") or [], dtype=np.float64)
        if pts.ndim != 2 or len(pts) < 2:
            return
        x, y = self.frame.to_local(pts[:, 0], pts[:, 1])
        xy = np.column_stack([x, y])
        # The planner snaps its route ends to its analysis grid (cells of several metres), so the route
        # can stop short of the chosen point: finish on the exact goal.
        if np.hypot(*(xy[-1] - a.goal)) > 0.5:
            xy = np.vstack([xy, a.goal])
        a.set_path(xy)

    def _agent_arg(self, cmd: dict):
        w = self.world
        aid = cmd.get("agent") or (w.selected if w else None)
        if w is None or aid not in w.agents:
            raise KeyError("agent")
        return w.agents[aid]

    def _end(self) -> None:
        """End the simulation completely: no mission, no objects (the map icons disappear)."""
        self.nav.pause()
        self.mission = None
        self.world = None
        self._spawn_background(self.broadcast({"type": "mission_ended"}, "events"))

    def _spawn(self) -> None:
        """Respawn at the route start facing path point 3, reload the path, reset avoidance."""
        if self.world is not None and self._world_mode():
            self.world.reset()
            a = self.world.sel
            self.router.spawn(a.x, a.y, a.yaw)
            self._spawn_world_selected()
            self.avoid.reset()
            return
        xy = self.path_xy
        ahead = xy[min(3, len(xy) - 1)] - xy[0]
        yaw = math.atan2(ahead[1], ahead[0])
        self.router.spawn(float(xy[0, 0]), float(xy[0, 1]), yaw)
        self.nav.load_path(xy)
        self.avoid.reset()
        if self.world is not None and self.world.sel is not None:   # back onto the route
            self.world.reset()
            self.world.sel.path = xy
            self._nav_version = self.world.sel.path_version

    def _texture_edges(self, level: str) -> tuple[float, float, float, float]:
        g = self.grids[level]
        return (g.lon_w, g.lon_e, g.lat_s, g.lat_n)

    async def _warm_textures(self, mission_id: str) -> None:
        loop = asyncio.get_running_loop()
        for level in ("context", "corridor"):
            if self.mission_id != mission_id:
                return
            try:
                await loop.run_in_executor(None, self.textures.ensure, mission_id, level,
                                           self._texture_edges(level))
            except TextureUnavailable as exc:
                log.warning("texture warm-up: %s", exc)
            except Exception:
                log.exception("texture warm-up failed for %s", level)

    def _spawn_background(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def demo_mission(self) -> dict:
        """C4 /api/route from the landing site to Cheyava Falls; drive the recommended route."""
        body = {"start": DEMO_START, "end": DEMO_END}
        try:
            async with ClientSession(timeout=ClientTimeout(total=120)) as http:
                async with http.post(f"{self.planner_url}/api/route", json=body) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status != 200:
                        raise PlannerUnavailable(f"planner returned {resp.status}: "
                                                 f"{data.get('detail') if isinstance(data, dict) else data}")
        except PlannerUnavailable:
            raise
        except Exception as exc:
            raise PlannerUnavailable(f"route planner unreachable at {self.planner_url}: {exc}") from exc
        routes = data.get("routes") or []
        rec = data.get("recommended")
        chosen = next((r for r in routes if r.get("id") == rec), routes[0] if routes else None)
        if chosen is None:
            raise PlannerUnavailable("planner returned no routes")
        return await self.build_mission({
            "route": chosen, "alternatives": [r for r in routes if r is not chosen],
            "start": DEMO_START, "end": DEMO_END, "obstacles": {"enabled": False},
        })

    # ------------------------------------------------------------------------------------ commands
    def command(self, cmd) -> dict:
        """C6 client commands. Returns `{ok, state, warp, model}` or `{ok: false, error}`."""
        if not isinstance(cmd, dict) or "type" not in cmd:
            return {"ok": False, "error": "command must be an object with a type"}
        kind = cmd["type"]
        w = self.world
        try:
            if kind in ("start", "pause", "reset", "select", "drive", "goto", "intercept", "end") \
                    and self.mission is None:
                return {"ok": False, "error": "no mission loaded"}
            if kind == "start":
                self.nav.start()
                if w is not None:
                    w.running = True
            elif kind == "pause":
                self.nav.pause()
                if w is not None:
                    w.running = False
            elif kind == "reset":
                self._spawn()
            elif kind == "end":
                self._end()
                return {"ok": True, "state": "ended", "warp": self.warp, "model": self.router.active.name}
            elif kind == "select":
                a = self._agent_arg(cmd)
                if a.id != w.selected:
                    self._bind_selected(a.id)
                if cmd.get("manual") and self._world_mode():
                    w.set_mode(a, "manual")
                    self._drive = (0.0, 0.0, -math.inf)
            elif kind == "drive":
                if not self._world_mode():         # manual control is a world-demo feature
                    return {"ok": False, "error": "manual control is only available in the world demo"}
                a = w.sel if w else None
                if a is None:
                    return {"ok": False, "error": "no selected object"}
                v, om = float(cmd.get("v", 0.0)), float(cmd.get("w", 0.0))
                if not (math.isfinite(v) and math.isfinite(om)):
                    return {"ok": False, "error": "drive needs finite v and w"}
                if a.mode != "manual":
                    w.set_mode(a, "manual")
                vmax = 2.0 if a.kind == "rover" else 1.2
                wmax = 0.6 if a.kind == "rover" else 1.5
                self._drive = (max(-1.0, min(1.0, v)) * vmax, max(-1.0, min(1.0, om)) * wmax,
                               time.monotonic())
            elif kind in ("goto", "intercept"):
                a = self._agent_arg(cmd)
                if kind == "goto":
                    lon, lat = float(cmd["lon"]), float(cmd["lat"])
                    if not (math.isfinite(lon) and math.isfinite(lat)):
                        raise ValueError
                    x, y = self.frame.to_local(lon, lat)
                    route = cmd.get("route")
                    w.assign_goto(a.id, float(x), float(y), route=str(route) if route else None)
                    self._spawn_background(self._plan_agent(a.id, a.path_version))
                else:
                    w.assign_intercept(a.id, str(cmd["target"]))
                w.running = True
                if self.nav.state == "paused":
                    self.nav.start()
            elif kind == "set_speed":
                v = float(cmd["value"])
                if not math.isfinite(v) or v < 0:
                    return {"ok": False, "error": "speed must be a non-negative number (m/s)"}
                # Optional "agent": set that object's speed (the map popup); default: the selected one.
                target = w.agents.get(cmd.get("agent") or w.selected) if w is not None else None
                if cmd.get("agent") and target is None:
                    return {"ok": False, "error": "unknown agent"}
                if target is None or target.id == w.selected:
                    self.nav.set_cruise(v)
                if target is not None:
                    target.cruise = v                 # kept when the object is deselected
            elif kind == "set_energy":
                # Battery / oxygen percentage of one object (testing the low-oxygen alert).
                a = self._agent_arg(cmd)
                e = float(cmd["value"])
                if not math.isfinite(e):
                    raise ValueError
                a.energy = min(100.0, max(0.0, e))
            elif kind == "set_warp":
                wv = float(cmd["value"])
                if not math.isfinite(wv):
                    return {"ok": False, "error": "warp must be a number"}
                self.warp = min(max(wv, WARP_RANGE[0]), WARP_RANGE[1])
            elif kind == "set_model":
                # The object is chosen before the run: no switching mid-simulation.
                if self._world_mode() or self.nav.state in ("driving", "paused"):
                    return {"ok": False, "error": "the object cannot be changed during a simulation"}
                if not self.router.set_model(str(cmd["value"])):
                    return {"ok": False, "error": f"unknown or unavailable model {cmd['value']!r}"}
            else:
                return {"ok": False, "error": f"unknown command type {kind!r}"}
        except (KeyError, TypeError, ValueError) as exc:
            if kind in ("select", "goto", "intercept"):
                return {"ok": False, "error": f"{kind}: {exc}" if isinstance(exc, ValueError) and str(exc)
                        else f"{kind} needs a valid agent / target / lon, lat"}
            return {"ok": False, "error": f"{kind} needs a valid 'value'"}
        return {"ok": True, "state": self.nav.state, "warp": self.warp,
                "model": self.router.active.name, "selected": w.selected if w else None}

    # ------------------------------------------------------------------------------------ streaming
    def _subscribers(self, topic: str) -> list[web.WebSocketResponse]:
        return [ws for ws, topics in self.clients.items() if topic in topics and not ws.closed]

    async def broadcast(self, msg: dict, topic: str) -> None:
        text = dumps(msg)
        for ws in self._subscribers(topic):
            try:
                await ws.send_str(text)
            except (ConnectionError, RuntimeError):
                self.clients.pop(ws, None)

    async def broadcast_bytes(self, data: bytes, topic: str) -> None:
        for ws in self._subscribers(topic):
            try:
                await ws.send_bytes(data)
            except (ConnectionError, RuntimeError):
                self.clients.pop(ws, None)

    def telemetry(self, pose) -> dict:
        roll, pitch, yaw = euler_from_quat(pose.q)
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, math.cos(roll) * math.cos(pitch)))))
        lon, lat = self.frame.to_lonlat(pose.x, pose.y)
        nav = self.nav.status()
        return {
            "type": "telemetry", "sim_time": _r(self.router.sim_time, 4), "wall": _r(time.time(), 3),
            "mission": self.mission_id,
            "pose": {"x": _r(pose.x), "y": _r(pose.y), "z": _r(pose.z),
                     "q": [_r(c, 6) for c in pose.q]},
            "lon": _r(lon, 8), "lat": _r(lat, 8), "elev_m": _r(pose.z + self.frame.z0, 2),
            "speed_m_s": _r(math.hypot(pose.vx, pose.vy)),
            "yaw_deg": _r(math.degrees(yaw), 2), "pitch_deg": _r(math.degrees(pitch), 2),
            "roll_deg": _r(math.degrees(roll), 2), "tilt_deg": _r(tilt, 2),
            "wheels": [{"steer": _r(w["steer"], 4), "spin": _r(w["spin"], 4)}
                       for w in self.router.wheels()],
            "warp": self.warp, "model": self.router.active.name,
            "nav": {k: (_r(v, 4) if isinstance(v, float) else v) for k, v in nav.items()},
            "avoid": None,
            "agent": self.world.selected if self.world is not None else None,
        }

    async def tick(self, elapsed: float) -> None:
        """One loop iteration in the C8 call order.

        All stepping happens before the first ``await``: an await yields to the event loop, where a
        mission install may run, and the step must never mix the old mission's pose with the new
        mission's path, terrain or obstacles. Messages are queued and sent at the end, in order.
        """
        router, nav, avoid, world = self.router, self.nav, self.avoid, self.world
        outbox: list[tuple[bool, object, str]] = []          # (binary, payload, topic)
        sel = world.sel if world is not None else None
        if sel is not None and sel.mode != "route" and sel.path_version != self._nav_version:
            # The selected object got a new path (new mission, re-plan, wander goal).
            self._nav_version = sel.path_version
            if sel.mode in AUTO_MODES and sel.path is not None:
                nav.load_path(sel.path)
                if world.running:
                    nav.start()
                if getattr(getattr(avoid, "brain", None), "mode", "track") != "track":
                    avoid.reset()                  # a detour planned around the old path is stale
        pose = router.pose()
        ev = avoid.tick(pose, nav, router.sim_time)
        if ev is not None:
            msg = dict(ev)
            msg.setdefault("type", "avoid_event")
            outbox.append((False, msg, "events"))
        nav.observe(pose)
        if sel is not None and sel.mode == "manual":
            v, om, at = self._drive
            cmd = (v, om) if time.monotonic() - at <= DRIVE_TIMEOUT_S else (0.0, 0.0)
        elif sel is not None and sel.mode in ("stopped", "idle"):
            cmd = (0.0, 0.0)
        else:
            cmd = avoid.command(nav.command(), pose, nav)
        router.set_inputs(*cmd)
        n = router.update(min(elapsed, 0.25), self.warp)
        for ev in router.pop_events():             # collider self-test failure, fall-through recovery
            outbox.append((False, ev, "events"))
        if world is not None:
            p = router.pose()
            world.sync_selected(p.x, p.y, p.z, euler_from_quat(p.q)[2], math.hypot(p.vx, p.vy))
            if sel is not None and sel.mode in AUTO_MODES and nav.state == "arrived":
                world.on_arrived(sel)
            world.step(n * router.timestep_s)
            for ev in world.pop_events():          # objects met
                outbox.append((False, ev, "events"))
            now = time.monotonic()
            if now - self._last_world_post >= 1.0 / WORLD_POST_HZ:
                self._last_world_post = now
                outbox.append((False, {"type": "world", "mission": self.mission_id, "sim_time":
                                       _r(router.sim_time, 2), **world.describe()}, "telemetry"))
        if avoid.enabled and self._subscribers("lidar") and avoid.stream_due():
            outbox.append((True, avoid.encode_frame(pose, nav), "lidar"))

        pose = router.pose()
        t = self.telemetry(pose)
        t["avoid"] = avoid.status(nav)
        outbox.append((False, t, "telemetry"))

        max_tilt = float(self.rover_cfg["limits"]["max_tilt_deg"])
        now = time.monotonic()
        if t["tilt_deg"] > max_tilt and now - self._last_tilt_event >= TILT_EVENT_PERIOD_S:
            self._last_tilt_event = now
            outbox.append((False, {"type": "physics_event", "event": "tilt_limit",
                                   "detail": f"tilt {t['tilt_deg']:.1f} deg exceeds {max_tilt:.0f} deg"},
                           "events"))

        for binary, payload, topic in outbox:
            if binary:
                await self.broadcast_bytes(payload, topic)
            else:
                await self.broadcast(payload, topic)

    async def run_loop(self) -> None:
        period = 1.0 / self.loop_hz
        last = time.perf_counter()
        while True:
            await asyncio.sleep(period)
            now = time.perf_counter()
            elapsed, last = now - last, now
            if self.mission is None or not self.clients:
                continue                       # nobody watching: no stepping (principle 8)
            try:
                await self.tick(elapsed)
            except Exception:
                log.exception("sim loop tick failed")

    # ------------------------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._loop_task = asyncio.ensure_future(self.run_loop())

    async def close_clients(self) -> None:
        for ws in list(self.clients):
            await ws.close(code=1001, message=b"server shutdown")
        self.clients.clear()

    async def stop(self) -> None:
        """Stop the loop and background tasks, then always release the physics client."""
        try:
            if self._loop_task is not None:
                self._loop_task.cancel()
                try:
                    await self._loop_task
                except asyncio.CancelledError:
                    pass
                except Exception:          # a failed loop must not keep PyBullet open
                    log.exception("sim loop ended with an error")
            for task in list(self._background):
                task.cancel()
        finally:
            self.router.close()


# ---------------------------------------------------------------------------------------- HTTP
SIM_KEY = web.AppKey("sim", SimServer)


def _error(status: int, detail: str) -> web.Response:
    return web.json_response({"detail": detail}, status=status)


async def handle_redirect(request: web.Request) -> web.Response:
    qs = f"?{request.query_string}" if request.query_string else ""
    raise web.HTTPFound(f"{request.path}/{qs}")


async def handle_index(request: web.Request) -> web.StreamResponse:
    index = SIM_DIR / "web" / "index.html"
    if not index.exists():
        return web.Response(status=404, text="sim/web/index.html has not been built yet")
    return web.FileResponse(index, headers={"Cache-Control": "no-cache",
                                            "Content-Type": "text/html; charset=utf-8"})


async def handle_config(request: web.Request) -> web.Response:
    return web.json_response(request.app[SIM_KEY].config(), dumps=dumps)


async def _mission_response(coro) -> web.Response:
    try:
        desc = await coro
    except ValueError as exc:
        return _error(422, str(exc))
    except TerrainMissing as exc:
        return _error(503, str(exc))
    except PlannerUnavailable as exc:
        return _error(503, str(exc))
    return web.json_response(desc, dumps=dumps)


async def handle_mission_post(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error(422, "request body must be JSON")
    return await _mission_response(request.app[SIM_KEY].build_mission(body))


async def handle_world_post(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error(422, "request body must be JSON")
    return await _mission_response(request.app[SIM_KEY].build_world(body))


async def handle_mission_get(request: web.Request) -> web.Response:
    sim = request.app[SIM_KEY]
    async with sim._demo_lock:  # concurrent first GETs build the demo mission once
        if sim.mission is not None:
            return web.json_response(sim.mission, dumps=dumps)
        return await _mission_response(sim.demo_mission())


async def handle_command(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"ok": False, "error": "request body must be JSON"}, status=400)
    return web.json_response(request.app[SIM_KEY].command(body), dumps=dumps)


def _stale(request: web.Request, sim: "SimServer") -> web.Response | None:
    """409 when the page asks (``?m=``) for a mission that is no longer loaded.

    Without this, a mission installed between the page's ``GET api/mission`` and its terrain or
    texture fetch would hand it another mission's data: a byte-count error ("world not loaded"),
    or, for a same-size corridor, one mission's ground drawn under the other's rover.
    """
    wanted = request.query.get("m")
    if wanted and wanted != sim.mission_id:
        return web.json_response({"ok": False, "error": "stale mission", "requested": wanted,
                                  "mission": sim.mission_id}, status=409)
    return None


async def handle_terrain(request: web.Request) -> web.Response:
    sim = request.app[SIM_KEY]
    level = request.match_info["level"]
    stale = _stale(request, sim)
    if stale is not None:
        return stale
    data = sim.terrain_bytes.get(level)
    if data is None:
        return _error(404, f"no {level} terrain (load a mission first)")
    return web.Response(body=data, content_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})


async def handle_texture(request: web.Request) -> web.StreamResponse:
    sim = request.app[SIM_KEY]
    level = request.match_info["level"]
    stale = _stale(request, sim)
    if stale is not None:
        return stale
    if sim.mission is None or level not in sim.grids:
        return _error(404, f"no {level} texture (load a mission first)")
    loop = asyncio.get_running_loop()
    try:
        path = await loop.run_in_executor(None, sim.textures.ensure, sim.mission_id, level,
                                          sim._texture_edges(level))
    except TextureUnavailable as exc:
        return _error(503, str(exc))
    return web.FileResponse(path, headers={"Content-Type": "image/jpeg",
                                           "Cache-Control": "private, max-age=3600"})


def _static_handler(root: Path):
    async def handler(request: web.Request) -> web.StreamResponse:
        tail = request.match_info["tail"]
        try:
            path = (root / tail).resolve()
            path.relative_to(root.resolve())
        except (ValueError, OSError):
            raise web.HTTPNotFound()
        if not path.is_file():
            raise web.HTTPNotFound()
        ctype = MIME.get(path.suffix.lower(), "application/octet-stream")
        # Always revalidate (cheap 304 via ETag): without it browsers cache ES modules
        # heuristically and keep running an old main.js against an updated server.
        return web.FileResponse(path, headers={"Content-Type": ctype, "Cache-Control": "no-cache"})
    return handler


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    sim = request.app[SIM_KEY]
    ws = web.WebSocketResponse(heartbeat=15.0)
    await ws.prepare(request)
    sim.clients[ws] = set(DEFAULT_TOPICS)
    try:
        await ws.send_str(dumps({"type": "hello", "protocol": PROTOCOL, "config": sim.config()}))
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                await ws.send_str(dumps({"type": "ack", "ok": False, "error": "invalid JSON"}))
                continue
            if isinstance(data, dict) and data.get("type") == "subscribe":
                topics = data.get("topics")
                if not isinstance(topics, list):
                    await ws.send_str(dumps({"type": "ack", "ok": False,
                                             "error": "topics must be a list"}))
                    continue
                chosen = {t for t in topics if t in KNOWN_TOPICS}
                sim.clients[ws] = chosen
                await ws.send_str(dumps({"type": "ack", "ok": True, "topics": sorted(chosen)}))
            else:
                await ws.send_str(dumps({"type": "ack", **sim.command(data)}))
    finally:
        sim.clients.pop(ws, None)
    return ws


def _cors_origins() -> set[str]:
    """Origins allowed to call /sim from another site (a static frontend on GitHub Pages).

    MO_CORS_ORIGINS is a comma-separated list, or "*" for any origin. Unset means none: local runs
    serve the planner and the sim from one origin and need no CORS.
    """
    return {o.strip().rstrip("/") for o in os.environ.get("MO_CORS_ORIGINS", "").split(",") if o.strip()}


CORS_KEY = web.AppKey("cors_origins", set)


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Answer CORS preflights and tag responses for the allowed origins (no cookies are used)."""
    allowed = request.app[CORS_KEY]
    origin = request.headers.get("Origin", "")
    ok = bool(origin) and ("*" in allowed or origin in allowed)
    if ok and request.method == "OPTIONS" and "Access-Control-Request-Method" in request.headers:
        resp: web.StreamResponse = web.Response(status=204)
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = request.headers.get("Access-Control-Request-Headers", "Content-Type")
        resp.headers["Access-Control-Max-Age"] = "600"
    else:
        resp = await handler(request)
    if ok and not resp.prepared:   # a prepared (WebSocket/streamed) response has sent its headers
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    return resp


def make_app(sim: SimServer | None = None) -> web.Application:
    """Build the aiohttp application with every C5 route under /sim."""
    app = web.Application(client_max_size=32 * 1024 * 1024, middlewares=[cors_middleware])
    app[CORS_KEY] = _cors_origins()
    app[SIM_KEY] = sim or SimServer()

    r = app.router
    r.add_get("/sim", handle_redirect)
    r.add_get("/sim/", handle_index)
    r.add_get("/sim/config", handle_config)
    r.add_post("/sim/api/mission", handle_mission_post)
    r.add_get("/sim/api/mission", handle_mission_get)
    r.add_post("/sim/api/world", handle_world_post)
    r.add_post("/sim/api/command", handle_command)
    r.add_get(r"/sim/api/terrain/{level:corridor|context}.bin", handle_terrain)
    r.add_get(r"/sim/api/texture/{level:corridor|context}.jpg", handle_texture)
    r.add_get("/sim/ws", handle_ws)
    for prefix, root in STATIC_ROOTS.items():
        r.add_get(f"/sim/{prefix}/{{tail:.+}}", _static_handler(root))

    async def on_startup(app: web.Application) -> None:
        await app[SIM_KEY].start()

    async def on_shutdown(app: web.Application) -> None:
        await app[SIM_KEY].close_clients()     # close sockets before cleanup (principle 8)

    async def on_cleanup(app: web.Application) -> None:
        await app[SIM_KEY].stop()
        log.info("sim loop stopped, physics closed")

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app


def _log_environment() -> None:
    """Log the physics binary and whether the environment is exactly pixi.lock (never fatal)."""
    try:
        from sim.tools.check_env import check

        rep = check()
        pyb = rep.version_of("pybullet") or "not installed"
        if rep.ok:
            log.info("environment matches pixi.lock (%d packages); pybullet %s", len(rep.installed), pyb)
        else:
            log.warning("pybullet %s; %s", pyb, rep.summary().replace("\n", " | "))
    except Exception as exc:  # diagnostics only
        log.warning("could not check the environment against pixi.lock: %s", exc)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="MARS ODYSSEY 3D sim server (module M4)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _log_environment()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    if hasattr(signal, "SIGBREAK"):
        # Windows: a launcher stops a child process group with CTRL_BREAK (Ctrl+C cannot be sent to
        # one), which by default kills the process outright. Python runs a signal handler wherever
        # the main thread happens to be, often inside the 50 Hz loop task, so raising there would
        # land in that task and abort cleanup. Instead, schedule aiohttp's GracefulExit as a loop
        # callback (what aiohttp itself does on POSIX): run_app then closes the WebSockets, stops
        # the loop task and disconnects PyBullet.
        def _raise_graceful_exit() -> None:
            raise web.GracefulExit()

        def _on_break(signum, frame) -> None:
            loop.call_soon_threadsafe(_raise_graceful_exit)

        signal.signal(signal.SIGBREAK, _on_break)
    web.run_app(make_app(), host=args.host, port=args.port, access_log=None, loop=loop)
    log.info("sim server stopped cleanly")


if __name__ == "__main__":
    main()
