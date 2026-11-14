/* Pop-up map for "Navigate to → Select on map": click a location icon, an object icon or any point
   of the map, then Confirm. Its own small Leaflet map (the background map's handle stays untouched). */
import { useMemo, useState } from 'react'
import L from 'leaflet'
import { CircleMarker, MapContainer, Marker, TileLayer, useMapEvents } from 'react-leaflet'
import { Check, Crosshair, X } from 'lucide-react'
import { TREK_LAYERS, trekUrl } from '../map/trek'
import { markerIcon } from '../map/markers'
import { fmtDist, fmtLat, fmtLon } from '../map/geo'
import { MARKER_STYLE, distM, type MapLocation, type WorldAgent } from '../sim/world'

export type PickedTarget =
  | { kind: 'point'; lon: number; lat: number }
  | { kind: 'location'; id: string; name: string; lon: number; lat: number }
  | { kind: 'agent'; id: string; name: string; lon: number; lat: number }

function Clicks({ onPick }: { onPick: (lon: number, lat: number) => void }) {
  useMapEvents({ click: e => onPick(e.latlng.lng, e.latlng.lat) })
  return null
}

export default function TargetPicker({ me, agents, locations, onConfirm, onCancel }: {
  me: WorldAgent; agents: WorldAgent[]; locations: MapLocation[]
  onConfirm: (t: PickedTarget) => void; onCancel: () => void
}) {
  const [target, setTarget] = useState<PickedTarget | null>(null)
  const base = TREK_LAYERS.find(l => l.id === 'viking') ?? TREK_LAYERS[0]
  const overlays = TREK_LAYERS.filter(l => l.kind === 'overlay' && (l.id === 'ctx' || l.id === 'hirise'))
  const center = useMemo<[number, number]>(() => [me.lat, me.lon], [])   // eslint-disable-line react-hooks/exhaustive-deps
  const stop = (e: L.LeafletMouseEvent) => L.DomEvent.stopPropagation(e)
  const what = !target ? 'Click a location, an object or any point on the map.'
    : target.kind === 'point' ? `Map point ${fmtLat(target.lat)} ${fmtLon(target.lon)}`
    : target.kind === 'location' ? `${target.name} (location · cautious route)`
    : `${target.name} (intercept)`
  return (
    <div className="fixed inset-0 z-[3000] grid place-items-center bg-black/55 p-4" role="dialog" aria-modal="true" aria-label="Choose a target on the map">
      <div className="glass flex h-[min(78vh,720px)] w-[min(92vw,1100px)] flex-col overflow-hidden rounded-2xl shadow-2xl">
        <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          <Crosshair size={15} className="text-cyan" />
          <div className="text-sm font-semibold">Choose a target for {me.name}</div>
          <button type="button" onClick={onCancel} aria-label="Close" className="ml-auto text-muted hover:text-ink"><X size={16} /></button>
        </div>
        <div className="relative min-h-0 flex-1">
          <MapContainer crs={L.CRS.EPSG4326} center={center} zoom={15} minZoom={8} maxZoom={19} zoomControl className="h-full w-full">
            <TileLayer url={trekUrl(base)} maxNativeZoom={base.maxNativeZoom} maxZoom={19} />
            {overlays.map(l => <TileLayer key={l.id} url={trekUrl(l)} maxNativeZoom={l.maxNativeZoom} maxZoom={19} minZoom={l.minZoom ?? 0}
              bounds={l.bounds ? L.latLngBounds(l.bounds) : undefined} />)}
            <Clicks onPick={(lon, lat) => setTarget({ kind: 'point', lon, lat })} />
            {locations.map(l => <Marker key={l.id} position={[l.lat, l.lon]} zIndexOffset={2200}
              icon={markerIcon(l.type, { name: l.name, highlight: target?.kind === 'location' && target.id === l.id })}
              eventHandlers={{ click: e => { stop(e); setTarget({ kind: 'location', id: l.id, name: l.name, lon: l.lon, lat: l.lat }) } }} />)}
            {agents.map(a => <Marker key={a.id} position={[a.lat, a.lon]} zIndexOffset={a.id === me.id ? 3000 : 2800}
              icon={markerIcon(a.kind, { name: a.id === me.id ? `${a.name} (you)` : a.name, selected: a.id === me.id,
                highlight: target?.kind === 'agent' && target.id === a.id })}
              eventHandlers={{ click: e => { stop(e); if (a.id !== me.id) setTarget({ kind: 'agent', id: a.id, name: a.name, lon: a.lon, lat: a.lat }) } }} />)}
            {target?.kind === 'point' && <CircleMarker center={[target.lat, target.lon]} radius={8} interactive={false}
              pathOptions={{ color: '#ffffff', weight: 2, fillColor: '#ff6a3d', fillOpacity: 0.7 }} />}
          </MapContainer>
        </div>
        <div className="flex items-center gap-3 border-t border-line px-4 py-2.5 text-xs">
          {target && target.kind !== 'point' && (() => {
            const t = target.kind === 'agent' ? agents.find(a => a.id === target.id) : locations.find(l => l.id === target.id)
            const type = target.kind === 'agent' ? (t as WorldAgent | undefined)?.kind : (t as MapLocation | undefined)?.type
            return type ? <i className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: MARKER_STYLE[type].color }} /> : null
          })()}
          <span className="min-w-0 flex-1 truncate text-muted">{what}{target ? ` · ${fmtDist(distM(me, target))}` : ''}</span>
          <button type="button" onClick={onCancel} className="btn-ghost !py-1.5 text-xs">Cancel</button>
          <button type="button" disabled={!target} onClick={() => target && onConfirm(target)} className="btn-primary !py-1.5 text-xs">
            <Check size={13} aria-hidden="true" />Confirm target
          </button>
        </div>
      </div>
    </div>
  )
}
