"""Navigator checks: unit behaviour plus closed-loop path following with both rover models (C8 order)."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from sim.backend.navigator import PurePursuitNavigator
from sim.server.physics import HAVE_PYBULLET, Pose, RoverModelRouter, quat_from_euler
from sim.tests.test_physics import make_grid

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


@pytest.fixture(scope="module")
def physics_cfg() -> dict:
    return yaml.safe_load((CONFIGS / "physics.yaml").read_text())


@pytest.fixture(scope="module")
def rover_cfg() -> dict:
    return yaml.safe_load((CONFIGS / "rover.yaml").read_text())


def pose(x, y, yaw=0.0, v=0.0) -> Pose:
    return Pose(x, y, 0.0, quat_from_euler(0.0, 0.0, yaw), v * math.cos(yaw), v * math.sin(yaw))


# ----------------------------------------------------------------------------------------- unit
def test_states_and_zero_command_when_not_driving(rover_cfg):
    nav = PurePursuitNavigator(rover_cfg)
    assert nav.state == "idle" and nav.command() == (0.0, 0.0)
    nav.load_path([[0, 0], [50, 0]])
    nav.observe(pose(0, 0))
    assert nav.command() == (0.0, 0.0)
    nav.start()
    assert nav.state == "driving"
    v, w = nav.command()
    assert v == pytest.approx(nav.cruise) and w == pytest.approx(0.0, abs=1e-9)
    nav.pause()
    assert nav.state == "paused" and nav.command() == (0.0, 0.0)
    nav.start()
    assert nav.state == "driving"


def test_signed_cross_track_and_monotone_progress(rover_cfg):
    nav = PurePursuitNavigator(rover_cfg)
    nav.load_path([[0, 0], [100, 0]])
    nav.observe(pose(20, 3))
    assert nav.xte == pytest.approx(3.0) and nav.s == pytest.approx(20.0)
    nav.observe(pose(25, -2))
    assert nav.xte == pytest.approx(-2.0)
    nav.observe(pose(10, 0))              # moving backwards does not reduce progress
    assert nav.s == pytest.approx(25.0)


def test_progress_window_ignores_far_ahead_overlap(rover_cfg):
    # An out-and-back route: the return leg passes 1 m from the outbound leg.
    out = [[x, 0.0] for x in range(0, 101)]
    back = [[x, 1.0] for x in range(100, -1, -1)]
    nav = PurePursuitNavigator(rover_cfg)
    nav.load_path(out + back)
    nav.observe(pose(10, 0.9))            # nearer the return leg, but that is far ahead in arc length
    assert nav.s == pytest.approx(10.0)


def test_turn_in_place_when_target_behind(rover_cfg):
    nav = PurePursuitNavigator(rover_cfg)
    nav.load_path([[0, 0], [50, 0]])
    nav.start()
    nav.observe(pose(0, 0, yaw=math.pi))  # facing away from the route
    v, w = nav.command()
    assert v == 0.0 and abs(w) == pytest.approx(rover_cfg["limits"]["max_yaw_rate_rad_s"])


def test_slows_near_goal_and_arrives(rover_cfg):
    nav = PurePursuitNavigator(rover_cfg)
    nav.load_path([[0, 0], [20, 0]])
    nav.set_cruise(2.0)
    nav.start()
    nav.observe(pose(16, 0))              # 4 m left, slow radius 8 m
    v, _ = nav.command()
    assert v == pytest.approx(2.0 * 4.0 / 8.0)
    nav.observe(pose(19.95, 0))
    v, _ = nav.command()
    assert nav.state == "arrived" and v == 0.0
    assert nav.status()["progress"] == pytest.approx(1.0, abs=0.01)


def test_rejects_degenerate_path(rover_cfg):
    nav = PurePursuitNavigator(rover_cfg)
    with pytest.raises(ValueError):
        nav.load_path([[1, 1], [1, 1]])


# ----------------------------------------------------------------------------------------- closed loop
@pytest.mark.parametrize("model", ["wheeled", "kinematic"])
def test_follows_sinusoidal_path(physics_cfg, rover_cfg, model):
    if model == "wheeled" and not HAVE_PYBULLET:
        pytest.skip("pybullet not installed")
    grid = make_grid(lambda X, Y: 1.5 * np.sin(X / 40.0) * np.cos(Y / 35.0), n=301, spacing=2.0)
    xs = np.linspace(-120.0, 120.0, 241)
    path = np.column_stack([xs, 12.0 * np.sin(xs / 30.0)])

    router = RoverModelRouter(dict(physics_cfg, model=model), rover_cfg)
    nav = PurePursuitNavigator(rover_cfg)
    try:
        router.set_terrain(grid)
        nav.load_path(path)
        yaw0 = math.atan2(*(path[3] - path[0])[::-1])
        router.spawn(*path[0], yaw0)
        for _ in range(150):                      # settle 3 s
            router.update(0.02)
        nav.set_cruise(1.0)
        nav.start()
        max_xte = 0.0
        for _ in range(int(600 / 0.1)):           # up to 600 s sim time at warp 5
            p = router.pose()
            nav.observe(p)
            router.set_inputs(*nav.command())
            router.update(0.02, 5.0)
            max_xte = max(max_xte, abs(nav.xte))
            if nav.state == "arrived":
                break
        assert nav.state == "arrived", nav.status()
        assert max_xte < 2.5, max_xte
    finally:
        router.close()
