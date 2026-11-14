"""Module A - prototype terrain hazard model.

Hazard = 0.4*SlopeRisk + 0.3*RoughnessRisk + 0.2*ObstacleRisk + 0.1*CraterRisk   (each 0-100)

PROTOTYPE ASSUMPTIONS - not NASA-certified astronaut safety thresholds.
  * SlopeRisk: piecewise-linear 0 deg->0, 5->10, 15->50, 25->90, >=30->100.
    (Breakpoints are prototype choices loosely informed by Apollo-era EVA/LRV experience that
    slopes >~15-20 deg strongly increase walking effort and fall risk.)
  * RoughnessRisk: 0 m->0, 0.10->25, 0.25->60, >=0.5 m->100 (RMS of 1 m detrended relief).
  * ObstacleRisk, CraterRisk: NO DATASET INTEGRATED YET -> marked "unavailable", NOT zero.
    The score is re-normalised over the available weights and the coverage is reported.
  * No-go mask: slope >= 30 deg (treated as impassable by the route planner).

Outputs: data/analysis/{slope_risk,roughness_risk,hazard}_5m.tif, data/metadata/hazard_model.json
"""
from __future__ import annotations

import json

import numpy as np
import rasterio

from config import ANALYSIS, METADATA, PROCESSED

SLOPE_STOPS = ([0, 5, 15, 25, 30], [0, 10, 50, 90, 100])
ROUGH_STOPS = ([0, 0.10, 0.25, 0.5], [0, 25, 60, 100])
WEIGHTS = {"slope": 0.4, "roughness": 0.3, "obstacle": 0.2, "crater": 0.1}
AVAILABLE = {"slope": True, "roughness": True, "obstacle": False, "crater": False}
NO_GO_SLOPE_DEG = 30.0
CLASSES = [(0, 20, "Low"), (20, 40, "Moderate"), (40, 60, "Elevated"), (60, 80, "High"), (80, 101, "Severe")]


def read(name):
    with rasterio.open(PROCESSED / f"{name}.tif") as ds:
        return ds.read(1), ds.profile


def main() -> None:
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    slope, profile = read("slope_5m")
    rough, _ = read("roughness_5m")

    risk = {
        "slope": np.interp(slope, *SLOPE_STOPS),
        "roughness": np.interp(rough, *ROUGH_STOPS),
    }
    w_avail = sum(w for k, w in WEIGHTS.items() if AVAILABLE[k])
    hazard = sum(WEIGHTS[k] * risk[k] for k in risk) / w_avail
    hazard[np.isnan(slope)] = np.nan

    for name, arr in [("slope_risk_5m", risk["slope"]), ("roughness_risk_5m", risk["roughness"]),
                      ("hazard_5m", hazard)]:
        with rasterio.open(ANALYSIS / f"{name}.tif", "w", **profile) as dst:
            dst.write(arr.astype("float32"), 1)

    v = hazard[np.isfinite(hazard)]
    class_frac = {label: float(((v >= lo) & (v < hi)).mean()) for lo, hi, label in CLASSES}
    model = {
        "formula": "0.4*Slope + 0.3*Roughness + 0.2*Obstacle + 0.1*Crater (0-100 each)",
        "weights": WEIGHTS,
        "factor_available": AVAILABLE,
        "model_weight_coverage": w_avail,
        "renormalised": True,
        "slope_stops": SLOPE_STOPS, "roughness_stops": ROUGH_STOPS,
        "no_go_slope_deg": NO_GO_SLOPE_DEG,
        "classes": [{"min": lo, "max": min(hi, 100), "label": l} for lo, hi, l in CLASSES],
        "class_fraction": class_frac,
        "no_go_fraction": float((slope >= NO_GO_SLOPE_DEG).mean()),
        "disclaimer": "Prototype weights and thresholds. Not a NASA-certified safety assessment. "
                      "A low score does not mean a location is safe for human exploration.",
    }
    (METADATA / "hazard_model.json").write_text(json.dumps(model, indent=2))
    print(json.dumps({"class_fraction": class_frac, "no_go_fraction": model["no_go_fraction"]}, indent=1))


if __name__ == "__main__":
    main()
