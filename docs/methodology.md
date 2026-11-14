# Methodology

## Coordinates (C1)

- Body: IAU Mars 2000 sphere, R = 3 396 190 m. Planetocentric latitude, east-positive longitude −180…180.
- Rasters: equirectangular, standard parallel 0°, central meridian 0° (`ESRI:103885`):
  `x = R·rad(lon)`, `y = R·rad(lat)`. Because x and y are linear in lon and lat, an image overlay
  stretched over the lon/lat pixel-edge bounds is exact; no reprojection is needed in the browser.
- One east-west projected metre is `cos(lat)` ground metres (0.949 at Jezero). Slope and any ground
  distance must apply it; distances are always the Mars-radius haversine.
- Elevations are metres above the MOLA areoid (about −2 600 … −1 900 m in the AOI).
- The analysis grid is 5 m, `2133 × 1007` cells, transform `(5, 0, 4581341, 0, −5, 1096879)`: the AOI
  (77.29–77.47 °E, 18.42–18.505 °N) snapped to whole DTM posts, so each cell is exactly 5 × 5 DTM posts.

## Terrain (C2)

| Raster | Method |
|---|---|
| elevation | mean of the 25 valid 1 m posts in the cell |
| slope | central differences of the 5 m elevation; dx = 5·cos(lat_row) m, dy = 5 m; `atan(|∇z|)` in degrees |
| roughness | RMS, per 5 m cell, of the 1 m residual about a 9 × 9 m NaN-aware moving mean (sub-cell relief the mean hides) |
| hillshade | Lambertian, sun azimuth 315°, altitude 35°, 0–255 |

## Hazard model (prototype)

`Hazard = 0.4·SlopeRisk + 0.3·RoughnessRisk + 0.2·Obstacle + 0.1·Crater`

- SlopeRisk: piecewise linear 0°→0, 5°→10, 15°→50, 25°→90, ≥30°→100.
- RoughnessRisk: 0 m→0, 0.10→25, 0.25→60, ≥0.5→100.
- Classes: Low 0–20, Moderate 20–40, Elevated 40–60, High 60–80, Severe 80–100. No-go: slope ≥ 30°.
- **Missing factors are renormalised, not zeroed.** In v1.0 obstacle and crater do not exist, so the
  score is `(0.4·S + 0.3·R)/0.7`; treating them as 0 would understate risk everywhere.
  `model_weight_coverage = 0.7`, `renormalised = true`.
- [v1.1] Factors are switched on in `scripts/config/hazard.yaml` (`enabled_factors`). A cell where an
  enabled optional factor is NaN is renormalised over the factors present in that cell; the share of
  such cells is `partial_factor_fraction`. Each factor carries a quality tag (`factor_quality`):
  slope/roughness/crater `derived_dtm`, obstacle `proxy_geologic_units`.

### Obstacle factor (proxy) [v1.1]

No rover-scale rock map exists for Jezero. The proxy is the Mars 2020 Science Team photogeologic map
(Stack et al. 2020, 1:5 000). Each polygon has a surface unit and the bedrock unit beneath; the surface
unit decides, except thin cover (`Minor Cover Us`), where the bedrock is effectively exposed. Units map
to groups with a risk score (`scripts/config/geology_risk.yaml`): bedrock_smooth 10, regolith_fines 20,
delta_layered 30, fractured_rough 55, aeolian_bedforms 70 (wheel sinkage), blocky_talus 90.
The map is rasterised at 1 m and the majority unit is taken per 5 m cell. The THEMIS thermal-inertia
prior (`[150, 400, 800] tiu → [0, 20, 60]`, high TI = rock / bedrock) refines it:
`obstacle = max(unit_risk, ti_prior)`. The TI prior never stands in for a missing unit.

### Crater factor [v1.1]

The Robbins database is complete only for D ≥ 1 km, so it is context only. Craters relevant to a rover
are detected in the 1 m DTM itself, in overlapping tiles:
1. fill closed depressions by morphological reconstruction (`skimage.morphology.reconstruction`,
   `method="erosion"`; NoData acts as an outlet so it cannot create false basins);
2. depth = filled − DTM; connected regions with depth > 5 cm;
3. keep regions with max depth ≥ 0.5 m, equivalent diameter 5–400 m and circularity
   `4πA/P² ≥ 0.55` (all in YAML);
4. score 100 inside the depression, decaying linearly to 0 one radius outside its rim
   (Euclidean distance transform); max over craters; max per 5 m cell.

## Routing (M2, summary)

M2 searches the 5 m grid with weights over hazard, slope effort and distance (C4). It returns three
routes (`direct`, `balanced`, `cautious`) with profiles and statistics; cells with slope ≥ 30° are
impassable. Ground distances use the C1 haversine and the cos(lat) correction.

## Science targets

NASA lists the 25 samples (name, feature, sealing sol) but not coordinates. Each sample is placed at the
last MMGIS waypoint with `sol ≤ sol_sealed` (`location_method = "last rover fix on/before sealing sol"`);
`location_sol_gap = sol_sealed − location_sol` is the honest uncertainty (0 = same sol).

## Weather is a chart, not a raster [v1.1]

Weather data never becomes a 5 m map layer:
- **MCD** is a climatology for one GCM cell, far larger than the AOI → series by solar longitude (Ls),
  labelled "climatological model, not a forecast".
- **MEDA** is a point series measured at the rover → per-sol summaries (air temperature from the five
  ATS sensors, pressure, humidity, wind) shown as a chart and as popups on the waypoints of that sol,
  labelled "measured at the rover".
- **MDAD** is a regional catalogue of MARCI-observed storms → members within 1 500 km of the AOI
  (great-circle) as points with Ls / Mars-year statistics, plus a global 1° frequency image.
- **Space weather**: Mars has no global magnetosphere. DONKI CMEs are kept only when a WSA-Enlil run
  lists Mars in its impact list; SEP and X-class flare events in the window are kept but labelled
  "heliospheric, not Mars-confirmed". The crustal field (Langlais et al. 2019, degree 134) is evaluated
  with a Schmidt semi-normalised Legendre recursion: |B| ≈ 86 nT at the surface over the AOI (weak),
  ~5 nT at 200 km.

## Honesty rules

Every derived or modelled number is labelled with its `category` (`observation`, `derived`, `model`,
`simulated`). Overlays coarser than the 5 m grid carry `stats.source_res_m` (THEMIS: 100 m) so the UI
never implies 5 m detail. Present-day Mars has no liquid mud; the honest "mud" proxy is clay-bearing or
hydrated-mineral terrain, shown as a geology attribute, never as a traction hazard.
