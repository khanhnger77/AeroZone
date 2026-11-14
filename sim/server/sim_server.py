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
import json
import logging
import math
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
        return {"route": route, "alternatives": alternatives,
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

        self.avoid.setup(obst["enabled"], obst["seed"], path_xy, corridor, self.router.world)
        self.router.set_terrain(corridor)          # after carving, so collider == served mesh
        self.frame, self.path_xy = frame, path_xy
        self.grids = {"corridor": corridor, "context": context}
        self.terrain_bytes = {k: g.h.astype("<f4", copy=False).tobytes() for k, g in self.grids.items()}
        self.nav.load_path(path_xy)
        self.warp = 1.0
        self._spawn()                               # spawn + avoid.reset()

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
        }
        log.info("mission %s: route %s %.0f m, corridor %dx%d, obstacles %s",
                 self.mission["id"], route["id"], length, corridor.nx, corridor.ny,
                 obstacles["enabled"])
        self._spawn_background(self._warm_textures(self.mission["id"]))
        self._spawn_background(self.broadcast({"type": "mission_loaded",
                                               "mission": self.mission["id"]}, "events"))
        return self.mission

    def _spawn(self) -> None:
        """Respawn at the route start facing path point 3, reload the path, reset avoidance."""
        xy = self.path_xy
        ahead = xy[min(3, len(xy) - 1)] - xy[0]
        yaw = math.atan2(ahead[1], ahead[0])
        self.router.spawn(float(xy[0, 0]), float(xy[0, 1]), yaw)
        self.nav.load_path(xy)
        self.avoid.reset()

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
        try:
            if kind in ("start", "pause", "reset") and self.mission is None:
                return {"ok": False, "error": "no mission loaded"}
            if kind == "start":
                self.nav.start()
            elif kind == "pause":
                self.nav.pause()
            elif kind == "reset":
                self._spawn()
            elif kind == "set_speed":
                v = float(cmd["value"])
                if not math.isfinite(v) or v < 0:
                    return {"ok": False, "error": "speed must be a non-negative number (m/s)"}
                self.nav.set_cruise(v)
            elif kind == "set_warp":
                w = float(cmd["value"])
                if not math.isfinite(w):
                    return {"ok": False, "error": "warp must be a number"}
                self.warp = min(max(w, WARP_RANGE[0]), WARP_RANGE[1])
            elif kind == "set_model":
                if not self.router.set_model(str(cmd["value"])):
                    return {"ok": False, "error": f"unknown or unavailable model {cmd['value']!r}"}
            else:
                return {"ok": False, "error": f"unknown command type {kind!r}"}
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": f"{kind} needs a valid 'value'"}
        return {"ok": True, "state": self.nav.state, "warp": self.warp,
                "model": self.router.active.name}

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
        }

    async def tick(self, elapsed: float) -> None:
        """One loop iteration in the C8 call order.

        All stepping happens before the first ``await``: an await yields to the event loop, where a
        mission install may run, and the step must never mix the old mission's pose with the new
        mission's path, terrain or obstacles. Messages are queued and sent at the end, in order.
        """
        router, nav, avoid = self.router, self.nav, self.avoid
        outbox: list[tuple[bool, object, str]] = []          # (binary, payload, topic)
        pose = router.pose()
        ev = avoid.tick(pose, nav, router.sim_time)
        if ev is not None:
            msg = dict(ev)
            msg.setdefault("type", "avoid_event")
            outbox.append((False, msg, "events"))
        nav.observe(pose)
        router.set_inputs(*avoid.command(nav.command(), pose, nav))
        router.update(min(elapsed, 0.25), self.warp)
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


def make_app(sim: SimServer | None = None) -> web.Application:
    """Build the aiohttp application with every C5 route under /sim."""
    app = web.Application(client_max_size=32 * 1024 * 1024)
    app[SIM_KEY] = sim or SimServer()

    r = app.router
    r.add_get("/sim", handle_redirect)
    r.add_get("/sim/", handle_index)
    r.add_get("/sim/config", handle_config)
    r.add_post("/sim/api/mission", handle_mission_post)
    r.add_get("/sim/api/mission", handle_mission_get)
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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="MARS ODYSSEY 3D sim server (module M4)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
