"""Contract tests for the M2 route-planner API (C4).

Why the stub fallback: M2 must be testable before M1's pipeline has produced
the rasters. If ``data/processed/elevation_5m.tif`` is missing and
``MARS_ODYSSEY_ROOT`` is unset, synthetic data with the exact C2 grid is
generated into a temp dir first. Tests that need real elevations skip on it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]
if "MARS_ODYSSEY_ROOT" not in os.environ and not (_REPO / "data/processed/elevation_5m.tif").exists():
    _stub_root = Path(tempfile.mkdtemp(prefix="mars_odyssey_stub_"))
    os.environ["MARS_ODYSSEY_ROOT"] = str(_stub_root)   # before backend reads ROOT
    from backend.tools.make_stub_data import main as _make_stub_data

    _make_stub_data(_stub_root)

from fastapi.testclient import TestClient  # noqa: E402

import backend.main as api  # noqa: E402
from backend.main import app  # noqa: E402
from backend.services.terrain import ROOT, get_terrain  # noqa: E402

client = TestClient(app)
IS_STUB = (ROOT / "data/STUB_DATA").exists()

DEMO_START = {"lon": 77.45088572, "lat": 18.44462715}   # landing site (C4)
DEMO_END = {"lon": 77.3052, "lat": 18.4975}             # Cheyava Falls / sample 25 (C4)
ROVER_ELEV_M = -2569.91


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["grid_shape"] == [1007, 2133]
    assert body["res_m"] == 5.0


@pytest.mark.skipif(IS_STUB, reason="needs M1's real elevation raster")
def test_point_matches_rover_elevation():
    # The spec gives the elevation but not the waypoint, so take the C3 waypoint
    # whose rover-reported elevation is the reference value.
    path = ROOT / "frontend/public/data/waypoints.geojson"
    if not path.exists():
        pytest.skip("needs M1's waypoints.geojson")
    feats = json.loads(path.read_text())["features"]
    wp = min(feats, key=lambda f: abs(f["properties"]["elev_geoid"] - ROVER_ELEV_M))["properties"]
    body = client.get("/api/point", params={"lon": wp["lon"], "lat": wp["lat"]}).json()
    assert body["inside"] is True
    assert abs(body["elevation_m"] - ROVER_ELEV_M) <= 1.0


def test_point_outside():
    assert client.get("/api/point", params={"lon": 10, "lat": 10}).json() == {"inside": False}


def test_route_three_alternatives():
    r = client.post("/api/route", json={"start": DEMO_START, "end": DEMO_END})
    assert r.status_code == 200, r.text
    body = r.json()
    routes = {rt["id"]: rt for rt in body["routes"]}
    assert set(routes) == {"direct", "balanced", "cautious"}
    assert body["recommended"] in routes
    for rt in routes.values():
        assert rt["length_m"] >= 0.99 * body["straight_line_m"]
        assert rt["slope_max_deg"] < 30
        assert len(rt["profile"]) <= 240
    assert routes["cautious"]["hazard_max"] <= routes["direct"]["hazard_max"]


def test_route_outside_grid_rejected():
    r = client.post("/api/route", json={"start": {"lon": 10, "lat": 10}, "end": DEMO_END})
    assert r.status_code == 422
    assert "detail" in r.json()


# ---------------------------------------------------------------- C4 details beyond the spec's five tests
ROUTE_KEYS = {"id", "label", "color", "weights", "coordinates", "profile", "length_m", "hazard_mean",
              "hazard_max", "slope_mean_deg", "slope_max_deg", "ascent_m", "descent_m", "effort_index",
              "hazard_class_fraction", "time_h_at_assumed_pace", "samples_within_150m"}
ROUTE_COLORS = {"direct": "#60a5fa", "balanced": "#34d399", "cautious": "#fbbf24"}


@pytest.fixture(scope="module")
def demo_route() -> dict:
    r = client.post("/api/route", json={"start": DEMO_START, "end": DEMO_END})
    assert r.status_code == 200, r.text
    return r.json()


def test_route_response_matches_c4_fields(demo_route):
    assert set(demo_route) == {"straight_line_m", "routes", "recommended", "rationale", "assumptions"}
    assert demo_route["assumptions"]["pace_kmh"] == 1.0
    assert demo_route["assumptions"]["no_go_slope_deg"] == 30.0
    assert demo_route["rationale"] and all(isinstance(s, str) for s in demo_route["rationale"])
    for rt in demo_route["routes"]:
        assert set(rt) == ROUTE_KEYS
        assert rt["color"] == ROUTE_COLORS[rt["id"]]
        assert set(rt["weights"]) == {"beta_hazard", "gamma_slope_effort", "hazard_exponent"}
        assert set(rt["hazard_class_fraction"]) == {"Low", "Moderate", "Elevated", "High", "Severe"}
        assert abs(sum(rt["hazard_class_fraction"].values()) - 1.0) < 0.01
        assert all(set(p) == {"d", "z", "slope", "hazard", "lon", "lat"} for p in rt["profile"])
        assert all(s["distance_m"] <= 150 for s in rt["samples_within_150m"])
        assert rt["coordinates"][0] == pytest.approx([DEMO_START["lon"], DEMO_START["lat"]], abs=1e-4)
        assert rt["coordinates"][-1] == pytest.approx([DEMO_END["lon"], DEMO_END["lat"]], abs=1e-4)


def test_route_steep_start_rejected():
    t = get_terrain()
    r, c = np.unravel_index(np.nanargmax(t.slope), t.shape)
    if t.slope[r, c] < 30:
        pytest.skip("no cell with slope >= 30 deg in this grid")
    lon, lat = (float(v) for v in t.rc_to_lonlat(r, c))
    resp = client.post("/api/route", json={"start": {"lon": lon, "lat": lat}, "end": DEMO_END})
    assert resp.status_code == 422
    assert "slope" in resp.json()["detail"]


def test_invalid_request_detail_is_string():
    r = client.post("/api/route", json={"start": {"lon": 200, "lat": 0}, "end": DEMO_END})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


def test_metadata_keys():
    r = client.get("/api/metadata")
    assert r.status_code == 200
    assert set(r.json()) == {"sources", "validation_report", "terrain_stats", "hazard_model"}


class _FakeFeed:
    def __init__(self, images: list[dict]):
        self._images = images

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"images": self._images}


def test_raw_images_ranked_and_truncated(monkeypatch):
    cams = ["NAVCAM_LEFT", "UNKNOWN", "MCZ_RIGHT", "SHERLOC_WATSON"]
    images = [{"imageid": f"img{i}", "sol": 900001, "camera": {"instrument": cam},
               "image_files": {"small": "s", "medium": "m", "large": "l", "full_res": "f"}}
              for i, cam in enumerate(cams)]
    api._fetch_raw_feed.cache_clear()
    monkeypatch.setattr(api.httpx, "get", lambda *a, **k: _FakeFeed(images))
    body = client.get("/api/raw-images", params={"sol": 900001, "n": 3}).json()
    assert [im["camera"]["instrument"] for im in body["images"]] == ["MCZ_RIGHT", "SHERLOC_WATSON", "NAVCAM_LEFT"]
    assert set(body["images"][0]["image_files"]) == {"small", "medium", "large", "full_res"}


def test_raw_images_feed_down_is_502(monkeypatch):
    def boom(*a, **k):
        raise api.httpx.ConnectError("offline")

    api._fetch_raw_feed.cache_clear()
    monkeypatch.setattr(api.httpx, "get", boom)
    r = client.get("/api/raw-images", params={"sol": 900002})
    assert r.status_code == 502
    assert "detail" in r.json()
