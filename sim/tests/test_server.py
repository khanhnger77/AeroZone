"""Sim server checks (C5/C6) against a synthetic GeoTIFF with the C2 grid geometry.

Each test runs the real aiohttp app (including the 50 Hz loop) on an ephemeral port. The planner
and the tile provider point at a closed local port, so no test touches the network.
"""
from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

rasterio = pytest.importorskip("rasterio")
pytest.importorskip("aiohttp")
from aiohttp import WSMsgType                                     # noqa: E402
from aiohttp.test_utils import TestClient, TestServer             # noqa: E402

from sim.server import sim_server                                 # noqa: E402
from sim.server.sim_server import SimServer, make_app             # noqa: E402

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
CLOSED = "http://127.0.0.1:9"     # nothing listens on the discard port
ROUTE = {"id": "balanced", "label": "Balanced", "color": "#34d399", "length_m": 1200.0,
         "coordinates": [[77.400, 18.460], [77.404, 18.462], [77.408, 18.463], [77.411, 18.466]]}
ALT = {"id": "direct", "label": "Direct", "color": "#60a5fa", "length_m": 1100.0,
       "coordinates": [[77.400, 18.460], [77.411, 18.466]]}


@pytest.fixture(scope="module")
def terrain_tif(tmp_path_factory) -> Path:
    from rasterio.transform import Affine

    W, H = 2133, 1007
    rows = np.arange(H)[:, None].astype(np.float32)
    cols = np.arange(W)[None, :].astype(np.float32)
    z = -2500.0 + 4.0 * np.sin(cols / 37.0) * np.cos(rows / 23.0) + 0.02 * cols
    path = tmp_path_factory.mktemp("terrain") / "elevation_5m.tif"
    with rasterio.open(path, "w", driver="GTiff", height=H, width=W, count=1, dtype="float32",
                       transform=Affine(5.0, 0, 4581341.0, 0, -5.0, 1096879.0), nodata=np.nan) as ds:
        ds.write(z.astype(np.float32), 1)
    return path


def make_sim(terrain: Path | None, tmp_path: Path) -> SimServer:
    pc = yaml.safe_load((CONFIGS / "physics.yaml").read_text())
    pc["backend"]["planner_url"] = CLOSED
    pc["textures"]["provider"] = CLOSED
    rc = yaml.safe_load((CONFIGS / "rover.yaml").read_text())
    return SimServer(pc, rc, terrain_path=terrain or tmp_path / "missing.tif",
                     cache_dir=tmp_path / "cache")


def run(coro_fn, sim: SimServer):
    """Run `coro_fn(client)` against a live app."""
    async def main():
        client = TestClient(TestServer(make_app(sim)))
        await client.start_server()
        try:
            return await coro_fn(client)
        finally:
            await client.close()
    return asyncio.run(main())


async def next_of(ws, kind: str, timeout: float = 5.0) -> dict:
    """Read WebSocket messages until one of `type == kind` arrives."""
    async def loop():
        while True:
            msg = await ws.receive()
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == kind:
                    return data
            elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                raise AssertionError(f"socket closed waiting for {kind}")
    return await asyncio.wait_for(loop(), timeout)


# ----------------------------------------------------------------------------------------- HTTP
def test_config_document(terrain_tif, tmp_path):
    async def body(c):
        resp = await c.get("/sim/config")
        assert resp.status == 200
        cfg = await resp.json()
        assert cfg["protocol"] == "mo-sim/1" and cfg["ws"] == "ws" and cfg["api"] == "api"
        assert cfg["physics"]["gravity_m_s2"] == pytest.approx(3.711)
        assert cfg["physics"]["timestep_s"] == pytest.approx(0.005)
        assert "kinematic" in cfg["physics"]["available"]
        assert cfg["rover"]["model_url"] == "rover/model/perseverance.glb"
        assert len(cfg["rover"]["wheel_positions_m"]) == 6
        assert cfg["controls"]["warps"] == [1, 2, 5]
        assert cfg["obstacle_avoid"]["available"] is True
        assert cfg["mission"] is None
    run(body, make_sim(terrain_tif, tmp_path))


