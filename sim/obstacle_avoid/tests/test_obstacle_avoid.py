"""M5 unit and closed-loop tests: boulders, lidar, classification, LDR1, detours (C8 stubs)."""
from __future__ import annotations

import numpy as np
import pytest

from sim.obstacle_avoid import obstacles as om
from sim.obstacle_avoid.geometry import load_config
from sim.obstacle_avoid.lidar import VirtualLidar
from sim.obstacle_avoid.planner import ArcPlanner, ObstacleMap, classify_parts, decode_keys, encode_keys
from sim.obstacle_avoid.service import ObstacleAvoidance, decode_frame
from sim.obstacle_avoid.tests.stubs import (HAVE_PYBULLET, StubNavigator, flat_grid, pose_on, rock_at,
                                            run_closed_loop, straight_route)

CFG = load_config()
needs_pb = pytest.mark.skipif(not HAVE_PYBULLET, reason="pybullet not installed")


def _with_samples(rocks):
    for r in rocks:
        r.samples = om.sample_surface(r.v_world, r.f, CFG["obstacles"]["surface_density_per_m2"])
    return rocks


# ----------------------------------------------------------------------------------------- boulders
def test_site_positions_sparse_and_on_route():
    c = CFG["obstacles"]
    for seed in range(20):
        rng = np.random.default_rng(seed)
        s = np.array(om.site_positions(3000.0, c, rng))
        assert len(s) >= 1
        assert s.min() >= c["keep_clear_start_m"]
        assert s.max() <= 3000.0 - c["keep_clear_end_m"]
        if len(s) > 1:
            assert np.diff(s).min() >= c["min_spacing_m"] - 1e-9
        assert len(s) <= 3000.0 / c["min_spacing_m"] + 1
    # A short route still gets its middle; a too-short one gets nothing.
    assert om.site_positions(100.0, c, np.random.default_rng(0))
    assert om.site_positions(60.0, c, np.random.default_rng(0)) == []


def test_rock_shape_is_outward_hull_on_base():
    v, f = om.make_rock_shape(2.0, 1.4, np.random.default_rng(3))
    assert len(v) <= 28
    assert abs(v[:, 2].min()) < 1e-9 and abs(v[:, 2].max() - 1.4) < 1e-6
    c = v.mean(0)
    a, b, cc = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    n = np.cross(b - a, cc - a)
    assert ((n * ((a + b + cc) / 3 - c)).sum(1) > 0).all()
    smp = om.sample_surface(v, f, 25)
    assert len(smp) >= 200


def test_generate_rocks_on_route_and_keep_away():
    path = straight_route(2000.0)
    g = flat_grid((-50, 2050, -50, 50))
    rocks = om.generate(path, g.height_at, CFG["obstacles"], seed=5)
    assert rocks
    c = CFG["obstacles"]
    for r in rocks:
        assert abs(r.y) <= c["lateral_jitter_m"] + 1e-9
        assert c["radius_m"][0] * 0.8 <= r.radius <= c["radius_m"][1] * 1.1
        assert r.samples is not None and len(r.samples) >= 200
        d = r.describe()
        assert set(d) >= {"id", "x", "y", "radius", "height", "s", "v", "f", "lon", "lat"}
        assert len(d["v"]) % 3 == 0 and len(d["f"]) % 3 == 0
    # Keep-away discs remove the boulders inside them.
    ka = [(r.x, r.y, 1.0) for r in rocks[:1]]
    again = om.generate(path, g.height_at, CFG["obstacles"], seed=5, keep_away=ka)
    assert len(again) == len(rocks) - 1


# -------------------------------------------------------------------------------------------- lidar
def test_lidar_shape_and_range():
    g = flat_grid()
    lid = VirtualLidar(CFG["lidar"])
    sc = lid.scan(g, [], pose_on(g, 0, 0, 0.3), 1.0)
    assert sc.depth.shape == (32, 240)
    assert sc.depth.max() <= 10.0 + 1e-6
    assert (np.linalg.norm(sc.points_sensor, axis=1) <= 10.0 + 1e-6).all()
    assert lid.pixel_rays().shape == (32, 240, 3)
    assert lid.describe() == {"range_m": 10.0, "width": 240, "height": 32, "mount_height_m": 2.0,
                              "h_res_deg": 1.5, "v_fov_deg": [-45.0, 15.0]}
    # Flat ground from 2 m: nearest return ≈ 2/sin(45°), farthest ≤ range.
    assert sc.depth[sc.depth > 0].min() > 2.0


