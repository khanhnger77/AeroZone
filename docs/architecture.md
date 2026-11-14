# Architecture

```
NASA / USGS sources ──► scripts/ (Python, run once) ──► data/processed, data/analysis (GeoTIFF)
  HiRISE DTM (S3)          download_data.py                │
  MMGIS rover JSON         validate_data.py                ├─► frontend/public/data/*.png, *.geojson, layers.json  (static)
                           process_terrain.py              │
                           generate_hazard.py              └─► backend/ (FastAPI) loads 5 m grids in memory
                           render_layers.py                      /api/health  /api/metadata  /api/point
                           build_science_targets.py              /api/route   /api/raw-images (proxy)
                                                                        │
NASA Mars Trek WMTS tiles (HiRISE, CTX, THEMIS, MOLA, Viking) ──────────┤
                                                                        ▼
                                  frontend/ React + TS + Vite + Tailwind + Leaflet (CRS.EPSG4326)
```

Why this shape
- **No database**: the AOI is ~2 M cells; numpy in memory answers routing in ~3 s. PostGIS adds setup time with no demo value.
- **Leaflet, not Cesium**: Trek tiles use the same 2×1 equirectangular tiling as `L.CRS.EPSG4326`, so imagery works with zero configuration. A 3D view is a stretch goal.
- **Static analysis layers**: pre-rendered PNGs keep the frontend fast and the backend stateless.
