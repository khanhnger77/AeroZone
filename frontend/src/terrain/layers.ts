export type TerrainLayer = 'elevation' | 'slope' | 'roughness' | 'hazard'
type RGB = readonly [number, number, number]
export const TERRAIN_LAYERS: { id: TerrainLayer; label: string; unit: string; field: 'elevation_m' | 'slope_deg' | 'roughness_m' | 'hazard'; colors: readonly RGB[]; range?: readonly [number, number] }[] = [
  { id: 'elevation', label: 'Elevation', unit: 'm', field: 'elevation_m', colors: [[38, 74, 117], [70, 163, 151], [211, 189, 121], [235, 230, 211]] },
  { id: 'slope', label: 'Slope', unit: '°', field: 'slope_deg', range: [0, 30], colors: [[72, 165, 118], [231, 204, 94], [224, 120, 66], [180, 48, 61]] },
  { id: 'roughness', label: 'Roughness', unit: 'm', field: 'roughness_m', range: [0, 0.5], colors: [[54, 91, 126], [66, 177, 185], [174, 120, 188], [234, 175, 213]] },
  { id: 'hazard', label: 'Hazard', unit: '/100', field: 'hazard', range: [0, 100], colors: [[63, 164, 113], [221, 201, 87], [227, 125, 66], [184, 49, 66]] },
]
export function layerRange(layer: TerrainLayer, min: number, max: number): readonly [number, number] {
  return TERRAIN_LAYERS.find(item => item.id === layer)!.range ?? [min, max]
}
export function layerColor(layer: TerrainLayer, value: number | null, range: readonly [number, number]): RGB {
  if (value === null || !Number.isFinite(value)) return [90, 98, 109]
  const colors = TERRAIN_LAYERS.find(item => item.id === layer)!.colors
  const fraction = range[1] === range[0] ? .5 : Math.max(0, Math.min(1, (value - range[0]) / (range[1] - range[0])))
  const position = fraction * (colors.length - 1), i = Math.min(colors.length - 2, Math.floor(position)), t = position - i
  return colors[i].map((v, j) => Math.round(v + (colors[i + 1][j] - v) * t)) as unknown as RGB
}
