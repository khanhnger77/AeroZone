# AeroZone (MARS ODYSSEY): repo analysis and test plan

Branch `merge/M2_M3` = `origin/M2` (route-planner API) + `origin/dev/huy` (M3 web app, M1 data pipeline).
Specs: [M2_planner_api.md](M2_planner_api.md), [M3_web_app.md](M3_web_app.md) (M3 file is untracked).

## 1. Entry points

| What | Command | File |
|---|---|---|
| Data pipeline (M1) | `make data` | `scripts/download_data.py → validate_data → process_terrain → generate_hazard → render_layers → build_science_targets` |
| API (M2) | `make backend` = `uvicorn backend.main:app --port 8000` | [backend/main.py](backend/main.py) |
| Web app (M3) | `make frontend` = `cd frontend && npx vite --port 5173` | [frontend/src/main.tsx](frontend/src/main.tsx) → [App.tsx](frontend/src/App.tsx) |
| API tests | `make test` = `pytest -q tests` | [tests/test_api.py](tests/test_api.py) |
| Frontend tests | `cd frontend && npm test` = `node --test tests/*.test.mjs` | [frontend/tests/contracts.test.mjs](frontend/tests/contracts.test.mjs) |
| Stub rasters | `python -m backend.tools.make_stub_data <out_dir>` | [backend/tools/make_stub_data.py](backend/tools/make_stub_data.py) |

## 2. Execution path

**Backend request** (`POST /api/route`):
`main.route` → `main._terrain` → `terrain.get_terrain` (reads 4 GeoTIFFs from `ROOT`, cached) →
`routing.plan_routes` → `_check_endpoint` ×2 → for each of 3 `PROFILES`: `_solve` (window + `_cost_surface` + `MCP_Geometric`) → `_route_metrics` (haversine, class fractions via `hazard_classes()` ← `data/metadata/hazard_model.json`, profile ≤240, `_nearby_samples` ← `main.samples()` ← `frontend/public/data/samples.geojson`) → `_recommend`.
Other endpoints: `/api/health`, `/api/metadata` (reads `data/metadata/*.json`), `/api/point`, `/api/raw-images` (httpx → NASA feed, `lru_cache` 256 sols).

**Frontend**: `App.tsx` loads `data/layers.json`, `samples/traverse/waypoints.geojson` (C3 static) + `/api/health` →
`MarsMap` (Leaflet, Trek tiles, PNG overlays) · `MissionPanel` → `api.planRoute` → `/api/route` ·
`SciencePanel` → `/api/raw-images` · `StatusBar` → `/api/point` (debounced) ·
Run demo → `services/sim.ts` → `/sim/api/mission` + `<iframe /sim/?embed=1>` + `LidarScreen` WebSocket `/sim/ws` (parsed by `services/lidar.ts`).

## 3. Core vs scaffolding

- **Core**: `backend/services/{terrain,routing}.py`, `backend/main.py`, `scripts/*` (pipeline), `frontend/src/{App.tsx,map/*,services/*,components/*}`, `frontend/public/data/*`.
- **Scaffolding / support**: `backend/tools/make_stub_data.py` (test stub), `tools/video/*` + `media/` (promo video, not part of the app), `docs/`, `Makefile`, `environment.yml`, `backend/api/__init__.py` (empty marker, nothing uses it).

## 4. State vs spec: gaps and risks

| # | Finding | Impact on testing |
|---|---|---|
| 1 | **No `sim/` dir** (M4 3D engine, M5 obstacle avoidance are not in this branch) | Everything `/sim/*` is untestable end to end: Run demo, tracking inset, DemoControls live data, LidarScreen. Only the client code is unit-tested (contracts.test.mjs) |
| 2 | **No `pixi.toml`**: specs say `pixi run …`, repo uses conda + `Makefile` with a hard-coded `~/miniconda3/envs/mars` | On this machine conda is not installed, pixi is. Need an env first (see §6) |
| 3 | `data/processed/`, `data/analysis/` absent (git-ignored, ~1.8 GB download to regenerate) | Without them the API returns 503 except via stub. `tests/test_api.py` auto-generates stub data, so pytest still works; `test_point_matches_rover_elevation` **skips** on stub |
| 4 | Layer PNGs + C3 geojson are committed | The frontend map works without running the pipeline; only the API needs rasters |
| 5 | Merge: frontend was written against dev/huy's backend; conflicts resolved to M2's backend | Field names are identical (both follow C4); M2 rounds to 2 decimals vs huy's 1. **Integration test M3↔M2 is the main merge risk to verify** |
| 6 | Existing API tests cover only the 5 spec tests | No test for: 422 on slope ≥ 30° / no passable route, 422 detail as string on bad input, `/api/metadata`, `/api/raw-images` ranking + 502, exact C4 field set, class fractions sum ≈ 1 |

## 5. Dependency map

- Backend core: fastapi, pydantic, numpy, rasterio, scikit-image (`MCP_Geometric`), httpx (raw images). Tests: pytest, scipy (stub generator).
- Pipeline only: gdal, geopandas, shapely, pyproj, pillow, requests, scipy.
- Frontend: react 19, leaflet + react-leaflet, recharts, lucide-react, tailwind v4, vite 6, typescript (also needed by `npm test`).

## 6. Test plan

### Step 0: Environment (one-time)

