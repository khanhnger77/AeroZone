# Data sources

Provenance of every downloaded file (url, size, sha256, retrieval time, citation) is written by the
pipeline to `data/metadata/sources.json` (v1.0) and `data/metadata/env_sources.json` (v1.1); both are
tracked in git. Download URLs live in `scripts/download_data.py` (v1.0) and
`scripts/config/env_sources.yaml` (v1.1).

## Core sources (v1.0, `pixi run data`)

| id | Dataset | URL | Resolution | Licence | Verification |
|---|---|---|---|---|---|
| `hirise_dtm` | HiRISE DTM mosaic of Jezero, MOLA-controlled, heights above the MOLA areoid (USGS / Mars 2020 TRN) | `https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif` (~1.8 GB) | 1 m, 21 400 × 21 488 | public domain | `validate_data.py`: CRS = Mars 2000 sphere equirectangular; AOI inside; DTM vs rover `elev_geoid` at waypoints |
| `hirise_dtm_label` | ISIS label of the DTM (`JEZ_DTM_1m.lbl`) | same S3 prefix, `.lbl` | | public domain | |
| `m20_waypoints` | Mars 2020 MMGIS localised waypoints (708) | `https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json` | per drive | NASA/JPL | inside DTM / AOI counts |
| `m20_traverse` | Mars 2020 MMGIS traverse (559 sol polylines) | `https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json` | per drive | NASA/JPL | |
| samples | NASA Mars Rock Samples (25, hard-coded table) | <https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples> | | NASA | placed on the last fix ≤ sealing sol |

## Environment & geology (v1.1, `pixi run data-env`)

### Geology, surface materials, craters

