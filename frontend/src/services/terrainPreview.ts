import { getPoint } from './api'
import { MARS_RADIUS_M } from '../map/geo'
import type { LonLat, PointInfo } from '../types'

export const PREVIEW_SPAN_M = 160
export const PREVIEW_GRID = 17
export interface TerrainPreview {
  point: LonLat
  center: PointInfo
  heights: (number | null)[] // north-to-south rows, west-to-east columns; MOLA areoid metres
  grid: number
  span_m: number
  min_m: number
  max_m: number
  valid: number
}
const cache = new Map<string, TerrainPreview>()

/** Sample the existing C4 point endpoint. No sim service or invented heights are used. */
export async function loadTerrainPreview(point: LonLat, signal: AbortSignal, onProgress: (percent: number) => void): Promise<TerrainPreview> {
  const key = `${point.lon.toFixed(7)},${point.lat.toFixed(7)}`
  const cached = cache.get(key)
  if (cached) { signal.throwIfAborted(); onProgress(100); return cached }
  const controller = new AbortController()
  const abort = () => controller.abort(signal.reason)
  signal.addEventListener('abort', abort, { once: true })
  const timer = setTimeout(() => controller.abort(new Error('Terrain loading timed out. Please retry.')), 30000)
  try {
    signal.throwIfAborted()
    const center = await getPoint(point, controller.signal)
    if (!center.inside) throw new Error('This point is outside the analysed terrain. Choose a point inside the Jezero study area.')
    if (center.elevation_m == null || !Number.isFinite(center.elevation_m)) throw new Error('No elevation data at this point. Choose a nearby location.')
    const count = PREVIEW_GRID ** 2, middle = (PREVIEW_GRID - 1) / 2
    const heights: (number | null)[] = Array(count).fill(null)
    heights[middle * PREVIEW_GRID + middle] = center.elevation_m
    const step = PREVIEW_SPAN_M / (PREVIEW_GRID - 1)
    const lonPerM = 180 / Math.PI / (MARS_RADIUS_M * Math.cos(point.lat * Math.PI / 180))
    const latPerM = 180 / Math.PI / MARS_RADIUS_M
    let next = 0, completed = 1
    async function worker() {
      while (next < count) {
        controller.signal.throwIfAborted()
        const i = next++
        if (i === middle * PREVIEW_GRID + middle) continue
        const row = Math.floor(i / PREVIEW_GRID), col = i % PREVIEW_GRID
        const info = await getPoint({ lon: point.lon + (col - middle) * step * lonPerM, lat: point.lat + (middle - row) * step * latPerM }, controller.signal)
        heights[i] = info.inside && info.elevation_m != null && Number.isFinite(info.elevation_m) ? info.elevation_m : null
        completed++
        if (completed % 12 === 0 || completed === count) onProgress(Math.round(completed / count * 100))
      }
    }
    await Promise.all(Array.from({ length: 8 }, worker))
    const values = heights.filter((h): h is number => h !== null)
    let faces = 0
    for (let row = 0; row < PREVIEW_GRID - 1; row++) for (let col = 0; col < PREVIEW_GRID - 1; col++) {
      const i = row * PREVIEW_GRID + col
      if ([i, i + PREVIEW_GRID, i + 1].every(index => heights[index] !== null)) faces++
      if ([i + 1, i + PREVIEW_GRID, i + PREVIEW_GRID + 1].every(index => heights[index] !== null)) faces++
    }
    if (faces < 2) throw new Error('Not enough terrain coverage here. Choose a point farther inside the study area.')
    const result = { point, center, heights, grid: PREVIEW_GRID, span_m: PREVIEW_SPAN_M, min_m: Math.min(...values), max_m: Math.max(...values), valid: values.length }
    signal.throwIfAborted()
    if (cache.size >= 8) cache.delete(cache.keys().next().value!)
    cache.set(key, result)
    onProgress(100)
    return result
  } catch (error) {
    if (controller.signal.aborted && !signal.aborted) throw new Error('Terrain loading timed out. Please retry.')
    if (!signal.aborted && error instanceof Error && (error instanceof TypeError || /^5\d\d\b/.test(error.message))) {
      throw new Error('Elevation data is unavailable. Check the Planner connection and retry.')
    }
    throw error
  } finally {
    controller.abort()
    clearTimeout(timer)
    signal.removeEventListener('abort', abort)
  }
}
