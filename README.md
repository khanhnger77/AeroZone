# AeroZone · Mars Odyssey

**Live demo:** <https://khanhnger77.github.io/AeroZone/>  
**User manual:** [docs/User_Manual.md](docs/User_Manual.md)  
**Web demo source:** the online demo (GitHub Pages + Google Cloud Run) is built from the [`deploy/web`](https://github.com/khanhnger77/AeroZone/tree/deploy/web) branch; `main` is for running locally.

A terrain exploration and route planning application for **Jezero Crater, Mars**.
The repository combines NASA/USGS data, an interactive map, a route planning API,
3D simulation, obstacle avoidance, and the Odyssey Copilot assistant.

## Quick start with Pixi

Install [Pixi](https://pixi.sh), then run:

```bash
git clone https://github.com/khanhnger77/AeroZone.git
cd AeroZone
git switch merge/M1_M2_M3_M4_M5
pixi install --locked
pixi run --locked demo
```

If you already have the repository, run the two Pixi commands from its root directory.

| Service | Address |
|---|---|
| Main web app | http://127.0.0.1:4173 |
| 3D simulation | http://127.0.0.1:4173/sim/ |
| Planner API health | http://127.0.0.1:8000/api/health |
| Sim server | http://127.0.0.1:8100/sim/ |

The launcher checks the environment, prepares missing data, builds the frontend,
and starts the services. The first run may download approximately **1.8 GB** of DTM
data and install frontend dependencies. Later runs reuse existing data.
Press **Ctrl+C** to stop services started by the launcher. If ports 8000, 8100,
or 4173 are already in use, stop the previous demo first.

```bash
pixi run demo --no-browser   # do not open a browser automatically
pixi run demo --no-env-data  # skip the optional environment data step
pixi run check-env           # compare the environment against pixi.lock
```

## Share through Cloudflare

```bash
pixi run --locked share
```

The terminal prints a `https://….trycloudflare.com` URL serving the web app, API,
and simulation. Keep the computer and terminal running while sharing; restarting
may generate a new URL. The launcher downloads `cloudflared` on Linux/Windows.
On macOS, install it first with `brew install cloudflared`.
Stop the local demo before running `share`, since both use the same ports.

## Permanent online demo

For a link that does not depend on a running laptop, the frontend is published to **GitHub Pages**.
The planner API, Copilot and 3D sim run on **Google Cloud Run** (`Dockerfile`,
`pixi run deploy-cloudrun`). This lives on the `deploy/web` branch: pushing it republishes the
frontend, and the backend is redeployed with `pixi run deploy-cloudrun`. `main` stays the
local-run branch and never deploys.
Setup and updating: [deployment guide](docs/deployment.md).

## Modules

| Module | Directory | Responsibility |
|---|---|---|
| M1 · Data platform | `scripts/`, `data/`, `docs/` | Download and process data, generate terrain and hazard rasters, record provenance |
| M2 · Planner API | `backend/` | Query terrain and compute Direct, Balanced, and Cautious routes |
| M3 · Web app | `frontend/` | Map, layers, Safety, Science, Weather, Mission, and Locator |
| M4 · Sim engine | `sim/server/`, `sim/web/` | PyBullet/Three.js simulation, rover and astronaut models, WebSocket telemetry |
| M5 · Obstacle avoidance | `sim/obstacle_avoid/` | Virtual lidar, obstacle detection, and local detour planning |
| Odyssey Copilot | `odyssey_copilot/` | Assistant for querying data and interacting with the map |

## Using the interface

- **Explorer / Data Layers:** explore the study area and enable data layers.
- **Locator:** drag or click to navigate the main map; scroll to zoom. The layer
  buttons beside the Locator select one layer at a time: selecting Slope turns off Elevation.
- **Safety:** inspect hazard, slope, and roughness. Hover over the **i** button for details.
- **Science:** view Perseverance rock samples, source information, and related raw images.
- **Weather:** select a sol to inspect temperature, pressure, and charts by sol or LMST hour.
  Enable **Localized waypoints** in Data Layers, then click a waypoint to request its sol.
- **Mission:** choose start and destination points, calculate and compare routes,
  then open the 3D demo. Modes include path traversal and a world demo with simulated objects.
- **Map points:** open a place card to view 3D terrain or a panorama where suitable imagery exists.

## Data

Core sources include the USGS HiRISE DTM, NASA Mars Trek basemaps, Perseverance
traverse and waypoints, and NASA's rock sample catalogue. The extended pipeline
adds geology, thermal inertia, terrain classification, craters, MEDA observations,
dust, and space weather where sources are available.

```bash
pixi run data          # terrain → hazard → layers → sample sites
pixi run data-env      # environment/geology data; run after data
pixi run weather-data  # build the MEDA snapshot used by the Weather tab
```

The Weather tab currently uses MEDA observations for **sols 1186–1215**, covering
20 June–21 July 2024 UTC. These are historical observations at the rover, not a
forecast or a spatial weather map. Each sol is assigned the last rover waypoint
on or before that sol; multiple sols may share a location. Temperatures are shown
in °C and pressure in Pa. Missing observations remain gaps.

Currently, `data-env` and `weather-data` can both write
`frontend/public/data/climate/meda_sols.json`, using different formats.
After running `data-env`, run **`pixi run weather-data`** again to restore the
hourly snapshot format required by the Weather tab.

DONKI provides a space weather event catalogue, while the Langlais model describes
Mars's crustal magnetic field. These are not radiation dose measurements at Jezero.
Some sources require manual downloads or exports, so not every layer is available
on a fresh installation.

See [data sources](docs/data_sources.md), [methodology](docs/methodology.md), and
[architecture](docs/architecture.md). Metadata and provenance are stored in `data/metadata/`.

## Development and testing

Run each service in a separate terminal:

```bash
pixi run backend     # FastAPI :8000
pixi run sim         # aiohttp/PyBullet :8100
pixi run frontend    # Vite dev :5173
```

Build and run tests:

```bash
pixi run build
pixi run test
pixi run python -m pytest -q scripts/tests scripts/test_weather.py scripts/test_share.py
pixi run npm --prefix frontend test
```

## Odyssey Copilot

Copilot is integrated into the web app and Planner API and calls an AI model through
[OpenRouter](https://openrouter.ai). The tracked `.env` in the repository root selects the
model and points to the key, which is kept out of git:

```dotenv
OPENROUTER_API_KEY=file:api_key.credentials
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct
```

**Add your API key** (once per machine):

1. Get an OpenRouter key at <https://openrouter.ai/keys> (it starts with `sk-or-v1-`), or ask
   the team for the shared one.
2. Create a file named `api_key.credentials` in the repository root, next to `.env`, holding
   only the key:

   ```text
   sk-or-v1-...
   ```

   Lines starting with `#` are ignored, and `OPENROUTER_API_KEY=sk-or-v1-...` also works.
3. Restart the backend (`pixi run backend` or `pixi run demo`).

`api_key.credentials` is listed in `.gitignore`: never commit it, and never paste the key
into `.env`. Setting the `OPENROUTER_API_KEY` environment variable still works and takes
precedence.

The backend includes fallback responses when no key is available (no or empty
`api_key.credentials`) or the AI service cannot be reached. See the [Copilot guide](odyssey_copilot/README.md),
[sim engine guide](sim/README.md), and [obstacle avoidance guide](sim/obstacle_avoid/README.md)
for further details.

## Simulation limitations

Hazard scores and route recommendations are experimental models, not certified
mission planning tools. The 5 m terrain grid cannot resolve every small boulder;
boulders and craters added by the obstacle avoidance demo are simulated objects.
Basemap imagery requires an Internet connection. Panoramas are available only at
selected locations.
