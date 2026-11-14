/** Mars geodesy helpers. Leaflet's built-in distance uses Earth's radius - never use it here. */
export const MARS_RADIUS_M = 3_396_190

export function haversineM(lon1: number, lat1: number, lon2: number, lat2: number): number {
  const r = Math.PI / 180
  const p1 = lat1 * r, p2 = lat2 * r
  const a = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(((lon2 - lon1) * r) / 2) ** 2
  return 2 * MARS_RADIUS_M * Math.asin(Math.sqrt(a))
}

export const fmtDist = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`)

export const fmtLat = (v: number) => `${Math.abs(v).toFixed(5)}°${v >= 0 ? 'N' : 'S'}`
export const fmtLon = (v: number) => `${Math.abs(v).toFixed(5)}°${v >= 0 ? 'E' : 'W'}`

// Landmark coordinates are rover localizations from the JPL MMGIS waypoint file.
export const LANDING_SITE = { lon: 77.45088572, lat: 18.44462715, name: 'Octavia E. Butler Landing (sol 13 fix)' }
