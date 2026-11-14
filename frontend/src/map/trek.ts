/**
 * NASA Mars Trek WMTS tile services (equirectangular, 2x1 tiles at level 0 - identical to
 * Leaflet's CRS.EPSG4326 tiling, so they are used without reprojection).
 * Layer identifiers verified against each service's WMTSCapabilities.xml.
 */
export interface TrekLayer {
  id: string
  name: string
  product: string
  ext: 'png' | 'jpg'
  maxNativeZoom: number
  minZoom?: number
  instrument: string
  mission: string
  kind: 'base' | 'overlay'
  bounds?: [[number, number], [number, number]]
  resolution: string
  description: string
}

const JEZ_BOUNDS: [[number, number], [number, number]] = [[18.3068, 77.2229], [18.6693, 77.584]]
const CTX_BOUNDS: [[number, number], [number, number]] = [[18.211, 77.1605], [18.7212, 77.6992]]

export const TREK_LAYERS: TrekLayer[] = [
  {
    id: 'hirise', name: 'HiRISE orthomosaic (Jezero)', product: 'JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first_dd',
    ext: 'png', maxNativeZoom: 17, minZoom: 14, instrument: 'HiRISE', mission: 'Mars Reconnaissance Orbiter', kind: 'overlay',
    bounds: JEZ_BOUNDS, resolution: '25 cm/px (shown from zoom 14)',
    description: 'USGS Mars 2020 TRN controlled HiRISE orthomosaic (doi:10.5066/P9QJDP48), served by NASA Mars Trek.',
  },
  {
    id: 'ctx', name: 'CTX orthomosaic (Jezero)', product: 'JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0',
    ext: 'png', maxNativeZoom: 13, instrument: 'CTX', mission: 'Mars Reconnaissance Orbiter', kind: 'overlay',
    bounds: CTX_BOUNDS, resolution: '6 m/px',
    description: 'USGS Mars 2020 TRN CTX orthomosaic (Lander Vision System map), served by NASA Mars Trek.',
  },
  {
    id: 'themis_day', name: 'THEMIS IR Day (global)', product: 'THEMIS_DayIR_ControlledMosaics_100m_v2_oct2018',
    ext: 'png', maxNativeZoom: 9, instrument: 'THEMIS', mission: 'Mars Odyssey', kind: 'overlay', resolution: '100 m/px',
    description: 'Daytime thermal-infrared brightness. Dominated by slope/albedo-controlled heating (morphology).',
  },
  {
    id: 'themis_night', name: 'THEMIS IR Night (global)', product: 'THEMIS_NightIR_ControlledMosaics_100m_v2_oct2018',
    ext: 'png', maxNativeZoom: 9, instrument: 'THEMIS', mission: 'Mars Odyssey', kind: 'overlay', resolution: '100 m/px',
    description: 'Night-time thermal-infrared brightness. Qualitatively tracks thermal inertia: bright = rocky/indurated, dark = fine dust/sand.',
  },
  {
    id: 'mola', name: 'MOLA colour shaded relief', product: 'Mars_MGS_MOLA_ClrShade_merge_global_463m',
    ext: 'jpg', maxNativeZoom: 7, instrument: 'MOLA', mission: 'Mars Global Surveyor', kind: 'base', resolution: '463 m/px',
    description: 'Regional topographic context from laser altimetry.',
  },
  {
    id: 'viking', name: 'Viking MDIM 2.1 colour', product: 'Mars_Viking_MDIM21_ClrMosaic_global_232m',
    ext: 'jpg', maxNativeZoom: 7, instrument: 'VIS', mission: 'Viking Orbiter', kind: 'base', resolution: '232 m/px',
    description: 'Global colour mosaic for regional context.',
  },
]

export const trekUrl = (l: TrekLayer) =>
  `https://trek.nasa.gov/tiles/Mars/EQ/${l.product}/1.0.0/default/default028mm/{z}/{y}/{x}.${l.ext}`

export const TREK_ATTRIB = 'Tiles: <a href="https://trek.nasa.gov/mars/" target="_blank">NASA Mars Trek</a>'
