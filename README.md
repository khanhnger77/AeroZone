# MARS ODYSSEY
**Explore Smarter. Navigate Safer. Discover More.**
NASA Space Apps Challenge 2026: *Interplanetary Survival Guide: Martian Map*

A layered EVA-planning map of Jezero Crater, Mars. It combines HiRISE imagery and a 1 m DTM, THEMIS thermal IR, CTX/MOLA/Viking context and Perseverance rover data, then derives slope, roughness and a prototype hazard model. A planner suggests alternative routes to scientific targets and explains why it recommends one.

> The hazard scores and routes come from a prototype model. They are not a NASA-certified safety assessment.

## Quick start

Install [Pixi](https://pixi.sh), then run from the repo root:

```bash
pixi install
pixi run demo     # builds and serves the web app + Planner at http://127.0.0.1:4173
pixi run share    # same app through a public Cloudflare quick-tunnel URL
```

On first setup, run `pixi run data` to download and process terrain (~1.8 GB raw DTM). Existing terrain products are reused. `share` reuses healthy services already running on :8000/:4173, starts missing services, installs/builds the frontend when necessary and downloads cloudflared on Linux/Windows if absent. On macOS install it with `brew install cloudflared`. `--no-browser` suppresses opening a browser. Keep the terminal and computer running for the public link; restarting creates a new URL. Ctrl+C stops the tunnel and services started by this command, leaving reused services running.

Terrain 3D and bundled panoramas are included in M3. The M4/M5 rover drive simulation is separate and remains unavailable until those modules are integrated.

Development commands:

```bash
pixi run backend   # FastAPI :8000
pixi run frontend  # Vite dev :5173, in another terminal
pixi run test      # API tests (requires processed terrain)
pixi run python -m pytest -q scripts/test_share.py
```

`environment.yml` and `make backend/frontend/data/test` remain available as the conda alternative.

## Screens
| Tab | What it does |
|---|---|
| Explorer | Study area overview, fly-to landmarks, data inventory |
| Data Layers | Toggle imagery, thermal, terrain products and rover layers; source and processing details for each |
| Safety | Hazard map, class breakdown, slope/roughness statistics, model factors, assumptions |
| Science | 25 Perseverance sample sites with NASA facts, derived location uncertainty, and the rover's raw images from that sol |
| Weather | Historical MEDA temperature and pressure; select a sol, compare sols or local hours, and inspect sources |
| Mission Planner | Pick start and target (or run the demo), then compare Direct, Balanced and Cautious routes and read the explained recommendation |

## Repo
```
scripts/    data pipeline (download → validate → terrain → hazard → render → science targets)
backend/    FastAPI: /api/point, /api/route, /api/raw-images, /api/metadata
frontend/   React + TS + Vite + Tailwind + Leaflet (Mars equirectangular CRS)
data/       raw (originals preserved), processed, analysis, metadata (provenance + validation)
docs/       data_sources.md (catalog + ranking), methodology.md, architecture.md
```

## Data and attribution
- HiRISE DTM and orthomosaic: Fergason et al. 2020, USGS Astrogeology, Mars 2020 TRN mosaics, doi:10.5066/P9REJ9JN and doi:10.5066/P9QJDP48 (CC0). NASA/JPL/University of Arizona.
- Map tiles: NASA Mars Trek (HiRISE, CTX, THEMIS, MOLA, Viking).
- Perseverance traverse and waypoints: NASA/JPL-Caltech Mars 2020 MMGIS. Samples: NASA Mars Rock Samples page. Raw images: NASA/JPL-Caltech.

See [docs/data_sources.md](docs/data_sources.md) for the full catalog and verification results.

## Historical weather

The bundled snapshot contains real NASA PDS MEDA observations for sols 1186–1215
(20 June–21 July 2024 UTC). It uses calibrated ATS1 local air temperatures and derived
pressure; these are observations at Perseverance, not live weather, forecasts or a
spatial weather layer. Means reflect available samples, not time-weighted full sols.
Missing values stay missing. Rover positions are approximate last fixes on/before a sol.

Open **Weather** in the left rail. Select a sol or click a rover waypoint to request
that sol; dates outside the snapshot show an explicit absence of data. Temperature
is displayed in °C and pressure in Pa. Source links and processing notes are included.
The tab works without the Planner API.

```bash
pixi run weather-data  # regenerate default snapshot; cached raw files stay in data/raw/weather/
pixi run python scripts/build_weather.py --start-sol 1186 --end-sol 1215
pixi run python -m pytest -q scripts/test_weather.py
```

Configuration: `scripts/config/meda.yaml`. Published data:
`frontend/public/data/climate/meda_sols.json`; product URLs and SHA-256 hashes:
`data/metadata/meda_sources.json`. The raw CSVs are ignored by Git.
Source: [NASA PDS MEDA archive](https://pds-atmospheres.nmsu.edu/data_and_services/atmospheres_data/PERSEVERANCE/meda.html).
