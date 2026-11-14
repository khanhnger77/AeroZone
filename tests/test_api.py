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

import pytest

_REPO = Path(__file__).resolve().parents[1]
if "MARS_ODYSSEY_ROOT" not in os.environ and not (_REPO / "data/processed/elevation_5m.tif").exists():
    _stub_root = Path(tempfile.mkdtemp(prefix="mars_odyssey_stub_"))
    os.environ["MARS_ODYSSEY_ROOT"] = str(_stub_root)   # before backend reads ROOT
    from backend.tools.make_stub_data import main as _make_stub_data

    _make_stub_data(_stub_root)

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend.services.terrain import ROOT  # noqa: E402

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
