import { useEffect, useRef } from 'react'
import { Camera, Mountain, X } from 'lucide-react'
import type { LonLat } from '../types'
import type { PlaceViewTab, SelectedPlace } from './PlaceCard'
import { fmtLat, fmtLon } from '../map/geo'
import TerrainPointView from './TerrainPointView'
import PanoramaView from './PanoramaView'
import TerrainCompareView from './TerrainCompareView'

export default function PlaceViewer({ point, tab, online, comparison, picking, onCompare, onCancelCompare, onClearCompare, onTab, onClose, onVisit }: {
  point: SelectedPlace; tab: PlaceViewTab; online: boolean
  comparison: SelectedPlace | null; picking: boolean
  onCompare: () => void; onCancelCompare: () => void; onClearCompare: () => void
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
  // Stays at the bottom right; its height is capped so it ends below the top-right Copilot (the
  // same height expression the Copilot uses, plus its 12 px top offset and a 12 px gap). The
  // content scrolls inside.
  const copilotH = 'clamp(240px, calc(100vh - var(--mo-dock-h, 260px) - 96px), 520px)'
  const wide = comparison && !picking && tab === 'terrain'
  return <section aria-label="Place explorer" className="glass absolute bottom-[60px] right-3 z-[1600] overflow-y-auto rounded-2xl shadow-2xl"
    style={{ width: wide ? 'min(1040px, calc(100vw - 110px))' : 'min(540px, calc(100vw - 480px))', maxHeight: `calc(100vh - 84px - ${copilotH})` }}>
    {/* compact header: kicker, name and coordinates on one line */}
    <header className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-line bg-panel/95 px-3 py-1.5">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <span className="label !text-[9px] !text-cyan">Explore</span>
        <h2 className="display truncate text-sm font-semibold">{point.name ?? 'Jezero terrain & rover views'}</h2>
        <span className="font-mono text-[10px] text-muted">{fmtLat(point.lat)} · {fmtLon(point.lon)}</span>
      </div>
      <button ref={close} type="button" aria-label="Close place explorer" onClick={onClose} className="shrink-0 rounded p-1 text-muted hover:bg-white/5 hover:text-ink"><X size={15} /></button>
    </header>
    {/* tabs and the Compare button share one row */}
    <div className="flex items-center gap-1 border-b border-line p-1.5">
      <div role="tablist" aria-label="Place view" className="flex flex-1 gap-1">
        {([['terrain', 'Terrain 3D', Mountain], ['panorama', 'Panorama 360°', Camera]] as const).map(([id, title, Icon]) => <button type="button" key={id} role="tab" id={`place-tab-${id}`} aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} onKeyDown={e => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return; e.preventDefault(); const next = e.key === 'Home' ? 'terrain' : e.key === 'End' ? 'panorama' : id === 'terrain' ? 'panorama' : 'terrain'; onTab(next); document.getElementById(`place-tab-${next}`)?.focus() }} aria-controls={`place-panel-${id}`} onClick={() => onTab(id)} className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs ${tab === id ? 'bg-cyan/10 text-cyan' : 'text-muted hover:bg-white/5 hover:text-ink'}`}><Icon size={13} aria-hidden="true" />{title}</button>)}
      </div>
      {tab === 'terrain' && !picking && !comparison && <button type="button" onClick={onCompare} title="Compare this terrain with another place" className="btn-ghost !px-2.5 !py-1.5 !text-xs">Compare</button>}
    </div>
    <div role="tabpanel" id={`place-panel-${tab}`} aria-labelledby={`place-tab-${tab}`} key={`${point.lon},${point.lat},${tab}`}>
      {tab === 'terrain' ? (picking || comparison ? <TerrainCompareView point={point} comparison={comparison} picking={picking} online={online} onPick={onCompare} onCancel={onCancelCompare} onRemove={onClearCompare} /> : <TerrainPointView point={point} online={online} />) : <PanoramaView point={point} onVisit={onVisit} />}
    </div>
  </section>
}
