"""Combine factor scores into the prototype hazard raster (C2) and document the model.

Hazard = 0.4*SlopeRisk + 0.3*RoughnessRisk + 0.2*Obstacle + 0.1*Crater (weights in hazard.yaml).

Why renormalise instead of zero-filling: in v1.0 the obstacle and crater factors do not exist. Treating
them as 0 would make every cell look safer than the evidence says; renormalising over the available
0.7 of weight keeps the 0-100 scale meaningful and the missing coverage is reported explicitly
(`model_weight_coverage`, `renormalised`). [v1.1] Factors listed in `enabled_factors` beyond slope and
roughness are read from the C2-ext rasters; a cell where such a factor is NaN is renormalised over the
factors present in that cell, and the share of those cells is `partial_factor_fraction`. With the
default config the output is byte-identical to v1.0.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import ANALYSIS, METADATA, PROCESSED, ROOT, info, load_yaml, read_grid_tif, warn, write_grid_tif, write_json  # noqa: E402

CORE_FACTORS = ("slope", "roughness")


def piecewise(x: np.ndarray, stops: list[list[float]]) -> np.ndarray:
    """Piecewise-linear score, clamped at both ends, NaN preserved."""
    xs, ys = np.asarray(stops[0], dtype=np.float64), np.asarray(stops[1], dtype=np.float64)
    out = np.interp(x.astype(np.float64), xs, ys).astype(np.float32)
    out[~np.isfinite(x)] = np.nan
    return out


def combine(factors: dict[str, np.ndarray], weights: dict[str, float]) -> tuple[np.ndarray, float]:
    """Weighted mean over the factors present per cell. Core factors must be present.

    Returns (hazard, partial_fraction). With only the core factors this reduces exactly to
    (0.4*S + 0.3*R) / 0.7, computed in float64 then cast, as in v1.0.
    """
    num = np.zeros(next(iter(factors.values())).shape, dtype=np.float64)
    den = np.zeros_like(num)
    core_ok = np.ones(num.shape, dtype=bool)
    partial = np.zeros(num.shape, dtype=bool)
    for name, f in factors.items():
        ok = np.isfinite(f)
        w = float(weights[name])
        num += np.where(ok, w * f.astype(np.float64), 0.0)
        den += np.where(ok, w, 0.0)
        if name in CORE_FACTORS:
            core_ok &= ok
        else:
            partial |= ~ok
    with np.errstate(invalid="ignore", divide="ignore"):
        hz = num / den
    hz[~core_ok] = np.nan
    valid = np.isfinite(hz)
    partial_fraction = float((partial & valid).sum() / max(int(valid.sum()), 1))
    return hz.astype(np.float32), partial_fraction


def main() -> int:
    cfg = load_yaml("hazard.yaml")
    weights: dict[str, float] = {k: float(v) for k, v in cfg["weights"].items()}
    enabled = list(cfg.get("enabled_factors") or list(CORE_FACTORS))
    for core in CORE_FACTORS:
        if core not in enabled:
            warn(f"hazard.yaml: core factor '{core}' cannot be disabled; using it anyway")
            enabled.insert(0, core)

    slope_p, rough_p = PROCESSED / "slope_5m.tif", PROCESSED / "roughness_5m.tif"
    if not (slope_p.exists() and rough_p.exists()):
        warn("slope/roughness rasters missing (run scripts/process_terrain.py)")
        return 1
    slope = read_grid_tif(slope_p)
    rough = read_grid_tif(rough_p)
    slope_risk = piecewise(slope, cfg["slope_stops"])
    rough_risk = piecewise(rough, cfg["roughness_stops"])
    write_grid_tif(ANALYSIS / "slope_risk_5m.tif", slope_risk)
    write_grid_tif(ANALYSIS / "roughness_risk_5m.tif", rough_risk)

    factors: dict[str, np.ndarray] = {"slope": slope_risk, "roughness": rough_risk}
    available = {name: name in CORE_FACTORS for name in weights}
    for name in enabled:
        if name in CORE_FACTORS:
            continue
        path = ROOT / cfg["factor_rasters"][name]
        if not path.exists():
            warn(f"factor '{name}' enabled but {path.relative_to(ROOT)} is missing (run pixi run data-env); skipped")
            continue
        factors[name] = read_grid_tif(path)
        available[name] = True

    hazard, partial_fraction = combine(factors, weights)
    write_grid_tif(ANALYSIS / "hazard_5m.tif", hazard)

    coverage = round(sum(w for k, w in weights.items() if available[k]), 6)
    valid = np.isfinite(hazard)
    n_valid = max(int(valid.sum()), 1)
    hv = hazard[valid]
    class_fraction = {}
    classes = cfg["classes"]
    for i, c in enumerate(classes):
        upper = hv <= c["max"] if i == len(classes) - 1 else hv < c["max"]
        class_fraction[c["label"]] = round(float(((hv >= c["min"]) & upper).sum() / n_valid), 6)
    sv = slope[np.isfinite(slope)]
    no_go = round(float((sv >= cfg["no_go_slope_deg"]).sum() / max(sv.size, 1)), 6)

    used = [k for k in weights if available[k]]
    formula = " + ".join(f"{weights[k]:g}*{k.capitalize()}{'Risk' if k in CORE_FACTORS else ''}" for k in weights)
    if coverage < 1.0:
        formula = (f"Hazard = ({' + '.join(f'{weights[k]:g}*{k.capitalize()}Risk' for k in used)}) / {coverage:g}"
                   f"  [full model: {formula}; renormalised over the available weight]")
    else:
        formula = f"Hazard = {formula}"

    model = {
        "formula": formula,
        "weights": weights,
        "factor_available": available,
        "model_weight_coverage": coverage,
        "renormalised": bool(coverage < 1.0),
        "slope_stops": cfg["slope_stops"],
        "roughness_stops": cfg["roughness_stops"],
        "no_go_slope_deg": cfg["no_go_slope_deg"],
        "classes": classes,
        "class_fraction": class_fraction,
        "no_go_fraction": no_go,
        "disclaimer": cfg["disclaimer"],
        # [v1.1, PROPOSED C2-ext] optional, additive keys
        "factor_quality": {k: (cfg["factor_quality"][k] if available[k] else "unavailable") for k in weights},
        "factor_sources": {k: cfg["factor_sources"][k] for k in weights if available[k]},
        "partial_factor_fraction": round(partial_fraction, 6),
    }
    write_json(METADATA / "hazard_model.json", model)
    info(f"wrote data/analysis/{{hazard,slope_risk,roughness_risk}}_5m.tif and hazard_model.json "
         f"(factors: {', '.join(used)}; coverage {coverage:g})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
