"""World demo: intercept geometry, object stepping, the meeting rule and the /sim/api/world commands."""
import asyncio
import math

import numpy as np
import pytest

from sim.server.terrain import Frame, Grid
from sim.server.world import MEET_RADIUS_M, World, intercept_point
from sim.tests.test_server import make_sim, next_of, run, terrain_tif  # noqa: F401  (fixture)


def flat_world(**kw) -> World:
    g = Grid(h=np.zeros((201, 201), np.float32), x0=-1000, y0=-1000, dx=10, dy=10,
             lon_w=77.3, lon_e=77.4, lat_s=18.4, lat_n=18.5, res_src_m=10)
    return World(g, Frame(77.35, 18.45, 0.0), **kw)


def test_intercept_point_meets_a_moving_target():
    aim, tau = intercept_point((0, 0), 2.0, (100, 0), (0, 1.0))
    assert tau is not None
    assert math.hypot(*aim) == pytest.approx(2.0 * tau, rel=1e-6)          # pursuer reaches it
    assert aim[1] == pytest.approx(tau * 1.0) and aim[0] == pytest.approx(100)
    aim, tau = intercept_point((0, 0), 0.5, (100, 0), (3.0, 0))            # target is faster
    assert tau is None and tuple(aim) == (100, 0)


def test_objects_are_named_by_type_and_number():
    w = flat_world()
    names = [w.add(k, 10 * i, 0, 0).name for i, k in enumerate(["rover", "astronaut", "rover"])]
    assert names == ["Rover_1", "Astronaut_1", "Rover_2"]


def test_goto_reaches_the_goal_and_goes_idle():
    w = flat_world()
    w.add("rover", -500, 0, 0, mode="idle")                 # selected: driven by the server
    a = w.add("astronaut", 0, 0, 0, cruise=1.0, mode="idle")
    w.assign_goto(a.id, 60, 40)
    w.running = True
    for _ in range(3000):
        w.step(0.1)
    assert a.mode == "idle" and math.hypot(a.x - 60, a.y - 40) < 4


def test_intercept_meets_a_wanderer_and_both_stop():
    w = flat_world(seed=3)
    w.add("rover", -800, -800, 0, mode="idle")
    runner = w.add("astronaut", 100, 0, 0, cruise=0.4)
    chaser = w.add("rover", -100, 0, 0, cruise=1.5, mode="idle")
    w.assign_intercept(chaser.id, runner.id)
    w.running = True
    for _ in range(6000):
        w.step(0.1)
        if chaser.mode == "stopped":
            break
    assert chaser.mode == "stopped" and runner.mode == "stopped"
    assert math.hypot(chaser.x - runner.x, chaser.y - runner.y) < MEET_RADIUS_M + 1
    assert any(e["event"] == "met" for e in w.pop_events())
    w.assign_goto(chaser.id, 300, 300)                     # a new mission releases it
    w.step(0.1)
    assert chaser.mode == "goto"


def test_objects_steer_around_obstacles():
    w = flat_world(obstacles=[(50.0, 0.0, 3.0)])
    w.add("rover", -900, -900, 0, mode="idle")
    a = w.add("rover", 0, 0, 0, cruise=1.0, mode="idle")
    w.assign_goto(a.id, 100, 0)
    w.running = True
    closest = 1e9
    for _ in range(4000):
        w.step(0.1)
        closest = min(closest, math.hypot(a.x - 50, a.y) - 3.0)
    assert closest > 0.5 and a.mode == "idle"


def test_no_manual_control_in_the_path_demo(terrain_tif, tmp_path):
    from sim.tests.test_server import ROUTE

    async def body(c):
        await c.post("/sim/api/mission", json={"route": ROUTE})
        r = await (await c.post("/sim/api/command", json={"type": "drive", "v": 1, "w": 0})).json()
        assert r["ok"] is False and "world demo" in r["error"]
        assert sim.world.sel.mode == "route"
    sim = make_sim(terrain_tif, tmp_path)
    run(body, sim)


