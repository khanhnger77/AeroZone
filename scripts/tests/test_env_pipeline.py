"""M1 checks: contract shapes (C2, C2-ext, C3, C3-ext, C10) and the v1.0-compatibility guarantees.

Data-dependent tests skip when the pipeline has not been run, so `pytest scripts/tests` works on a
clean checkout; the unit tests of the maths always run.

Run: pixi run python -m pytest -q scripts/tests
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import config as cfg  # noqa: E402
from config import ANALYSIS, METADATA, PROCESSED, WEB, read_json  # noqa: E402

C2 = [PROCESSED / f"{n}_5m.tif" for n in ("elevation", "slope", "roughness", "hillshade")] + [
    ANALYSIS / f"{n}_5m.tif" for n in ("hazard", "slope_risk", "roughness_risk")]
C2_EXT = [PROCESSED / "geologic_units_5m.tif", PROCESSED / "thermal_inertia_5m.tif", PROCESSED / "terrain_class_5m.tif",
          ANALYSIS / "crater_risk_5m.tif", ANALYSIS / "obstacle_risk_5m.tif"]
V10_HAZARD_KEYS = ["formula", "weights", "factor_available", "model_weight_coverage", "renormalised", "slope_stops",
                   "roughness_stops", "no_go_slope_deg", "classes", "class_fraction", "no_go_fraction", "disclaimer"]


def need(*paths: Path) -> None:
    missing = [p for p in paths if not p.exists()]
    if missing:
        pytest.skip("not built: " + ", ".join(p.name for p in missing))


# ---------------------------------------------------------------- maths (always run)
def test_grid_matches_contract():
    assert (cfg.GRID_WIDTH, cfg.GRID_HEIGHT) == (2133, 1007)
    assert cfg.GRID_TRANSFORM_GDAL == (5.0, 0.0, 4581341.0, 0.0, -5.0, 1096879.0)
    (lat_s, lon_w), (lat_n, lon_e) = cfg.grid_bounds_latlon()
    # the grid is the AOI snapped inward/outward to whole metres: within one 5 m cell of C1's AOI
    tol = 5.0 / cfg.MARS_RADIUS_M * 180 / np.pi * 1.01
    assert abs(lon_w - cfg.AOI["lon_w"]) < tol and abs(lon_e - cfg.AOI["lon_e"]) < tol
    assert abs(lat_s - cfg.AOI["lat_s"]) < tol and abs(lat_n - cfg.AOI["lat_n"]) < tol


def test_coordinates_round_trip_and_haversine():
    x, y = cfg.deg_to_proj(77.38, 18.46)
    lon, lat = cfg.proj_to_deg(x, y)
    assert abs(lon - 77.38) < 1e-12 and abs(lat - 18.46) < 1e-12
    # one degree of latitude on the Mars sphere
    assert abs(cfg.haversine_m(0, 0, 0, 1) - cfg.MARS_RADIUS_M * np.pi / 180) < 1e-6
    assert cfg.normalise_lon(282.0) == pytest.approx(-78.0)


def test_piecewise_and_combine_default_is_v10_formula():
    from generate_hazard import combine, piecewise

    stops = [[0, 5, 15, 25, 30], [0, 10, 50, 90, 100]]
    s = piecewise(np.array([0, 2.5, 5, 20, 30, 45, np.nan], dtype=np.float32), stops)
    assert np.allclose(s[:6], [0, 5, 10, 70, 100, 100]) and np.isnan(s[6])
    rng = np.random.default_rng(0)
    S = rng.uniform(0, 100, (50, 60)).astype(np.float32)
    R = rng.uniform(0, 100, (50, 60)).astype(np.float32)
    S[0, 0] = np.nan
    hz, partial = combine({"slope": S, "roughness": R}, {"slope": 0.4, "roughness": 0.3, "obstacle": 0.2, "crater": 0.1})
    ref = ((0.4 * S.astype(np.float64) + 0.3 * R.astype(np.float64)) / (0.4 + 0.3)).astype(np.float32)
    assert np.isnan(hz[0, 0]) and partial == 0.0
    assert np.array_equal(hz[1:], ref[1:])


def test_combine_partial_factor_renormalises_per_cell():
    from generate_hazard import combine

    w = {"slope": 0.4, "roughness": 0.3, "obstacle": 0.2, "crater": 0.1}
    one = np.full((2, 2), 50.0, dtype=np.float32)
    obst = np.array([[100, np.nan], [100, 100]], dtype=np.float32)
    hz, partial = combine({"slope": one, "roughness": one, "obstacle": obst}, w)
    assert hz[0, 0] == pytest.approx((0.4 * 50 + 0.3 * 50 + 0.2 * 100) / 0.9)
    assert hz[0, 1] == pytest.approx(50.0)
    assert partial == pytest.approx(0.25)


def test_block_mode_majority():
    from process_geology import block_mode

    a = np.zeros((5, 10), dtype=np.int16)
    a[:, :5] = 2
    a[:2, :5] = 1
    a[:, 5:] = 0
    a[0, 5] = 3
    out = block_mode(a, 3)
    assert out[0, 0] == 2 and np.isnan(out[0, 1])


def test_crater_detection_on_synthetic_bowl():
    from process_geology import detect_tile

    yy, xx = np.mgrid[0:200, 0:200].astype(np.float64)
    z = 0.01 * xx  # tilted plane
    r = np.hypot(xx - 100, yy - 100)
    z -= np.where(r < 10, 2.0 * (1 - (r / 10) ** 2), 0.0)  # 20 m wide, 2 m deep bowl
    score, craters = detect_tile(z.astype(np.float32), {"min_depth_m": 0.5, "min_diameter_m": 5.0,
                                                        "max_diameter_m": 400.0, "min_circularity": 0.5})
    assert len(craters) == 1
    c = craters[0]
    assert abs(c["row"] - 100) < 1.5 and abs(c["col"] - 100) < 1.5
    assert 15 < c["diam_m"] < 22 and 1.5 < c["depth_m"] <= 2.0
    assert score[100, 100] == 100 and score[100, 100 + 25] == 0  # decays to 0 one radius outside


def test_crustal_field_dipole():
    """A pure axial dipole g10: |B| = |g10| (a/r)^3 at the equator and 2x that at the poles."""
    from fetch_space_weather import field_grid

    G = np.zeros((3, 3))
    H = np.zeros((3, 3))
    G[1, 0] = -100.0
    b = field_grid(G, H, 2, 3393.5, 3393.5, np.array([0.0, 89.999]), np.array([0.0, 90.0]))
    assert np.allclose(b[0], 100.0, rtol=1e-9)
    assert np.allclose(b[1], 200.0, rtol=1e-4)


def test_schmidt_legendre_matches_closed_forms():
    from fetch_space_weather import schmidt_legendre

    th = np.radians(np.array([10.0, 50.0, 120.0]))
    P, _ = schmidt_legendre(3, th)
    c, s = np.cos(th), np.sin(th)
    assert np.allclose(P[2, 0], 0.5 * (3 * c * c - 1))
    assert np.allclose(P[2, 1], np.sqrt(3) * c * s)
    assert np.allclose(P[2, 2], np.sqrt(3) / 2 * s * s)
    assert np.allclose(P[3, 3], np.sqrt(10) / 4 * s ** 3)


# ---------------------------------------------------------------- data contracts (skip if not built)
def _grid_ok(path: Path) -> None:
    import rasterio

    with rasterio.open(path) as s:
        assert (s.width, s.height, s.count) == (2133, 1007, 1), path.name
        assert s.dtypes[0] == "float32", path.name
        assert np.isnan(s.nodata), path.name
        assert tuple(s.transform)[:6] == (5.0, 0.0, 4581341.0, 0.0, -5.0, 1096879.0), path.name
        assert abs(s.crs.to_dict().get("R", s.crs.to_dict().get("a", 3396190)) - 3396190) < 1 or "3396190" in s.crs.to_wkt()


@pytest.mark.parametrize("path", C2, ids=lambda p: p.name)
def test_c2_rasters_on_grid(path: Path):
    need(path)
    _grid_ok(path)


@pytest.mark.parametrize("path", C2_EXT, ids=lambda p: p.name)
def test_c2_ext_rasters_on_grid(path: Path):
    need(path)
    _grid_ok(path)


def test_default_hazard_is_v10():
    """With the default hazard.yaml the hazard raster is byte-identical to the v1.0 formula's output."""
    from config import load_yaml, read_grid_tif, write_grid_tif

    hcfg = load_yaml("hazard.yaml")
    if set(hcfg["enabled_factors"]) != {"slope", "roughness"}:
        pytest.skip("hazard.yaml is not the v1.0 default")
    need(ANALYSIS / "hazard_5m.tif", ANALYSIS / "slope_risk_5m.tif", ANALYSIS / "roughness_risk_5m.tif",
         METADATA / "hazard_model.json")
    S = read_grid_tif(ANALYSIS / "slope_risk_5m.tif").astype(np.float64)
    R = read_grid_tif(ANALYSIS / "roughness_risk_5m.tif").astype(np.float64)
    ref = ((0.4 * S + 0.3 * R) / (0.4 + 0.3)).astype(np.float32)
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "hazard_5m.tif"
        write_grid_tif(p, ref)
        assert p.read_bytes() == (ANALYSIS / "hazard_5m.tif").read_bytes()

    model = read_json(METADATA / "hazard_model.json")
    for k in V10_HAZARD_KEYS:
        assert k in model, k
    assert model["weights"] == {"slope": 0.4, "roughness": 0.3, "obstacle": 0.2, "crater": 0.1}
    assert model["factor_available"] == {"slope": True, "roughness": True, "obstacle": False, "crater": False}
    assert model["model_weight_coverage"] == pytest.approx(0.7)
    assert model["renormalised"] is True
    assert model["slope_stops"] == [[0, 5, 15, 25, 30], [0, 10, 50, 90, 100]]
    assert model["roughness_stops"] == [[0, 0.1, 0.25, 0.5], [0, 25, 60, 100]]
    assert model["no_go_slope_deg"] == 30
    assert [c["label"] for c in model["classes"]] == ["Low", "Moderate", "Elevated", "High", "Severe"]


