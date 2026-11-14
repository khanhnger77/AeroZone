# MARS ODYSSEY
**Explore Smarter. Navigate Safer. Discover More.**
NASA Space Apps Challenge 2026: *Interplanetary Survival Guide: Martian Map*

A layered EVA-planning map of Jezero Crater, Mars. It combines HiRISE imagery and a 1 m DTM, THEMIS thermal IR, CTX/MOLA/Viking context and Perseverance rover data, then derives slope, roughness and a prototype hazard model. A planner suggests alternative routes to scientific targets and explains why it recommends one.

> The hazard scores and routes come from a prototype model. They are not a NASA-certified safety assessment.

## Quick start

```bash
# 1. environment (Python geo stack + Node 20 in a single conda env)
conda env create -f environment.yml
export PATH=$HOME/miniconda3/envs/mars/bin:$PATH   # the system Node 12 is too old for Vite
cd frontend && npm install && cd ..

# 2. data pipeline (downloads 1.8 GB HiRISE DTM once, then ~10 s processing)
make data

# 3. run (two terminals)
make backend     # FastAPI  http://127.0.0.1:8000/docs
make frontend    # Vite     http://127.0.0.1:5173
make test        # API smoke tests
```

## Screens
| Tab | What it does |
|---|---|
| Explorer | Study area overview, fly-to landmarks, data inventory |
| Data Layers | Toggle imagery, thermal, terrain products and rover layers; source and processing details for each |
| Safety | Hazard map, class breakdown, slope/roughness statistics, model factors, assumptions |
| Science | 25 Perseverance sample sites with NASA facts, derived location uncertainty, and the rover's raw images from that sol |
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
