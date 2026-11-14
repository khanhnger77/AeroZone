import { useEffect, useRef } from 'react'
import { Camera, Mountain, X } from 'lucide-react'
import type { LonLat } from '../types'
import type { PlaceViewTab, SelectedPlace } from './PlaceCard'
import { fmtLat, fmtLon } from '../map/geo'
import TerrainPointView from './TerrainPointView'
import PanoramaView from './PanoramaView'

export default function PlaceViewer({ point, tab, online, onTab, onClose, onVisit }: {
  point: SelectedPlace; tab: PlaceViewTab; online: boolean
  onTab: (tab: PlaceViewTab) => void; onClose: () => void; onVisit: (point: LonLat & { name?: string }) => void
}) {
  const close = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    close.current?.focus()
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); onCloseRef.current() } }
    window.addEventListener('keydown', key)
    return () => { window.removeEventListener('keydown', key); if (previous?.isConnected) previous.focus() }
  }, [])
  return <section aria-label="Place explorer" className="glass absolute bottom-[60px] right-3 z-[1200] max-h-[calc(100vh-155px)] overflow-y-auto rounded-2xl shadow-2xl" style={{ width: 'min(540px, calc(100vw - 480px))' }}>
    <header className="sticky top-0 z-10 bg-panel/95 flex items-start justify-between gap-3 border-b border-line px-4 py-3">
      <div><div className="label !text-cyan">Explore this place</div><h2 className="display mt-1 text-base font-semibold">{point.name ?? 'Jezero terrain & rover views'}</h2><p className="mt-1 font-mono text-[10px] text-muted">{fmtLat(point.lat)} · {fmtLon(point.lon)}</p></div>
      <button ref={close} type="button" aria-label="Close place explorer" onClick={onClose} className="rounded p-1.5 text-muted hover:bg-white/5 hover:text-ink"><X size={17} /></button>
    </header>
    <div role="tablist" aria-label="Place view" className="flex gap-1 border-b border-line p-2">
      {([['terrain', 'Terrain 3D', Mountain], ['panorama', 'Panorama 360°', Camera]] as const).map(([id, title, Icon]) => <button type="button" key={id} role="tab" id={`place-tab-${id}`} aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} onKeyDown={e => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return; e.preventDefault(); const next = e.key === 'Home' ? 'terrain' : e.key === 'End' ? 'panorama' : id === 'terrain' ? 'panorama' : 'terrain'; onTab(next); document.getElementById(`place-tab-${next}`)?.focus() }} aria-controls={`place-panel-${id}`} onClick={() => onTab(id)} className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs ${tab === id ? 'bg-cyan/10 text-cyan' : 'text-muted hover:bg-white/5 hover:text-ink'}`}><Icon size={14} aria-hidden="true" />{title}</button>)}
    </div>
    <div role="tabpanel" id={`place-panel-${tab}`} aria-labelledby={`place-tab-${tab}`} key={`${point.lon},${point.lat},${tab}`}>
      {tab === 'terrain' ? <TerrainPointView point={point} online={online} /> : <PanoramaView point={point} onVisit={onVisit} />}
    </div>
  </section>
}