def test_world_endpoint_and_commands(terrain_tif, tmp_path):
    sim = make_sim(terrain_tif, tmp_path)

    async def body(c):
        r = await c.post("/sim/api/world", json={"agents": [{"kind": "tank"}]})
        assert r.status == 422
        r = await c.post("/sim/api/world", json={"agents": [{"kind": "rover"}, {"kind": "astronaut"},
                                                           {"kind": "rover"}],
                                                "obstacles": {"enabled": False, "seed": 4}})
        assert r.status == 200, await r.text()
        m = await r.json()
        assert m["kind"] == "world" and [a["name"] for a in m["world"]["agents"]] == \
            ["Rover_1", "Astronaut_1", "Rover_2"]
        assert m["obstacles"]["enabled"]                    # lidar always on, no rocks
        r = await (await c.post("/sim/api/command", json={"type": "set_model", "value": "kinematic"})).json()
        assert r["ok"] is False
        ws = await c.ws_connect("/sim/ws")
        await c.post("/sim/api/command", json={"type": "start"})
        for _ in range(10):                                 # a 5 Hz frame sent before "start" landed
            wmsg = await next_of(ws, "world")
            if wmsg["running"]:
                break
        assert len(wmsg["agents"]) == 3 and wmsg["running"]
        r = await (await c.post("/sim/api/command", json={"type": "select", "agent": "a1", "manual": True})).json()
        assert r["ok"] and r["selected"] == "a1" and r["model"] == "astronaut"
        r = await (await c.post("/sim/api/command", json={"type": "drive", "v": 1, "w": 0})).json()
        assert r["ok"]
        for _ in range(20):                                 # skip frames queued before the select
            t = await next_of(ws, "telemetry")
            if t["agent"] == "a1":
                break
        assert t["agent"] == "a1" and t["model"] == "astronaut"
        a0 = m["world"]["agents"][0]
        r = await (await c.post("/sim/api/command", json={"type": "goto", "agent": "r1",
                                                            "lon": a0["lon"] + 0.001, "lat": a0["lat"]})).json()
        assert r["ok"]
        r = await (await c.post("/sim/api/command", json={"type": "intercept", "agent": "r2",
                                                            "target": "r1"})).json()
        assert r["ok"]
        r = await (await c.post("/sim/api/command", json={"type": "intercept", "agent": "r2",
                                                            "target": "r2"})).json()
        assert r["ok"] is False
        await asyncio.sleep(0.5)
        wmsg = await next_of(ws, "world")
        modes = {a["id"]: a["mode"] for a in wmsg["agents"]}
        assert modes["a1"] == "manual" and modes["r1"] == "goto" and modes["r2"] == "intercept"
        r = await (await c.post("/sim/api/command", json={"type": "end"})).json()
        assert r["ok"] and sim.mission is None and sim.world is None
        await ws.close()
    run(body, sim)


def test_world_obstacles_are_scattered_and_paths_reported(terrain_tif, tmp_path):
    sim = make_sim(terrain_tif, tmp_path)

    async def body(c):
        r = await c.post("/sim/api/world", json={"agents": [{"kind": "rover"}, {"kind": "astronaut"}],
                                                "obstacles": {"enabled": True, "seed": 9}})
        assert r.status == 200, await r.text()
        m = await r.json()
        rocks = m["obstacles"]["rocks"]
        assert 20 <= len(rocks) <= 200                     # sparse: ~2 per km2 over the AOI
        ys = np.array([rk["y"] for rk in rocks])
        assert len(np.unique(np.round(ys, -1))) > 0.8 * len(rocks)   # random, not lined up on rows
        a = m["world"]["agents"][1]
        r = await (await c.post("/sim/api/command", json={"type": "goto", "agent": a["id"], "route": "cautious",
                                                            "lon": a["lon"] + 0.002, "lat": a["lat"]})).json()
        assert r["ok"] and sim.world.agents[a["id"]].route_pref == "cautious"
        desc = sim.world.describe_agent(sim.world.agents[a["id"]])
        assert desc["path"] and len(desc["path"]) >= 2
        r = await (await c.post("/sim/api/command", json={"type": "select", "agent": a["id"]})).json()
        assert r["ok"] and sim.world.agents[a["id"]].mode == "goto"      # selecting keeps the mission
    run(body, sim)


def test_set_speed_of_one_object(terrain_tif, tmp_path):
    sim = make_sim(terrain_tif, tmp_path)

    async def body(c):
        await c.post("/sim/api/world", json={"agents": [{"kind": "rover"}, {"kind": "astronaut"}]})
        r = await (await c.post("/sim/api/command", json={"type": "set_speed", "value": 0.5, "agent": "a1"})).json()
        assert r["ok"] and sim.world.agents["a1"].cruise == 0.5
        assert sim.nav.cruise != 0.5                        # a1 is not selected: the nav is untouched
        r = await (await c.post("/sim/api/command", json={"type": "set_speed", "value": 2.0})).json()
        assert r["ok"] and sim.world.sel.cruise == 2.0 and sim.nav.cruise == 2.0
        r = await (await c.post("/sim/api/command", json={"type": "set_speed", "value": 1, "agent": "zz"})).json()
        assert r["ok"] is False
    run(body, sim)


def test_battery_and_oxygen_drain_slowly():
    w = flat_world()
    rover = w.add("rover", 0, 0, 0, mode="idle")
    walker = w.add("astronaut", 50, 0, 0, mode="idle")
    w.running = True
    for _ in range(3600):
        w.step(1.0)                                    # one sim hour, standing still
    assert rover.energy == pytest.approx(97.0, abs=0.01)
    assert walker.energy == pytest.approx(89.0, abs=0.01)
    assert w.describe_agent(rover)["energy"] == pytest.approx(97.0, abs=0.01)
    w.reset()
    assert rover.energy == 100.0


def test_set_energy_for_testing_alerts(terrain_tif, tmp_path):
    sim = make_sim(terrain_tif, tmp_path)

    async def body(c):
        await c.post("/sim/api/world", json={"agents": [{"kind": "rover"}, {"kind": "astronaut"}]})
        r = await (await c.post("/sim/api/command", json={"type": "set_energy", "agent": "a1", "value": 25})).json()
        assert r["ok"] and sim.world.agents["a1"].energy == 25
        r = await (await c.post("/sim/api/command", json={"type": "set_energy", "agent": "a1", "value": 250})).json()
        assert r["ok"] and sim.world.agents["a1"].energy == 100          # clamped to 0..100
        r = await (await c.post("/sim/api/command", json={"type": "set_energy", "agent": "zz", "value": 5})).json()
        assert r["ok"] is False
    run(body, sim)
