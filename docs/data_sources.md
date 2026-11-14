# Data Catalog — MARS ODYSSEY (Jezero Crater)

Study area (AOI): **77.29–77.47 °E, 18.42–18.505 °N** (planetocentric, east-positive, IAU Mars 2000 sphere R = 3 396 190 m). ≈ 9.5 × 5 km ground. Covers Perseverance sols 13–1215: landing site, Séítah, delta front, upper fan, Cheyava Falls.

Category legend: **OBS** = NASA observation · **DER** = derived by us from observations · **MOD** = our model estimate · **SIM** = simulated (none used).

## 1. Catalog

| # | Dataset | Mission / Instrument | Type | Coverage | Resolution | Format | Access (verified 2026-11-12) | Processing needed | App feature |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Mars 2020 TRN HiRISE DTM mosaic | MRO / HiRISE (USGS Astrogeology) | Topography (stereo DTM), OBS-derived | 77.2229–77.5840 E, 18.3068–18.6693 N | 1 m/post | GeoTIFF Float32, 1.8 GB, Equirectangular Mars sphere, NoData = ISIS NULL | `asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif` · doi:10.5066/P9REJ9JN (CC0) | Clip AOI, NoData mask, 5 m aggregation, slope / roughness / hillshade | Elevation, slope, roughness, hazard, routing, cursor readout |
| 2 | Mars 2020 TRN HiRISE orthomosaic | MRO / HiRISE | Optical imagery, OBS | same as #1 | 25 cm/px | 7.4 GB BigTIFF; **used as NASA Mars Trek WMTS tiles** (`JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first_dd`, z0–17) | doi:10.5066/P9QJDP48 | None (tiles) | Primary visual basemap |
| 3 | Mars 2020 TRN CTX orthomosaic | MRO / CTX | Optical imagery, OBS | 77.16–77.70 E, 18.21–18.72 N | 6 m/px | Trek WMTS (`JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0`, z0–13) | trek.nasa.gov | None | Context around HiRISE |
| 4 | Mars 2020 TRN CTX DTM | MRO / CTX | Topography | as #3 | 20 m | GeoTIFF (USGS) | Trek product `JEZ_ctx_B_soc_008_DTM_…_20m` | — | *Not used* (HiRISE DTM is better inside AOI) |
| 5 | THEMIS IR Day mosaic v2 | Mars Odyssey / THEMIS | Thermal IR brightness, OBS | Global ±65° | 100 m/px | Trek WMTS (`THEMIS_DayIR_ControlledMosaics_100m_v2_oct2018`, z0–9) | trek.nasa.gov | None (qualitative) | Thermal layer |
| 6 | THEMIS IR Night mosaic v2 | Mars Odyssey / THEMIS | Thermal IR brightness, OBS | Global ±65° | 100 m/px | Trek WMTS (`THEMIS_NightIR_…`) | trek.nasa.gov | None (qualitative proxy for thermal inertia) | Thermal layer |
| 7 | MOLA colour shaded relief | MGS / MOLA | Topography, OBS | Global | 463 m/px | Trek WMTS | trek.nasa.gov | None | Regional basemap |
| 8 | Viking MDIM 2.1 colour | Viking Orbiter | Optical, OBS | Global | 232 m/px | Trek WMTS | trek.nasa.gov | None | Regional basemap |
| 9 | Perseverance waypoints | Mars 2020 | Rover localizations, OBS | 708 fixes, sol 13–2022 | ~1 m (HiRISE-tied) | GeoJSON (lon/lat, elev, sol) | `mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json` | Strip to needed fields | Waypoints, sample locations |
| 10 | Perseverance traverse | Mars 2020 | Rover path, OBS | sol 14–2022 | — | GeoJSON LineStrings (CRS84) | `…/M20_traverse.json` | Drop Z | Traverse layer |
| 11 | Perseverance sample list | Mars 2020 | Sample metadata, OBS | 30 samples | — | Web page | science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples | Manual transcription (25 in AOI), location join by sol → DER | Science targets |
| 12 | Mars 2020 raw images | Mars 2020 cameras | Rover images, OBS | per sol | — | JSON feed + JPG | `mars.nasa.gov/rss/api/?feed=raw_images&category=mars2020&sol=N` (backend-proxied, no CORS) | None | Science image gallery |
| 13 | CRISM mineral maps (Jezero) | MRO / CRISM | Spectral / mineral | Jezero (various) | 18–100 m | Zenodo / MOCAAS / paper supplements | Zenodo rec. 5575824 (rate-limited at check); MOCAAS | Georeference check, legend | **Not integrated yet** |

## 2. Ranking (1 = worst, 5 = best)

| Dataset | Sci. relevance | Accessibility | Processing ease | Integration time | Demo value | Total |
|---|---|---|---|---|---|---|
| HiRISE DTM (#1) | 5 | 5 | 4 | 4 | 5 | **23** |
| HiRISE ortho tiles (#2) | 4 | 5 | 5 | 5 | 5 | **24** |
| Perseverance traverse/waypoints (#9–10) | 5 | 5 | 5 | 5 | 5 | **25** |
| Sample list (#11) | 5 | 4 | 4 | 4 | 5 | 22 |
| Raw images (#12) | 3 | 4 | 5 | 4 | 5 | 21 |
| THEMIS IR tiles (#5–6) | 3 | 5 | 5 | 5 | 3 | 21 |
| CTX ortho (#3) | 3 | 5 | 5 | 5 | 3 | 21 |
| MOLA / Viking (#7–8) | 2 | 5 | 5 | 5 | 3 | 20 |
| CRISM (#13) | 5 | 2 | 2 | 2 | 4 | 15 |

## 3. MVP selection (first three)

1. **HiRISE orthoimagery** (optical) — via Mars Trek tiles, zero download.
2. **HiRISE 1 m DTM** (topographic) — source of all terrain analysis.
3. **Perseverance rover data** (in-situ / science) — traverse, waypoints, sample sites, raw images.

Plus free extras: THEMIS thermal, CTX, MOLA, Viking context tiles.

## 4. Verified facts

- DTM CRS: Equirectangular, standard parallel 0°, central meridian 0°, Mars 2000 sphere — read from the GeoTIFF and the ISIS label, not assumed.
- AOI fully inside DTM, 0 % NoData.
- DTM vs rover elevation (`elev_geoid`) at 612 waypoints: median diff +0.005 m, |median| 0.03 m, p05/p95 −0.09/+4.1 m. Rover fixes are tied to HiRISE basemaps, so this confirms consistency, not independent accuracy. See `data/metadata/validation_report.json`.
- Provenance with sha256: `data/metadata/sources.json`.
