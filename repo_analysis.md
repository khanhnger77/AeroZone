# AeroZone (MARS ODYSSEY): whole-system analysis and test plan

Branch `merge/M1_M2_M3_M4` = `merge/M2_M3` (M2 API + M3 web app) + `feat/sim_engine` (M4) + `feat/M3` (toggle fix) + `feat/data_platform` (M1).
Not merged yet: **M5** obstacle avoidance (`feat/m5/khanh`).
Specs: [M1_data_platform_v1.1.md](M1_data_platform_v1.1.md), [M2_planner_api.md](M2_planner_api.md), [M3_web_app.md](M3_web_app.md). M4's design: [sim/README.md](sim/README.md), [WEB_3D_SIM_ENGINE_GUIDE.md](WEB_3D_SIM_ENGINE_GUIDE.md).

## 1. What the system is

An EVA-planning map of Jezero crater (Mars) plus a 3D rover simulation. Five modules, joined only by contracts C1–C10 (each spec copies them verbatim; "the contract is the referee").

```
NASA/USGS sources ──► M1 data pipeline ──► C2 rasters (data/processed, data/analysis; git-ignored)
                                       └─► C3 static web data (frontend/public/data; tracked)
C2 ──► M2 planner API :8000 ──C4 /api/*──► M3 web app (Vite :5173 / preview :4173)
C2 ──► M4 sim server  :8100 ──C5 /sim HTTP, C6 /sim/ws, C7 iframe──► M3
M4 ──C4 (demo mission only)──► M2          M4 ◄──C8 in-process──► M5 (not merged; built-in stand-in)
```
The browser talks only to the web origin; Vite proxies `/api` → 8000 and `/sim` (+ WebSocket) → 8100.

| Module | Owns | Run | Key files |
|---|---|---|---|
| M1 data + env + launcher | `scripts/`, `data/`, `pixi.toml`, `sim/tools/run_demo.py`, docs | `pixi run data` (`data-env` optional) | `scripts/{download_data,validate_data,process_terrain,generate_hazard,render_layers,build_science_targets}.py` |
| M2 route-planner API | `backend/`, `tests/test_api.py` | `pixi run backend` | `backend/main.py`, `backend/services/{terrain,routing}.py` |
| M3 web app | `frontend/` | `pixi run frontend` | `src/App.tsx`, `src/services/{api,sim,lidar}.ts`, `src/map/MarsMap.tsx` |
| M4 3D sim | `sim/` (except `obstacle_avoid/`, `tools/run_demo.py`) | `pixi run sim` | `sim/server/{sim_server,physics,terrain,textures}.py`, `sim/backend/navigator.py`, `sim/web/js/*` |
| M5 obstacle avoidance | `sim/obstacle_avoid/` | (not in branch) | n/a |

**Everything at once:** `pixi run demo` (M1 launcher): builds data if missing, `npm install`/`build`, starts API + sim + `vite preview` on **:4173**, waits for health checks, prints a banner.

## 2. Request flows (the paths a test must cover)

1. **Plan a route**: M3 `MissionPanel` → `api.planRoute` → `/api/route` → M2 `plan_routes` (3 least-cost profiles on C2 rasters via `MCP_Geometric`) → 3 routes + recommendation → map + profile chart.
2. **Run the 3D demo**: M3 `sim.loadSimMission` → `POST /sim/api/mission` (selected route + alternatives) → M4 crops a drive corridor from `elevation_5m.tif`, builds the PyBullet heightfield collider, returns mission id → M3 embeds `<iframe /sim/?embed=1&m=id>` → page loads `terrain/*.bin` + `texture/*.jpg`, opens `/sim/ws` → M3 sends `set_speed`/`set_warp`/`start` → 50 Hz loop: navigator → physics → telemetry → map tracking inset + DemoControls.
3. **Lidar**: M3 `LidarScreen` subscribes to `lidar` on `/sim/ws` → binary LDR1 frames (needs M5).
4. **Science**: M3 `SciencePanel` → `/api/raw-images?sol=` → M2 proxies the NASA feed (needs internet).

## 3. Test layers and how to run them

Setup once: `pixi install` and `cd frontend && npm install`. The 1.8 GB DTM is downloaded on the first `pixi run data`, then it's skipped by size check.

