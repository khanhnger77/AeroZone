import { useEffect, useId, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { Minus, Plus, RotateCcw } from 'lucide-react'
import type { LonLat } from '../types'
import { loadTerrainPreview, type TerrainPreview } from '../services/terrainPreview'
import { DEFAULT_CAMERA, drawTerrain, type TerrainCamera } from '../terrain/drawTerrain'
import { TERRAIN_LAYERS, layerRange, type TerrainLayer } from '../terrain/layers'

export interface TerrainViewControls {
  layer: TerrainLayer; setLayer: (layer: TerrainLayer) => void
  camera: TerrainCamera; setCamera: Dispatch<SetStateAction<TerrainCamera>>
  elevationRange?: readonly [number, number]; relief?: number
}
export default function TerrainPointView({ point, online, controls, onData, label }: {
  point: LonLat; online: boolean; controls?: TerrainViewControls; onData?: (data: TerrainPreview) => void; label?: string
}) {
  const helpId = useId()
  const onDataRef = useRef(onData)
  onDataRef.current = onData
  const [data, setData] = useState<TerrainPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState(0)
  const [revision, setRevision] = useState(0)
  const [localLayer, setLocalLayer] = useState<TerrainLayer>('elevation')
  const [localCamera, setLocalCamera] = useState(DEFAULT_CAMERA)
  const layer = controls?.layer ?? localLayer, setLayer = controls?.setLayer ?? setLocalLayer
  const camera = controls?.camera ?? localCamera, setCamera = controls?.setCamera ?? setLocalCamera
  const [size, setSize] = useState(0)
  const canvas = useRef<HTMLCanvasElement>(null)
  const drag = useRef<{ x: number; y: number } | null>(null)
  useEffect(() => {
    setData(null); setError(null); setProgress(0); setLocalCamera(DEFAULT_CAMERA)
    if (!online) return
    let active = true
    const controller = new AbortController()
    loadTerrainPreview(point, controller.signal, p => { if (active) setProgress(p) })
      .then(result => { if (active) { setData(result); onDataRef.current?.(result) } })
      .catch(error => { if (active) setError(error instanceof Error ? error.message : 'Terrain could not load. Please retry.') })
    return () => { active = false; controller.abort() }
  }, [point, online, revision])
  useEffect(() => {
    const el = canvas.current
    if (!el) return
    const observer = new ResizeObserver(() => setSize(s => s + 1))
    observer.observe(el)
    const wheel = (event: WheelEvent) => {
      event.preventDefault()
      setCamera(c => ({ ...c, zoom: Math.max(.65, Math.min(2.2, c.zoom * (event.deltaY < 0 ? 1.08 : .92))) }))
    }
    el.addEventListener('wheel', wheel, { passive: false })
    return () => { observer.disconnect(); el.removeEventListener('wheel', wheel) }
  }, [data, setCamera])
  useEffect(() => {
    if (!data || !canvas.current) return
    const frame = requestAnimationFrame(() => { if (canvas.current) drawTerrain(canvas.current, data, camera, layer, controls?.elevationRange, controls?.relief) })
    return () => cancelAnimationFrame(frame)
  }, [data, camera, layer, size, controls?.elevationRange, controls?.relief])
  if (!online) return <div className="grid min-h-[270px] place-items-center p-6 text-center text-sm text-muted">Connect the Planner to load elevation data. Panorama images can be viewed independently.</div>
  if (error) return <div role="alert" className="grid min-h-[270px] place-content-center gap-3 p-6 text-center"><p className="text-sm text-muted">{error}</p><button type="button" onClick={() => setRevision(r => r + 1)} className="btn-ghost mx-auto">Retry terrain</button></div>
  if (!data) return <div role="status" className="grid min-h-[270px] place-content-center gap-3 p-6 text-center"><div className="text-sm">Loading measured terrain… {progress}%</div><progress aria-label="Loading terrain" value={progress} max={100} className="h-1.5 w-56 accent-mars" /><p className="text-[11px] text-muted">Sampling a 160 × 160 m patch around this point.</p></div>
  const selectedLayer = TERRAIN_LAYERS.find(item => item.id === layer)!
  const range = layer === 'elevation' && controls?.elevationRange ? controls.elevationRange : layerRange(layer, data.min_m, data.max_m)
  const missing = data.heights.some((height, i) => height !== null && data.layers[layer][i] === null)
  return <div>
    {!controls && <div role="group" aria-label="Terrain 3D layers" className="grid grid-cols-4 gap-1 border-b border-line p-2">
      {TERRAIN_LAYERS.map(item => <button key={item.id} type="button" aria-pressed={layer === item.id}
        disabled={!data.layers[item.id].some(value => value !== null)} onClick={() => setLayer(item.id)}
        className={`rounded-lg px-2 py-2 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-30 ${layer === item.id ? 'bg-cyan/10 text-cyan' : 'text-muted hover:bg-white/5 hover:text-ink'}`}>{item.label}</button>)}
    </div>}
    <div className="relative">
      <canvas ref={canvas} tabIndex={0} role="img" aria-label={label ? `Interactive 3D terrain ${label}` : 'Interactive 3D terrain around selected point'} aria-describedby={helpId}
        className="h-[280px] w-full cursor-grab outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan active:cursor-grabbing" style={{ touchAction: 'none' }}
        onPointerDown={e => { if (e.button !== 0) return; drag.current = { x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId) }}
        onPointerMove={e => { if (!drag.current) return; const dx = e.clientX - drag.current.x, dy = e.clientY - drag.current.y; drag.current = { x: e.clientX, y: e.clientY }; setCamera(c => ({ ...c, yaw: c.yaw + dx * .01, pitch: Math.max(.25, Math.min(1.5, c.pitch + dy * .008)) })) }}
        onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }} onLostPointerCapture={() => { drag.current = null }}
        onKeyDown={e => {
          if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '=', '-', '0'].includes(e.key)) return
          e.preventDefault()
          if (e.key === '0') { setCamera(DEFAULT_CAMERA); return }
          setCamera(c => ({ ...c, yaw: c.yaw + (e.key === 'ArrowLeft' ? -.12 : e.key === 'ArrowRight' ? .12 : 0), pitch: Math.max(.25, Math.min(1.5, c.pitch + (e.key === 'ArrowUp' ? .08 : e.key === 'ArrowDown' ? -.08 : 0))), zoom: Math.max(.65, Math.min(2.2, c.zoom * (['+', '='].includes(e.key) ? 1.1 : e.key === '-' ? .9 : 1))) }))
        }} />
      <span className="pointer-events-none absolute left-3 top-3 rounded bg-void/75 px-2 py-1 font-mono text-[10px] text-muted">160 m patch · true height scale</span>
      <div className="absolute right-3 top-3 flex flex-col gap-1">
        <button type="button" aria-label="Zoom terrain in" onClick={() => setCamera(c => ({ ...c, zoom: Math.min(2.2, c.zoom * 1.15) }))} className="glass grid h-8 w-8 place-items-center rounded"><Plus size={14} /></button>
        <button type="button" aria-label="Zoom terrain out" onClick={() => setCamera(c => ({ ...c, zoom: Math.max(.65, c.zoom / 1.15) }))} className="glass grid h-8 w-8 place-items-center rounded"><Minus size={14} /></button>
        <button type="button" aria-label="Reset terrain view" onClick={() => setCamera(DEFAULT_CAMERA)} className="glass grid h-8 w-8 place-items-center rounded"><RotateCcw size={13} /></button>
      </div>
    </div>
    <div className="space-y-2 border-t border-line px-4 py-3">
      <div aria-label={`${selectedLayer.label} color scale`} className="space-y-1">
        <div className="flex justify-between text-[10px] text-muted"><span>{selectedLayer.label} · {selectedLayer.unit}{layer === 'elevation' ? ' above MOLA areoid' : ''}</span><span>Point: {data.center[selectedLayer.field]?.toFixed(layer === 'roughness' ? 2 : 1) ?? 'No data'} {selectedLayer.unit}</span></div>
        <div className="h-2 rounded" style={{ background: `linear-gradient(90deg, ${selectedLayer.colors.map(rgb => `rgb(${rgb.join(',')})`).join(',')})` }} />
        <div className="flex justify-between font-mono text-[9px] text-muted"><span>{range[0].toFixed(layer === 'roughness' ? 2 : 0)}</span><span>{range[1].toFixed(layer === 'roughness' ? 2 : 0)}{layer !== 'elevation' ? '+' : ''}</span></div>
        {missing && <p className="text-[10px] text-muted">Gray areas have no {selectedLayer.label.toLowerCase()} data.</p>}
      </div>
      <div className="flex items-center justify-between gap-3 text-[11px]"><p id={helpId} className="text-muted">Drag to orbit · scroll to zoom · arrow keys to rotate</p><label className="flex items-center gap-1.5"><input type="checkbox" checked={camera.wireframe} onChange={e => setCamera(c => ({ ...c, wireframe: e.target.checked }))} />Mesh</label></div>
      <div className="grid grid-cols-3 gap-3 font-mono text-[11px]">
        <div><div className="text-[9px] text-muted">POINT ELEVATION</div>{data.center.elevation_m?.toFixed(1)} m</div>
        <div><div className="text-[9px] text-muted">POINT SLOPE</div>{data.center.slope_deg?.toFixed(1) ?? '—'}°</div>
        <div><div className="text-[9px] text-muted">PATCH RELIEF</div>{(data.max_m - data.min_m).toFixed(1)} m</div>
      </div>
      <p className="text-[10px] leading-relaxed text-muted">HiRISE DTM · 10 m preview mesh sampled from the 5 m analysis grid. Shape uses measured elevation; colours show the selected analysis layer with terrain lighting. Hazard is a prototype model score.</p>
      {data.valid < data.grid ** 2 && <p className="text-[10px] text-amber-200">Partial terrain coverage: areas without elevation data are left empty.</p>}
    </div>
  </div>
}
