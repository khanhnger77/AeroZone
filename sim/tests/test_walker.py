"""Checks for the on-foot "astronaut" motion model (sim/server/walker.py) and its router integration."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from sim.backend.navigator import PurePursuitNavigator
from sim.server.physics import RoverModelRouter, euler_from_quat
from sim.server.walker import AstronautWalker, tobler_factor
from sim.tests.test_physics import make_grid

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


@pytest.fixture(scope="module")
def physics_cfg() -> dict:
    return yaml.safe_load((CONFIGS / "physics.yaml").read_text())


@pytest.fixture(scope="module")
def rover_cfg() -> dict:
    return yaml.safe_load((CONFIGS / "rover.yaml").read_text())


@pytest.fixture(scope="module")
def astro_cfg() -> dict:
    return yaml.safe_load((CONFIGS / "astronaut.yaml").read_text())


def walk(w: AstronautWalker, seconds: float, v: float, omega: float = 0.0, dt: float = 0.005) -> None:
    for _ in range(int(round(seconds / dt))):
        w.step(dt, v, omega)


def slope_grid(deg: float):
    k = math.tan(math.radians(deg))
    return make_grid(lambda X, Y: k * X, n=201, spacing=2.0)      # rises towards +x (east)


def test_tobler_is_one_on_flat_and_peaks_slightly_downhill():
    assert tobler_factor(0.0) == pytest.approx(1.0)
    assert tobler_factor(-0.05) > 1.0 > tobler_factor(0.1) > tobler_factor(0.3)
    assert tobler_factor(0.3, 0.8) > tobler_factor(0.3, 1.0)          # softening flattens the curve


def test_walks_at_max_speed_on_flat_and_stays_upright(astro_cfg):
    w = AstronautWalker(astro_cfg)
    w.set_grid(slope_grid(0.0))
    w.spawn(0, 0, 0)
    walk(w, 6.0, v=3.0)                                               # command above the cap
    assert w.v == pytest.approx(astro_cfg["walker"]["max_speed_m_s"], rel=0.02)
    roll, pitch, yaw = euler_from_quat(w.pose().q)
    assert abs(roll) < 1e-9 and abs(pitch) < 1e-9 and abs(yaw) < 1e-9


def test_uphill_is_slower_and_too_steep_stops(astro_cfg):
    speeds = {}
    for deg in (0.0, 15.0, 25.0):
        w = AstronautWalker(astro_cfg)
        w.set_grid(slope_grid(deg))
        w.spawn(-50, 0, 0)                                            # facing +x = uphill
        walk(w, 6.0, v=3.0)
        speeds[deg] = w.v
        assert w.pose().z == pytest.approx(math.tan(math.radians(deg)) * w.x, abs=0.05)   # on the ground
    assert speeds[0.0] > speeds[15.0] > speeds[25.0] > 0.0
    w = AstronautWalker(astro_cfg)
    w.set_grid(slope_grid(35.0))
    w.spawn(-50, 0, 0)
    walk(w, 6.0, v=3.0)
    assert abs(w.x + 50) < 0.05                                       # no uphill progress at 35 deg


def test_turns_faster_than_the_rover_and_gait_advances(astro_cfg, rover_cfg):
    w = AstronautWalker(astro_cfg)
    w.set_grid(slope_grid(0.0))
    w.spawn(0, 0, 0)
    walk(w, 2.0, v=0.0, omega=5.0)
    assert w.w == pytest.approx(astro_cfg["walker"]["max_yaw_rate_rad_s"], rel=0.02)
    assert w.w > rover_cfg["limits"]["max_yaw_rate_rad_s"]
    g0 = w.gait
    walk(w, 1.0, v=1.0)
    assert w.gait != g0
    wheels = w.wheels()
    assert len(wheels) == 6 and all(set(x) == {"steer", "spin"} for x in wheels)


def test_router_offers_astronaut_and_switches_pose(physics_cfg, rover_cfg):
    router = RoverModelRouter(dict(physics_cfg, model="kinematic"), rover_cfg)
    try:
        assert "astronaut" in router.available
        router.set_terrain(slope_grid(0.0))
        router.spawn(5.0, -3.0, 0.4)
        assert router.set_model("astronaut")
        assert router.active.name == "astronaut"
        p = router.pose()
        assert (p.x, p.y) == pytest.approx((5.0, -3.0), abs=1e-6)
        assert euler_from_quat(p.q)[2] == pytest.approx(0.4, abs=1e-6)
        router.set_inputs(1.0, 0.0)
        for _ in range(250):
            router.update(0.02)
        assert router.pose().x > 5.0 + 3.0
        assert router.describe()["model"] == "astronaut"
        assert router.set_model("kinematic") and router.active.name == "kinematic"
    finally:
        router.close()


def test_astronaut_follows_route_with_navigator(physics_cfg, rover_cfg):
    grid = make_grid(lambda X, Y: 1.5 * np.sin(X / 40.0) * np.cos(Y / 35.0), n=301, spacing=2.0)
    xs = np.linspace(-120.0, 120.0, 241)
    path = np.column_stack([xs, 12.0 * np.sin(xs / 30.0)])
    router = RoverModelRouter(dict(physics_cfg, model="astronaut"), rover_cfg)
    nav = PurePursuitNavigator(rover_cfg)
    try:
        assert router.active.name == "astronaut"
        router.set_terrain(grid)
        nav.load_path(path)
        router.spawn(*path[0], math.atan2(*(path[3] - path[0])[::-1]))
        nav.set_cruise(1.0)
        nav.start()
        max_xte = 0.0
        for _ in range(int(900 / 0.1)):
            p = router.pose()
            nav.observe(p)
            router.set_inputs(*nav.command())
            router.update(0.02, 5.0)
            max_xte = max(max_xte, abs(nav.xte))
            if nav.state == "arrived":
                break
        assert nav.state == "arrived", nav.status()
        assert max_xte < 2.0, max_xte
    finally:
        router.close()
