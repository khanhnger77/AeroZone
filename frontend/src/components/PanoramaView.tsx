import { useEffect, useRef, useState } from 'react'
import { ExternalLink, Minus, Plus, RotateCcw } from 'lucide-react'
import { nearbyPanoramas, PANORAMAS } from '../data/panoramas'
import { fmtDist } from '../map/geo'
import type { LonLat } from '../types'

export default function PanoramaView({ point, onVisit }: { point: LonLat; onVisit: (point: LonLat & { name?: string }) => void }) {
  const sites = nearbyPanoramas(point)
  const [siteId, setSiteId] = useState(sites[0]?.id ?? '')
  const site = sites.find(p => p.id === siteId) ?? sites[0]
  const imageUrl = site?.image
  const [image, setImage] = useState<HTMLImageElement | null>(null)
  const [error, setError] = useState(false)
  const [revision, setRevision] = useState(0)
  const [camera, setCamera] = useState({ yaw: 0, zoom: 1, vertical: 0 })
  const [size, setSize] = useState(0)
  const canvas = useRef<HTMLCanvasElement>(null)
  const drag = useRef<{ x: number; y: number } | null>(null)
  useEffect(() => {
    setImage(null); setError(false); setCamera({ yaw: 0, zoom: 1, vertical: 0 })
    if (!imageUrl) return
    const img = new Image()
    img.onload = () => setImage(img)
    img.onerror = () => setError(true)
    img.src = new URL(imageUrl, document.baseURI).href
    return () => { img.onload = null; img.onerror = null; img.removeAttribute('src') }
  }, [imageUrl, revision]) // image identity, not distance to the selected point
  useEffect(() => {
    const el = canvas.current
    if (!el) return
    const observer = new ResizeObserver(() => setSize(v => v + 1)); observer.observe(el)
    const wheel = (e: WheelEvent) => { e.preventDefault(); setCamera(c => ({ ...c, zoom: Math.max(1, Math.min(4, c.zoom * (e.deltaY < 0 ? 1.1 : .9))) })) }
    el.addEventListener('wheel', wheel, { passive: false })
    return () => { observer.disconnect(); el.removeEventListener('wheel', wheel) }
  }, [image])
  useEffect(() => {
    const el = canvas.current, ctx = el?.getContext('2d')
    if (!el || !ctx || !image) return
    const dpr = Math.min(devicePixelRatio || 1, 2), width = el.clientWidth, height = el.clientHeight
    el.width = width * dpr; el.height = height * dpr; ctx.scale(dpr, dpr)
    ctx.fillStyle = '#04060a'; ctx.fillRect(0, 0, width, height)
    const scale = height / image.naturalHeight * camera.zoom
    const fullWidth = image.naturalWidth * scale, fullHeight = image.naturalHeight * scale
    const fraction = ((camera.yaw / 360 + .5) % 1 + 1) % 1
    const x = width / 2 - fraction * fullWidth
    const y = (height - fullHeight) / 2 + camera.vertical * (fullHeight - height) / 2
    for (let k = -2; k <= 2; k++) ctx.drawImage(image, x + k * fullWidth, y, fullWidth, fullHeight)
  }, [image, camera, size])
  if (!site) return <div className="space-y-3 p-5"><h3 className="text-sm font-semibold">No panorama within 500 m</h3><p className="text-xs leading-relaxed text-muted">This point has no nearby image in the curated collection. Explore one of the photographed rover locations:</p>{PANORAMAS.map(p => <button key={p.id} type="button" onClick={() => onVisit(p)} className="btn-ghost w-full justify-between">{p.name}<span>360°</span></button>)}</div>
  return <div>
    <div className="space-y-1.5 px-4 py-3">
      <label className="flex items-center justify-between gap-3 text-xs text-muted">Photo location
        <select aria-label="Panorama location" value={site.id} onChange={e => setSiteId(e.target.value)} className="min-w-0 max-w-[70%] rounded border border-line-2 bg-panel-2 px-2 py-1.5 text-ink">{sites.map(p => <option key={p.id} value={p.id}>{p.name} · sol {p.sols}</option>)}</select>
      </label>
      <p className="text-[10px] leading-relaxed text-muted">Approx. {fmtDist(site.distance_m)} from the selected point · {site.locationNote}</p>
    </div>
    {error ? <div role="alert" className="grid min-h-[260px] place-content-center gap-3 p-6 text-center"><p className="text-sm text-muted">Panorama image could not load.</p><button type="button" className="btn-ghost mx-auto" onClick={() => setRevision(v => v + 1)}>Retry panorama</button></div> : !image ? <div role="status" className="grid min-h-[260px] place-items-center text-sm text-muted">Loading panorama…</div> : <div className="relative">
      <canvas ref={canvas} role="img" tabIndex={0} aria-label={`Interactive 360-degree panorama at ${site.name}`} aria-describedby="panorama-controls-help" className="h-[260px] w-full cursor-grab outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan active:cursor-grabbing" style={{ touchAction: 'none' }}
        onPointerDown={e => { if (e.button !== 0) return; drag.current = { x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId) }}
        onPointerMove={e => { if (!drag.current) return; const dx = e.clientX - drag.current.x, dy = e.clientY - drag.current.y; drag.current = { x: e.clientX, y: e.clientY }; setCamera(c => ({ ...c, yaw: (c.yaw - dx * .25 / c.zoom + 360) % 360, vertical: Math.max(-1, Math.min(1, c.vertical + dy * .01)) })) }}
        onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }} onLostPointerCapture={() => { drag.current = null }}
        onKeyDown={e => {
          if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '=', '-', '0'].includes(e.key)) return
          e.preventDefault()
          if (e.key === '0') { setCamera({ yaw: 0, zoom: 1, vertical: 0 }); return }
          setCamera(c => ({ ...c, yaw: (c.yaw + (e.key === 'ArrowLeft' ? -8 : e.key === 'ArrowRight' ? 8 : 0) + 360) % 360, vertical: Math.max(-1, Math.min(1, c.vertical + (e.key === 'ArrowUp' ? .1 : e.key === 'ArrowDown' ? -.1 : 0))), zoom: Math.max(1, Math.min(4, c.zoom * (['+', '='].includes(e.key) ? 1.15 : e.key === '-' ? 1 / 1.15 : 1))) }))
        }} />
      <span className="pointer-events-none absolute left-3 top-3 rounded bg-void/75 px-2 py-1 font-mono text-[10px] text-white">360° horizontal · {Math.round(camera.yaw)}° image azimuth</span>
      <div className="absolute right-3 top-3 flex flex-col gap-1">
        <button type="button" aria-label="Zoom panorama in" className="glass grid h-8 w-8 place-items-center rounded" onClick={() => setCamera(c => ({ ...c, zoom: Math.min(4, c.zoom * 1.15) }))}><Plus size={14} /></button>
        <button type="button" aria-label="Zoom panorama out" className="glass grid h-8 w-8 place-items-center rounded" onClick={() => setCamera(c => ({ ...c, zoom: Math.max(1, c.zoom / 1.15) }))}><Minus size={14} /></button>
        <button type="button" aria-label="Reset panorama view" className="glass grid h-8 w-8 place-items-center rounded" onClick={() => setCamera({ yaw: 0, zoom: 1, vertical: 0 })}><RotateCcw size={13} /></button>
      </div>
    </div>}
    <div className="space-y-2 border-t border-line px-4 py-3 text-[10px] leading-relaxed text-muted">
      <p id="panorama-controls-help">Drag left/right to look around · scroll to zoom · arrow keys to pan. Vertical coverage is limited to the original photo.</p>
      <div className="flex items-center justify-between gap-3"><span>NASA/JPL-Caltech/ASU/MSSS · Mastcam-Z</span><a href={site.source} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-cyan hover:underline">Source & full resolution<ExternalLink size={11} /></a></div>
      <p>Natural-colour preview of stitched rover images. This is the view from the photographed location, not from an arbitrary point on the map.</p>
    </div>
  </div>
}
