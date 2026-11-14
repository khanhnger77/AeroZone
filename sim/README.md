# sim: 3D simulation engine (module M4)

A browser 3D simulation of Perseverance driving a planned route on the Jezero terrain. An aiohttp
server under `/sim` (port 8100) runs a fixed-step PyBullet world with Mars gravity and a six-wheel,
four-corner-steered rover. The rover follows the route with a pure-pursuit navigator, and the server
streams telemetry over a WebSocket to a Three.js view.

The design rules, and the reasons behind them, are in
[`WEB_3D_SIM_ENGINE_GUIDE.md`](../WEB_3D_SIM_ENGINE_GUIDE.md).

## Run it

From the repo root, in the team pixi environment (`pixi.toml` belongs to M1):

| Command | What it does |
|---|---|
| `pixi run sim` | `python -m sim.server.sim_server --host 127.0.0.1 --port 8100` |
| `pixi run fetch-assets` | Re-downloads three.js r170 and the NASA rover model (both are already committed) |
| `pixi run test` | Runs `sim/tests` together with the other modules' tests |

Then open <http://127.0.0.1:8100/sim/>.

**Inputs the server needs:**
- `data/processed/elevation_5m.tif` from M1's data pipeline (`pixi run data`). Without it, mission
  requests return **503**.
- The M2 planner at `http://127.0.0.1:8000` (`pixi run backend`), but only for the standalone demo:
  `GET /sim/api/mission` with no mission loaded asks M2 for the landing site → Cheyava Falls route.
  M3 instead posts its own mission to `POST /sim/api/mission`.
- Internet access for the NASA Mars Trek imagery. Offline, `/sim/api/texture/*` returns 503 and the
  page shows plain terrain.

**Page options:**
- `?embed=1`: layout for the planner's iframe (C7). The title, telemetry and controls are hidden.
- `?debug=1`: exposes `window.__sim` (scene, camera, rover, grids) for inspection in the console.

## Interfaces

| Contract | Role | Where |
|---|---|---|
| C5 HTTP API under `/sim` | produce | `server/sim_server.py` (`make_app`) |
| C6 WebSocket `/sim/ws` (telemetry, events, commands) | produce the transport | `server/sim_server.py` (`handle_ws`, `SimServer.tick`) |
| C7 iframe postMessage | produce the sim side | `web/js/main.js` |
| C8 `Grid`, `Frame` | produce | `server/terrain.py` |
| C8 `Pose`, `RoverModelRouter`, `HAVE_PYBULLET`, `euler_from_quat` | produce | `server/physics.py` |
| C8 `PurePursuitNavigator` | produce | `backend/navigator.py` |
| C8 `ObstacleAvoidance` facade | consume (M5) | `obstacle_avoid/service.py` |
| C4 `POST /api/route` | consume (demo only) | `SimServer.demo_mission` |

Until M5's `sim/obstacle_avoid/service.py` exists, the server falls back to a built-in stand-in with
the same interface, and obstacle avoidance stays disabled. No stub file is needed.

**Commands** (WebSocket text or `POST /sim/api/command`): `start`, `pause`, `reset`,
`set_speed` (m/s), `set_warp` (clamped to 0.1–50), `set_model` (`wheeled` or `kinematic`).

Example mission request:

```bash
curl -X POST http://127.0.0.1:8100/sim/api/mission -H "Content-Type: application/json" -d '{"route": {"id": "balanced", "label": "Balanced", "color": "#34d399", "coordinates": [[77.45088572, 18.44462715], [77.3052, 18.4975]]}, "obstacles": {"enabled": true, "seed": 1}}'
```

## Layout

```
sim/
├── configs/            physics.yaml, rover.yaml (C10: values are part of the contract)
├── server/
│   ├── terrain.py      Frame (local ENU), Grid, TerrainSource, build_mission_terrain
│   ├── physics.py      PhysicsWorld, WheeledRover, KinematicRover, RoverModelRouter
│   ├── textures.py     NASA Mars Trek CTX/HiRISE mosaics and cache
│   └── sim_server.py   aiohttp app, WebSocket, 50 Hz loop, mission build, CLI
├── backend/
│   └── navigator.py    PurePursuitNavigator
├── web/
│   ├── index.html      page, HUD, import map
│   ├── js/             net.js, terrain.js, rover.js, main.js (plain ES modules, no bundler)
│   └── vendor/three/   three.js r170 and add-ons (vendored)
├── rover/model/        perseverance.glb (NASA/JPL-Caltech)
├── tools/
│   └── fetch_assets.py downloads the vendored assets; writes assets_sources.json
├── tests/              test_physics.py, test_navigator.py, test_server.py
├── obstacle_avoid/     module M5 (not part of M4)
└── cache/              tiles and textures (git-ignored, created at run time)
```

## Tests

```bash
python -m pytest -q sim/tests
```

All 32 tests pass in about 30 s. They need no network and no M1 data: the server tests generate a
synthetic GeoTIFF with the C2 grid geometry and point the planner and tile provider at a closed local
port. The physics tests skip themselves if PyBullet is not installed.

| File | Covers |
|---|---|
| `test_physics.py` | Mars gravity in the config and no `9.81` in the code; free fall at 3.711 m/s²; collider vs. rendered grid within 0.15 m; substep cap and backlog drop; turn-in-place steering geometry; settling and driving 1 m/s straight with both models; kinematic attitude on a slope; model switching keeps the pose |
| `test_navigator.py` | States; signed cross-track error; monotone progress; a route that doubles back; turning in place; slowing down and arriving; closed-loop following of a sinusoidal route with both models (cross-track < 2.5 m) |
| `test_server.py` | `/sim/config`; the `/sim` redirect; 422 and 503 errors; mission description and terrain bytes; demo and texture failure modes; commands; static MIME types and blocked `../` paths; WebSocket hello, telemetry, subscriptions and driving at 5×; no stepping without clients |

## Credits

- Terrain: HiRISE DTM of Jezero crater, processed by M1 (see `data/metadata/sources.json`).
- Imagery: NASA Mars Trek (CTX and HiRISE orthomosaics).
- Rover model: NASA/JPL-Caltech, NASA 3D Resources.
- three.js (MIT) and the Google Draco decoder (Apache-2.0); see `assets_sources.json`.

Speeds above 0.042 m/s and the time warp are demo accelerations, not Perseverance's real
performance.
