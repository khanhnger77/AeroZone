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

from sim.server.physics import (HAVE_PYBULLET, ColliderCheck, PhysicsWorld, RoverModelRouter,
                                euler_from_quat, quat_from_euler, wheel_commands)
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
    """Non-square grid the size of the real demo corridor (well above 512 x 512 posts)."""
    x0, y0 = -0.5 * (nx - 1) * spacing, -0.5 * (ny - 1) * spacing
    X, Y = np.meshgrid(x0 + spacing * np.arange(nx), y0 + spacing * np.arange(ny))
    h = (3.0 * np.sin(X / 60.0) * np.cos(Y / 45.0) + 0.002 * X).astype(np.float32)
    return Grid(h=h, x0=x0, y0=y0, dx=spacing, dy=spacing, lon_w=0, lon_e=0, lat_s=0, lat_n=0,
                res_src_m=spacing, meta={"label": "corridor-sized test"})


@needs_pybullet
def test_corridor_sized_collider_matches_render_grid(physics_cfg):
    """One heightfield for the whole demo-sized corridor must collide everywhere (no fall-through)."""
    grid = corridor_grid()
    world = PhysicsWorld(physics_cfg)
    try:
        world.set_terrain(grid)
        rng = np.random.default_rng(1)
        x_min, x_max, y_min, y_max = grid.extent()
        errs = []
        for x, y in zip(rng.uniform(x_min + 5, x_max - 5, 400), rng.uniform(y_min + 5, y_max - 5, 400)):
            z_ray = world.ground_height_at(x, y)
            assert z_ray is not None, (x, y)
            errs.append(abs(z_ray - grid.height_at(x, y)))
        assert max(errs) < 0.15, max(errs)
    finally:
        world.close()


@needs_pybullet
def test_tiled_collider_matches_on_seams_and_corners(physics_cfg):
    """Tiles share edge posts: the collider must match the grid exactly on seams and tile corners."""
    grid = corridor_grid()
    world = PhysicsWorld(physics_cfg)
    try:
        world.set_terrain(grid)
        assert len(world.terrain_bodies) > 1
        step = world.tile_posts - 1
        _, _, y_min, y_max = grid.extent()
        seam_x, seam_y = grid.x0 + step * grid.dx, grid.y0 + step * grid.dy
        pts = [(seam_x + dx, y) for dx in (-0.5, 0.0, 0.5) for y in np.linspace(y_min + 5, y_max - 5, 20)]
        pts += [(seam_x + d, seam_y + d) for d in (-0.5, 0.0, 0.5)]      # four tiles meet here
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
        seam_x = grid.x0 + (router.world.tile_posts - 1) * grid.dx
        router.spawn(seam_x - 10.0, 0.0, 0.0)
        simulate(router, 3.0)                      # settle
        router.set_inputs(1.0, 0.0)
        simulate(router, 20.0)                     # drive east over the seam
        b = router.pose()
        assert router.active.name == "wheeled"
        assert b.x > seam_x + 5.0
        assert b.z == pytest.approx(float(grid.height_at(b.x, b.y)), abs=0.2)
    finally:
        router.close()


# ------------------------------------------------------------------ robustness on any PyBullet build
def real_like_grid() -> Grid:
    """Corridor-sized grid with the real demo corridor's non-round origin and spacing.

    Round numbers hide the seam crack: with x0 = -4345.56 and dx = 4.7424 a vertical ray exactly on
    a tile seam can slip between the two heightfields.
    """
    nx, ny, dx, dy = 1834, 727, 4.742418922493159, 5.0
    x0, y0 = -4345.5595274148, -1815.206341884099
    X, Y = np.meshgrid(x0 + dx * np.arange(nx), y0 + dy * np.arange(ny))
    h = (40.0 * np.sin(X / 300.0) * np.cos(Y / 220.0) + 0.01 * X).astype(np.float32)
    return Grid(h=h, x0=x0, y0=y0, dx=dx, dy=dy, lon_w=0, lon_e=0, lat_s=0, lat_n=0,
                res_src_m=5.0, meta={"label": "real-like corridor"})


