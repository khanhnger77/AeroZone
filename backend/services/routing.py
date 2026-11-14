"""Least-cost EVA route planning over the 5 m terrain grid (contract C4).

Why least-cost and not shortest-path: a crew on foot pays for hazard and for
climbing, not just distance. Each cell costs, per ground metre,

    1 + β·(hazard/100)^p + γ·(slope/15°)²

and three weightings (direct, balanced, cautious) give the planner a spread of
trade-offs to choose from. Cells with slope ≥ 30° (the no-go threshold) or a
non-finite cost are impassable.

The solver is ``skimage.graph.MCP_Geometric`` (8-connected), which integrates
cost × step length. Its ``sampling`` is ``(res, ground_dx(mid_row))`` so that
diagonal and east-west steps are measured in ground metres; the cos(lat)
variation across the 600 m-padded search window is small enough to ignore.

Reported metrics are recomputed from Mars-haversine segment lengths along the
returned vertices, so they do not depend on the solver's internal units.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
from skimage.graph import MCP_Geometric

from .terrain import ROOT, Terrain, haversine_m

# ---------------------------------------------------------------- configuration
NO_GO_SLOPE_DEG = 30.0
SLOPE_REF_DEG = 15.0
WINDOW_PAD_CELLS = 120          # 600 m at 5 m posts
PACE_KMH = 1.0
SAMPLE_RADIUS_M = 150.0
SAMPLE_VERTEX_STEP = 3          # check every third route vertex
MAX_PROFILE_POINTS = 240

ASSUMPTIONS_NOTE = (
    "Prototype planner: costs come from the 5 m prototype hazard model and slope; "
    "obstacle and crater factors are not modelled. Time assumes a constant walking pace."
)


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    color: str
    beta_hazard: float
    gamma_slope_effort: float
    hazard_exponent: float


PROFILES = (
    Profile("direct", "Direct", "#60a5fa", 0.3, 0.0, 1.0),
    Profile("balanced", "Balanced", "#34d399", 3.0, 0.5, 1.0),
    Profile("cautious", "Cautious", "#fbbf24", 12.0, 1.0, 2.0),
)

# Used only if data/metadata/hazard_model.json (M1) is missing.
DEFAULT_HAZARD_CLASSES = (
    {"label": "Low", "min": 0, "max": 20},
    {"label": "Moderate", "min": 20, "max": 40},
    {"label": "Elevated", "min": 40, "max": 60},
    {"label": "High", "min": 60, "max": 80},
    {"label": "Severe", "min": 80, "max": 100},
)


class RouteError(ValueError):
    """A request that cannot be routed; the message is the 422 ``detail``."""


@lru_cache(maxsize=1)
def hazard_classes() -> tuple[dict[str, Any], ...]:
    """The hazard class bins M1 used, so class fractions match the map legend."""
    path = ROOT / "data/metadata/hazard_model.json"
    try:
        return tuple(json.loads(path.read_text())["classes"])
    except (OSError, KeyError, ValueError):
        return DEFAULT_HAZARD_CLASSES


# ---------------------------------------------------------------- solver
def _check_endpoint(t: Terrain, lon: float, lat: float, name: str) -> tuple[int, int]:
    r, c = t.lonlat_to_rc(lon, lat)
    if not t.inside(r, c):
        raise RouteError(f"{name} is outside the terrain grid")
    s = t.slope[r, c]
    if not np.isfinite(s) or s >= NO_GO_SLOPE_DEG:
        raise RouteError(f"{name} is on slope ≥ {NO_GO_SLOPE_DEG:g}° (or has no data)")
    return r, c


def _cost_surface(hazard: np.ndarray, slope: np.ndarray, p: Profile) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        cost = (1.0 + p.beta_hazard * (hazard / 100.0) ** p.hazard_exponent
                + p.gamma_slope_effort * (slope / SLOPE_REF_DEG) ** 2)
        cost[~(slope < NO_GO_SLOPE_DEG)] = np.inf     # also catches NaN slope
    cost[~np.isfinite(cost)] = np.inf
    return cost


def _solve(t: Terrain, start_rc, end_rc, p: Profile) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = t.shape
    r0 = max(0, min(start_rc[0], end_rc[0]) - WINDOW_PAD_CELLS)
    r1 = min(rows, max(start_rc[0], end_rc[0]) + WINDOW_PAD_CELLS + 1)
    c0 = max(0, min(start_rc[1], end_rc[1]) - WINDOW_PAD_CELLS)
    c1 = min(cols, max(start_rc[1], end_rc[1]) + WINDOW_PAD_CELLS + 1)

    cost = _cost_surface(t.hazard[r0:r1, c0:c1], t.slope[r0:r1, c0:c1], p)
    mid_row = (start_rc[0] + end_rc[0]) / 2
    mcp = MCP_Geometric(cost, fully_connected=True, sampling=(t.res_m, t.ground_dx(mid_row)))
    s = (start_rc[0] - r0, start_rc[1] - c0)
    e = (end_rc[0] - r0, end_rc[1] - c0)
    cum, _ = mcp.find_costs([s], [e])
    if not np.isfinite(cum[e]):
        raise RouteError("no passable route in the search window")
    path = np.asarray(mcp.traceback(e))
    return path[:, 0] + r0, path[:, 1] + c0


# ---------------------------------------------------------------- metrics
def _seg_mean(v: np.ndarray) -> np.ndarray:
    return (v[:-1] + v[1:]) / 2 if len(v) > 1 else v


def _weighted_mean(v: np.ndarray, d: np.ndarray) -> float:
    ok = np.isfinite(v)
    w = d[ok].sum()
    return float((v[ok] * d[ok]).sum() / w) if w > 0 else float(np.nanmean(v) if ok.any() else 0.0)


def _r(x: float, nd: int = 2) -> float | None:
    return round(float(x), nd) if np.isfinite(x) else None


def _nearby_samples(lon: np.ndarray, lat: np.ndarray,
                    samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    idx = np.unique(np.r_[np.arange(0, len(lon), SAMPLE_VERTEX_STEP), len(lon) - 1])
    vlon, vlat = lon[idx], lat[idx]
    out = []
    for f in samples:
        slon, slat = f["geometry"]["coordinates"][:2]
        dist = float(haversine_m(vlon, vlat, slon, slat).min())
        if dist <= SAMPLE_RADIUS_M:
            props = f.get("properties", {})
            out.append({"name": props.get("name"), "feature": props.get("feature"),
                        "distance_m": round(dist, 1)})
    return sorted(out, key=lambda s: s["distance_m"])


def _route_metrics(t: Terrain, rows: np.ndarray, cols: np.ndarray, p: Profile,
                   samples: list[dict[str, Any]]) -> dict[str, Any]:
    lon, lat = t.rc_to_lonlat(rows, cols)
    z = t.elevation[rows, cols].astype(float)
    slope = t.slope[rows, cols].astype(float)
    hz = t.hazard[rows, cols].astype(float)

    d = haversine_m(lon[:-1], lat[:-1], lon[1:], lat[1:]) if len(lon) > 1 else np.zeros(1)
    length = float(d.sum())
    seg_hz, seg_slope = _seg_mean(hz), _seg_mean(slope)
    dz = np.diff(z) if len(z) > 1 else np.zeros(1)
    dz = np.where(np.isfinite(dz), dz, 0.0)

    classes = hazard_classes()
    frac = {}
    for i, cl in enumerate(classes):
        hi_ok = seg_hz <= cl["max"] if i == len(classes) - 1 else seg_hz < cl["max"]
        m = (seg_hz >= cl["min"]) & hi_ok
        frac[cl["label"]] = round(float(d[m].sum() / length), 4) if length > 0 else 0.0

    cum = np.r_[0.0, np.cumsum(d)] if len(lon) > 1 else np.zeros(1)
    pidx = np.unique(np.linspace(0, len(lon) - 1, min(len(lon), MAX_PROFILE_POINTS)).round().astype(int))
    profile = [{"d": round(float(cum[i]), 1), "z": _r(z[i]), "slope": _r(slope[i]),
                "hazard": _r(hz[i]), "lon": round(float(lon[i]), 7), "lat": round(float(lat[i]), 7)}
               for i in pidx]

    return {
        "id": p.id,
        "label": p.label,
        "color": p.color,
        "weights": {"beta_hazard": p.beta_hazard, "gamma_slope_effort": p.gamma_slope_effort,
                    "hazard_exponent": p.hazard_exponent},
        "coordinates": [[round(float(a), 7), round(float(b), 7)] for a, b in zip(lon, lat)],
        "profile": profile,
        "length_m": round(length, 1),
        "hazard_mean": round(_weighted_mean(seg_hz, d), 2),
        "hazard_max": _r(np.nanmax(hz)),
        "slope_mean_deg": round(_weighted_mean(seg_slope, d), 2),
        "slope_max_deg": _r(np.nanmax(slope)),
        "ascent_m": round(float(dz[dz > 0].sum()), 1),
        "descent_m": round(float(-dz[dz < 0].sum()), 1),
        "effort_index": round(float(np.nansum(d * (1 + (seg_slope / SLOPE_REF_DEG) ** 2)) / 1000), 3),
        "hazard_class_fraction": frac,
        "time_h_at_assumed_pace": round(length / 1000 / PACE_KMH, 2),
        "samples_within_150m": _nearby_samples(lon, lat, samples),
    }


# ---------------------------------------------------------------- recommendation
def _severe_frac(route: dict[str, Any]) -> float:
    f = route["hazard_class_fraction"]
    return f.get("High", 0.0) + f.get("Severe", 0.0)


def _recommend(routes: list[dict[str, Any]]) -> tuple[str, list[str]]:
    len_best = max(min(r["length_m"] for r in routes), 1e-9)
    hz_best = max(min(r["hazard_mean"] for r in routes), 1.0)

    def score(r: dict[str, Any]) -> float:
        return (0.4 * r["length_m"] / len_best + 0.4 * r["hazard_mean"] / hz_best
                + 2.0 * _severe_frac(r) + 0.2 * (r["slope_max_deg"] or 0.0) / NO_GO_SLOPE_DEG)

    best = min(routes, key=score)
    direct = next(r for r in routes if r["id"] == "direct")

    if best["id"] == "direct":
        rationale = [
            f"Direct is the shortest option at {direct['length_m']:.0f} m "
            f"(mean hazard {direct['hazard_mean']:.1f}, max slope {direct['slope_max_deg']:.1f}°).",
            "Detours do not reduce hazard enough to justify the extra distance.",
        ]
    else:
        extra = 100 * (best["length_m"] / max(direct["length_m"], 1e-9) - 1)
        rationale = [
            f"{best['label']} is {extra:.0f}% longer than Direct "
            f"({best['length_m']:.0f} m vs {direct['length_m']:.0f} m).",
        ]
        if best["hazard_mean"] < direct["hazard_mean"]:
            drop = 100 * (1 - best["hazard_mean"] / max(direct["hazard_mean"], 1e-9))
            rationale.append(
                f"Lower mean hazard: {best['hazard_mean']:.1f} vs {direct['hazard_mean']:.1f} "
                f"for Direct ({drop:.0f}% lower).")
        b_hs, d_hs = 100 * _severe_frac(best), 100 * _severe_frac(direct)
        if b_hs < d_hs:
            rationale.append(f"High/Severe terrain: {b_hs:.0f}% of the route vs {d_hs:.0f}% for Direct.")
        word = "lower" if best["slope_max_deg"] < direct["slope_max_deg"] else "not lower"
        rationale.append(
            f"Max slope {best['slope_max_deg']:.1f}° vs {direct['slope_max_deg']:.1f}° "
            f"for Direct ({word}).")
    return best["id"], rationale


# ---------------------------------------------------------------- entry point
def plan_routes(t: Terrain, start: tuple[float, float], end: tuple[float, float],
                samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the C4 ``RouteResponse`` for ``start``/``end`` given as ``(lon, lat)``."""
    s_rc = _check_endpoint(t, *start, "start")
    e_rc = _check_endpoint(t, *end, "destination")

    routes = [_route_metrics(t, *_solve(t, s_rc, e_rc, p), p, samples) for p in PROFILES]
    recommended, rationale = _recommend(routes)
    return {
        "straight_line_m": round(float(haversine_m(*start, *end)), 1),
        "routes": routes,
        "recommended": recommended,
        "rationale": rationale,
        "assumptions": {"pace_kmh": PACE_KMH, "no_go_slope_deg": NO_GO_SLOPE_DEG,
                        "note": ASSUMPTIONS_NOTE},
    }
