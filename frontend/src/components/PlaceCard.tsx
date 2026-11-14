import { Camera, Mountain, X } from 'lucide-react'
import { fmtLat, fmtLon } from '../map/geo'
import { nearbyPanoramas } from '../data/panoramas'
import type { LonLat } from '../types'

export type PlaceViewTab = 'terrain' | 'panorama'
export interface SelectedPlace extends LonLat { name?: string }
export default function PlaceCard({ point, plannerOnline, onView, onStart, onTarget, onClose }: {
  point: SelectedPlace; plannerOnline: boolean
  onView: (tab: PlaceViewTab) => void; onStart: () => void; onTarget: () => void; onClose: () => void
}) {
  const panoramas = nearbyPanoramas(point)
  return <div className="p-3 text-ink">
    <div className="flex items-start justify-between gap-2"><h3 className="text-sm font-semibold">{point.name ?? 'Explore this point'}</h3><button type="button" aria-label="Close location card" onClick={onClose} className="rounded p-1 text-muted hover:text-ink"><X size={14} /></button></div>
    <div className="mt-1 font-mono text-[10px] text-muted">{fmtLat(point.lat)} · {fmtLon(point.lon)}</div>
    <div className="mt-3 grid grid-cols-2 gap-2">
      <button type="button" aria-label="View terrain in 3D" disabled={!plannerOnline} aria-describedby={!plannerOnline ? 'place-terrain-help' : undefined} onClick={() => onView('terrain')} className="btn-primary !px-2 !text-xs"><Mountain size={14} />Terrain 3D</button>
      <button type="button" aria-label="View panorama 360" disabled={!panoramas.length} aria-describedby={!panoramas.length ? 'place-panorama-help' : undefined} onClick={() => onView('panorama')} className="btn-ghost justify-center disabled:opacity-40"><Camera size={14} />360° view</button>
    </div>
    {!plannerOnline && <p id="place-terrain-help" className="mt-2 text-[10px] leading-relaxed text-muted">Terrain 3D needs the Planner connection.</p>}
    {panoramas.length ? <p className="mt-2 text-[10px] leading-relaxed text-muted">{panoramas.length} nearby panorama{panoramas.length > 1 ? 's' : ''} · approximate rover locations</p> : <p id="place-panorama-help" className="mt-2 text-[10px] leading-relaxed text-muted">No curated panorama within 500 m. Try a 360° marker.</p>}
    <div className="mt-2 flex gap-3 border-t border-line pt-2 text-[11px]"><button type="button" onClick={onStart} className="text-cyan hover:underline">Use as start</button><button type="button" onClick={onTarget} className="text-cyan hover:underline">Set as target</button></div>
  </div>
}