def test_layers_manifest():
    need(WEB / "layers.json")
    m = read_json(WEB / "layers.json")
    assert [lay["id"] for lay in m["raster_layers"]] == ["elevation", "slope", "roughness", "hazard"]
    keys = {"id", "name", "url", "bounds", "group", "category", "units", "source", "processing", "legend"}
    for lay in m["raster_layers"] + m.get("overlay_layers", []):
        assert keys <= set(lay), lay["id"]
        assert lay["url"] == f"data/layers/{lay['id']}.png"
        assert lay["category"] in ("observation", "derived", "model", "simulated")
        assert lay["legend"]["type"] in ("ramp", "classes")
        assert (WEB / "layers" / f"{lay['id']}.png").exists()
    assert set(m["raster_layers"][3]["model"]) >= set(V10_HAZARD_KEYS)
    allowed = {"geologic_units", "terrain_class", "thermal_inertia", "obstacle_risk", "crater_risk"}
    assert {lay["id"] for lay in m.get("overlay_layers", [])} <= allowed
    for lay in m.get("overlay_layers", []):
        if lay["id"] == "thermal_inertia":
            assert lay["stats"]["source_res_m"] == 100


def test_vector_files():
    need(WEB / "samples.geojson", WEB / "traverse.geojson", WEB / "waypoints.geojson")
    s = read_json(WEB / "samples.geojson")["features"]
    assert len(s) == 25
    assert set(s[0]["properties"]) == {"sample_no", "name", "feature", "type", "sol_sealed", "rock_class",
                                       "location_method", "location_sol", "location_sol_gap", "elev_m", "in_aoi", "source"}
    t = read_json(WEB / "traverse.geojson")["features"]
    assert len(t) == 559 and set(t[0]["properties"]) == {"sol", "length_m"}
    w = read_json(WEB / "waypoints.geojson")["features"]
    assert len(w) == 708
    base = {"sol", "site", "drive", "lon", "lat", "elev_geoid", "dist_total_m", "Note"}
    for f in w:
        assert base <= set(f["properties"]) <= base | {"meda"}


