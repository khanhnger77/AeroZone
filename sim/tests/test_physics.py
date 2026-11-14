"""Physics checks for module M4: Mars gravity, collider/render agreement, driving and ICR steering.

`RoverModelRouter.update()` caps substeps per call, so "simulate T seconds" is written as many
`update(0.02)` calls, exactly as the 50 Hz server loop does.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from sim.server.physics import (HAVE_PYBULLET, PhysicsWorld, RoverModelRouter, euler_from_quat,
                                quat_from_euler, wheel_commands)
from sim.server.terrain import Grid

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
needs_pybullet = pytest.mark.skipif(not HAVE_PYBULLET, reason="pybullet not installed")


@pytest.fixture(scope="module")
def physics_cfg() -> dict:
    return yaml.safe_load((CONFIGS / "physics.yaml").read_text())


@pytest.fixture(scope="module")
def rover_cfg() -> dict:
    return yaml.safe_load((CONFIGS / "rover.yaml").read_text())


def make_grid(fn, n: int = 301, spacing: float = 2.0) -> Grid:
    """Square grid centred on the origin with heights h = fn(x, y)."""
    x0 = -0.5 * (n - 1) * spacing
    xs = x0 + spacing * np.arange(n)
    X, Y = np.meshgrid(xs, xs)  # rows are y (south -> north), columns are x
    h = np.asarray(fn(X, Y), dtype=np.float32)
    return Grid(h=h, x0=x0, y0=x0, dx=spacing, dy=spacing, lon_w=0, lon_e=0, lat_s=0, lat_n=0,
                res_src_m=spacing, meta={"label": "test"})


def flat_grid() -> Grid:
    return make_grid(lambda X, Y: np.zeros_like(X))


def simulate(router: RoverModelRouter, seconds: float, warp: float = 1.0) -> None:
    for _ in range(int(round(seconds / 0.02))):
        router.update(0.02, warp)


# ----------------------------------------------------------------------------------------- config
def test_mars_gravity_in_config(physics_cfg):
    assert physics_cfg["gravity_m_s2"] == pytest.approx(3.711)
    assert physics_cfg["timestep_s"] == pytest.approx(0.005)


def test_no_earth_gravity_literal():
    src = (Path(__file__).resolve().parents[1] / "server" / "physics.py").read_text()
    assert "9.81" not in src


def test_quaternion_round_trip():
    for r, pch, y in [(0.1, -0.2, 2.5), (-0.3, 0.25, -1.0), (0.0, 0.0, math.pi / 2)]:
        rr, pp, yy = euler_from_quat(quat_from_euler(r, pch, y))
        assert (rr, pp, yy) == pytest.approx((r, pch, y), abs=1e-9)


# ----------------------------------------------------------------------------------------- world
@needs_pybullet
def test_collider_matches_render_grid(physics_cfg):
    grid = make_grid(lambda X, Y: 3.0 * np.sin(X / 25.0) * np.cos(Y / 31.0) + 0.02 * X, n=201)
    world = PhysicsWorld(physics_cfg)
    try:
        world.set_terrain(grid)
        rng = np.random.default_rng(0)
        x_min, x_max, y_min, y_max = grid.extent()
        errs = []
        for x, y in zip(rng.uniform(x_min + 5, x_max - 5, 300), rng.uniform(y_min + 5, y_max - 5, 300)):
            z_ray = world.ground_height_at(x, y)
            assert z_ray is not None
            errs.append(abs(z_ray - grid.height_at(x, y)))
        assert max(errs) < 0.15, max(errs)
    finally:
        world.close()


def corridor_grid(nx: int = 1834, ny: int = 731, spacing: float = 5.0) -> Grid:
    """Non-square grid the size of the real demo corridor (well above one 512 x 512 heightfield)."""
    x0, y0 = -0.5 * (nx - 1) * spacing, -0.5 * (ny - 1) * spacing
    X, Y = np.meshgrid(x0 + spacing * np.arange(nx), y0 + spacing * np.arange(ny))
    h = (3.0 * np.sin(X / 60.0) * np.cos(Y / 45.0) + 0.002 * X).astype(np.float32)
    return Grid(h=h, x0=x0, y0=y0, dx=spacing, dy=spacing, lon_w=0, lon_e=0, lat_s=0, lat_n=0,
                res_src_m=spacing, meta={"label": "corridor-sized test"})


@needs_pybullet
def test_large_collider_matches_render_grid(physics_cfg):
    grid = corridor_grid()
    world = PhysicsWorld(physics_cfg)
    try:
        world.set_terrain(grid)
        assert len(world.terrain_bodies) > 1
        rng = np.random.default_rng(1)
        x_min, x_max, y_min, y_max = grid.extent()
        seam_x = grid.x0 + 511 * grid.dx                        # shared edge of the first two tiles
        pts = list(zip(rng.uniform(x_min + 5, x_max - 5, 300), rng.uniform(y_min + 5, y_max - 5, 300)))
        pts += [(seam_x + dx, y) for dx in (-0.5, 0.0, 0.5) for y in np.linspace(y_min + 5, y_max - 5, 20)]
        errs = []
        for x, y in pts:
            z_ray = world.ground_height_at(x, y)
            assert z_ray is not None, (x, y)
            errs.append(abs(z_ray - grid.height_at(x, y)))
        assert max(errs) < 0.15, max(errs)
    finally:
        world.close()


@needs_pybullet
def test_wheeled_drives_across_tile_seam(physics_cfg, rover_cfg):
    grid = corridor_grid()
    router = RoverModelRouter(dict(physics_cfg, model="wheeled"), rover_cfg)
    try:
        router.set_terrain(grid)
        seam_x = grid.x0 + 511 * grid.dx
        router.spawn(seam_x - 10.0, 0.0, 0.0)
        simulate(router, 3.0)                      # settle
        router.set_inputs(1.0, 0.0)
        simulate(router, 20.0)                     # drive east over the seam
        b = router.pose()
        assert b.x > seam_x + 5.0
        assert b.z == pytest.approx(float(grid.height_at(b.x, b.y)), abs=0.2)
    finally:
        router.close()


@needs_pybullet
def test_free_fall_at_mars_gravity(physics_cfg):
    import pybullet as p

    world = PhysicsWorld(physics_cfg)
    try:
        cid = world.client
        shape = p.createCollisionShape(p.GEOM_SPHERE, radius=0.1, physicsClientId=cid)
        body = p.createMultiBody(1.0, shape, -1, [0, 0, 100], physicsClientId=cid)
        p.changeDynamics(body, -1, linearDamping=0.0, physicsClientId=cid)
        steps = 400  # 2 s
        for _ in range(steps):
            world.step()
        t = steps * world.timestep_s
        vz = p.getBaseVelocity(body, physicsClientId=cid)[0][2]
        assert vz == pytest.approx(-3.711 * t, rel=1e-3)
    finally:
        world.close()


# ----------------------------------------------------------------------------------------- stepping
def test_accumulator_caps_and_drops_backlog(physics_cfg, rover_cfg):
    cfg = dict(physics_cfg, engine="kinematic", model="kinematic")
    router = RoverModelRouter(cfg, rover_cfg)
    router.set_terrain(flat_grid())
    router.spawn(0.0, 0.0, 0.0)
    assert router.update(0.02, 1.0) == 4
    assert router.update(0.02, 5.0) == 20
    assert router.update(10.0, 1.0) == physics_cfg["max_substeps"]   # stall: capped
    assert router.update(0.0, 1.0) == 0                              # backlog dropped
    assert router.update(10.0, 50.0) == physics_cfg["max_substeps_cap"]


def test_no_stepping_before_spawn(physics_cfg, rover_cfg):
    router = RoverModelRouter(dict(physics_cfg, engine="kinematic"), rover_cfg)
    assert router.update(0.02) == 0
    assert router.sim_time == 0.0


# ----------------------------------------------------------------------------------------- steering
def test_icr_turn_in_place_geometry(rover_cfg):
    d = rover_cfg["dynamics"]
    pos, steer, r = d["wheel_positions_m"], d["steerable"], d["wheel_radius_m"]
    omega = 0.3
    cmds = wheel_commands(0.0, omega, pos, steer, r)
    for (xi, yi), can, (angle, spin) in zip(pos, steer, cmds):
        if can:
            # wheel heading is tangent to the circle about the body centre
            assert math.cos(angle) * xi + math.sin(angle) * yi == pytest.approx(0.0, abs=1e-9)
            assert abs(spin) == pytest.approx(omega * math.hypot(xi, yi) / r)
            assert abs(angle) <= math.pi / 2 + 1e-12
        else:
            assert angle == 0.0
            assert spin == pytest.approx(-omega * yi / r)
    # Left and right wheels spin in opposite senses when turning in place.
    assert cmds[1][1] * cmds[4][1] < 0


def test_straight_line_commands(rover_cfg):
    d = rover_cfg["dynamics"]
    cmds = wheel_commands(1.0, 0.0, d["wheel_positions_m"], d["steerable"], d["wheel_radius_m"])
    for angle, spin in cmds:
        assert angle == pytest.approx(0.0)
        assert spin == pytest.approx(1.0 / d["wheel_radius_m"])


# ----------------------------------------------------------------------------------------- driving
def _drive_straight(router: RoverModelRouter, speed: float = 1.0):
    router.set_terrain(flat_grid())
    router.spawn(-150.0, 0.0, 0.0)
    simulate(router, 3.0)                      # settle
    router.set_inputs(speed, 0.0)
    simulate(router, 5.0)                      # spin up
    a = router.pose()
    simulate(router, 5.0)
    b = router.pose()
    return a, b


@pytest.mark.parametrize("model", ["wheeled", "kinematic"])
def test_settles_and_drives_straight(physics_cfg, rover_cfg, model):
    if model == "wheeled" and not HAVE_PYBULLET:
        pytest.skip("pybullet not installed")
    router = RoverModelRouter(dict(physics_cfg, model=model), rover_cfg)
    try:
        assert router.active.name == model
        a, b = _drive_straight(router)
        speed = math.hypot(b.x - a.x, b.y - a.y) / 5.0
        assert speed == pytest.approx(1.0, abs=0.1)
        assert abs(b.y) < 0.5
        roll, pitch, yaw = euler_from_quat(b.q)
        assert abs(math.degrees(yaw)) < 3.0
        assert abs(math.degrees(roll)) < 2.0 and abs(math.degrees(pitch)) < 2.0
        assert b.z == pytest.approx(0.0, abs=0.1)  # ground reference point on the flat ground
    finally:
        router.close()


@needs_pybullet
def test_wheeled_turns_in_place(physics_cfg, rover_cfg):
    router = RoverModelRouter(dict(physics_cfg, model="wheeled"), rover_cfg)
    try:
        router.set_terrain(flat_grid())
        router.spawn(0.0, 0.0, 0.0)
        simulate(router, 3.0)
        start = router.pose()
        router.set_inputs(0.0, 0.3)
        simulate(router, 5.0)
        end = router.pose()
        assert math.hypot(end.x - start.x, end.y - start.y) < 0.5
        yaw = euler_from_quat(end.q)[2]
        assert math.degrees(yaw) > 30.0   # turned left by a substantial angle
    finally:
        router.close()


def test_kinematic_attitude_on_slope(physics_cfg, rover_cfg):
    router = RoverModelRouter(dict(physics_cfg, engine="kinematic", model="kinematic"), rover_cfg)
    router.set_terrain(make_grid(lambda X, Y: 0.1 * X + 0.05 * Y))
    router.spawn(0.0, 0.0, 0.0)
    roll, pitch, _ = euler_from_quat(router.pose().q)
    assert pitch == pytest.approx(-math.atan(0.1), abs=1e-3)   # facing uphill: nose up
    assert roll == pytest.approx(math.atan(0.05), abs=1e-3)    # left (north) side higher


@needs_pybullet
def test_model_switch_transfers_pose(physics_cfg, rover_cfg):
    router = RoverModelRouter(dict(physics_cfg, model="wheeled"), rover_cfg)
    try:
        router.set_terrain(flat_grid())
        router.spawn(10.0, -5.0, 0.5)
        simulate(router, 2.0)
        before = router.pose()
        assert router.set_model("kinematic")
        after = router.pose()
        assert (after.x, after.y) == pytest.approx((before.x, before.y), abs=1e-6)
        assert euler_from_quat(after.q)[2] == pytest.approx(euler_from_quat(before.q)[2], abs=1e-6)
        assert router.set_model("wheeled")
        simulate(router, 2.0)
        back = router.pose()
        assert math.hypot(back.x - before.x, back.y - before.y) < 0.3
        assert not router.set_model("hovercraft")
    finally:
        router.close()
