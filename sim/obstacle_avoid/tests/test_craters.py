"""M5 crater tests: carved profile, generator keep-out, drop-off detection, slope false positives."""
from __future__ import annotations

import numpy as np
import pytest

from sim.obstacle_avoid import craters as cm
from sim.obstacle_avoid.geometry import load_config
from sim.obstacle_avoid.lidar import VirtualLidar
from sim.obstacle_avoid.planner import ObstacleMap, classify_parts, shadow_hazards
from sim.obstacle_avoid.service import ObstacleAvoidance
from sim.obstacle_avoid.tests.stubs import (HAVE_PYBULLET, dataclasses, flat_grid, make_grid, pose_on,
                                            run_closed_loop, straight_route)

CFG = load_config()


def test_profile_at_grid_posts():
    g = flat_grid((-60, 60, -60, 60))
    c = cm.Crater("c0", 2.5, -1.5, 10.0, 3.0, 0.8, 0.0)
    cm.apply(g, [c])
    xs = g.x0 + np.arange(g.h.shape[1]) * g.dx
    ys = g.y0 + np.arange(g.h.shape[0]) * g.dy
    X, Y = np.meshgrid(xs, ys)
    r = np.hypot(X - c.x, Y - c.y)
    want = np.where(r <= 30.0, np.where(r <= 10, 0.8 - 3.8 * (1 - (r / 10) ** 2), 0.8 * (10 / np.maximum(r, 1e-9)) ** 3), 0)
    assert np.allclose(g.h, want, atol=1e-5)
    assert abs(c.dz(0.0) + 3.0) < 1e-9 and abs(c.dz(10.0) - 0.8) < 1e-9


def test_generator_keep_out_and_streams():
    path = straight_route(3000.0)
    cc = cm.generate(path, CFG["craters"], 11)
    k = CFG["craters"]
    assert cc
    s = np.array([c.s_route for c in cc])
    assert s.min() >= k["keep_clear_start_m"] and s.max() <= 3000 - k["keep_clear_end_m"]
    if len(s) > 1:
        assert np.diff(s).min() >= k["min_spacing_m"]
    for c in cc:
        assert k["radius_m"][0] <= c.radius <= k["radius_m"][1]
        D = 2 * c.radius
        assert k["depth_ratio"][0] * D <= c.depth <= k["depth_ratio"][1] * D
        assert k["rim_ratio"][0] * D <= c.rim <= k["rim_ratio"][1] * D
        assert abs(c.y) <= k["lateral_jitter_m"] + 1e-9
    # Boulders stay away from craters (service keep-away).
    g = flat_grid((-50, 3050, -60, 60))
    av = ObstacleAvoidance({})
    av.setup(True, 11, path, g, None)
    assert av.craters and av.rocks
    for r in av.rocks:
        for c in av.craters:
            assert np.hypot(r.x - c.x, r.y - c.y) >= c.radius + k["boulder_clearance_m"]
    # The prior is the pre-carve terrain; the grid is carved in place.
    assert float(np.abs(av.prior.h).max()) == 0.0 and float(g.h.min()) < -1.0
    assert av.describe()["craters"][0].keys() >= {"id", "x", "y", "radius", "depth", "rim", "s", "lon", "lat"}


def test_shadow_detection_inside_rim():
    prior = flat_grid((-60, 80, -60, 60))
    g = dataclasses.replace(prior, h=prior.h.copy())
    c = cm.Crater("c0", 20.0, 0.0, 10.0, 3.0, 0.8, 20.0)
    cm.apply(g, [c])
    lid = VirtualLidar(CFG["lidar"])
    # 4 m before the rim: closer than that the raised rim (0.8 m) still hides the floor from a 2 m mast.
    sc = lid.scan(g, [], pose_on(g, 6.0, 0.0, 0.0), 0.0)
    sh = shadow_hazards(sc, lid.pixel_rays(), prior, 10.0)
    hole, _ = classify_parts(sc.points_world, prior, CFG["planner"])
    assert len(sh) > 10
    r = np.hypot(sh[:, 0] - c.x, sh[:, 1] - c.y)
    assert (r <= c.radius + 0.5).all()
    m = ObstacleMap(CFG["planner"])
    m.insert(sc.points_world, prior, sh)
    assert len(m.neg) > 0 or hole.any()


@pytest.mark.parametrize("kind", ["plane", "tilted", "rolling"])
def test_no_false_hazards_on_sloped_terrain(kind):
    if kind == "plane":
        g = flat_grid((-60, 60, -60, 60), slope_deg=15.0)
    elif kind == "tilted":
        g = make_grid(lambda X, Y: 0.22 * X - 0.12 * Y, -60, 60, -60, 60)
    else:
        g = make_grid(lambda X, Y: 2.0 * np.sin(X / 17.0) + 1.5 * np.cos(Y / 13.0), -60, 60, -60, 60)
    lid = VirtualLidar(CFG["lidar"])
    for yaw in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        sc = lid.scan(g, [], pose_on(g, 1.0, -2.0, yaw), 0.0)
        hole, step = classify_parts(sc.points_world, g, CFG["planner"])
        sh = shadow_hazards(sc, lid.pixel_rays(), g, 10.0)
        assert not hole.any() and not step.any(), (kind, yaw)
        assert len(sh) == 0, (kind, yaw, len(sh))


@pytest.mark.skipif(not HAVE_PYBULLET, reason="pybullet not installed")
@pytest.mark.parametrize("lateral", [0.0, 2.5])
def test_crater_detour_without_crossing_rim(lateral):
    g = flat_grid((-60, 220, -80, 80))
    c = cm.Crater("c0", 70.0, lateral, 10.0, 3.0, 0.8, 70.0)
    res, _ = run_closed_loop(g, straight_route(140), model="kinematic", speed=1.0, warp=5, rocks=[], craters=[c])
    assert res.arrived and not res.blocked and res.final_mode == "track", res
    assert res.min_rim_margin_m > 0.0, res
    assert res.detours >= 1