| Layer | Command | Needs | What it proves |
|---|---|---|---|
| L1 static (M3) | `cd frontend && npx tsc -b && npm run lint && npm run build` | node | types, lint, production bundle |
| L2 unit, M1 | `pixi run python -m pytest -q scripts/tests` | C2 data for most checks | hazard byte-identical to v1.0, C2-ext grids, C10 shapes (5 skip without `data-env`) |
| L2 unit, M2 | `pixi run python -m pytest -q tests` | nothing (auto stub data) / real data for rover-elevation test | C4 contract, 422/502 paths, metrics |
| L2 unit, M3 | `cd frontend && npm test` | node | LDR1 parser, C5 client, C4 error surfacing |
| L2 unit, M4 | `pixi run python -m pytest -q sim/tests` | nothing (synthetic 201×201 grid) | gravity, collider vs render, navigator, server HTTP/WS |
| L3 system smoke | `pixi run demo --no-browser --no-env-data`, then `pixi run python <scratchpad>/system_smoke.py` (25 checks through :4173) | real data, internet for textures | every contract boundary as the browser sees it |
| L4 browser (manual) | open http://127.0.0.1:4173 | stack running | UI checklist below |

`pixi run test` (M1's task) also lists `sim/obstacle_avoid/tests`, so it fails until M5 is merged. Use the explicit paths above until then.

**L4 browser checklist**
- Planner: tabs Explorer / Layers / Safety / Science / Mission; cursor readout; Compute routes (landing → sample 25 preset) shows 3 cards, rationale, profile hover moves the marker; out-of-grid pick shows the 422 message.
- 3D demo: Mission → Run demo → 3D background, tracking inset, DemoControls; set 2 m/s and 5×; the rover must move along the route and its elevation must stay near the terrain (≈ −2570 m at the start). **Currently fails, see §5.**
- With M5 merged: tick "Randomly generate obstacles", check the lidar screen and a detour/rejoin.

## 4. Results (2026-11-14, `merge/M1_M2_M3_M4`, real data rebuilt with M1's scripts)

| Layer | Result |
|---|---|
| L1 | tsc clean · lint 0 errors / 3 warnings (`react-refresh` in `MarsMap.tsx`) · build OK |
| L2 Python (M1 + M2 + M4) | **63 passed, 5 skipped** (skips = `data-env` rasters not built) |
| L2 M3 | 5/5 |
| L3 system smoke | **24/25**: web, C3, all C4 endpoints (demo route 0.7 s, landing elevation −2569.92 m), C5 config/page/assets/mission (1.6 s)/terrain bins/textures, C6 hello/acks/telemetry 42 Hz. Fail: LDR1 lidar frames (expected; M5 missing) |

## 5. Defect found: rover falls through the terrain on real routes (M4)

- **Symptom:** after `start` on the demo mission, telemetry `elev_m` drops at ~3.7 m/s² (Mars free fall) from −2570 m to −4200 m within 30 s sim time; `distance_done_m` ≈ 0. The smoke check "rover drives" passed only because it compared tiny numbers. The real signal is elevation.
- **Root cause:** the PyBullet heightfield collider (`sim/server/physics.py` `PhysicsWorld.set_terrain`) stops colliding when the grid has **more than 262,144 posts (2¹⁸ = 512²)**, whatever its shape (pybullet 3.25, conda-forge osx-arm64). Measured by drop test: 512×512, 1024×256 and 2048×128 hold a ball; 513×513, 600×600 and 2200×120 let it fall. The demo corridor is 1834×731 = 1.34 M posts, and C5 allows up to 2200 posts per side.
- **Why tests miss it:** `sim/tests/test_physics.py` uses 201×201 grids; the server tests use synthetic small terrain.
- **Fix options for the M4 owner** (not applied; M4 owns `sim/`):
  1. Split the corridor collider into tiles of ≤ 512×512 posts (overlapping by one post). This keeps the native 5 m collider, which the spec requires so that the collider, mesh and lidar agree.
  2. Use `GEOM_MESH` triangles instead of a heightfield.
  3. Cap the corridor at ≤ 262,144 posts by resampling (`GridDesc.resampled` exists). Simplest, but coarser, and it changes what C5 promises.
  - Add a regression test: build a > 512² grid, drop the rover, assert it settles.
- Also worth checking on Linux/Windows; the limit may be specific to this build.

## 6. Merge notes

- 28 add/add conflicts with M1 were resolved to `feat/data_platform` (M1 owns those paths). M1's C3 data is a superset, so M2/M3 needed no change.
- `pixi run data` / `demo` rewrite tracked files (`data/metadata/sources.json` timestamps, `layers.json` loses `overlay_layers` without `data-env`, `package-lock.json` key order). Restore with `git checkout -- data/metadata frontend/public/data frontend/package-lock.json` unless you mean to commit new data.
- `.pyc` files are tracked in git and show as modified after every test run; `git rm -r --cached '**/__pycache__'` would stop it.