def _types(obj: dict, spec: dict) -> None:
    for k, t in spec.items():
        optional = k.endswith("?")
        k = k.rstrip("?")
        if k not in obj:
            assert optional, f"missing key {k}"
            continue
        assert isinstance(obj[k], t), f"{k}: {type(obj[k]).__name__}"


NUM = (int, float)


def test_c10_files():
    files = {
        "climate/mcd_jezero.json": {"category": str, "source": str, "mcd_version": str, "retrieved_utc": str,
                                    "location": dict, "local_time_h": NUM, "ls_deg": list, "scenarios": list,
                                    "series": dict, "disclaimer": str},
        "climate/meda_sols.json": {"category": str, "source": str, "pds_release": str, "retrieved_utc": str, "sols": list},
        "climate/dust_storm_stats.json": {"category": str, "source": str, "radius_km": NUM, "years": list,
                                          "by_ls_bin": list, "by_mars_year": list, "global_frequency_png?": str,
                                          "global_bounds?": list},
        "space_weather/donki_mars.json": {"category": str, "source": str, "retrieved_utc": str, "window": dict,
                                          "events": list, "disclaimer": str},
        "space_weather/crustal_field.json": {"category": str, "source": str, "model": str, "altitude_km": NUM,
                                             "aoi_b_nT": dict, "global_png?": str, "global_bounds?": list},
    }
    present = [k for k in files if (WEB / k).exists()]
    if not present:
        pytest.skip("no C10 files built")
    for name in present:
        obj = read_json(WEB / name)
        _types(obj, files[name])
        assert obj["category"] in ("observation", "derived", "model", "simulated")
        if name.endswith("mcd_jezero.json"):
            assert len(obj["ls_deg"]) == 24
            for sc in obj["scenarios"]:
                assert set(obj["series"][sc]) == {"tsurf_k", "psurf_pa", "wind_m_s", "dust_tau"}
        if name.endswith("meda_sols.json"):
            for s in obj["sols"]:
                _types(s, {"sol": int, "t_air_mean_k": NUM, "t_air_min_k": NUM, "t_air_max_k": NUM, "p_mean_pa": NUM,
                           "rh_mean_pct?": NUM, "wind_mean_m_s?": NUM, "n_samples": int})
        if name.endswith("donki_mars.json"):
            for e in obj["events"]:
                _types(e, {"id": str, "type": str, "start_utc": str, "mars_arrival_utc?": str, "speed_km_s?": NUM,
                           "note": str, "link": str})
                assert e["type"] in ("CME", "SEP", "FLR", "IPS")
        if name.endswith("crustal_field.json"):
            assert set(obj["aoi_b_nT"]) == {"min", "mean", "max"}
    storms = WEB / "climate" / "dust_storms.geojson"
    if storms.exists():
        for f in read_json(storms)["features"][:50]:
            assert set(f["properties"]) == {"seq_id", "member_id", "mars_year", "ls_deg", "area_km2", "distance_km", "source"}


def test_geology_vectors_and_legend():
    need(METADATA / "geology_legend.json")
    leg = read_json(METADATA / "geology_legend.json")
    for u in leg["units"]:
        assert {"code", "unit_id", "name", "group", "risk_score", "color"} <= set(u)
    for t in leg["terrain_classes"]:
        assert {"code", "name", "risk_score", "color"} <= set(t)
    g = WEB / "geology" / "geologic_units.geojson"
    if g.exists():
        f = read_json(g)["features"][0]
        assert set(f["properties"]) == {"unit_code", "unit_id", "unit_name", "group", "risk_score", "source"}
    r = WEB / "geology" / "craters_robbins.geojson"
    if r.exists():
        for f in read_json(r)["features"]:
            assert set(f["properties"]) == {"crater_id", "diam_km", "lon", "lat", "source"}
    d = WEB / "geology" / "craters_dtm.geojson"
    if d.exists():
        for f in read_json(d)["features"][:20]:
            assert set(f["properties"]) == {"id", "diam_m", "depth_m", "lon", "lat", "risk_peak"}