| id | Dataset | Link | Resolution / covers AOI | Format, CRS | Used for | Verified (Nov 2026) |
|---|---|---|---|---|---|---|
| `stack2020_units` | Photogeologic map of the Perseverance field site, Mars 2020 Science Team (Stack et al. 2020) | ESM zip `https://static-content.springer.com/esm/art%3A10.1007%2Fs11214-020-00739-x/MediaObjects/11214_2020_739_MOESM3_ESM.zip` (landing: <https://link.springer.com/article/10.1007/s11214-020-00739-x>) | 1:5 000, **yes** (100 % of the grid) | Shapefile, Mars 2000 sphere equidistant cylindrical; attributes `quadname, SurfUName, BedrkUName` | `geologic_units`, obstacle proxy | URL stable, 3.5 MB |
| `themis_ti` | THEMIS quantitative thermal inertia mosaic, tile **00N060E** (0–30 °N, 60–120 °E) | `https://asc-astropedia.s3.us-west-2.amazonaws.com/Mars/Odyssey/THEMIS-Global-Thermal-Inertia-Mosaic/Quantitative-32-Bit/THEMIS_TI_Mosaic_Quant_00N060E_100mpp.cub` | 100 m, ~20 % absolute error; yes (coarse) | ISIS3 cube, sphere R = 3 396 190 (GDAL reads it as a sphere) | `thermal_inertia`, TI prior | tile is 2.5 GB: only the AOI window is read through `/vsicurl/` |
| `noah_h_jezero` | NOAH-H deep-learning terrain classification of Jezero (Wright et al. 2022) | <https://doi.org/10.21954/ou.rd.17121482.v1> (figshare file 33917705) | **25 cm** mosaic, 14 descriptive classes; covers 84 % of the grid (west edge missing) | GeoTIFF uint8, C1 CRS; class names from `DescriptiveClasses_rank_DC.lyrx` | `terrain_class` | zip 352 MB, 7.8 GB unpacked: only `NOAH-H_mosaics/` is extracted |
| `robbins_craters` | Mars crater database (Robbins & Hynek 2012), USGS OGC API | `https://astrogeology.usgs.gov/pygeoapi/collections/mars/robbinsv1/items?bbox=77.29,18.42,77.47,18.505&f=json&limit=1000` | complete for D ≥ 1 km; **0 craters in the AOI** | GeoJSON, `lon_e` −180…180 | context only | |
| `sim3464` | Geologic map of Jezero and Nili Planum, USGS SIM 3464 (Sun & Stack 2020) | <https://pubs.usgs.gov/publication/sim3464> | 1:75 000 | PDF + GIS | context | not used by the pipeline |
| `crumpler2023` | In-situ geologic map along the traverse (Crumpler et al. 2023) | <https://zenodo.org/records/7545299> | traverse | Shapefile | optional annotation | not used yet |
| `crism_jezero` | CRISM mineralogy (carbonates, clays, olivine) | <https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8243932/> | ~18 m | per paper | the honest "mud" proxy | raster release unverified |
| `sim3292` | Geologic Map of Mars (Tanaka et al. 2014) | <https://pubs.usgs.gov/publication/sim3292> | 1:20 M | Shapefile | globe only | link unverified |
| `tes_rock` | TES rock abundance (Nowicki & Christensen 2007) | PDS / ASU | ~3 km+ | raster | globe only | link unverified |
| (ref.) | InSight HiRISE rock CFA products (Golombek et al.) | <https://data.caltech.edu/records/0ah85-w5237> | not Jezero | | method template for a measured obstacle factor | |
| (ref.) | Jezero landing hazard map (JPL) | <https://www.jpl.nasa.gov/images/jezeros-hazard-map> | image | | visual sanity check | |

### Atmosphere, weather, dust storms

| id | Dataset | Link | Kind | Map layer? | Notes |
|---|---|---|---|---|---|
| `mcd` | Mars Climate Database v6.1 (LMD / ESA / CNES) | <https://www-mars.lmd.jussieu.fr/> | GCM climatology | **no** (one GCM cell ≫ AOI) → chart by Ls | `manual: true`: request access, export the AOI-centre column for `climatology` and `dust_storm` at 14 h LT, Ls 0–345 step 15, into `data/raw/climate/mcd/mcd_<scenario>.{nc,txt}` |
| `meda` | Perseverance MEDA (PDS Atmospheres Node), doi:10.17189/1522849 | <https://atmos.nmsu.edu/PDS/data/PDS4/Mars2020/mars2020_meda/> | point series at the rover | no → per-sol chart, waypoint popups | Release 16 (2026-08-11). Per sol: `data_derived_env/…/DER_PS` (pressure, Pa), `data_calibrated_env/…/CAL_ATS` (air temperature, 5 sensors, K), `DER_RHS` (RH %), `DER_WS` (wind, when present). Default: 12 evenly spaced traverse sols (~6 MB each) |
| `mdad` | Mars Dust Activity Database v1.0 (Battalio & Wang 2021) | `https://dataverse.harvard.edu/api/access/datafile/3607412` (`MDAD.tab`) | catalogue, MY24–MY32 | global 1° frequency image; points near Jezero | tab-separated; CC0 |
| (ref.) | MARCI daily global maps (MSSS) | PDS Imaging | images | | source of MDAD |
| (ref.) | MCS / TES dust opacity | <https://pds-atmospheres.nmsu.edu/> | retrievals | gridded only | prefer the LMD gridded product |
| (ref.) | Curiosity REMS | <https://pds-atmospheres.nmsu.edu/> | point, Gale | no | comparison only |

### Space weather, magnetic field

| id | Dataset | Link | Kind | Notes |
|---|---|---|---|---|
| `donki` | NASA DONKI (CCMC) | `https://ccmc.gsfc.nasa.gov/DONKI-API/get/` | event catalogue JSON, no key | replaces `kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/` since 2026-09-30. **Windows > 60 days are rejected** (`API Error: Date range cannot exceed 60 days`), so the script fetches 59-day chunks |
| `langlais2019` | Crustal field model, MGS + MAVEN (Langlais et al. 2019) | `https://zenodo.org/records/3876714/files/Langlais2019.sh.gz` | SH coefficients to degree 134, reference radius 3 393.5 km, nT | CC BY 4.0 |
| (ref.) | MGS MAG/ER crustal field map | <https://catalog.data.gov/dataset/mgs-mars-er-5-field-map-v1-0> | global ASCII | alternative |
| (ref.) | MAVEN SEP / MAG / SWIA, MSL RAD | <https://pds-ppi.igpp.ucla.edu/> | time series | phase 2 |

### Tile services (reference for M3; nothing produced by M1)

| Service | Endpoint |
|---|---|
| NASA Mars Trek WMTS | `https://trek.nasa.gov/tiles/Mars/EQ/{layer}/1.0.0/WMTSCapabilities.xml` |
| USGS STAC | <https://stac.astrogeology.usgs.gov/api> |

## Deprecated — do not use

| API | Why |
|---|---|
| InSight weather `https://api.nasa.gov/insight_weather/` | frozen at sols ~675–681 (Oct 2020); mission ended 2022-12-21; Elysium Planitia, not Jezero. Never present it as "current weather" |
| DONKI `https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/` | superseded by `ccmc.gsfc.nasa.gov/DONKI-API/get/` (2026-09-30) |
| DONKI via `https://api.nasa.gov/DONKI/*` | reportedly redirects (developer reports); use the CCMC base directly |

## Caveats

- **CRS radius.** Every source is checked for the C1 sphere before reprojection
  (`process_geology.force_sphere`); an ellipsoid is replaced by the sphere with a warning.
- **Longitude domains** are normalised to −180…180 in code (`config.normalise_lon`).
- **Resampling.** Class rasters: majority / nearest only. 1 m → 5 m continuous: mean. THEMIS 100 m →
  5 m: bilinear, with `source_res_m = 100` recorded.
- **Datum.** Elevation products stay on the MOLA areoid.
- **CORS.** MMGIS has no CORS headers, hence the static snapshot.
