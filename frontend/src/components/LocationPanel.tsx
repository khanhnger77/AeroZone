import { useState } from 'react'
import { Eye, EyeOff, MapPin, Trash2 } from 'lucide-react'
import { PanelHeader, Section } from './ui'
import { fmtDist } from '../map/geo'
import { LOCATION_TYPES, MARKER_STYLE, distM, type LocationType, type MapLocation, type MarkerType, type WorldAgent } from '../sim/world'

interface Props {
  locations: MapLocation[]
  visible: Record<MarkerType, boolean>
  onToggle: (t: MarkerType) => void
  highlight: string | null
  placing: LocationType | null
  onPlace: (t: LocationType | null) => void
  onFocus: (id: string) => void
  onMove: (id: string, p: { lon: number; lat: number }) => void
  onDelete: (id: string) => void
  /** selected sim object, for distances */
  selected: WorldAgent | null
}

export default function LocationPanel(p: Props) {
  const [type, setType] = useState<LocationType>('HQ')
  return (
    <div>
      <PanelHeader kicker="Locations" title="Mark the map" subtitle="Add HQ, geological, cargo, supply and attention points. Drag a marker on the map or type its coordinates." />
      <Section title="Add a location">
        <div className="flex gap-2">
          <select aria-label="Location type" value={type} onChange={e => setType(e.target.value as LocationType)}
            className="flex-1 rounded border border-line bg-panel-2 p-2 text-xs text-ink">
            {LOCATION_TYPES.map(t => <option key={t} value={t}>{MARKER_STYLE[t].label}</option>)}
          </select>
          <button type="button" onClick={() => p.onPlace(p.placing ? null : type)}
            className={p.placing ? 'btn-ghost border-mars text-ink' : 'btn-primary'}>
            <MapPin size={14} aria-hidden="true" />{p.placing ? 'Click the map…' : 'Add on map'}
          </button>
        </div>
        {p.placing && <p role="status" className="mt-2 text-[11px] text-muted">Click the map to place a new <b>{p.placing}</b> location. It is named {p.placing}_n.</p>}
      </Section>
      <Section title="Show on map">
        <div className="flex flex-wrap gap-1.5">
          {LOCATION_TYPES.map(t => <TypeToggle key={t} type={t} on={p.visible[t]} onToggle={p.onToggle} />)}
        </div>
      </Section>
      <Section title={`Locations · ${p.locations.length}`}>
        {p.locations.length === 0 && <p className="text-xs text-muted">No locations yet. The sample sites (geology) stay on the map.</p>}
        {LOCATION_TYPES.filter(t => p.visible[t]).map(t => {
          const items = p.locations.filter(l => l.type === t).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
          if (!items.length) return null
          const { Icon, color } = MARKER_STYLE[t]
          return <div key={t} className="mb-3">
            <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted"><Icon size={12} style={{ color }} />{t}</div>
            <ul className="space-y-1">
              {items.map(l => <LocationRow key={l.id} loc={l} active={p.highlight === l.id} selected={p.selected}
                onFocus={() => p.onFocus(l.id)} onMove={pt => p.onMove(l.id, pt)} onDelete={() => p.onDelete(l.id)} />)}
            </ul>
          </div>
        })}
      </Section>
    </div>
  )
}

export function TypeToggle({ type, on, onToggle }: { type: MarkerType; on: boolean; onToggle: (t: MarkerType) => void }) {
  const { label, color, Icon } = MARKER_STYLE[type]
  return <button type="button" aria-pressed={on} onClick={() => onToggle(type)} title={`${on ? 'Hide' : 'Show'} ${label} icons`}
    className={`flex items-center gap-1.5 rounded-full border px-2 py-1 text-[10.5px] transition-colors ${on ? 'border-line-2 text-ink' : 'border-line text-faint'}`}>
    {on ? <Eye size={11} aria-hidden="true" /> : <EyeOff size={11} aria-hidden="true" />}
    <span className="grid h-4 w-4 place-items-center rounded-full" style={{ background: on ? color : 'transparent', border: `1px solid ${color}` }}>
      <Icon size={10} color={on ? '#05070a' : color} />
    </span>{label}
  </button>
}

function LocationRow({ loc, active, selected, onFocus, onMove, onDelete }: {
  loc: MapLocation; active: boolean; selected: WorldAgent | null
  onFocus: () => void; onMove: (p: { lon: number; lat: number }) => void; onDelete: () => void
}) {
  const [edit, setEdit] = useState<{ lon: string; lat: string } | null>(null)
  const valid = edit && Number.isFinite(Number(edit.lon)) && Number.isFinite(Number(edit.lat)) && edit.lon !== '' && edit.lat !== ''
  return <li className={`rounded-lg border px-2 py-1.5 ${active ? 'border-mars/70 bg-mars/10' : 'border-line'}`}>
    <div className="flex items-center gap-2">
      <button type="button" onClick={onFocus} className="min-w-0 flex-1 text-left">
        <div className="truncate text-xs font-medium">{loc.name}</div>
        <div className="truncate font-mono text-[10px] text-muted">{loc.lat.toFixed(5)}°N {loc.lon.toFixed(5)}°E{selected ? ` · ${fmtDist(distM(selected, loc))} from ${selected.name}` : ''}</div>
      </button>
      <button type="button" onClick={() => setEdit(edit ? null : { lon: loc.lon.toFixed(6), lat: loc.lat.toFixed(6) })}
        className="rounded border border-line px-1.5 py-0.5 text-[10px] text-muted hover:text-ink">{edit ? 'Cancel' : 'Edit'}</button>
      <button type="button" onClick={onDelete} aria-label={`Delete ${loc.name}`} title="Delete" className="text-muted hover:text-red-300"><Trash2 size={13} /></button>
    </div>
    {edit && <form className="mt-1.5 flex items-end gap-1.5" onSubmit={e => { e.preventDefault(); if (valid) { onMove({ lon: Number(edit.lon), lat: Number(edit.lat) }); setEdit(null) } }}>
      {(['lat', 'lon'] as const).map(k => <label key={k} className="flex-1 text-[10px] text-muted">{k === 'lat' ? 'Lat °N' : 'Lon °E'}
        <input inputMode="decimal" value={edit[k]} onChange={e => setEdit({ ...edit, [k]: e.target.value })}
          className="mt-0.5 block w-full rounded border border-line bg-panel-2 px-1.5 py-1 font-mono text-[11px] text-ink" />
      </label>)}
      <button type="submit" disabled={!valid} className="btn-primary !px-2 !py-1 text-[11px]">Set</button>
    </form>}
  </li>
}
