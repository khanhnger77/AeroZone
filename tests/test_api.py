"""Smoke tests for the API. Require processed data (`make data`)."""
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)
LANDING = {"lon": 77.45088572, "lat": 18.44462715}  # rover sol-13 fix, elev_geoid -2569.91 m
CHEYAVA = {"lon": 77.3052, "lat": 18.4975}


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_point_matches_rover_elevation():
    j = client.get("/api/point", params=LANDING).json()
    assert j["inside"]
    assert abs(j["elevation_m"] - (-2569.91)) < 1.0


def test_point_outside():
    assert client.get("/api/point", params={"lon": 10, "lat": 10}).json()["inside"] is False


def test_route_three_alternatives():
    j = client.post("/api/route", json={"start": LANDING, "end": CHEYAVA}).json()
    assert {r["id"] for r in j["routes"]} == {"direct", "balanced", "cautious"}
    assert j["recommended"] in {"direct", "balanced", "cautious"}
    for r in j["routes"]:
        assert r["length_m"] >= j["straight_line_m"] * 0.99  # never shorter than great circle
        assert r["slope_max_deg"] < 30  # no-go respected
    caut = next(r for r in j["routes"] if r["id"] == "cautious")
    direct = next(r for r in j["routes"] if r["id"] == "direct")
    assert caut["hazard_max"] <= direct["hazard_max"]


def test_route_outside_grid_rejected():
    r = client.post("/api/route", json={"start": LANDING, "end": {"lon": 10, "lat": 10}})
    assert r.status_code == 422