Option A, pixi (already installed; matches the spec's C9):
```bash
pixi init --import environment.yml     # creates pixi.toml (M1-owned file in the spec)
pixi install
pixi shell                             # then python / pytest / uvicorn / node 20 available
```
Option B, conda (what README/Makefile expect): install miniconda, `conda env create -f environment.yml`.
Frontend: `cd frontend && npm install`.

### Level 1: Static checks (frontend, no backend needed)
```bash
cd frontend
npx tsc -b          # spec "done when": clean
npm run lint        # spec: no errors
npm run build       # spec: succeeds -> frontend/dist/
```

### Level 2: Unit / contract tests (no real data needed)
```bash
pytest -q tests                 # API: 4 pass + 1 skip on stub data (auto-generated in a temp dir)
cd frontend && npm test         # LDR1 parser + C5 sim client (fetch mocked)
```

### Level 3: API with real data
```bash
make data                       # or the 6 scripts; downloads 1.8 GB once
pytest -q tests                 # now 5/5 must pass (rover-elevation test no longer skips)
make backend                    # :8000, open http://127.0.0.1:8000/docs
```
Manual contract checks (compare against C4 field by field):
```bash
curl -s localhost:8000/api/health                                    # {"status":"ok","grid_shape":[1007,2133],"res_m":5.0}
curl -s 'localhost:8000/api/point?lon=77.45088572&lat=18.44462715'   # inside, elevation ≈ -2570
curl -s 'localhost:8000/api/point?lon=10&lat=10'                     # {"inside":false}
curl -s -XPOST localhost:8000/api/route -H 'content-type: application/json' \
  -d '{"start":{"lon":77.45088572,"lat":18.44462715},"end":{"lon":77.3052,"lat":18.4975}}' | jq '.recommended, .rationale, [.routes[]|{id,length_m,hazard_max,slope_max_deg}]'
curl -s -XPOST localhost:8000/api/route -H 'content-type: application/json' \
  -d '{"start":{"lon":200,"lat":0},"end":{"lon":77.3,"lat":18.5}}'  # 422, detail is a string
curl -s 'localhost:8000/api/raw-images?sol=<sol_sealed of any sample>&n=3' | jq '.images[].camera.instrument'   # needs internet
curl -s localhost:8000/api/metadata | jq 'keys'
```
Timing: demo route should return in "a few seconds" (M2 done-when).

### Level 4: M3 ↔ M2 integration (manual, browser)
Run `make backend` + `make frontend`, open http://127.0.0.1:5173.

| Area | Check |
|---|---|
| Load | Briefing modal on first visit; map centred [18.462, 77.385] z13; base Viking, CTX+HiRISE on, traverse + samples on; StatusBar shows API connected |
| Cursor | StatusBar lon/lat + terrain readout updates (`/api/point`), "outside" off the AOI |
| Explorer | 5 Fly-to places go to the coordinates in the M3 spec table |
| Layers | Toggle 4 analysis rasters + opacity; legend dock shows last two |
| Safety | Entering tab turns hazard overlay on; classes/stats match `hazard_model.json` |
| Science | 25 samples; selecting one loads raw images of that sol (502 → readable error offline) |
| Mission | Pick start/target on map → Compute routes → 3 cards (Direct blue, Balanced green, Cautious amber), recommendation + rationale, details, nearby samples; profile chart hover moves map marker; Reset clears; demo-scenario preset = landing → sample 25 |
| Errors | Pick a start outside the grid or on a steep slope → 422 detail shown in UI. Stop backend → API state shows down, planner shows an error (no crash) |
| Phone | Narrow window < 768 px: phone layout usable |

### Level 5: 3D demo (blocked in this branch)
Needs M4/M5 (`sim/`). Until merged:
- Verify the graceful failure: Run demo → message "Sim server not reachable on :8100…".
- Optional: build the stubs from M3 spec §4 (static `/sim/` page posting `ready` + fake `telemetry`; a small WebSocket emitting synthetic LDR1 frames on :8100) to test tracking inset, DemoControls and LidarScreen.

### Tests worth adding (gaps from §4 #6)
`tests/test_api.py`:
1. Start on slope ≥ 30° → 422 (find a cell from the slope raster / stub crater rim).
2. 422 `detail` is a `str` for out-of-range lon (custom validation handler).
3. Route response has exactly the C4 keys; colours `#60a5fa/#34d399/#fbbf24`; `len(profile) ≤ 240`; `sum(hazard_class_fraction) ≈ 1`; every `samples_within_150m.distance_m ≤ 150`.
4. `/api/metadata` keys = `{sources, validation_report, terrain_stats, hazard_model}`.
5. `/api/raw-images` with `httpx.get` monkeypatched: camera ranking MCZ first, `n` respected, 502 on `httpx.HTTPError`.

`frontend/tests`: `getJSON` surfaces `detail` from a 422 body (what the planner shows to the user).

## 7. Results (2026-11-14, branch merge/M2_M3, stub data)

Environment: `pixi.toml` created from `environment.yml` (+ tasks `data, backend, frontend, test-api, test-web, test`). Run everything with `pixi run test`.

| Level | Check | Result |
|---|---|---|
| 1 | `npx tsc -b` | clean |
| 1 | `npm run lint` | 0 errors, 3 warnings (`react-refresh/only-export-components` in `src/map/MarsMap.tsx`) |
| 1 | `npm run build` | OK |
| 2 | `pytest tests` (5 spec + 6 new tests) | 10 passed, 1 skipped (rover elevation needs real data) |
| 2 | `npm test` (4 existing + 1 new) | 5 passed |
| 4 (partial) | backend on stub + Vite: `/api/health`, `/api/point`, `/api/route` via the :5173 proxy; `/`, `data/layers.json`, `samples.geojson`, layer PNG | all 200; demo route 0.8 s, 3 routes, 240-pt profiles, recommendation `cautious` |
| 5 | `/sim/config` via proxy | 500, no sim server (M4/M5 not in branch), as expected |

Not done yet: Level 3 (`pixi run data` downloads 1.8 GB, then the rover-elevation test runs) and the browser checklist in Level 4.
