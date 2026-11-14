# Architecture

MARS ODYSSEY is five modules that meet only at written contracts (C1–C10, copied into every module
document). This page explains the shape of the system and why it is built this way.

```
                     NASA / USGS sources (HiRISE DTM, MMGIS rover JSON, NASA sample list)
          + geology (Stack 2020 units, THEMIS TI, NOAH-H, Robbins craters)            [v1.1]
          + climate (MCD, MEDA, MDAD) + space weather (DONKI, crustal field)          [v1.1]
                                            │
                      ┌─────────────────────▼─────────────────────┐
                      │ M1  Data pipeline + environment + launcher │
                      │ scripts/  data/  pixi.toml  run_demo.py    │
                      └───┬──────────────┬───────────────┬─────────┘
          5 m GeoTIFFs (C2)│  static web data (C3, C10)   │ elevation_5m.tif (C2)
                ┌─────────▼────────┐     │       ┌───────▼──────────────────────────────┐
                │ M2 Route planner │     │       │ M4  3D simulation engine             │
                │ API (FastAPI)    │     │       │ aiohttp :8100 under /sim, PyBullet,  │
                │ backend/  :8000  │     │       │ Three.js view                        │
                └───┬──────────┬───┘     │       │     ▲ in-process Python API (C8)     │
     /api/* (C4)    │          └────────────────►│ ┌───┴──────────────────────────────┐ │
                    │   /api/route (demo)│       │ │ M5  Obstacle avoidance            │ │
                    │                    │       │ └──────────────────────────────────┘ │
                    │                    │       └───┬──────────────────────────────────┘
                    │                    │ /sim HTTP (C5), /sim/ws + LDR1 frames (C6), postMessage (C7)
                ┌───▼────────────────────▼───────────▼───┐
                │ M3  Planner web app (React + Leaflet)  │
                │ frontend/   Vite :5173 / preview :4173 │
                └────────────────────────────────────────┘
```

## Decisions and their reasons

**One equirectangular sphere grid for everything (C1, C2).** The HiRISE DTM is delivered on the Mars
2000 sphere in a simple equirectangular projection. Keeping every raster on that exact CRS, on one
5 m grid aligned to whole DTM posts (`2133 × 1007`, origin `4581341, 1096879`), means:
the 5 m elevation is an exact 5 × 5 mean with no resampling kernel; every C2 raster can be indexed
with the same row/column by M2, M4 and M5; and a Leaflet image overlay with the lon/lat pixel-edge
bounds places each PNG pixel exactly, because lon and lat are linear in projected x and y.

**Static files instead of a data server (C3, C10).** The web app reads PNG overlays and compact
GeoJSON/JSON from `frontend/public/data/`. They are produced once by the pipeline, cache well, survive a
Cloudflare quick tunnel, and avoid CORS problems: MMGIS sends no CORS headers, and DONKI / PDS are
fetched at build time for the same reason.

**A single pixi environment (C9).** GDAL, PyBullet and Node all come from conda-forge for the four
platforms, so `pixi run demo` works on a clean machine without system packages. `environment.yml` and
the `Makefile` are a conda fallback.

**One launcher process (`sim/tools/run_demo.py`).** It does each preparation step only when its
inputs changed (data, 3D assets, `npm install`, `npm run build`), then owns the three servers as child
processes: one Ctrl+C, or any child dying, tears the whole tree down (`killpg` / `taskkill /T /F`).
The browser only ever talks to the Vite preview on :4173, which proxies `/api` to :8000 and `/sim`
(including the WebSocket) to :8100, so a single public tunnel URL serves the whole app.

**Optional environment track (v1.1, proposed C2-ext / C3-ext / C9-ext / C10).** Everything new is
additive: `layers.json` keeps exactly four `raster_layers` and puts new rasters in `overlay_layers`;
`hazard_model.json` only gains keys; with the default `scripts/config/hazard.yaml` the hazard raster is
byte-identical to v1.0 (asserted by `scripts/tests/test_env_pipeline.py`). The track runs under
`pixi run data-env`, never inside `pixi run data`, and the launcher runs it non-fatally with a per-step
timeout so a slow PDS or DONKI server can never block a demo.

## Data flow (M1)

| Step | Script | Output |
|---|---|---|
| download | `download_data.py` | `data/raw/hirise_dtm/`, `data/raw/perseverance/`, `data/metadata/sources.json` |
| validate | `validate_data.py` | `data/metadata/validation_report.json` |
| terrain | `process_terrain.py` | `data/processed/{elevation,slope,roughness,hillshade}_5m.tif`, `terrain_stats.json` |
| hazard | `generate_hazard.py` | `data/analysis/{hazard,slope_risk,roughness_risk}_5m.tif`, `hazard_model.json` |
| layers | `render_layers.py` | `frontend/public/data/layers/*.png`, `layers.json` |
| science | `build_science_targets.py` | `frontend/public/data/{traverse,waypoints,samples}.geojson` |
| [v1.1] env download | `download_env_data.py` | `data/raw/{geology,climate,space_weather}/`, `env_sources.json` |
| [v1.1] geology | `process_geology.py` | C2-ext rasters, `geology/*.geojson`, `geology_legend.json` |
| [v1.1] climate | `build_climate.py` | `climate/*.json`, `layers/dust_storm_frequency_global.png`, `meda` on waypoints |
| [v1.1] space weather | `fetch_space_weather.py` | `space_weather/*.json`, `layers/crustal_field_global.png` |

Configuration (URLs, stops, weights, unit tables) lives in `scripts/config/*.yaml`; constants and
coordinate helpers in `scripts/config.py`; the shared streaming downloader in `scripts/_http.py`.
