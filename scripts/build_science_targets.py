"""Snapshot the rover track and place the 25 NASA rock samples on it (C3 vector files).

Why the samples are placed this way: NASA publishes the sample list (name, feature, sol sealed) but
not coordinates. The rover's position when it sealed a tube is best approximated by the last MMGIS
localised waypoint on or before the sealing sol; `location_sol_gap` records how many sols separate
the two, which is an honest uncertainty indicator (0 = fix from that very sol).

Writes frontend/public/data/{traverse,waypoints,samples}.geojson as compact JSON.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PERSEVERANCE_DIR, ROOT, WEB, in_aoi, info, read_json, warn, write_json  # noqa: E402

SAMPLES_URL = "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples"
LOCATION_METHOD = "last rover fix on/before sealing sol"

# (sample no., name, feature, type, sol sealed, rock class) from the NASA Mars Rock Samples page.
SAMPLES: list[tuple[int, str, str, str, int, str]] = [
    (1, "Roubion", "Roubion", "Atmospheric", 164, "n/a (no rock retained)"),
    (2, "Montdenier", "Rochette", "Rock core", 194, "Igneous"),
    (3, "Montagnac", "Rochette", "Rock core", 196, "Igneous"),
    (4, "Salette", "Brac", "Rock core", 262, "Igneous"),
    (5, "Coulettes", "Brac", "Rock core", 271, "Igneous"),
    (6, "Robine", "Issole", "Rock core", 298, "Igneous"),
    (7, "Malay", "Issole", "Rock core", 337, "Igneous"),
    (8, "Ha'ahóni", "Sid", "Rock core", 371, "Igneous"),
    (9, "Atsá", "Sid", "Rock core", 377, "Igneous"),
    (10, "Swift Run", "Skinner Ridge", "Rock core", 490, "Sedimentary"),
    (11, "Skyland", "Skinner Ridge", "Rock core", 495, "Sedimentary"),
    (12, "Hazeltop", "Wildcat Ridge", "Rock core", 509, "Sedimentary"),
    (13, "Bearwallow", "Wildcat Ridge", "Rock core", 516, "Sedimentary"),
    (14, "Shuyak", "Amalik", "Rock core", 575, "Sedimentary"),
    (15, "Mageik", "Amalik", "Rock core", 619, "Sedimentary"),
    (16, "Kukaklek", "Hidden Harbor", "Rock core", 631, "Sedimentary"),
    (17, "Atmo Mountain", "Observation Mountain", "Regolith", 634, "Regolith"),
    (18, "Crosswind Lake", "Observation Mountain", "Regolith", 639, "Regolith"),
    (19, "Melyn", "Berea", "Rock core", 749, "Sedimentary"),
    (20, "Otis Peak", "Emerald Lake", "Rock core", 832, "Sedimentary"),
    (21, "Pilot Mountain", "Dream Lake", "Rock core", 913, "Sedimentary"),
    (22, "Pelican Point", "Hans Amundsen Memorial Workspace", "Rock core", 923, "Sedimentary"),
    (23, "Lefroy Bay", "Turquoise Bay", "Rock core", 949, "Sedimentary"),
    (24, "Comet Geyser", "Bunsen Peak", "Rock core", 1088, "Silica-cemented carbonate"),
    (25, "Sapphire Canyon", "Cheyava Falls", "Rock core", 1215, "Sedimentary (Bright Angel fm.)"),
]

WAYPOINT_KEYS = ("sol", "site", "drive", "lon", "lat", "elev_geoid", "dist_total_m", "Note")


def fc(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def point(lon: float, lat: float, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 8), round(lat, 8)]},
            "properties": props}


def build_traverse(raw: dict[str, Any]) -> dict[str, Any]:
    out = []
    for f in raw["features"]:
        coords = [[round(c[0], 7), round(c[1], 7)] for c in f["geometry"]["coordinates"]]
        dedup = [c for i, c in enumerate(coords) if i == 0 or c != coords[i - 1]]
        if len(dedup) < 2:
            dedup = coords[:2] if len(coords) >= 2 else coords * 2
        p = f["properties"]
        out.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": dedup},
                    "properties": {"sol": int(p["sol"]), "length_m": float(p.get("length", 0.0))}})
    return fc(out)


def build_waypoints(raw: dict[str, Any], meda: dict[int, dict[str, float]]) -> dict[str, Any]:
    out = []
    for f in raw["features"]:
        p = f["properties"]
        props = {k: p.get(k) for k in WAYPOINT_KEYS}
        if int(p["sol"]) in meda:
            props["meda"] = meda[int(p["sol"])]
        out.append(point(float(p["lon"]), float(p["lat"]), props))
    return fc(out)


def build_samples(raw: dict[str, Any]) -> dict[str, Any]:
    wps = sorted((f["properties"] for f in raw["features"]), key=lambda p: (p["sol"], p.get("site", 0), p.get("drive", 0)))
    out = []
    for no, name, feature, typ, sol, rock in SAMPLES:
        before = [p for p in wps if p["sol"] <= sol]
        if not before:
            warn(f"sample {no} {name}: no waypoint on/before sol {sol}")
            continue
        w = before[-1]
        lon, lat = float(w["lon"]), float(w["lat"])
        out.append(point(lon, lat, {
            "sample_no": no, "name": name, "feature": feature, "type": typ, "sol_sealed": sol,
            "rock_class": rock, "location_method": LOCATION_METHOD, "location_sol": int(w["sol"]),
            "location_sol_gap": int(sol - w["sol"]), "elev_m": float(w["elev_geoid"]),
            "in_aoi": in_aoi(lon, lat), "source": SAMPLES_URL,
        }))
    return fc(out)


def load_meda_summary() -> dict[int, dict[str, float]]:
    """[v1.1] Optional per-sol MEDA summary (C10) to attach to waypoints."""
    p = WEB / "climate" / "meda_sols.json"
    if not p.exists():
        return {}
    try:
        return {int(s["sol"]): {"t_air_mean_k": s["t_air_mean_k"], "p_mean_pa": s["p_mean_pa"]}
                for s in read_json(p)["sols"]}
    except (KeyError, ValueError, TypeError) as exc:
        warn(f"ignoring malformed {p.name}: {exc}")
        return {}


def main() -> int:
    wp_raw = read_json(PERSEVERANCE_DIR / "M20_waypoints.json")
    tr_raw = read_json(PERSEVERANCE_DIR / "M20_traverse.json")
    files = {
        "traverse.geojson": build_traverse(tr_raw),
        "waypoints.geojson": build_waypoints(wp_raw, load_meda_summary()),
        "samples.geojson": build_samples(wp_raw),
    }
    for name, obj in files.items():
        write_json(WEB / name, obj, compact=True)
        info(f"wrote {(WEB / name).relative_to(ROOT)} ({len(obj['features'])} features)")
    return 0 if len(files["samples.geojson"]["features"]) == len(SAMPLES) else 1


if __name__ == "__main__":
    sys.exit(main())