def test_lidar_occlusion_behind_rock():
    g = flat_grid()
    lid = VirtualLidar(CFG["lidar"])
    rock = _with_samples([rock_at(5.0, 0.0, 1.5, 1.5, grid=g)])
    pose = pose_on(g, 0, 0, 0)
    free = lid.scan(g, [], pose, 0).points_world
    hid = lid.scan(g, rock, pose, 0).points_world
    # Ground straight behind the rock (its shadow from a 2 m mast reaches far past 10 m).
    wedge = lambda P: ((P[:, 0] > 7.2) & (P[:, 0] < 9.8) & (np.abs(P[:, 1]) < 0.5) & (P[:, 2] < 0.05)).sum()
    assert wedge(free) >= 4
    assert wedge(hid) == 0


def test_classification_rock_vs_ground():
    g = flat_grid(slope_deg=8.0)
    lid = VirtualLidar(CFG["lidar"])
    rock = _with_samples([rock_at(6.0, 0.5, 1.8, 1.4, grid=g)])
    sc = lid.scan(g, rock, pose_on(g, 0, 0, 0), 0)
    hole, step = classify_parts(sc.points_world, g, CFG["planner"])
    P = sc.points_world
    near = np.hypot(P[:, 0] - 6.0, P[:, 1] - 0.5) < 2.2
    assert step[near].sum() > 20
    assert step[~near & (np.hypot(P[:, 0] - 6.0, P[:, 1] - 0.5) > 3.0)].sum() == 0
    assert not hole.any()


def test_map_keys_roundtrip_and_local_clearance():
    i = np.array([-5, 0, 7, -123456, 99999])
    j = np.array([3, -1, 0, 44, -77777])
    ii, jj = decode_keys(encode_keys(i, j))
    assert (ii == i).all() and (jj == j).all()
    m = ObstacleMap(CFG["planner"])
    m.pos = np.unique(m._keys(np.array([[10.0, 0.0]])))
    lm = m.local(0.0, 0.0, 80.0, 1.8)
    assert abs(float(lm.clear_at(13.0, 0.0)) - 3.0) < 0.3
    assert bool(lm.blocked(11.0, 0.0)) and not bool(lm.blocked(13.0, 0.0))
    ap = ArcPlanner(CFG["planner"])
    assert ap.footprint_clear(lm, [[7.0, 0.0]], 0.0)[0] < float(lm.clear_at(7.0, 0.0))


def test_astar_is_optimal_vs_dijkstra():
    """A* (heuristic = distance to the nearest goal) must find the same cost as plain Dijkstra."""
    from skimage.graph import MCP_Geometric
    from sim.obstacle_avoid.planner import astar
    rng = np.random.default_rng(0)
    for trial in range(6):
        n = 120
        cost = 1.0 + 4.0 * rng.random((n, n))
        walls = rng.random((n, n)) < 0.18
        cost[walls] = np.inf
        start = (int(rng.integers(n)), int(rng.integers(n)))
        cost[start] = 1.0
        goals = np.zeros((n, n), bool)
        for _ in range(5):
            goals[int(rng.integers(n)), int(rng.integers(n))] = True
        goals &= np.isfinite(cost)
        res = astar(cost, start, goals, 0.4)
        mcp = MCP_Geometric(cost, fully_connected=True, sampling=(0.4, 0.4))
        cum, _ = mcp.find_costs([start])
        best = cum[goals].min() if goals.any() else np.inf
        if not np.isfinite(best):
            assert res.reached is None
            continue
        assert res.reached is not None and goals[res.reached]
        path = np.array(res.path)
        assert tuple(path[0]) == start
        steps = np.abs(np.diff(path, axis=0))
        assert steps.max() <= 1 and (steps.sum(1) > 0).all()
        assert abs(res.g[res.reached[0] * n + res.reached[1]] - best) < 1e-6 * max(1.0, best)


