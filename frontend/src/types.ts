export type DataCategory = 'observation' | 'derived' | 'model' | 'simulated'

export interface RasterLayer {
  id: string
  name: string
  url: string
  bounds: [[number, number], [number, number]]
  group: string
  category: DataCategory
  units: string
  source: string
  processing: string
  legend:
    | { type: 'ramp'; colors: string[]; min: number; max: number; stops?: [number, string][] }
    | { type: 'classes'; classes: { label: string; min: number; max: number; color: string }[] }
  stats?: Record<string, number>
  model?: HazardModel
}

export interface HazardModel {
  formula: string
  weights: Record<string, number>
  factor_available: Record<string, boolean>
  renormalised?: boolean
  model_weight_coverage: number
  slope_stops: [number[], number[]]
  roughness_stops: [number[], number[]]
  no_go_slope_deg: number
  classes: { min: number; max: number; label: string }[]
  class_fraction: Record<string, number>
  no_go_fraction: number
  disclaimer: string
}

export interface LayersManifest {
  aoi_bounds: [[number, number], [number, number]]
  raster_layers: RasterLayer[]
}

export interface SampleProps {
  sample_no: number
  name: string
  feature: string
  type: string
  sol_sealed: number
  rock_class: string
  location_method: string
  location_sol: number
  location_sol_gap: number
  elev_m: number
  in_aoi: boolean
  source: string
}

export interface Feature<P, G = { type: string; coordinates: number[] | number[][] | number[][][] }> {
  type: 'Feature'
  geometry: G
  properties: P
}
export interface FC<P> {
  type: 'FeatureCollection'
  features: Feature<P>[]
}

export interface Route {
  id: string
  label: string
  color: string
  coordinates: [number, number][]
  length_m: number
  hazard_mean: number
  hazard_max: number
  slope_mean_deg: number
  slope_max_deg: number
  ascent_m: number
  descent_m: number
  effort_index: number
  hazard_class_fraction: Record<string, number>
  time_h_at_assumed_pace: number
  samples_within_150m: { name: string; feature: string; distance_m: number }[]
  weights: Record<string, number>
  profile: ProfilePoint[]
}

export interface ProfilePoint { d: number; z: number; slope: number; hazard: number; lon: number; lat: number }

export interface RouteResponse {
  straight_line_m: number
  routes: Route[]
  recommended: string
  rationale: string[]
  assumptions: { pace_kmh: number; no_go_slope_deg: number; note: string }
}

export interface PointInfo {
  inside: boolean
  elevation_m?: number | null
  slope_deg?: number | null
  roughness_m?: number | null
  hazard?: number | null
}

export type LonLat = { lon: number; lat: number }
export type Tab = 'explorer' | 'layers' | 'safety' | 'science' | 'mission' | 'weather'

export interface MapViewport { center: LonLat; zoom: number; bounds: [[number, number], [number, number]] }
