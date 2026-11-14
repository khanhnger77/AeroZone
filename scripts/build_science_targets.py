"""Vector layers: Perseverance traverse, waypoints and sample sites (Module B inputs).

Sample list: NASA Mars 2020 "Mars Rock Samples" page (sample no., name, feature, type, sol
sealed). Sample LOCATIONS are derived here: the rover's last localized position on or before
the sealing sol (from the JPL MMGIS waypoint file). This is an approximation - the rover may
have moved between coring and sealing, and waypoints are end-of-drive fixes. Uncertainty is
reported per site as the sol gap.

Outputs: frontend/public/data/{traverse,waypoints,samples}.geojson
"""
from __future__ import annotations

import bisect
import json

from config import AOI, RAW, WEB

SAMPLES_SRC = "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples"
# (no, sample name, feature/target name, type, sol sealed, rock class from NASA page)
SAMPLES = [
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


def in_aoi(lon, lat):
    return AOI["lon_min"] <= lon <= AOI["lon_max"] and AOI["lat_min"] <= lat <= AOI["lat_max"]


def main() -> None:
    wp = json.loads((RAW / "perseverance" / "M20_waypoints.json").read_text())["features"]
    wp.sort(key=lambda f: f["properties"]["sol"])
    sols = [f["properties"]["sol"] for f in wp]

    keep = ("sol", "site", "drive", "lon", "lat", "elev_geoid", "dist_total_m", "Note")
    waypoints = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": f["geometry"] if f.get("geometry") else
         {"type": "Point", "coordinates": [f["properties"]["lon"], f["properties"]["lat"]]},
         "properties": {k: f["properties"].get(k) for k in keep}} for f in wp]}
    for f in waypoints["features"]:
        f["geometry"]["coordinates"] = f["geometry"]["coordinates"][:2]

    tr = json.loads((RAW / "perseverance" / "M20_traverse.json").read_text())
    for f in tr["features"]:
        f["geometry"]["coordinates"] = [c[:2] for c in f["geometry"]["coordinates"]]
        f["properties"] = {"sol": f["properties"].get("sol"), "length_m": f["properties"].get("length")}
    tr.pop("crs", None)

    samples = []
    for no, name, feature, typ, sol, rock in SAMPLES:
        i = bisect.bisect_right(sols, sol) - 1
        p = wp[i]["properties"]
        samples.append({"type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                        "properties": {
                            "sample_no": no, "name": name, "feature": feature, "type": typ,
                            "sol_sealed": sol, "rock_class": rock,
                            "location_method": "Rover waypoint on/before sealing sol (JPL MMGIS)",
                            "location_sol": p["sol"], "location_sol_gap": sol - p["sol"],
                            "elev_m": p["elev_geoid"], "in_aoi": in_aoi(p["lon"], p["lat"]),
                            "source": SAMPLES_SRC}})

    WEB.mkdir(parents=True, exist_ok=True)
    for name, fc in [("waypoints", waypoints), ("traverse", tr),
                     ("samples", {"type": "FeatureCollection", "features": samples})]:
        (WEB / f"{name}.geojson").write_text(json.dumps(fc, separators=(",", ":")))
        print(f"wrote {name}.geojson ({len(fc['features'])} features)")
    print("samples in AOI:", sum(s["properties"]["in_aoi"] for s in samples), "/", len(samples))


if __name__ == "__main__":
    main()