def test_ldr1_roundtrip():
    g = flat_grid()
    av = ObstacleAvoidance({})
    path = straight_route(150.0)
    rk = [rock_at(60.0, 0.0, 2.0, 1.5, grid=g)]
    av.setup(True, 3, path, g, None, rocks=rk, craters=[])
    nav = StubNavigator()
    nav.load_path(path)
    nav.start()
    pose = pose_on(g, 52.0, 0.0, 0.0)
    nav.observe(pose)
    av.tick(pose, nav, 0.0)
    buf = av.encode_frame(pose, nav)
    assert buf[:4] == b"LDR1"
    hl = int.from_bytes(buf[4:8], "little")
    assert hl % 4 == 0
    fr = decode_frame(buf)
    h = fr["header"]
    assert (h["w"], h["h"], h["range"], h["v_fov"]) == (240, 32, 10.0, [-45.0, 15.0])
    assert fr["points"].shape == (h["n"], 3) and fr["flags"].shape == (h["n"],)
    assert fr["depth"].shape == (32, 240)
    assert fr["flags"].sum() > 0 and h["rocks_near"] == 1
    assert len(buf) == 8 + hl + 7 * h["n"] + 2 * 32 * 240
    assert set(h) >= {"n", "w", "h", "range", "v_fov", "sim_time", "mode", "route", "candidates", "carrot",
                      "shadows", "rocks_near", "craters_near"}
    st = av.status(nav)
    assert set(st) >= {"mode", "detours", "recoveries", "obstacle_cells", "blocked_ahead_m", "planner", "choice",
                       "arc", "rocks", "craters", "scans", "scan_ms", "points"}


def test_disabled_is_transparent():
    g = flat_grid()
    av = ObstacleAvoidance({})
    av.setup(False, None, straight_route(100), g, None)
    assert av.status() is None and av.tick(None, None, 0.0) is None
    assert av.command((0.7, 0.1), None, None) == (0.7, 0.1)
    assert av.describe()["enabled"] is False and av.describe()["rocks"] == []
    assert not av.stream_due()


# -------------------------------------------------------------------------------------- closed loop
def _check(res, min_gap=0.15):
    assert res.arrived, res
    assert not res.blocked, res
    assert res.contacts == 0, res
    assert res.min_gap_m > min_gap, res
    assert res.final_mode == "track", res
    assert res.final_xte_m < 0.6, res
    assert res.detours >= 1, res


@needs_pb
@pytest.mark.parametrize("model", ["kinematic", "wheeled"])
def test_detour_and_rejoin_single_rock(model):
    g = flat_grid()
    rk = [rock_at(50.0, 0.3, 2.0, 1.5, grid=g)]
    res, _ = run_closed_loop(g, straight_route(100), model=model, speed=1.0, warp=5, rocks=rk, craters=[])
    _check(res, 0.25 if model == "kinematic" else 0.15)


@needs_pb
def test_two_rocks_4m_apart():
    g = flat_grid()
    rk = [rock_at(50.0, 0.4, 1.8, 1.4, rid="a", grid=g, seed=1), rock_at(54.0, -0.4, 1.8, 1.4, rid="b", grid=g, seed=2)]
    res, _ = run_closed_loop(g, straight_route(100), model="kinematic", speed=1.0, warp=5, rocks=rk, craters=[])
    _check(res)


@needs_pb
@pytest.mark.parametrize("paired", [False, True])
def test_big_boulders(paired):
    g = flat_grid()
    rk = [rock_at(50.0, 0.0, 2.5, 2.25, rid="a", grid=g, seed=4)]
    if paired:
        rk.append(rock_at(50.0 + 5.0 * 1.6, 0.5, 2.5, 2.0, rid="b", grid=g, seed=5))
    res, _ = run_closed_loop(g, straight_route(110), model="wheeled", speed=1.0, warp=5, rocks=rk, craters=[])
    _check(res)


@needs_pb
def test_double_boulder_fast_regression():
    """Regression: a close pair at 2 m/s and 5× warp (controller at 10 Hz of sim time)."""
    g = flat_grid()
    rk = [rock_at(60.0, 0.5, 2.0, 1.6, rid="a", grid=g, seed=7), rock_at(60.0 + 4.2 * 1.6, -0.6, 2.2, 1.7, rid="b", grid=g, seed=8)]
    for model in ("kinematic", "wheeled"):
        res, _ = run_closed_loop(g, straight_route(120), model=model, speed=2.0, warp=5, rocks=rk, craters=[])
        _check(res)


@needs_pb
def test_wedged_start_inside_margin():
    g = flat_grid()
    rk = [rock_at(1.0, 3.2, 1.5, 1.2, grid=g, seed=9)]
    res, av = run_closed_loop(g, straight_route(60), model="kinematic", speed=1.0, warp=5, rocks=rk, craters=[])
    assert res.arrived and not res.blocked and res.contacts == 0 and res.final_mode == "track", res