@needs_pybullet
def test_rays_exactly_on_tile_seams_find_the_ground(physics_cfg):
    grid = real_like_grid()
    world = PhysicsWorld(physics_cfg)
    try:
        world.set_terrain(grid)
        step = world.tile_posts - 1
        _, _, y_min, y_max = grid.extent()
        seams = [grid.x0 + k * step * grid.dx for k in range(1, (grid.nx - 1) // step + 1)]
        misses = []
        for sx in seams:
            for y in np.linspace(y_min + 5, y_max - 5, 60):
                z = world.ground_height_at(sx, y)
                if z is None or abs(z - grid.height_at(sx, y)) > 0.15:
                    misses.append((sx, y, z))
        assert not misses, misses[:3]
    finally:
        world.close()


@needs_pybullet
def test_collider_self_test_passes_on_a_real_like_corridor(physics_cfg):
    world = PhysicsWorld(physics_cfg)
    try:
        check = world.build_verified_terrain(real_like_grid())
        assert check.ok, check.failures
        assert check.tile_posts == 512                 # no needless fallback to smaller tiles
    finally:
        world.close()


@needs_pybullet
def test_collider_self_test_passes_and_covers_seams(physics_cfg):
    grid = corridor_grid()
    world = PhysicsWorld(physics_cfg)
    try:
        check = world.build_verified_terrain(grid)
        assert check.ok, check.failures
        assert check.tile_posts == 512 and check.tiles == len(world.terrain_bodies) > 1
        assert check.probes > 24                   # random probes plus seams and tile corners
    finally:
        world.close()


@needs_pybullet
def test_collider_self_test_detects_a_non_colliding_terrain(physics_cfg):
    """The macOS failure mode: a heightfield that exists but does not collide. Must be caught."""
    import pybullet as p

    grid = corridor_grid()
    world = PhysicsWorld(physics_cfg)
    try:
        world.set_terrain(grid)
        for body in world.terrain_bodies:          # keep the bodies, switch their contacts off
            p.setCollisionFilterGroupMask(body, -1, 0, 0, physicsClientId=world.client)
        check = world.verify_terrain()
        assert not check.ok
        assert any("fell" in f for f in check.failures)
    finally:
        world.close()


@needs_pybullet
def test_self_test_retries_with_smaller_tiles(physics_cfg, monkeypatch):
    grid = corridor_grid()
    world = PhysicsWorld(physics_cfg)
    real_verify = PhysicsWorld.verify_terrain

    def verify(self, *a, **k):                     # pretend this build only copes with <= 256 posts
        check = real_verify(self, *a, **k)
        if self.tile_posts > 256:
            check.ok, check.failures = False, ["simulated: probe fell"]
        return check

    monkeypatch.setattr(PhysicsWorld, "verify_terrain", verify)
    try:
        check = world.build_verified_terrain(grid)
        assert check.ok and check.tile_posts == 256 and world.tile_posts == 256
    finally:
        world.close()


@needs_pybullet
def test_failed_self_test_switches_mission_to_kinematic(physics_cfg, rover_cfg, monkeypatch):
    def broken(self, *a, **k):
        return ColliderCheck(False, self.tile_posts, len(self.terrain_bodies), 10, ["simulated: probe fell"])

    monkeypatch.setattr(PhysicsWorld, "verify_terrain", broken)
    router = RoverModelRouter(dict(physics_cfg, model="wheeled"), rover_cfg)
    try:
        router.set_terrain(flat_grid())
        assert router.active.name == "kinematic" and "wheeled" not in router.available
        assert "kinematic" in router.available
        assert router.degraded and router.describe()["collider"]["verified"] is False
        assert router.set_model("wheeled") is False
        events = router.pop_events()
        assert [e["event"] for e in events] == ["collider_unverified"]
        assert events[0]["type"] == "physics_event" and router.pop_events() == []
        router.spawn(0.0, 0.0, 0.0)                # still drives, on the grid
        router.set_inputs(1.0, 0.0)
        simulate(router, 5.0)
        assert router.pose().x > 3.0
    finally:
        router.close()


@needs_pybullet
def test_astronaut_survives_terrain_rebuild_and_collider_failure(physics_cfg, rover_cfg, monkeypatch):
    """The on-foot model follows the rendered grid: a collider self-test never takes it away."""
    router = RoverModelRouter(dict(physics_cfg, model="wheeled"), rover_cfg)
    if router.astronaut is None:
        pytest.skip("astronaut model not configured")
    try:
        router.set_terrain(flat_grid())
        assert router.available == ["wheeled", "kinematic", "astronaut"]
        router.spawn(0.0, 0.0, 0.0)
        assert router.set_model("astronaut") and router.active.name == "astronaut"
        monkeypatch.setattr(PhysicsWorld, "verify_terrain",
                            lambda self, *a, **k: ColliderCheck(False, self.tile_posts, 1, 1, ["simulated"]))
        router.set_terrain(flat_grid())            # next mission on a "broken" PyBullet build
        assert router.available == ["kinematic", "astronaut"]
        assert router.active.name == "astronaut"   # still walking, not forced onto the rover
    finally:
        router.close()


@needs_pybullet
def test_good_terrain_restores_the_preferred_wheeled_model(physics_cfg, rover_cfg, monkeypatch):
    router = RoverModelRouter(dict(physics_cfg, model="wheeled"), rover_cfg)
    try:
        with monkeypatch.context() as m:
            m.setattr(PhysicsWorld, "verify_terrain",
                      lambda self, *a, **k: ColliderCheck(False, self.tile_posts, 1, 1, ["simulated"]))
            router.set_terrain(flat_grid())
        assert router.active.name == "kinematic"
        router.set_terrain(flat_grid())            # next mission, collider fine again
        assert router.active.name == "wheeled" and "wheeled" in router.available
        assert router.degraded is None
    finally:
        router.close()


@needs_pybullet
def test_watchdog_recovers_a_rover_that_falls_through(physics_cfg, rover_cfg):
    import pybullet as p

    grid = flat_grid()
    router = RoverModelRouter(dict(physics_cfg, model="wheeled"), rover_cfg)
    try:
        router.set_terrain(grid)
        router.spawn(0.0, 0.0, 0.3)
        simulate(router, 2.0)
        good = router.pose()
        assert router.pop_events() == []
        body = router.wheeled.body                 # simulate a collider miss: rover 5 m underground
        pos, orn = p.getBasePositionAndOrientation(body, physicsClientId=router.world.client)
        p.resetBasePositionAndOrientation(body, [pos[0], pos[1], pos[2] - 5.0], orn,
                                          physicsClientId=router.world.client)
        router.update(0.02)
        events = router.pop_events()
        assert [e["event"] for e in events] == ["fell_through"]
        assert router.active.name == "kinematic" and router.degraded
        now = router.pose()
        assert math.hypot(now.x - good.x, now.y - good.y) < 0.5      # last good position
        assert now.z == pytest.approx(float(grid.height_at(now.x, now.y)), abs=0.05)
        assert euler_from_quat(now.q)[2] == pytest.approx(0.3, abs=0.05)
    finally:
        router.close()


@needs_pybullet
def test_wheeled_holds_and_drives_on_corridor_sized_grid(physics_cfg, rover_cfg):
    """Settle and drive 20 m far from the grid centre: the rover must neither sink nor stall."""
    grid = corridor_grid()
    router = RoverModelRouter(dict(physics_cfg, model="wheeled"), rover_cfg)
    try:
        router.set_terrain(grid)
        x_start = grid.x0 + 2500.0                     # well off-centre in a 9 km wide grid
        router.spawn(x_start, 400.0, 0.0)
        simulate(router, 3.0)
        a = router.pose()
        assert a.z == pytest.approx(float(grid.height_at(a.x, a.y)), abs=0.2)
        router.set_inputs(1.0, 0.0)
        simulate(router, 20.0)
        b = router.pose()
        assert b.x - a.x > 15.0                        # drove, did not stall
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
