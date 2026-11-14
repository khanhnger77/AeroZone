"""Seed campaign: drive a whole route with random boulders and craters for many seeds.

usage: python -m sim.obstacle_avoid.tests.campaign --seeds 1-48 --model wheeled --speed 2 --warp 5 [--jobs 8]

**Real mode** (default when M1 data, M2 and M4 are present, e.g. on the integration branch): the route is
M2's recommended route from the landing site to Cheyava Falls (``plan_routes``, in-process, no server),
the terrain is M4's drive corridor from M1's ``elevation_5m.tif`` (``build_mission_terrain``), and the
rover is M4's ``RoverModelRouter`` (``wheeled`` = the PyBullet 6-wheel rover) with M4's
``PurePursuitNavigator``. Mission build order and the 50 Hz loop are exactly C8's (as ``sim_server``).
Body-to-rock gaps are measured in M4's own PyBullet world (``getClosestPoints``).

**Synthetic mode** (``--synthetic`` or when M1/M2/M4 are missing): rolling terrain, a 1.5 km curved
route and the test stand-ins of ``stubs.py`` — clearly labelled in the output.

A seed PASSES when the rover arrives, never touches a boulder, never puts its footprint inside a crater
rim, never ends ``blocked`` and finishes in ``track`` mode.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

# Repo root holding M1 data and M4 configs. Override with M5_REPO_ROOT to run this branch's M5 against
# another checkout of the integrated repo (e.g. a read-only worktree of the integration branch).
ROOT = Path(os.environ.get("M5_REPO_ROOT", Path(__file__).resolve().parents[3]))
LANDING = (77.45088572, 18.44462715)
CHEYAVA = (77.3052, 18.4975)
BODY_L, BODY_W, BODY_H = 2.9, 2.7, 1.56
FOOT_X = np.array([-0.97, 0.0, 0.97])
FOOT_R = 1.45


def parse_seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _yaml(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text())


def real_available() -> bool:
    try:
        import sim.server.terrain  # noqa: F401  (M4)
        import sim.server.physics  # noqa: F401
        import backend.services.routing  # noqa: F401  (M2)
    except Exception:
        return False
    return (ROOT / "data" / "processed" / "elevation_5m.tif").exists()


_CACHE: dict = {}


def real_mission():
    """(frame, corridor, path_xy, label) for M2's recommended demo route on M4's corridor (cached per process)."""
    if "real" not in _CACHE:
        from backend.services.routing import plan_routes
        from backend.services.terrain import get_terrain
        from sim.server.terrain import TerrainSource, build_mission_terrain
        rr = plan_routes(get_terrain(), LANDING, CHEYAVA, [])
        route = next(r for r in rr["routes"] if r["id"] == rr["recommended"])
        pcfg = _yaml(ROOT / "sim" / "configs" / "physics.yaml")
        frame, corridor, _ = build_mission_terrain(TerrainSource(ROOT / "data" / "processed" / "elevation_5m.tif"),
                                                   route["coordinates"], pcfg)
        c = np.asarray(route["coordinates"], dtype=np.float64)
        x, y = frame.to_local(c[:, 0], c[:, 1])
        _CACHE["real"] = (frame, corridor, np.column_stack([x, y]),
                          f"real: Jezero demo route, M2 '{route['id']}' {route['length_m']:.0f} m, M4 corridor "
                          f"{corridor.h.shape[1]}x{corridor.h.shape[0]} @ {corridor.dx:.2f} m")
    return _CACHE["real"]


def synthetic_mission():
    from sim.obstacle_avoid.tests.stubs import make_grid
    g = make_grid(lambda X, Y: 6.0 * np.sin(X / 140.0) + 4.0 * np.cos(Y / 95.0) + 0.004 * X,
                  -300, 1700, -700, 700, d=5.0)
    s = np.arange(0.0, 1500.0, 2.0)
    xy = np.stack([s, 180.0 * np.sin(s / 260.0)], axis=1)
    return None, g, xy, "synthetic stand-in (rolling terrain, 1.5 km curved route, stub rover)"


def _rim_margin(pose_xy, yaw, craters) -> float:
    if not craters:
        return math.inf
    cx = pose_xy[0] + math.cos(yaw) * FOOT_X
    cy = pose_xy[1] + math.sin(yaw) * FOOT_X
    return min(float((np.hypot(cx - c.x, cy - c.y) - c.radius - FOOT_R).min()) for c in craters)


SEG_BEFORE_M = 25.0      # segment starts this far before a cluster's first obstacle edge (lidar 10 m + braking)
SEG_AFTER_M = 25.0       # ...and ends this far after its last edge (room to rejoin the route)
SEG_MERGE_M = 20.0       # obstacles whose segments would be closer than this are driven together


def obstacle_segments(rocks, craters, length: float) -> list[tuple[float, float]]:
    """Route-arc-length windows [s0, s1] that cover every obstacle with an approach and a rejoin margin."""
    spans = [(r.s - r.radius, r.s + r.radius) for r in rocks] + \
            [(c.s_route - c.radius * 1.5, c.s_route + c.radius * 1.5) for c in craters]
    spans.sort()
    segs: list[list[float]] = []
    for a, b in spans:
        a, b = max(0.0, a - SEG_BEFORE_M), min(length, b + SEG_AFTER_M)
        if segs and a <= segs[-1][1] + SEG_MERGE_M:
            segs[-1][1] = max(segs[-1][1], b)
        else:
            segs.append([a, b])
    return [(float(a), float(b)) for a, b in segs]


def run_real(seed: int, model: str, speed: float, warp: float, segments: bool = False) -> dict:
    """One seed on the real route with M4's router + navigator, in the C8 build order and call order.

    ``segments=True`` (mode A) drives only the stretches around each obstacle group: for every window of
    ``obstacle_segments`` the rover is spawned on the route at the window start, the navigator gets that
    slice of the route, the avoidance map is reset (as on a respawn) and the C8 loop runs until arrival.
    Everything else (terrain, obstacles, rover, planner) is identical to the full drive."""
    import dataclasses
    import pybullet as p
    from sim.backend.navigator import PurePursuitNavigator
    from sim.obstacle_avoid.geometry import yaw_of
    from sim.obstacle_avoid.service import ObstacleAvoidance
    from sim.server.physics import RoverModelRouter

    frame, corridor0, path_xy, label = real_mission()
    corridor = dataclasses.replace(corridor0, h=corridor0.h.copy())     # avoid.setup carves in place
    pcfg = _yaml(ROOT / "sim" / "configs" / "physics.yaml")
    rcfg = _yaml(ROOT / "sim" / "configs" / "rover.yaml")
    router = RoverModelRouter(pcfg, rcfg)
    nav = PurePursuitNavigator(rcfg)
    avoid = ObstacleAvoidance(rcfg)
    # C8 mission build order: terrain -> avoid.setup (carves) -> router.set_terrain -> load_path -> spawn -> reset
    avoid.setup(True, seed, path_xy, corridor, router.world)
    router.set_terrain(corridor)
    if not router.set_model(model):
        raise SystemExit(f"model {model!r} not available: {router.available}")
    nav.set_cruise(speed)
    cid = router.world.client if router.world is not None else None
    ghost = None
    if cid is not None and model != "wheeled":       # kinematic rover is not in PyBullet: probe with a box
        shape = p.createCollisionShape(p.GEOM_BOX, halfExtents=[BODY_L / 2, BODY_W / 2, BODY_H / 2], physicsClientId=cid)
        ghost = p.createMultiBody(0, shape, -1, [0, 0, -1e4], physicsClientId=cid)
        p.setCollisionFilterGroupMask(ghost, -1, 0, 0, physicsClientId=cid)

    from sim.obstacle_avoid.geometry import Route, quat_to_matrix
    route = Route(path_xy)
    if segments:
        windows = obstacle_segments(avoid.rocks, avoid.craters, route.length)
    else:
        windows = [(0.0, route.length)]
    t0 = time.time()
    st = {"min_gap": math.inf, "contacts": 0, "rim": math.inf, "max_tilt": 0.0, "detours": 0, "recoveries": 0,
          "failed": [], "driven_m": 0.0}

    def drive(s0: float, s1: float) -> dict:
        """Spawn on the route at s0 and run the C8 loop on the slice [s0, s1] until arrival (or timeout/blocked)."""
        ss = np.arange(s0, s1, 1.0)
        sub = route.point(np.append(ss, s1))
        ahead = sub[min(3, len(sub) - 1)] - sub[0]
        router.spawn(float(sub[0, 0]), float(sub[0, 1]), math.atan2(ahead[1], ahead[0]))
        nav.load_path(sub)
        avoid.reset()
        nav.start()
        t_start = router.sim_time
        max_sim = (s1 - s0) / max(speed, 0.05) * 3.0 + 300.0
        while router.sim_time - t_start < max_sim and nav.state != "arrived":
            pose = router.pose()
            avoid.tick(pose, nav, router.sim_time)
            nav.observe(pose)
            router.set_inputs(*avoid.command(nav.command(), pose, nav))
            router.update(0.02, warp)
            pose = router.pose()
            yaw = yaw_of(pose.q)
            roll, pitch, _ = _euler(pose.q)
            st["max_tilt"] = max(st["max_tilt"], math.degrees(math.acos(max(-1.0, min(1.0, math.cos(roll) * math.cos(pitch))))))
            near = [r for r in avoid.rocks if r.body is not None and math.hypot(r.x - pose.x, r.y - pose.y) < r.radius + 6.0]
            if near and cid is not None:
                body = router.wheeled.body if model == "wheeled" else ghost
                if ghost is not None:
                    c = np.array([pose.x, pose.y, pose.z]) + quat_to_matrix(pose.q) @ np.array([0, 0, BODY_H / 2])
                    p.resetBasePositionAndOrientation(ghost, c.tolist(), list(pose.q), physicsClientId=cid)
                for r in near:
                    for cp in p.getClosestPoints(body, r.body, 6.0, physicsClientId=cid):
                        st["min_gap"] = min(st["min_gap"], cp[8])
                        st["contacts"] += int(cp[8] <= 0.0)
            st["rim"] = min(st["rim"], _rim_margin((pose.x, pose.y), yaw, avoid.craters))
            if avoid.brain.mode == "blocked":
                break
        st["detours"] += avoid.brain.detours
        st["recoveries"] += avoid.brain.recoveries
        st["driven_m"] += s1 - s0
        return {"arrived": nav.state == "arrived", "mode": avoid.brain.mode}

    for s0, s1 in windows:
        r = drive(s0, s1)
        if not r["arrived"] or r["mode"] != "track":
            st["failed"].append({"s0": round(s0), "s1": round(s1), **r})

    res = {"seed": seed, "label": label + (f" · mode A: {len(windows)} obstacle segments, {st['driven_m']:.0f} m driven"
                                           if segments else " · full drive"),
           "model": model, "rocks": len(avoid.rocks), "craters": len(avoid.craters),
           "arrived": not any(not f["arrived"] for f in st["failed"]),
           "final_mode": "track" if not st["failed"] else st["failed"][-1]["mode"],
           "blocked": any(f["mode"] == "blocked" for f in st["failed"]), "failed_segments": st["failed"],
           "contacts": st["contacts"], "min_gap_m": round(st["min_gap"], 3) if math.isfinite(st["min_gap"]) else None,
           "min_rim_margin_m": round(st["rim"], 3) if math.isfinite(st["rim"]) else None,
           "detours": st["detours"], "recoveries": st["recoveries"], "max_tilt_deg": round(st["max_tilt"], 1),
           "sim_s": round(router.sim_time, 1), "wall_s": round(time.time() - t0, 1), "scan_ms": round(avoid.scan_ms, 1)}
    res["pass"] = (not st["failed"] and res["contacts"] == 0
                   and (res["min_rim_margin_m"] is None or res["min_rim_margin_m"] > 0))
    return res


def _euler(q):
    x, y, z, w = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    return roll, pitch, 0.0


def run_synthetic(seed: int, model: str, speed: float, warp: float) -> dict:
    import dataclasses
    from sim.obstacle_avoid.tests.stubs import run_closed_loop
    _, grid, xy, label = synthetic_mission()
    grid = dataclasses.replace(grid, h=grid.h.copy())
    t0 = time.time()
    res, av = run_closed_loop(grid, xy, model=model, speed=speed, warp=warp, seed=seed)
    return {"seed": seed, "pass": res.ok, "label": label, "model": model, "rocks": len(av.rocks),
            "craters": len(av.craters), "contacts": res.contacts, "blocked": res.blocked, "arrived": res.arrived,
            "final_mode": res.final_mode,
            "min_gap_m": round(res.min_gap_m, 3) if np.isfinite(res.min_gap_m) else None,
            "min_rim_margin_m": round(res.min_rim_margin_m, 3) if np.isfinite(res.min_rim_margin_m) else None,
            "detours": res.detours, "recoveries": res.recoveries, "max_tilt_deg": None,
            "sim_s": round(res.sim_time_s, 1), "wall_s": round(time.time() - t0, 1), "scan_ms": round(av.scan_ms, 1)}


def one(args) -> dict:
    seed, model, speed, warp, use_real, segments = args
    try:
        if use_real:
            return run_real(seed, model, speed, warp, segments=segments)
        return run_synthetic(seed, model, speed, warp)
    except Exception as e:  # one broken seed must not hide the others
        return {"seed": seed, "pass": False, "error": f"{type(e).__name__}: {e}", "label": "error", "rocks": 0,
                "craters": 0, "contacts": 0, "blocked": False, "min_gap_m": None, "min_rim_margin_m": None,
                "detours": 0, "recoveries": 0, "final_mode": None, "sim_s": 0, "wall_s": 0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1-48")
    ap.add_argument("--model", default="wheeled", choices=["wheeled", "kinematic"])
    ap.add_argument("--speed", type=float, default=2.0)
    ap.add_argument("--warp", type=float, default=5.0)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--synthetic", action="store_true", help="force the synthetic stand-in")
    ap.add_argument("--segments", action="store_true",
                    help="mode A: drive only the stretches around each obstacle group (real route, ~3-5x faster)")
    ap.add_argument("--out", default=None, help="write the per-seed results as JSON")
    a = ap.parse_args()
    use_real = not a.synthetic and real_available()
    seeds = parse_seeds(a.seeds)
    print(f"mode: {'REAL (M1 data + M2 route + M4 rover)' if use_real else 'SYNTHETIC'}"
          f"{' · A: obstacle segments' if a.segments and use_real else ''} · model {a.model} · "
          f"{a.speed} m/s · {a.warp}× · {len(seeds)} seeds · {a.jobs} jobs", flush=True)
    rows = []
    with ProcessPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for r in ex.map(one, [(s, a.model, a.speed, a.warp, use_real, a.segments) for s in seeds]):
            rows.append(r)
            if "error" in r:
                print(f"seed {r['seed']:3d}  ERROR {r['error']}", flush=True)
                continue
            print(f"seed {r['seed']:3d}  {'PASS' if r['pass'] else 'FAIL'}  rocks {r['rocks']:3d}  craters {r['craters']:2d}"
                  f"  gap {r['min_gap_m']}  rim {r['min_rim_margin_m']}  detours {r['detours']:2d}"
                  f"  rec {r['recoveries']}  {r['final_mode']}  tilt {r.get('max_tilt_deg')}  sim {r['sim_s']}s"
                  f"  wall {r['wall_s']}s", flush=True)
    ok = [r for r in rows if "error" not in r]
    gaps = [r["min_gap_m"] for r in ok if r["min_gap_m"] is not None]
    rims = [r["min_rim_margin_m"] for r in ok if r["min_rim_margin_m"] is not None]
    summary = {"route": ok[0]["label"] if ok else None, "failed_seeds": [r["seed"] for r in rows if not r["pass"]], "model": a.model, "speed_m_s": a.speed, "warp": a.warp,
               "passed": sum(r["pass"] for r in rows), "total": len(rows), "errors": len(rows) - len(ok),
               "boulders": sum(r["rocks"] for r in ok), "craters": sum(r["craters"] for r in ok),
               "detours": sum(r["detours"] for r in ok), "recoveries": sum(r["recoveries"] for r in ok),
               "contacts": sum(r["contacts"] for r in ok), "blocked": sum(r["blocked"] for r in ok),
               "closest_boulder_gap_m": min(gaps) if gaps else None,
               "closest_rim_margin_m": min(rims) if rims else None}
    print(json.dumps(summary, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps({"summary": summary, "seeds": rows}, indent=1))


if __name__ == "__main__":
    main()
