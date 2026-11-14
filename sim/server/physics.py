"""Rigid-body and kinematic rover models behind one router (C8).

Why this module exists: the server is the single authority on vehicle state (principle 1). PyBullet in
`DIRECT` mode gives a real multibody (six sphere-contact wheels, four steering links) on the same
height grid the browser renders (principle 5). A terrain-following kinematic integrator is kept as a
cheap, deterministic fallback for machines without PyBullet and for very high warps.

Robust on any platform/PyBullet build: the collider is never trusted blindly. Each terrain build is
tiled and self-tested by dropping probes through the real contact pipeline (smaller tiles are tried
if a probe falls through), and a runtime watchdog catches a wheeled rover that ends up below the
rendered surface. Either failure switches the mission to the kinematic model and queues a
`physics_event` for the page, so the rover can never visibly fall out of the world.

Time is advanced by a fixed-step accumulator (principle 3): real elapsed time x warp is consumed in
`timestep_s` slices, at most `cap` per call; when the cap is hit the backlog is dropped rather than
carried, so a stalled event loop never makes the simulation spiral.

Frames (C1): ENU world, rover body x forward, y left, z up; yaw 0 faces east. `Pose.(x, y, z)` is the
ground reference point under the body centre, not the chassis centre.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

try:  # PyBullet is optional at import time; the kinematic model works without it.
    import pybullet as p
    HAVE_PYBULLET = True
except ImportError:  # pragma: no cover - depends on the environment
    p = None
    HAVE_PYBULLET = False

log = logging.getLogger(__name__)

HEIGHTFIELD_TILE_POSTS = 512   # PyBullet heightfields above 512 x 512 posts stop colliding on some builds
# Collider self-test: tile sizes tried in order until dropped probes rest on the surface everywhere.
HEIGHTFIELD_TILE_FALLBACKS = (512, 256, 128)
PROBE_RADIUS_M = 0.25
PROBE_DROP_M = 0.5            # released this far above the surface
PROBE_SETTLE_S = 0.8          # > free-fall time from PROBE_DROP_M at Mars gravity (0.52 s)
PROBE_SINK_TOL_M = 0.5        # a probe this far below the rendered surface fell through
PROBE_RANDOM = 24
FALL_THROUGH_M = 1.0          # wheeled rover this far below the rendered surface: it fell through
SPAWN_DROP_M = 0.3      # spawn height above the ground, so the rover settles onto the terrain
SWITCH_DROP_M = 0.05    # smaller drop when transferring the pose between models
STEER_MAX_RATE_RAD_S = 1.0
STEER_LINK_MASS_KG = 1.0
STEER_LINK_INERTIA = 0.05
KINEMATIC_LAG_S = 0.5


# --------------------------------------------------------------------------------------------- maths
@dataclass
class ColliderCheck:
    """Result of `PhysicsWorld.verify_terrain`: did dropped probes rest on the surface everywhere?"""

    ok: bool
    tile_posts: int
    tiles: int
    probes: int
    failures: list[str]

    def describe(self) -> dict:
        return {"verified": self.ok, "tile_posts": self.tile_posts, "tiles": self.tiles,
                "probes": self.probes, "failures": self.failures[:5]}


@dataclass
class Pose:
    """Vehicle pose in the local ENU frame. (x, y, z) is the ground reference point under the body."""

    x: float
    y: float
    z: float
    q: tuple[float, float, float, float]   # (qx, qy, qz, qw)
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    wz: float = 0.0


def euler_from_quat(q) -> tuple[float, float, float]:
    """Quaternion (qx, qy, qz, qw) -> (roll, pitch, yaw) in radians, intrinsic Z-Y-X convention."""
    x, y, z, w = (float(c) for c in q)
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return roll, pitch, yaw


def quat_from_euler(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    """(roll, pitch, yaw) radians, Z-Y-X -> quaternion (qx, qy, qz, qw)."""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy)


def _rotate(q, v) -> np.ndarray:
    """Rotate vector v by quaternion q = (qx, qy, qz, qw)."""
    u = np.array(q[:3], dtype=np.float64)
    w = float(q[3])
    v = np.asarray(v, dtype=np.float64)
    return v + 2.0 * np.cross(u, np.cross(u, v) + w * v)


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def wheel_commands(v: float, omega: float, positions, steerable, radius: float,
                   prev_steer=None, steer_limit: float = 1.6) -> list[tuple[float, float]]:
    """Instantaneous-centre-of-rotation (ICR) steering for a rigid body moving at (v, omega).

    For a wheel at body position (xi, yi) the contact velocity is `vx = v - omega*yi`, `vy = omega*xi`.
    Steerable wheels point along that velocity (angle `atan2(vy, vx)`, flipped by pi to stay within
    +/-90 deg with the spin reversed) and spin at `hypot(vx, vy)/r`. Fixed wheels spin at `vx/r`.
    Returns `[(steer_rad, spin_rad_s), ...]` in the order of `positions`.
    """
    out: list[tuple[float, float]] = []
    for k, ((xi, yi), can_steer) in enumerate(zip(positions, steerable)):
        vx = v - omega * yi
        vy = omega * xi
        if not can_steer:
            out.append((0.0, vx / radius))
            continue
        speed = math.hypot(vx, vy)
        if speed < 1e-6:  # standing still: keep the wheel where it is
            keep = float(prev_steer[k]) if prev_steer is not None else 0.0
            out.append((keep, 0.0))
            continue
        angle = math.atan2(vy, vx)
        spin = speed / radius
        if angle > math.pi / 2:
            angle -= math.pi
            spin = -spin
        elif angle < -math.pi / 2:
            angle += math.pi
            spin = -spin
        out.append((max(-steer_limit, min(steer_limit, angle)), spin))
    return out


# --------------------------------------------------------------------------------------------- world
class PhysicsWorld:
    """One PyBullet `DIRECT` client per session, configured entirely from physics.yaml."""

    def __init__(self, cfg: dict) -> None:
        if not HAVE_PYBULLET:
            raise RuntimeError("pybullet is not installed")
        self.cfg = cfg
        self.client: int = p.connect(p.DIRECT)
        self.timestep_s = float(cfg["timestep_s"])
        col = cfg.get("collision", {})
        p.setGravity(0.0, 0.0, -float(cfg["gravity_m_s2"]), physicsClientId=self.client)
        p.setTimeStep(self.timestep_s, physicsClientId=self.client)
        p.setPhysicsEngineParameter(numSolverIterations=int(col.get("solver_iterations", 50)),
                                    physicsClientId=self.client)
        self.terrain_bodies: list[int] = []
        self.tile_posts = HEIGHTFIELD_TILE_POSTS
        self.grid = None
        self._z_range = (0.0, 0.0)

    def contact_params(self) -> dict:
        col = self.cfg.get("collision", {})
        return {
            "lateralFriction": float(col.get("lateral_friction", 0.8)),
            "restitution": float(col.get("restitution", 0.05)),
            "rollingFriction": float(col.get("rolling_friction", 0.002)),
            "spinningFriction": float(col.get("spinning_friction", 0.002)),
        }

    def set_terrain(self, grid, tile_posts: int = HEIGHTFIELD_TILE_POSTS) -> list[int]:
        """Build the heightfield collider from exactly the grid the browser renders.

        On some PyBullet builds (seen on macOS) a single heightfield silently stops colliding above
        512 x 512 posts, and a drive corridor can be up to 2200 posts per side, so the grid is split
        into tiles of at most `tile_posts` per side. Neighbouring tiles share their edge posts, so
        the surface has no seams and every post keeps its exact height. Use `build_verified_terrain`
        to also prove the result collides on this machine.
        """
        tile_posts = max(2, int(tile_posts))
        for body in self.terrain_bodies:
            p.removeBody(body, physicsClientId=self.client)
        self.terrain_bodies = []
        ny, nx = grid.h.shape
        step = tile_posts - 1
        for j0 in range(0, max(ny - 1, 1), step):
            for i0 in range(0, max(nx - 1, 1), step):
                self.terrain_bodies.append(self._add_tile(
                    grid, j0, i0, grid.h[j0:j0 + tile_posts, i0:i0 + tile_posts]))
        self.grid = grid
        self.tile_posts = tile_posts
        self._z_range = (float(grid.h.min()), float(grid.h.max()))
        return self.terrain_bodies

    # -- collider self-test
    def _probe_points(self, grid, tile_posts: int, n_random: int, seed: int) -> list[tuple[float, float]]:
        """Random points plus points on every tile seam and at every tile corner (the risky spots)."""
        rng = np.random.default_rng(seed)
        ny, nx = grid.h.shape
        inset_x, inset_y = 2.0 * grid.dx, 2.0 * grid.dy
        x_lo, x_hi = grid.x0 + inset_x, grid.x0 + (nx - 1) * grid.dx - inset_x
        y_lo, y_hi = grid.y0 + inset_y, grid.y0 + (ny - 1) * grid.dy - inset_y
        if x_hi <= x_lo or y_hi <= y_lo:
            return [(grid.x0 + 0.5 * (nx - 1) * grid.dx, grid.y0 + 0.5 * (ny - 1) * grid.dy)]
        pts = list(zip(rng.uniform(x_lo, x_hi, n_random), rng.uniform(y_lo, y_hi, n_random)))
        pts += [(x_lo, y_lo), (x_hi, y_lo), (x_lo, y_hi), (x_hi, y_hi)]
        step = tile_posts - 1
        seams_x = [grid.x0 + k * step * grid.dx for k in range(1, (nx - 1) // step + 1) if k * step < nx - 1]
        seams_y = [grid.y0 + k * step * grid.dy for k in range(1, (ny - 1) // step + 1) if k * step < ny - 1]
        for sx in seams_x:
            pts += [(sx, y) for y in rng.uniform(y_lo, y_hi, 3)]
        for sy in seams_y:
            pts += [(x, sy) for x in rng.uniform(x_lo, x_hi, 3)]
        off = 0.3 * min(grid.dx, grid.dy)
        for sx in seams_x:
            for sy in seams_y:
                pts += [(sx, sy), (sx + off, sy + off), (sx - off, sy - off)]
        return pts

    def verify_terrain(self, n_random: int = PROBE_RANDOM, seed: int = 0) -> "ColliderCheck":
        """Drop probe spheres through the real pipeline (broadphase + contacts) and check they rest.

        A ray test alone is not enough: the failure seen on macOS let rays hit the heightfield while
        contacts silently missed it. Probes only have to not end up *below* the rendered surface; one
        resting on an obstacle above it is fine. Steps the world, so call it before spawning.
        """
        grid = self.grid
        if grid is None or not self.terrain_bodies:
            return ColliderCheck(False, self.tile_posts, len(self.terrain_bodies), 0, ["no terrain"])
        pts = self._probe_points(grid, self.tile_posts, n_random, seed)
        cid = self.client
        sphere = p.createCollisionShape(p.GEOM_SPHERE, radius=PROBE_RADIUS_M, physicsClientId=cid)
        probes, failures = [], []
        for x, y in pts:
            ground = float(grid.height_at(x, y))
            ray = self.ground_height_at(x, y)
            if ray is None or abs(ray - ground) > 0.15:
                failures.append(f"ray at ({x:.1f}, {y:.1f}): {ray} vs grid {ground:.2f}")
            body = p.createMultiBody(1.0, sphere, -1, [x, y, ground + PROBE_RADIUS_M + PROBE_DROP_M],
                                     physicsClientId=cid)
            probes.append((body, x, y, ground))
        try:
            for _ in range(int(math.ceil(PROBE_SETTLE_S / self.timestep_s))):
                p.stepSimulation(physicsClientId=cid)
            for body, x, y, ground in probes:
                z = p.getBasePositionAndOrientation(body, physicsClientId=cid)[0][2] - PROBE_RADIUS_M
                if not math.isfinite(z) or z < ground - PROBE_SINK_TOL_M:
                    failures.append(f"probe at ({x:.1f}, {y:.1f}) fell to {z - ground:+.2f} m")
        finally:
            for body, *_ in probes:
                p.removeBody(body, physicsClientId=cid)
        return ColliderCheck(not failures, self.tile_posts, len(self.terrain_bodies), len(probes), failures)

    def build_verified_terrain(self, grid, sizes=HEIGHTFIELD_TILE_FALLBACKS) -> "ColliderCheck":
        """Build tiles, self-test, and retry with smaller tiles until the collider holds everywhere."""
        check = None
        for size in sizes:
            self.set_terrain(grid, size)
            check = self.verify_terrain()
            if check.ok:
                if size != sizes[0]:
                    log.warning("terrain collider holds only with %d-post tiles on this PyBullet build", size)
                return check
            log.warning("terrain collider with %d-post tiles failed its self-test: %s",
                        size, "; ".join(check.failures[:3]))
        return check

    def _add_tile(self, grid, j0: int, i0: int, h: np.ndarray) -> int:
        tny, tnx = h.shape
        shape = p.createCollisionShape(
            p.GEOM_HEIGHTFIELD,
            meshScale=[grid.dx, grid.dy, 1.0],
            heightfieldData=h.astype(np.float32).ravel().tolist(),
            numHeightfieldRows=tnx,
            numHeightfieldColumns=tny,
            physicsClientId=self.client,
        )
        # Bullet centres a heightfield on its x/y extent and on its height range.
        cx = grid.x0 + (i0 + 0.5 * (tnx - 1)) * grid.dx
        cy = grid.y0 + (j0 + 0.5 * (tny - 1)) * grid.dy
        cz = 0.5 * (float(h.min()) + float(h.max()))
        body = p.createMultiBody(0, shape, -1, [cx, cy, cz], [0, 0, 0, 1], physicsClientId=self.client)
        p.changeDynamics(body, -1, physicsClientId=self.client, **self.contact_params())
        return body

    def ground_height_at(self, x: float, y: float) -> float | None:
        """Height of the collider under (x, y) by ray cast, or None outside it.

        A vertical ray exactly on the shared edge of two tiles can slip through the zero-width
        crack between their heightfields (depends on floating-point rounding of the seam position).
        Bodies of finite size cannot, so on a miss the ray is re-cast 1 cm to each side.
        """
        if not self.terrain_bodies:
            return None
        lo, hi = self._z_range
        for dx, dy in ((0.0, 0.0), (0.01, 0.0), (-0.01, 0.0), (0.0, 0.01), (0.0, -0.01)):
            hit = p.rayTest([x + dx, y + dy, hi + 50.0], [x + dx, y + dy, lo - 50.0],
                            physicsClientId=self.client)[0]
            if hit[0] in self.terrain_bodies:
                return float(hit[3][2])
        return None

    def step(self) -> None:
        p.stepSimulation(physicsClientId=self.client)

    def close(self) -> None:
        if self.client is not None and p.isConnected(self.client):
            p.disconnect(self.client)
        self.client = None


# --------------------------------------------------------------------------------------------- rovers
class _RoverGeometry:
    """Shared rover.yaml geometry for both models."""

    def __init__(self, rcfg: dict) -> None:
        d = rcfg["dynamics"]
        self.cfg = rcfg
        self.length, self.width, self.height = (float(v) for v in d["chassis_size_m"])
        self.clearance = float(d["chassis_clearance_m"])
        self.r = float(d["wheel_radius_m"])
        self.positions = [tuple(map(float, xy)) for xy in d["wheel_positions_m"]]
        self.steerable = [bool(s) for s in d["steerable"]]
        self.steer_limit = float(d.get("steer_limit_rad", 1.6))
        # Chassis centre height above the wheel-contact plane.
        self.base_h = self.clearance + 0.5 * self.height


class WheeledRover(_RoverGeometry):
    """Six-wheel, four-corner-steered PyBullet multibody."""

    name = "wheeled"

    def __init__(self, world: PhysicsWorld, rcfg: dict) -> None:
        super().__init__(rcfg)
        self.world = world
        d = rcfg["dynamics"]
        cid = world.client
        self.wheel_torque = float(d["wheel_max_torque_nm"])
        self.steer_torque = float(d["steer_max_torque_nm"])
        atm = world.cfg.get("atmosphere", {})
        self._drag_k = 0.5 * float(atm.get("air_density_kg_m3", 0.0)) \
            * float(atm.get("drag_coefficient", 1.0)) * float(d.get("frontal_area_m2", 0.0))

        wheel_mass = float(d["wheel_mass_kg"])
        n_steer = sum(self.steerable)
        chassis_mass = float(d["mass_kg"]) - 6 * wheel_mass - n_steer * STEER_LINK_MASS_KG

        box = p.createCollisionShape(p.GEOM_BOX, halfExtents=[self.length / 2, self.width / 2,
                                                              self.height / 2], physicsClientId=cid)
        sphere = p.createCollisionShape(p.GEOM_SPHERE, radius=self.r, physicsClientId=cid)
        z_wheel = self.r - self.base_h  # wheel centre relative to the chassis centre

        masses, shapes, pos, parents, jtypes, axes = [], [], [], [], [], []
        self.steer_joint: list[int | None] = []
        self.wheel_joint: list[int] = []
        for (xi, yi), can_steer in zip(self.positions, self.steerable):
            if can_steer:
                masses.append(STEER_LINK_MASS_KG); shapes.append(-1); pos.append([xi, yi, z_wheel])
                parents.append(0); jtypes.append(p.JOINT_REVOLUTE); axes.append([0, 0, 1])
                steer_idx = len(masses) - 1
                self.steer_joint.append(steer_idx)
                masses.append(wheel_mass); shapes.append(sphere); pos.append([0, 0, 0])
                parents.append(steer_idx + 1); jtypes.append(p.JOINT_REVOLUTE); axes.append([0, 1, 0])
            else:
                self.steer_joint.append(None)
                masses.append(wheel_mass); shapes.append(sphere); pos.append([xi, yi, z_wheel])
                parents.append(0); jtypes.append(p.JOINT_REVOLUTE); axes.append([0, 1, 0])
            self.wheel_joint.append(len(masses) - 1)

        n = len(masses)
        self.body: int = p.createMultiBody(
            baseMass=chassis_mass, baseCollisionShapeIndex=box, baseVisualShapeIndex=-1,
            basePosition=[0, 0, 10], baseOrientation=[0, 0, 0, 1],
            linkMasses=masses, linkCollisionShapeIndices=shapes, linkVisualShapeIndices=[-1] * n,
            linkPositions=pos, linkOrientations=[[0, 0, 0, 1]] * n,
            linkInertialFramePositions=[[0, 0, 0]] * n,
            linkInertialFrameOrientations=[[0, 0, 0, 1]] * n,
            linkParentIndices=parents, linkJointTypes=jtypes, linkJointAxis=axes,
            physicsClientId=cid,
        )
        contact = world.contact_params()
        p.changeDynamics(self.body, -1, physicsClientId=cid, **contact)
        for j in range(n):
            p.changeDynamics(self.body, j, linearDamping=0.0, angularDamping=0.0,
                             physicsClientId=cid, **contact)
        for j in self.steer_joint:
            if j is not None:
                p.changeDynamics(self.body, j, localInertiaDiagonal=[STEER_LINK_INERTIA] * 3,
                                 physicsClientId=cid)
        # Release PyBullet's default velocity motors so only our commands drive the joints.
        p.setJointMotorControlArray(self.body, list(range(n)), p.VELOCITY_CONTROL,
                                    forces=[0.0] * n, physicsClientId=cid)
        self._n_links = n
        self._steer_cmd = [0.0] * 6
        self._parked = False
        self.park()

    # -- lifecycle
    def _set_collisions(self, enabled: bool) -> None:
        group, mask = (1, -1) if enabled else (0, 0)
        for link in range(-1, self._n_links):
            p.setCollisionFilterGroupMask(self.body, link, group, mask,
                                          physicsClientId=self.world.client)

    def park(self) -> None:
        """Hide the body (no collisions, no ray hits) while the kinematic model is active."""
        self._set_collisions(False)
        self._parked = True
        self.hold_parked()

    def hold_parked(self) -> None:
        if self._parked:
            p.resetBasePositionAndOrientation(self.body, [0, 0, -1.0e4], [0, 0, 0, 1],
                                              physicsClientId=self.world.client)
            p.resetBaseVelocity(self.body, [0, 0, 0], [0, 0, 0], physicsClientId=self.world.client)

    def spawn(self, x: float, y: float, yaw: float, drop: float = SPAWN_DROP_M) -> None:
        cid = self.world.client
        ground = self.world.ground_height_at(x, y)
        if ground is None:
            ground = float(self.world.grid.height_at(x, y)) if self.world.grid is not None else 0.0
        p.resetBasePositionAndOrientation(self.body, [x, y, ground + self.base_h + drop],
                                          quat_from_euler(0.0, 0.0, yaw), physicsClientId=cid)
        p.resetBaseVelocity(self.body, [0, 0, 0], [0, 0, 0], physicsClientId=cid)
        for j in range(self._n_links):
            p.resetJointState(self.body, j, 0.0, 0.0, physicsClientId=cid)
        self._steer_cmd = [0.0] * 6
        self._parked = False
        self._set_collisions(True)

    # -- control
    def _steer_angles(self) -> list[float]:
        cid = self.world.client
        return [p.getJointState(self.body, j, physicsClientId=cid)[0] if j is not None else 0.0
                for j in self.steer_joint]

    def apply(self, v: float, omega: float) -> None:
        """Set joint motors for (v, omega) and add aerodynamic drag for the next substep."""
        cid = self.world.client
        cmds = wheel_commands(v, omega, self.positions, self.steerable, self.r,
                              self._steer_cmd, self.steer_limit)
        actual = self._steer_angles()
        for k, (angle, spin) in enumerate(cmds):
            self._steer_cmd[k] = angle
            sj = self.steer_joint[k]
            if sj is not None:
                p.setJointMotorControl2(self.body, sj, p.POSITION_CONTROL, targetPosition=angle,
                                        force=self.steer_torque, maxVelocity=STEER_MAX_RATE_RAD_S,
                                        physicsClientId=cid)
            # Do not drive a wheel hard while it still points the wrong way.
            scale = max(0.0, math.cos(angle - actual[k]))
            p.setJointMotorControl2(self.body, self.wheel_joint[k], p.VELOCITY_CONTROL,
                                    targetVelocity=spin * scale, force=self.wheel_torque,
                                    physicsClientId=cid)
        if self._drag_k > 0.0:
            lin, _ = p.getBaseVelocity(self.body, physicsClientId=cid)
            vel = np.asarray(lin)
            force = -self._drag_k * float(np.linalg.norm(vel)) * vel
            pos, _ = p.getBasePositionAndOrientation(self.body, physicsClientId=cid)
            p.applyExternalForce(self.body, -1, force.tolist(), pos, p.WORLD_FRAME,
                                 physicsClientId=cid)

    def step(self, dt: float, v: float, omega: float) -> None:
        self.apply(v, omega)
        self.world.step()

    # -- state
    def pose(self) -> Pose:
        cid = self.world.client
        pos, orn = p.getBasePositionAndOrientation(self.body, physicsClientId=cid)
        lin, ang = p.getBaseVelocity(self.body, physicsClientId=cid)
        ref = np.asarray(pos) + _rotate(orn, [0.0, 0.0, -self.base_h])
        return Pose(float(ref[0]), float(ref[1]), float(ref[2]), tuple(float(c) for c in orn),
                    float(lin[0]), float(lin[1]), float(lin[2]), float(ang[2]))

    def wheels(self) -> list[dict]:
        cid = self.world.client
        steer = self._steer_angles()
        return [{"steer": float(steer[k]),
                 "spin": float(p.getJointState(self.body, self.wheel_joint[k],
                                               physicsClientId=cid)[0])}
                for k in range(len(self.positions))]


class KinematicRover(_RoverGeometry):
    """Terrain-following integrator: (v, omega) with a first-order lag, attitude from the grid."""

    name = "kinematic"

    def __init__(self, rcfg: dict) -> None:
        super().__init__(rcfg)
        self.grid = None
        self.x = self.y = self.yaw = 0.0
        self.v = self.w = 0.0
        self._steer = [0.0] * 6
        self._spin = [0.0] * 6

    def set_grid(self, grid) -> None:
        self.grid = grid

    def _h(self, x, y) -> float:
        return float(self.grid.height_at(x, y)) if self.grid is not None else 0.0

    def spawn(self, x: float, y: float, yaw: float, drop: float = 0.0, v: float = 0.0,
              w: float = 0.0) -> None:
        self.x, self.y, self.yaw = float(x), float(y), float(yaw)
        self.v, self.w = float(v), float(w)
        self._steer = [0.0] * 6
        self._spin = [0.0] * 6

    def step(self, dt: float, v_cmd: float, w_cmd: float) -> None:
        a = 1.0 - math.exp(-dt / KINEMATIC_LAG_S)
        self.v += (v_cmd - self.v) * a
        self.w += (w_cmd - self.w) * a
        self.yaw = _wrap_pi(self.yaw + self.w * dt)
        self.x += self.v * math.cos(self.yaw) * dt
        self.y += self.v * math.sin(self.yaw) * dt
        cmds = wheel_commands(self.v, self.w, self.positions, self.steerable, self.r,
                              self._steer, self.steer_limit)
        for k, (angle, spin) in enumerate(cmds):
            self._steer[k] = angle
            self._spin[k] = (self._spin[k] + spin * dt) % (2.0 * math.pi)

    def _attitude(self) -> tuple[float, float, float]:
        """(z, pitch, roll) from terrain samples at +/- half-length and +/- half-width."""
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        hl, hw = 0.5 * self.length, 0.5 * self.width
        zf = self._h(self.x + hl * c, self.y + hl * s)
        zb = self._h(self.x - hl * c, self.y - hl * s)
        zl = self._h(self.x - hw * s, self.y + hw * c)
        zr = self._h(self.x + hw * s, self.y - hw * c)
        pitch = -math.atan2(zf - zb, 2.0 * hl)   # nose up is negative pitch about +y
        roll = math.atan2(zl - zr, 2.0 * hw)     # left side up is positive roll about +x
        return self._h(self.x, self.y), pitch, roll

    def pose(self) -> Pose:
        z, pitch, roll = self._attitude()
        q = quat_from_euler(roll, pitch, self.yaw)
        return Pose(self.x, self.y, z, q, self.v * math.cos(self.yaw), self.v * math.sin(self.yaw),
                    0.0, self.w)

    def wheels(self) -> list[dict]:
        return [{"steer": float(a), "spin": float(s)} for a, s in zip(self._steer, self._spin)]


# --------------------------------------------------------------------------------------------- router
class RoverModelRouter:
    """Holds both rover models, routes (v, omega) to the active one and owns the step accumulator."""

    def __init__(self, physics_cfg: dict, rover_cfg: dict, astronaut_cfg: dict | None = None) -> None:
        self.cfg = physics_cfg
        self.rover_cfg = rover_cfg
        self.timestep_s = float(physics_cfg["timestep_s"])
        self.max_substeps = int(physics_cfg["max_substeps"])
        self.max_substeps_cap = int(physics_cfg["max_substeps_cap"])

        self.world: PhysicsWorld | None = None
        self.wheeled: WheeledRover | None = None
        if HAVE_PYBULLET and physics_cfg.get("engine", "pybullet") == "pybullet":
            self.world = PhysicsWorld(physics_cfg)
            self.wheeled = WheeledRover(self.world, rover_cfg)
        elif physics_cfg.get("engine", "pybullet") == "pybullet":
            log.warning("pybullet unavailable: falling back to the kinematic rover model")
        self.kinematic = KinematicRover(rover_cfg)
        # Third model: a suited astronaut on foot (sim/server/walker.py, sim/configs/astronaut.yaml).
        from sim.server.walker import AstronautWalker   # late import: walker.py imports Pose from here
        if astronaut_cfg is None:
            import yaml
            from pathlib import Path
            acfg_path = Path(__file__).resolve().parents[1] / "configs" / "astronaut.yaml"
            astronaut_cfg = yaml.safe_load(acfg_path.read_text()) if acfg_path.exists() else None
        self.astronaut = AstronautWalker(astronaut_cfg) if astronaut_cfg else None

        self.available: list[str] = ((["wheeled"] if self.wheeled else []) + ["kinematic"]
                                     + (["astronaut"] if self.astronaut else []))
        wanted = physics_cfg.get("model", "wheeled")
        self.active = self._model(wanted) if wanted in self.available else self.kinematic
        self._preferred = self.active.name     # what the config / user asked for

        self.sim_time = 0.0
        self.grid = None
        self.collider: ColliderCheck | None = None
        self.degraded: str | None = None       # why the wheeled model is off for this mission
        self._events: list[dict] = []          # physics_event messages for the server to broadcast
        self._last_good: tuple[float, float, float] | None = None   # (x, y, yaw) of the wheeled rover
        self._acc = 0.0
        self._v = 0.0
        self._w = 0.0
        self._spawned = False

    def _model(self, name: str):
        return {"wheeled": self.wheeled, "astronaut": self.astronaut}.get(name) or self.kinematic

    def _event(self, event: str, detail: str) -> None:
        log.warning("physics event %s: %s", event, detail)
        self._events.append({"type": "physics_event", "event": event, "detail": detail})

    def pop_events(self) -> list[dict]:
        """Physics events since the last call (collider self-test failure, fall-through recovery)."""
        events, self._events = self._events, []
        return events

    # -- setup
    def set_terrain(self, grid) -> None:
        """Install a mission's terrain. With PyBullet, the collider is built and self-tested first;
        if no tile size holds on this machine, the mission runs on the kinematic model instead."""
        self.grid = grid
        self.kinematic.set_grid(grid)
        if self.astronaut is not None:
            self.astronaut.set_grid(grid)
        self._spawned = False
        self._last_good = None
        self.degraded = None
        if self.world is None or self.wheeled is None:
            return
        self.wheeled.park()                    # out of the way while the probes are dropped
        self.collider = self.world.build_verified_terrain(grid)
        # Kinematic and astronaut follow the rendered grid, so they never depend on the collider.
        grid_models = ["kinematic"] + (["astronaut"] if self.astronaut is not None else [])
        if self.collider.ok:
            self.available = ["wheeled"] + grid_models
            self.active = self._model(self._preferred)
            return
        self.available = grid_models
        self.active = self._model(self._preferred) if self._preferred in grid_models else self.kinematic
        self.degraded = "terrain collider failed its self-test on this PyBullet build"
        self._event("collider_unverified",
                    f"{self.degraded} ({'; '.join(self.collider.failures[:2])}); "
                    "driving with the kinematic model")

    def spawn(self, x: float, y: float, yaw: float) -> None:
        if self.wheeled is not None and self.active is not self.wheeled:
            self.wheeled.park()
        self.active.spawn(x, y, yaw, drop=SPAWN_DROP_M if self.active is self.wheeled else 0.0)
        self._acc = 0.0
        self._v = self._w = 0.0
        self._spawned = True

    def set_model(self, name: str) -> bool:
        if name not in self.available:
            return False
        self._preferred = name
        target = self._model(name)
        if target is self.active:
            return True
        if not self._spawned:
            self.active = target
            return True
        pose = self.active.pose()
        _, _, yaw = euler_from_quat(pose.q)
        speed = math.hypot(pose.vx, pose.vy)
        if target is self.wheeled:
            target.spawn(pose.x, pose.y, yaw, drop=SWITCH_DROP_M)
        else:
            target.spawn(pose.x, pose.y, yaw, v=speed, w=pose.wz)
            if self.wheeled is not None:
                self.wheeled.park()
        self.active = target
        self._acc = 0.0
        return True

    def set_inputs(self, v: float, omega: float) -> None:
        self._v, self._w = float(v), float(omega)

    # -- stepping
    def update(self, elapsed_s: float, warp: float = 1.0) -> int:
        """Advance by `elapsed_s * warp` of sim time in fixed steps. Returns the substeps taken."""
        if self.wheeled is not None and self.active is not self.wheeled:
            self.wheeled.hold_parked()
        if not self._spawned or self.grid is None:
            return 0
        dt = self.timestep_s
        self._acc += max(0.0, float(elapsed_s)) * max(0.0, float(warp))
        cap = int(min(self.max_substeps * max(warp, 1.0), self.max_substeps_cap))
        n = int(self._acc / dt + 1e-9)
        if n > cap:
            n = cap
            self._acc = 0.0          # drop the backlog instead of spiralling
        else:
            self._acc -= n * dt
        for _ in range(n):
            self.active.step(dt, self._v, self._w)
        self.sim_time += n * dt
        if n and self.active is self.wheeled:
            self._watch_fall_through()
        return n

    def _watch_fall_through(self) -> None:
        """A wheeled rover below the rendered surface fell through the collider: recover it.

        Physically impossible otherwise (craters are carved into the grid itself), so this only
        fires on a collider failure the self-test missed. The rover is put back at its last good
        position on the kinematic model, which follows the rendered grid and cannot fall.
        """
        pose = self.wheeled.pose()
        ground = float(self.grid.height_at(pose.x, pose.y))
        if math.isfinite(pose.z) and pose.z >= ground - FALL_THROUGH_M:
            self._last_good = (pose.x, pose.y, euler_from_quat(pose.q)[2])
            return
        x, y, yaw = self._last_good or (pose.x, pose.y, euler_from_quat(pose.q)[2])
        self.kinematic.spawn(x, y, yaw)
        self.wheeled.park()
        self.active = self.kinematic
        self._acc = 0.0
        self.degraded = "wheeled rover fell through the terrain collider"
        self._event("fell_through",
                    f"wheeled rover was {ground - pose.z:.1f} m below the terrain at "
                    f"({pose.x:.1f}, {pose.y:.1f}); recovered at its last good position on the "
                    "kinematic model")

    # -- state
    def pose(self) -> Pose:
        return self.active.pose()

    def wheels(self) -> list[dict]:
        return self.active.wheels()

    def describe(self) -> dict:
        return {
            "model": self.active.name,
            "engine": "pybullet" if self.world is not None else "kinematic",
            "timestep_s": self.timestep_s,
            "max_substeps": self.max_substeps,
            "max_substeps_cap": self.max_substeps_cap,
            "gravity_m_s2": float(self.cfg["gravity_m_s2"]),
            "collider": self.collider.describe() if self.collider else None,
            "degraded": self.degraded,
        }

    def close(self) -> None:
        if self.world is not None:
            self.world.close()
