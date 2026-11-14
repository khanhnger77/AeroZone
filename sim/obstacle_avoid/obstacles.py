"""Random boulders placed on the planned route (M5).

Why: the 5 m orbital DTM cannot resolve rover-scale rocks, so the demo injects a few large,
clearly-artificial boulders *on* the route to exercise the lidar and the local navigator. They
are sparse (one site every ~250 m) so that the drive still follows the orbital plan most of the
time. Every boulder is a convex hull, so the same mesh is used for the PyBullet collider, the
Three.js view (RockDesc, C5) and the lidar surface samples, and all three agree.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import ConvexHull

from .geometry import Route


def site_positions(length: float, cfg: dict, rng: np.random.Generator) -> list[float]:
    """Arc lengths of obstacle sites along a route of ``length`` metres.

    The first site is at ``keep_clear_start + U(0.2, 1)·min(mean, usable)``, then sites are spaced by
    ``U(min, 2·mean − min)`` (mean spacing = ``mean``) until ``length − keep_clear_end``. A route long
    enough to have a usable stretch always gets at least one site (its middle).
    """
    start = float(cfg["keep_clear_start_m"])
    end = float(length) - float(cfg["keep_clear_end_m"])
    usable = end - start
    if usable <= 0:
        return []
    mean = float(cfg["mean_spacing_m"])
    mn = float(cfg["min_spacing_m"])
    s = start + rng.uniform(0.2, 1.0) * min(mean, usable)
    out: list[float] = []
    while s <= end:
        out.append(float(s))
        s += rng.uniform(mn, 2 * mean - mn)
    if not out:
        out.append(0.5 * (start + end))
    return out


def make_rock_shape(r: float, h: float, rng: np.random.Generator, n: int = 28):
    """A boulder: convex hull of ``n`` points on a squashed ellipsoid, base at z = 0.

    Points lie mostly on the upper hemisphere (the buried part is small). Faces are re-oriented so
    every normal points away from the centroid, which Three.js needs for correct lighting.
    Returns ``(vertices (m, 3), faces (k, 3))``; vertices are hull points only.
    """
    u = rng.uniform(-0.25, 1.0, n)            # z on the unit sphere: mostly upper hemisphere
    phi = rng.uniform(0, 2 * np.pi, n)
    phi[:6] = np.linspace(0, 2 * np.pi, 6, endpoint=False)  # guarantee a full ring of footprint points
    u[:6] = 0.0
    u[6] = 1.0                                  # a top
    rho = np.sqrt(np.clip(1 - u * u, 0, None))
    sx, sy = rng.uniform(0.85, 1.05, 2)
    pts = np.stack([r * sx * rho * np.cos(phi), r * sy * rho * np.sin(phi), h * u], axis=1)
    pts[:, 2] -= pts[:, 2].min()
    pts[:, 2] *= h / max(pts[:, 2].max(), 1e-9)
    hull = ConvexHull(pts)
    used = np.unique(hull.simplices)
    remap = -np.ones(len(pts), dtype=np.int64)
    remap[used] = np.arange(len(used))
    v = pts[used]
    f = remap[hull.simplices]
    c = v.mean(0)
    a, b, cc = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    nrm = np.cross(b - a, cc - a)
    flip = (nrm * ((a + b + cc) / 3 - c)).sum(1) < 0
    f[flip] = f[flip][:, [0, 2, 1]]
    return v, f


def sample_surface(v: np.ndarray, f: np.ndarray, density: float, rng=None, min_n: int = 200) -> np.ndarray:
    """Area-weighted uniform samples on a triangle mesh (``density`` per m², at least ``min_n``)."""
    rng = rng or np.random.default_rng(0)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    n = max(min_n, int(np.ceil(area.sum() * density)))
    tri = rng.choice(len(f), size=n, p=area / area.sum())
    r1 = np.sqrt(rng.random(n))
    r2 = rng.random(n)
    return (1 - r1)[:, None] * a[tri] + (r1 * (1 - r2))[:, None] * b[tri] + (r1 * r2)[:, None] * c[tri]


@dataclass
class Rock:
    id: str
    x: float
    y: float
    z: float                    # base height (world, local ENU)
    yaw: float
    radius: float               # max horizontal extent of the hull from (x, y)
    height: float
    s: float                    # route arc length of the site
    v_local: np.ndarray         # hull vertices, rock frame (base at z = 0)
    f: np.ndarray               # triangle indices (outward)
    samples: np.ndarray = field(repr=False, default=None)  # world surface samples (lidar)
    body: int | None = None     # PyBullet body id once spawned

    @property
    def v_world(self) -> np.ndarray:
        c, s = np.cos(self.yaw), np.sin(self.yaw)
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        return self.v_local @ R.T + np.array([self.x, self.y, self.z])

    def describe(self, frame=None) -> dict:
        """C5 RockDesc."""
        lon = lat = None
        if frame is not None:
            lon, lat = frame.to_lonlat(self.x, self.y)
        return {
            "id": self.id, "x": round(self.x, 3), "y": round(self.y, 3),
            "radius": round(self.radius, 3), "height": round(self.height, 3), "s": round(self.s, 2),
            "v": [round(float(t), 3) for t in self.v_world.ravel()],
            "f": [int(t) for t in self.f.ravel()],
            "lon": lon, "lat": lat,
        }


def _inside_keep_away(x: float, y: float, keep_away) -> bool:
    for kx, ky, kr in keep_away or ():
        if (x - kx) ** 2 + (y - ky) ** 2 < kr * kr:
            return True
    return False


def generate(path_xy, height_at, cfg: dict, seed: int, keep_away=None) -> list[Rock]:
    """Boulders on the route: 1–2 per site, lateral jitter, partly buried; skips ``keep_away`` discs.

    ``height_at(x, y) -> z`` is the terrain sampler; ``keep_away`` is an iterable of ``(x, y, r)``.
    """
    rng = np.random.default_rng(seed)
    route = Route(path_xy)
    rocks: list[Rock] = []
    lo, hi = cfg["rocks_per_site"]
    for s in site_positions(route.length, cfg, rng):
        n = int(rng.integers(lo, hi + 1))
        r1 = rng.uniform(*cfg["radius_m"])
        specs = [(s, r1)]
        if n >= 2:
            r2 = rng.uniform(*cfg["radius_m"])
            specs.append((s + (r1 + r2) * rng.uniform(*cfg["pair_spacing"]), r2))
        for si, r in specs:
            si = float(min(si, route.length))
            p = route.point(si)
            hd = float(route.heading(si))
            off = rng.uniform(-cfg["lateral_jitter_m"], cfg["lateral_jitter_m"])
            x = float(p[0] - np.sin(hd) * off)
            y = float(p[1] + np.cos(hd) * off)
            h = r * rng.uniform(*cfg["height_ratio"])
            v, f = make_rock_shape(r, h, rng)
            yaw = float(rng.uniform(0, 2 * np.pi))
            sub = int(rng.integers(2 ** 31))      # drawn before any skip: skipping never shifts the stream
            if _inside_keep_away(x, y, keep_away):
                continue
            z = float(height_at(x, y)) - cfg["bury_fraction"] * h
            rock = Rock(id=f"r{len(rocks)}", x=x, y=y, z=z, yaw=yaw,
                        radius=float(np.hypot(v[:, 0], v[:, 1]).max()), height=float(h),
                        s=si, v_local=v, f=f)
            rock.samples = sample_surface(rock.v_world, f, cfg["surface_density_per_m2"], np.random.default_rng(sub))
            rocks.append(rock)
    return rocks


def spawn(rocks: list[Rock], world) -> None:
    """Add each rock as a static convex ``GEOM_MESH`` collider to the PyBullet world (if any)."""
    if world is None or getattr(world, "client", None) is None:
        return
    try:
        import pybullet as p
    except ImportError:
        return
    cid = world.client
    coll = (getattr(world, "cfg", None) or {}).get("collision", {})
    for rock in rocks:
        shape = p.createCollisionShape(p.GEOM_MESH, vertices=rock.v_local.tolist(), physicsClientId=cid)
        body = p.createMultiBody(0, shape, -1, [rock.x, rock.y, rock.z],
                                 p.getQuaternionFromEuler([0, 0, rock.yaw]), physicsClientId=cid)
        p.changeDynamics(body, -1, lateralFriction=float(coll.get("lateral_friction", 0.8)),
                         restitution=float(coll.get("restitution", 0.05)), physicsClientId=cid)
        rock.body = body


def despawn(rocks: list[Rock], world) -> None:
    if world is None or getattr(world, "client", None) is None:
        return
    try:
        import pybullet as p
    except ImportError:
        return
    for rock in rocks:
        if rock.body is not None:
            try:
                p.removeBody(rock.body, physicsClientId=world.client)
            except Exception:  # world already reset by M4
                pass
            rock.body = None
