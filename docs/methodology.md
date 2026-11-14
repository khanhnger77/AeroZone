# Methodology

## Coordinates
- Body: IAU Mars 2000 sphere, R = 3 396 190 m. Latitude planetocentric, longitude east-positive (−180…180).
- Source rasters are Equirectangular with standard parallel 0°. Projected x/y are therefore *linear* in lon/lat, so PNG overlays are placed on Leaflet's EPSG:4326-style grid (same tiling as NASA Mars Trek) with **no resampling**.
- Consequence: an east-west projected metre is cos(φ) ground metres (0.949 at 18.45° N). Slope and routing use the corrected ground size.
- All distances use a Mars-radius haversine (Leaflet's built-in distance assumes Earth and is not used).

## Terrain (scripts/process_terrain.py)
- Elevation: 1 m DTM → 5 m mean.
- Slope: central differences on the 5 m grid (10 m baseline), degrees.
- Roughness: RMS of (z − 9×9 m moving mean) at 1 m, per 5 m cell. Values < ~0.1 m approach DTM noise; DTM effective resolution ≈ 3× post spacing, so sub-3 m boulders are not resolved.

## Module A — hazard (scripts/generate_hazard.py) · MODEL
`Hazard = 0.4·Slope + 0.3·Roughness + 0.2·Obstacle + 0.1·Crater` (each 0–100).
- Slope risk: 0°→0, 5°→10, 15°→50, 25°→90, ≥30°→100. Roughness risk: 0→0, 0.10 m→25, 0.25 m→60, ≥0.5 m→100.
- Obstacle & crater factors: **unavailable** → score re-normalised over the 0.7 of weight that is available, and the UI says so. Not treated as zero risk.
- Slope ≥ 30° = no-go mask.
- Weights and thresholds are prototype assumptions, **not** NASA-certified safety limits.

## Module C — routing (backend/services/routing.py) · MODEL
- Cell cost per ground metre: `1 + β·(hazard/100)^p + γ·(slope/15°)²`; no-go cells = ∞.
- Profiles: Direct (β 0.3, γ 0), Balanced (β 3, γ 0.5), Cautious (β 12, γ 1, p 2).
- Solver: `skimage.graph.MCP_Geometric` (Dijkstra, 8-connected, anisotropic cell size). Search window = endpoint bbox + 600 m.
- Recommendation: minimum of 0.4·len/len_best + 0.4·hz/hz_best + 2·(High+Severe fraction) + 0.2·maxslope/30. Rationale text is generated from the numbers.
- "Effort index" = Σ distance·(1+(slope/15)²) / 1000 — a relative index, **not** energy. Walk time assumes a constant 1 km/h (stated assumption).

## Module B — science targets
- Facts (sample number, name, feature, type, sol sealed, rock class) from NASA's sample page (OBS).
- Location = last rover fix on/before sealing sol (DER); the sol gap is shown as an uncertainty indicator.
- Scientific-interest scoring: **pending** (needs mineral data; see data catalog #13).
