import type { FC, LayersManifest, LonLat, PointInfo, RouteResponse, SampleProps } from '../types'

async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`
    try {
      const j = await r.json()
      if (j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* not JSON */ }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

export const loadManifest = () => getJSON<LayersManifest>('data/layers.json')
export const loadSamples = () => getJSON<FC<SampleProps>>('data/samples.geojson')
export const loadTraverse = () => getJSON<FC<{ sol: number; length_m: number }>>('data/traverse.geojson')
export const loadWaypoints = () =>
  getJSON<FC<{ sol: number; lon: number; lat: number; elev_geoid: number; dist_total_m: number; Note: string }>>(
    'data/waypoints.geojson',
  )

export const getPoint = (p: LonLat, signal?: AbortSignal) =>
  getJSON<PointInfo>(`/api/point?lon=${p.lon}&lat=${p.lat}`, { signal })

export const planRoute = (start: LonLat, end: LonLat) =>
  getJSON<RouteResponse>('/api/route', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ start, end }),
  })

export interface RawImage {
  imageid: string
  sol: number
  caption: string
  date_taken_utc: string
  camera: { instrument: string }
  image_files: { small: string; medium: string; large: string; full_res: string }
  credit: string
}

/** NASA Mars 2020 raw-image feed, proxied by the backend (browser CORS is blocked).
 *  Images are tied to a sol, not a precise position. Science cameras are ranked first. */
export async function rawImagesForSol(sol: number, n = 12): Promise<RawImage[]> {
  const j = await getJSON<{ images: RawImage[] }>(`/api/raw-images?sol=${sol}&n=${n}`)
  return j.images
}