def test_redirect_keeps_query(terrain_tif, tmp_path):
    async def body(c):
        resp = await c.get("/sim?embed=1&m=abc", allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"] == "/sim/?embed=1&m=abc"
    run(body, make_sim(terrain_tif, tmp_path))


def test_cors_only_for_configured_origins(terrain_tif, tmp_path, monkeypatch):
    """A static frontend on another origin (GitHub Pages) may call /sim only when allowed."""
    pages = "https://team.github.io"
    preflight = {"Origin": pages, "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type"}

    async def unset(c):   # local default: same origin, no CORS headers at all
        resp = await c.get("/sim/config", headers={"Origin": pages})
        assert resp.status == 200 and "Access-Control-Allow-Origin" not in resp.headers

    monkeypatch.delenv("MO_CORS_ORIGINS", raising=False)
    run(unset, make_sim(terrain_tif, tmp_path))

    async def allowed(c):
        resp = await c.options("/sim/api/command", headers=preflight)
        assert resp.status == 204
        assert resp.headers["Access-Control-Allow-Origin"] == pages
        assert "POST" in resp.headers["Access-Control-Allow-Methods"]
        assert resp.headers["Access-Control-Allow-Headers"] == "content-type"
        resp = await c.get("/sim/config", headers={"Origin": pages})
        assert resp.status == 200 and resp.headers["Access-Control-Allow-Origin"] == pages
        resp = await c.get("/sim/config", headers={"Origin": "https://evil.example"})
        assert "Access-Control-Allow-Origin" not in resp.headers
        ws = await c.ws_connect("/sim/ws", headers={"Origin": pages})   # sockets are not blocked
        await ws.close()

    monkeypatch.setenv("MO_CORS_ORIGINS", pages + "/, https://other.example")
    run(allowed, make_sim(terrain_tif, tmp_path))

    async def wildcard(c):
        resp = await c.get("/sim/config", headers={"Origin": "https://anyone.example"})
        assert resp.headers["Access-Control-Allow-Origin"] == "https://anyone.example"

    monkeypatch.setenv("MO_CORS_ORIGINS", "*")
    run(wildcard, make_sim(terrain_tif, tmp_path))


def test_mission_validation_and_missing_terrain(terrain_tif, tmp_path):
    async def bad(c):
        for payload in ({}, {"route": {"coordinates": [[77.4, 18.46]]}},
                        {"route": {"coordinates": [[0, 0], [1, 1]]}},
                        {"route": ROUTE, "obstacles": {"seed": "x"}}):
            resp = await c.post("/sim/api/mission", json=payload)
            assert resp.status == 422, payload
            assert "detail" in await resp.json()
    run(bad, make_sim(terrain_tif, tmp_path))

    async def missing(c):
        resp = await c.post("/sim/api/mission", json={"route": ROUTE})
        assert resp.status == 503
    run(missing, make_sim(None, tmp_path))


def test_mission_description_and_terrain_bins(terrain_tif, tmp_path):
    sim = make_sim(terrain_tif, tmp_path)

    async def body(c):
        resp = await c.post("/sim/api/mission", json={"route": ROUTE, "alternatives": [ALT],
                                                      "start": {"lon": 77.4, "lat": 18.46},
                                                      "end": {"lon": 77.411, "lat": 18.466}})
        assert resp.status == 200, await resp.text()
        m = await resp.json()
        assert len(m["id"]) == 10 and int(m["id"], 16) >= 0
        assert set(m) >= {"id", "frame", "route", "alternatives", "start", "end", "terrain", "obstacles"}
        assert m["route"]["xy"][0] == pytest.approx(
            list(sim.frame.to_local(*ROUTE["coordinates"][0])), abs=1e-3)
        assert m["alternatives"][0]["id"] == "direct" and len(m["alternatives"][0]["xy"]) == 2
        assert m["obstacles"]["enabled"] is False and m["obstacles"]["rocks"] == []
        assert abs(m["frame"]["z0"] * 10 - round(m["frame"]["z0"] * 10)) < 1e-6

        for level in ("corridor", "context"):
            desc = m["terrain"][level]
            raw = await (await c.get(f"/sim/api/terrain/{level}.bin")).read()
            assert len(raw) == desc["nx"] * desc["ny"] * 4
            h = np.frombuffer(raw, "<f4").reshape(desc["ny"], desc["nx"])
            np.testing.assert_array_equal(h, sim.grids[level].h)
            assert h.min() == pytest.approx(desc["h_min"]) and h.max() == pytest.approx(desc["h_max"])
        assert m["terrain"]["corridor"]["resampled"] is False
        assert m["terrain"]["context"]["dx"] > m["terrain"]["corridor"]["dx"]

        again = await (await c.get("/sim/api/mission")).json()
        assert again["id"] == m["id"]
        cfg = await (await c.get("/sim/config")).json()
        assert cfg["mission"] == m["id"]
    run(body, sim)


def test_stale_mission_requests_get_409(terrain_tif, tmp_path):
    """A page still loading mission A must not receive mission B's terrain or imagery."""
    sim = make_sim(terrain_tif, tmp_path)

    async def body(c):
        a = await (await c.post("/sim/api/mission", json={"route": ROUTE})).json()
        ok = await c.get(f"/sim/api/terrain/corridor.bin?m={a['id']}")
        assert ok.status == 200
        assert len(await ok.read()) == a["terrain"]["corridor"]["nx"] * a["terrain"]["corridor"]["ny"] * 4

        b = await (await c.post("/sim/api/mission", json={"route": ALT})).json()
        assert b["id"] != a["id"]
        for url in (f"/sim/api/terrain/corridor.bin?m={a['id']}",
                    f"/sim/api/terrain/context.bin?m={a['id']}",
                    f"/sim/api/texture/corridor.jpg?m={a['id']}"):
            resp = await c.get(url)
            assert resp.status == 409, url
            assert (await resp.json())["mission"] == b["id"]
        # Current id, or no id at all (older clients), still serve the loaded mission.
        assert (await c.get(f"/sim/api/terrain/corridor.bin?m={b['id']}")).status == 200
        assert (await c.get("/sim/api/terrain/corridor.bin")).status == 200
    run(body, sim)


def test_tick_does_not_interleave_with_mission_install(terrain_tif, tmp_path):
    """No await between reading the pose and stepping: an install during a broadcast is harmless."""
    sim = make_sim(terrain_tif, tmp_path)

    async def body(c):
        await c.post("/sim/api/mission", json={"route": ROUTE})
        sim.nav.start()
        calls = []
        real_update = sim.router.update

        def update(*args, **kwargs):
            calls.append(("update", sim.mission_id))
            return real_update(*args, **kwargs)

        async def broadcast(msg, topic):
            calls.append(("broadcast", sim.mission_id))

        sim.router.update = update
        sim.broadcast = broadcast
        sim.avoid.tick = lambda *a: {"type": "avoid_event", "mode": "avoid", "detail": "test"}
        await sim.tick(0.02)
        # Old order was broadcast(event) -> update -> broadcast(telemetry): a yield mid-step.
        # event, telemetry and the (rate-limited) world message: all sent after the step.
        kinds = [k for k, _ in calls]
        assert kinds[0] == "update" and kinds[1:] == ["broadcast"] * (len(kinds) - 1) and len(kinds) >= 3
    run(body, sim)


def test_physics_events_are_broadcast_and_collider_reported(terrain_tif, tmp_path):
    sim = make_sim(terrain_tif, tmp_path)

    async def body(c):
        m = await (await c.post("/sim/api/mission", json={"route": ROUTE})).json()
        assert m["physics"]["model"] == sim.router.active.name
        if sim.router.world is not None:           # PyBullet present: collider was self-tested
            assert m["physics"]["collider"]["verified"] is True
            assert m["physics"]["collider"]["probes"] > 0 and m["physics"]["degraded"] is None
        sent = []

        async def broadcast(msg, topic):
            sent.append((topic, msg))

        sim.broadcast = broadcast
        sim.router._event("fell_through", "simulated")
        await sim.tick(0.02)
        assert ("events", {"type": "physics_event", "event": "fell_through", "detail": "simulated"}) in sent
        assert [t for t, _ in sent].index("events") < [t for t, _ in sent].index("telemetry")
    run(body, sim)


def test_static_files_revalidate(terrain_tif, tmp_path):
    async def body(c):
        resp = await c.get("/sim/js/main.js")
        assert resp.status == 200
        assert resp.headers["Cache-Control"] == "no-cache"
    run(body, make_sim(terrain_tif, tmp_path))


def test_demo_mission_needs_planner(terrain_tif, tmp_path):
    async def body(c):
        resp = await c.get("/sim/api/mission")
        assert resp.status == 503
        assert "planner" in (await resp.json())["detail"]
    run(body, make_sim(terrain_tif, tmp_path))


def test_texture_unavailable_is_503(terrain_tif, tmp_path):
    async def body(c):
        assert (await c.get("/sim/api/texture/context.jpg")).status == 404   # no mission yet
        await c.post("/sim/api/mission", json={"route": ROUTE})
        resp = await c.get("/sim/api/texture/corridor.jpg")
        assert resp.status == 503
    run(body, make_sim(terrain_tif, tmp_path))


def test_commands_over_http(terrain_tif, tmp_path):
    async def body(c):
        r = await (await c.post("/sim/api/command", json={"type": "start"})).json()
        assert r == {"ok": False, "error": "no mission loaded"}
        await c.post("/sim/api/mission", json={"route": ROUTE})
        r = await (await c.post("/sim/api/command", json={"type": "set_warp", "value": 100})).json()
        assert r["ok"] and r["warp"] == 50.0
        r = await (await c.post("/sim/api/command", json={"type": "set_warp", "value": 0.01})).json()
        assert r["warp"] == pytest.approx(0.1)
        r = await (await c.post("/sim/api/command", json={"type": "set_speed", "value": 2.0})).json()
        assert r["ok"]
        r = await (await c.post("/sim/api/command", json={"type": "set_model", "value": "kinematic"})).json()
        assert r["ok"] and r["model"] == "kinematic"
        r = await (await c.post("/sim/api/command", json={"type": "set_model", "value": "tank"})).json()
        assert r["ok"] is False
        r = await (await c.post("/sim/api/command", json={"type": "fly"})).json()
        assert r["ok"] is False and "unknown" in r["error"]
        r = await (await c.post("/sim/api/command", json={"type": "set_speed"})).json()
        assert r["ok"] is False
        r = await (await c.post("/sim/api/command", json={"type": "start"})).json()
        assert r["ok"] and r["state"] == "driving"
    run(body, make_sim(terrain_tif, tmp_path))


def test_static_files_and_traversal(terrain_tif, tmp_path, monkeypatch):
    js_root = tmp_path / "js"
    js_root.mkdir()
    (js_root / "main.js").write_text("export const x = 1;\n")
    (tmp_path / "secret.txt").write_text("nope")
    monkeypatch.setitem(sim_server.STATIC_ROOTS, "js", js_root)

    async def body(c):
        resp = await c.get("/sim/js/main.js")
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/javascript")
        assert (await c.get("/sim/js/nothing.js")).status == 404
        assert (await c.get("/sim/js/..%2Fsecret.txt")).status == 404
    run(body, make_sim(terrain_tif, tmp_path))


# ----------------------------------------------------------------------------------------- WebSocket
def test_websocket_hello_telemetry_and_driving(terrain_tif, tmp_path):
    sim = make_sim(terrain_tif, tmp_path)

    async def body(c):
        ws = await c.ws_connect("/sim/ws")
        hello = await next_of(ws, "hello")
        assert hello["protocol"] == "mo-sim/1" and hello["config"]["mission"] is None

        await c.post("/sim/api/mission", json={"route": ROUTE})
        loaded = await next_of(ws, "mission_loaded")
        assert loaded["mission"] == sim.mission_id

        t0 = await next_of(ws, "telemetry")
        assert t0["mission"] == sim.mission_id and t0["nav"]["state"] == "idle"
        assert len(t0["wheels"]) == 6 and len(t0["pose"]["q"]) == 4
        assert t0["model"] in ("wheeled", "kinematic") and t0["avoid"] is None
        assert t0["elev_m"] == pytest.approx(t0["pose"]["z"] + sim.frame.z0, abs=0.02)
        lon0, lat0 = ROUTE["coordinates"][0]
        assert t0["lon"] == pytest.approx(lon0, abs=1e-4) and t0["lat"] == pytest.approx(lat0, abs=1e-4)

        await ws.send_json({"type": "set_warp", "value": 5})
        assert (await next_of(ws, "ack"))["warp"] == 5
        await ws.send_json({"type": "start"})
        ack = await next_of(ws, "ack")
        assert ack["ok"] and ack["state"] == "driving"

        await asyncio.sleep(2.0)
        t1 = await next_of(ws, "telemetry")
        while t1["sim_time"] < sim.router.sim_time - 0.5:   # skip frames queued during the sleep
            t1 = await next_of(ws, "telemetry")
        assert t1["sim_time"] > t0["sim_time"] + 5.0           # about 10 s at 5x
        assert t1["nav"]["distance_done_m"] > 2.0
        assert abs(t1["nav"]["cross_track_m"]) < 2.5
        assert t1["tilt_deg"] < 30.0
        assert t1["pose"]["z"] == pytest.approx(sim.grids["corridor"].height_at(
            t1["pose"]["x"], t1["pose"]["y"]), abs=0.3)

        await ws.send_json({"type": "subscribe", "topics": ["lidar", "bogus"]})
        ack = await next_of(ws, "ack")
        assert ack == {"type": "ack", "ok": True, "topics": ["lidar"]}

        await ws.send_json({"type": "reset"})
        await asyncio.sleep(0.2)
        assert sim.nav.state == "idle" and sim.nav.s == pytest.approx(0.0, abs=0.5)
        await ws.close()
    run(body, sim)


def test_no_stepping_without_clients(terrain_tif, tmp_path):
    sim = make_sim(terrain_tif, tmp_path)

    async def body(c):
        await c.post("/sim/api/mission", json={"route": ROUTE})
        await c.post("/sim/api/command", json={"type": "start"})
        t = sim.router.sim_time
        await asyncio.sleep(0.5)
        assert sim.router.sim_time == t        # nobody watching: paused
        ws = await c.ws_connect("/sim/ws")
        await asyncio.sleep(0.5)
        assert sim.router.sim_time > t
        await ws.close()
    run(body, sim)
