"""Module C - least-cost route planning on the 5 m terrain grid.

Cost of crossing a cell (per metre of ground distance):
    c = 1 + beta * (hazard/100)^p + gamma * effort(slope)
    effort(slope) = (slope/15 deg)^2  -> a dimensionless *relative* effort index, NOT energy.
Cells with slope >= NO_GO are impassable.

Three profiles give the alternatives:
    direct   beta=0.3, gamma=0.0  - closest to shortest distance (still avoids no-go cells)
    balanced beta=3.0, gamma=0.5
    cautious beta=12,  gamma=1.0, p=2 - strongly avoids elevated/high hazard

Least-cost path: skimage.graph.MCP_Geometric (Dijkstra on an 8-connected grid; diagonal
steps cost sqrt(dx^2+dy^2)). `sampling` = ground cell size (dy, dx), so the cos(lat)
shrink of east-west cells is honoured.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from skimage.graph import MCP_Geometric

from .terrain import Terrain, haversine_m

NO_GO_SLOPE_DEG = 30.0
MARGIN_CELLS = 120  # search window padding around the endpoints (600 m)
ASSUMED_PACE_KMH = 1.0  # ASSUMPTION for an indicative EVA walking time; adjustable in UI


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    beta: float
    gamma: float
    p: float
    color: str


PROFILES = [
    Profile("direct", "Direct", 0.3, 0.0, 1.0, "#60a5fa"),
    Profile("balanced", "Balanced", 3.0, 0.5, 1.0, "#34d399"),
    Profile("cautious", "Cautious", 12.0, 1.0, 2.0, "#fbbf24"),
]


class RouteError(ValueError):
    pass


def _window(t: Terrain, a: tuple[int, int], b: tuple[int, int]):
    r0 = max(min(a[0], b[0]) - MARGIN_CELLS, 0)
    r1 = min(max(a[0], b[0]) + MARGIN_CELLS + 1, t.shape[0])
    c0 = max(min(a[1], b[1]) - MARGIN_CELLS, 0)
    c1 = min(max(a[1], b[1]) + MARGIN_CELLS + 1, t.shape[1])
    return slice(r0, r1), slice(c0, c1)


def _metrics(t: Terrain, path: list[tuple[int, int]], samples: list[dict]) -> dict:
    rows = np.array([p[0] for p in path])
    cols = np.array([p[1] for p in path])
    coords = [t.rc_to_lonlat(r, c) for r, c in path]
    seg = [haversine_m(*coords[i], *coords[i + 1]) for i in range(len(coords) - 1)]
    length = float(sum(seg))
    hz = t.hazard[rows, cols]
    sl = t.slope[rows, cols]
    z = t.elevation[rows, cols]
    dz = np.diff(z)
    w = np.array(seg + [0.0])  # distance weights per vertex
    wsum = w.sum() or 1.0
    classes = {"Low": (0, 20), "Moderate": (20, 40), "Elevated": (40, 60), "High": (60, 80), "Severe": (80, 101)}
    near = []
    for s in samples:
        lon, lat = s["geometry"]["coordinates"]
        d = min(haversine_m(lon, lat, c[0], c[1]) for c in coords[::3])
        if d <= 150:
            near.append({"name": s["properties"]["name"], "feature": s["properties"]["feature"], "distance_m": round(d)})
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    step = max(1, len(path) // 240)
    idx = list(range(0, len(path), step)) + ([len(path) - 1] if (len(path) - 1) % step else [])
    profile = [{"d": round(float(cum[i]), 1), "z": round(float(z[i]), 1), "slope": round(float(sl[i]), 1),
                "hazard": round(float(hz[i]), 1), "lon": round(coords[i][0], 6), "lat": round(coords[i][1], 6)}
               for i in idx]
    return {
        "profile": profile,
        "coordinates": [[round(lon, 7), round(lat, 7)] for lon, lat in coords],
        "length_m": round(length, 1),
        "hazard_mean": round(float((hz * w).sum() / wsum), 1),
        "hazard_max": round(float(hz.max()), 1),
        "slope_mean_deg": round(float((sl * w).sum() / wsum), 1),
        "slope_max_deg": round(float(sl.max()), 1),
        "ascent_m": round(float(dz[dz > 0].sum()), 1),
        "descent_m": round(float(-dz[dz < 0].sum()), 1),
        "effort_index": round(float((w * (1 + (sl / 15.0) ** 2)).sum() / 1000), 2),
        "hazard_class_fraction": {k: round(float(w[(hz >= lo) & (hz < hi)].sum() / wsum), 3)
                                  for k, (lo, hi) in classes.items()},
        "time_h_at_assumed_pace": round(length / 1000 / ASSUMED_PACE_KMH, 2),
        "samples_within_150m": near,
    }


def plan_routes(t: Terrain, start: tuple[float, float], end: tuple[float, float], samples: list[dict]) -> dict:
    a, b = t.lonlat_to_rc(*start), t.lonlat_to_rc(*end)
    for name, p in (("start", a), ("destination", b)):
        if not t.inside(*p):
            raise RouteError(f"{name} is outside the analysed terrain grid")
        if t.slope[p] >= NO_GO_SLOPE_DEG:
            raise RouteError(f"{name} lies on terrain steeper than {NO_GO_SLOPE_DEG} deg (no-go)")

    rs, cs = _window(t, a, b)
    hz, sl = t.hazard[rs, cs], t.slope[rs, cs]
    la, lb = (a[0] - rs.start, a[1] - cs.start), (b[0] - rs.start, b[1] - cs.start)
    mid_row = (rs.start + rs.stop) / 2
    sampling = (t.res_m, t.ground_dx(mid_row))

    routes = []
    for pr in PROFILES:
        cost = 1 + pr.beta * (hz / 100.0) ** pr.p + pr.gamma * (sl / 15.0) ** 2
        cost[(sl >= NO_GO_SLOPE_DEG) | ~np.isfinite(cost)] = np.inf
        mcp = MCP_Geometric(cost, sampling=sampling)
        cum, _ = mcp.find_costs([la], [lb])
        if not np.isfinite(cum[lb]):
            raise RouteError("no passable route found inside the search window")
        path = [(r + rs.start, c + cs.start) for r, c in mcp.traceback(lb)]
        m = _metrics(t, path, samples)
        routes.append({"id": pr.id, "label": pr.label, "color": pr.color,
                       "weights": {"beta_hazard": pr.beta, "gamma_slope_effort": pr.gamma, "hazard_exponent": pr.p},
                       **m})

    rec, why = _recommend(routes)
    straight = haversine_m(*start, *end)
    return {"straight_line_m": round(straight, 1), "routes": routes, "recommended": rec, "rationale": why,
            "assumptions": {
                "pace_kmh": ASSUMED_PACE_KMH, "no_go_slope_deg": NO_GO_SLOPE_DEG,
                "note": "Hazard is a prototype model (slope + roughness only). Times assume a constant walking "
                        "pace and ignore stops, suit limits and terrain-dependent speed. Not a safety certification."}}


def _recommend(routes: list[dict]) -> tuple[str, list[str]]:
    """Pick the route with the best trade-off: each metric normalised to the best alternative."""
    best_len = min(r["length_m"] for r in routes)
    best_hz = max(min(r["hazard_mean"] for r in routes), 1.0)

    def score(r):
        high = r["hazard_class_fraction"]["High"] + r["hazard_class_fraction"]["Severe"]
        return 0.4 * r["length_m"] / best_len + 0.4 * r["hazard_mean"] / best_hz + 2.0 * high + \
            0.2 * r["slope_max_deg"] / 30.0

    rec = min(routes, key=score)
    direct = next(r for r in routes if r["id"] == "direct")
    why = [f"Lowest combined score of distance, mean hazard, exposure to High/Severe cells and max slope."]
    if rec["id"] != "direct":
        dl = (rec["length_m"] / direct["length_m"] - 1) * 100
        dh = (1 - rec["hazard_mean"] / max(direct["hazard_mean"], 1e-6)) * 100
        why.append(f"{dl:+.0f}% distance vs Direct, {dh:.0f}% lower mean hazard.")
        why.append(f"Max slope {rec['slope_max_deg']}° vs {direct['slope_max_deg']}° on Direct.")
    else:
        why.append("Detours do not reduce hazard enough to justify the extra distance.")
    return rec["id"], why
