import { useState } from 'react'
import type { SelectedPlace } from './PlaceCard'
import TerrainPointView from './TerrainPointView'
import type { TerrainPreview } from '../services/terrainPreview'
import { DEFAULT_CAMERA } from '../terrain/drawTerrain'
import { TERRAIN_LAYERS, type TerrainLayer } from '../terrain/layers'
import { fmtLat, fmtLon } from '../map/geo'

export default function TerrainCompareView({ point, comparison, picking, online, onPick, onCancel, onRemove }: {
  point: SelectedPlace; comparison: SelectedPlace | null; picking: boolean; online: boolean
  onPick: () => void; onCancel: () => void; onRemove: () => void
}) {
  const [layer, setLayer] = useState<TerrainLayer>('elevation')
  const [camera, setCamera] = useState(DEFAULT_CAMERA)
  const [a, setA] = useState<TerrainPreview | null>(null)
  const [b, setB] = useState<TerrainPreview | null>(null)
  const currentB = comparison && b?.point.lon === comparison.lon && b.point.lat === comparison.lat ? b : null
  const elevationRange: readonly [number, number] | undefined = a && currentB ? [Math.min(a.min_m, currentB.min_m), Math.max(a.max_m, currentB.max_m)] : undefined
  const relief = a && currentB ? Math.max(a.max_m - a.min_m, currentB.max_m - currentB.min_m) : undefined
  const controls = { layer, setLayer, camera, setCamera, elevationRange, relief }
  return <div>
    <div className="space-y-2 border-b border-line p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-muted">Compare two 160 m patches · linked rotation, zoom and color scale</p>
        <div className="flex gap-2">
          {picking ? <button type="button" onClick={onCancel} className="btn-ghost !text-xs">Cancel selection</button> : <button type="button" onClick={onPick} className="btn-primary !text-xs">{comparison ? 'Choose another point' : 'Compare'}</button>}
          {comparison && <button type="button" onClick={onRemove} className="btn-ghost !text-xs">Clear comparison</button>}
        </div>
      </div>
      {picking && <p role="status" className="rounded-lg border border-cyan/30 bg-cyan/5 p-2 text-xs text-cyan">Click another point on the map for terrain B. Terrain A stays fixed.</p>}
      <div role="group" aria-label="Comparison terrain layers" className="grid grid-cols-4 gap-1">
        {TERRAIN_LAYERS.map(item => <button key={item.id} type="button" aria-pressed={layer === item.id} onClick={() => setLayer(item.id)} className={`rounded-lg px-2 py-2 text-[11px] ${layer === item.id ? 'bg-cyan/10 text-cyan' : 'text-muted hover:bg-white/5 hover:text-ink'}`}>{item.label}</button>)}
      </div>
    </div>
    <div className="grid grid-cols-2 divide-x divide-line">
      {[{ label: 'A', point, onData: setA }, { label: 'B', point: comparison, onData: setB }].map(item => <section key={item.label} aria-label={`Terrain ${item.label}`} className="min-w-0">
        <div className="border-b border-line p-3">
          <h3 className="text-xs font-semibold"><span className={item.label === 'A' ? 'text-cyan' : 'text-purple-300'}>Terrain {item.label}</span>{item.point?.name ? ` · ${item.point.name}` : ''}</h3>
          <p className="mt-1 font-mono text-[10px] text-muted">{item.point ? `${fmtLat(item.point.lat)} · ${fmtLon(item.point.lon)}` : 'No second point selected'}</p>
        </div>
        {item.point ? <TerrainPointView key={`${item.point.lon},${item.point.lat}`} point={item.point} online={online} controls={controls} onData={item.onData} label={item.label} /> : <div className="grid min-h-[390px] place-content-center gap-3 p-6 text-center text-sm text-muted"><p>Choose a second terrain patch on the map.</p>{!picking && <button type="button" onClick={onPick} className="btn-primary mx-auto">Compare</button>}</div>}
      </section>)}
    </div>
  </div>
}
