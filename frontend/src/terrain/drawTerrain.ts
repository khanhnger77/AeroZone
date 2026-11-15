import type { TerrainPreview } from '../services/terrainPreview'
import { layerColor, layerRange, type TerrainLayer } from './layers'

export interface TerrainCamera { yaw: number; pitch: number; zoom: number; wireframe: boolean }
export const DEFAULT_CAMERA: TerrainCamera = { yaw: -0.45, pitch: 0.9, zoom: 1, wireframe: false }
type Vertex = { x: number; y: number; z: number }

/** Perspective projection of measured terrain triangles, rendered back-to-front. */
export function drawTerrain(canvas: HTMLCanvasElement, terrain: TerrainPreview, camera: TerrainCamera, layer: TerrainLayer = 'elevation', elevationRange?: readonly [number, number], relief?: number) {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const width = canvas.clientWidth, height = canvas.clientHeight, dpr = Math.min(devicePixelRatio || 1, 2)
  canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr)
  ctx.scale(dpr, dpr)
  const sky = ctx.createLinearGradient(0, 0, 0, height)
  sky.addColorStop(0, '#101a24'); sky.addColorStop(1, '#080b10')
  ctx.fillStyle = sky; ctx.fillRect(0, 0, width, height)
  const half = terrain.span_m / 2, step = terrain.span_m / (terrain.grid - 1)
  const centerZ = terrain.center.elevation_m!
  const distance = Math.max(terrain.span_m * 1.7, (relief ?? terrain.max_m - terrain.min_m) * 2.5) / camera.zoom
  const focal = Math.min(width, height * 1.5) * 1.1
  const cy = Math.cos(camera.yaw), sy = Math.sin(camera.yaw), cp = Math.cos(camera.pitch), sp = Math.sin(camera.pitch)
  function project(v: Vertex) {
    const x = cy * v.x - sy * v.y, y = sy * v.x + cy * v.y
    const depth = cp * y - sp * v.z
    const scale = focal / Math.max(terrain.span_m * 0.1, distance + depth)
    return { x: width / 2 + x * scale, y: height * .57 - (sp * y + cp * v.z) * scale, depth }
  }
  const vertices: (Vertex | null)[] = terrain.heights.map((z, i) => z === null ? null : { x: (i % terrain.grid) * step - half, y: half - Math.floor(i / terrain.grid) * step, z: z - centerZ })
  const range = layer === 'elevation' && elevationRange ? elevationRange : layerRange(layer, terrain.min_m, terrain.max_m)
  const triangles: { v: Vertex[]; depth: number; value: number | null }[] = []
  for (let row = 0; row < terrain.grid - 1; row++) for (let col = 0; col < terrain.grid - 1; col++) {
    const i = row * terrain.grid + col
    for (const indices of [[i, i + terrain.grid, i + 1], [i + 1, i + terrain.grid, i + terrain.grid + 1]]) {
      const v = indices.map(index => vertices[index])
      if (v.every((point): point is Vertex => point !== null)) {
        const values = indices.map(index => terrain.layers[layer][index])
        const value = values.every((v): v is number => v !== null) ? values.reduce((sum, v) => sum + v, 0) / 3 : null
        triangles.push({ v, value, depth: v.reduce((sum, p) => sum + project(p).depth, 0) / 3 })
      }
    }
  }
  triangles.sort((a, b) => b.depth - a.depth)
  for (const { v: [a, b, c], value } of triangles) {
    const ab = { x: b.x - a.x, y: b.y - a.y, z: b.z - a.z }, ac = { x: c.x - a.x, y: c.y - a.y, z: c.z - a.z }
    const normal = { x: ab.y * ac.z - ab.z * ac.y, y: ab.z * ac.x - ab.x * ac.z, z: ab.x * ac.y - ab.y * ac.x }
    const norm = Math.hypot(normal.x, normal.y, normal.z) || 1
    const light = Math.max(0, (-.45 * normal.x - .3 * normal.y + .84 * normal.z) / norm)
    const shade = .4 + .6 * light
    const base = layerColor(layer, value, range)
    const color = `rgb(${base.map(v => Math.round(v * shade)).join(',')})`
    const projected = [a, b, c].map(project)
    ctx.beginPath(); ctx.moveTo(projected[0].x, projected[0].y)
    projected.slice(1).forEach(p => ctx.lineTo(p.x, p.y)); ctx.closePath()
    ctx.fillStyle = color; ctx.fill()
    ctx.strokeStyle = camera.wireframe ? 'rgba(255,232,206,.25)' : color
    ctx.lineWidth = camera.wireframe ? .6 : .7; ctx.stroke()
  }
  const selected = project({ x: 0, y: 0, z: 0 })
  ctx.strokeStyle = '#5ee6ff'; ctx.lineWidth = 1.5
  ctx.beginPath(); ctx.moveTo(selected.x, selected.y); ctx.lineTo(selected.x, selected.y - 32); ctx.stroke()
  ctx.fillStyle = '#5ee6ff'; ctx.beginPath(); ctx.arc(selected.x, selected.y, 4, 0, Math.PI * 2); ctx.fill()
  ctx.fillStyle = '#e8eef5'; ctx.font = '10px monospace'; ctx.textAlign = 'center'
  ctx.fillText('Selected point', selected.x, selected.y - 40)
  for (const [label, x, y] of [['N', 0, half + 10], ['E', half + 10, 0], ['S', 0, -half - 10], ['W', -half - 10, 0]] as const) {
    const p = project({ x, y, z: terrain.min_m - centerZ })
    ctx.fillStyle = label === 'N' ? '#5ee6ff' : '#8494a7'; ctx.fillText(label, p.x, p.y)
  }
}
