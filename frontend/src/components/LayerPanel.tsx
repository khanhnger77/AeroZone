import type { ReactNode } from 'react'
import type { LayersManifest } from '../types'
import type { MapLayerState } from '../map/MarsMap'
import { TREK_LAYERS } from '../map/trek'
import { CategoryTag, Legend, Section, Toggle, PanelHeader } from './ui'

interface Props {
  manifest: LayersManifest | null
  layers: MapLayerState
  setLayers: (fn: (s: MapLayerState) => MapLayerState) => void
  detailed?: boolean
}

function LayerDetails({ label, children }: { label: string; children: ReactNode }) {
  return <details className="mt-2 rounded-lg border border-line/70 bg-panel-2/30 px-2.5">
    <summary className="cursor-pointer py-2 text-[11px] text-muted transition-colors hover:text-ink focus-visible:outline-2 focus-visible:outline-cyan" aria-label={`Source and processing for ${label}`}>Source & processing</summary>
    <div className="space-y-1.5 break-words pb-2.5 text-[11px] leading-relaxed text-muted">{children}</div>
  </details>
}

export default function LayerPanel({ manifest, layers, setLayers, detailed }: Props) {
  const bases = TREK_LAYERS.filter((l) => l.kind === 'base')
  const overlays = TREK_LAYERS.filter((l) => l.kind === 'overlay')
  return (
    <div>
      <PanelHeader kicker="Data layers" title="Every layer on the map" subtitle="Switch layers on or off. Open a layer’s details to see its source and how it was made." />
      <Section title="Basemap (regional context)">
        <div className="grid grid-cols-2 gap-2">
          {bases.map((b) => (
            <button key={b.id} onClick={() => setLayers((s) => ({ ...s, base: b.id }))}
              className={`rounded-md border px-2 py-1.5 text-left text-xs transition-colors ${layers.base === b.id ? 'border-mars bg-mars/10 text-ink' : 'border-line text-muted hover:text-ink'}`}>
              <div className="font-medium">{b.instrument}</div>
              <div className="text-[10px] text-muted">{b.mission} · {b.resolution}</div>
            </button>
          ))}
        </div>
      </Section>

      <Section title="Orbital imagery & thermal">
        <ul className="space-y-2.5">
          {overlays.map((l) => (
            <li key={l.id}>
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm leading-snug">{l.name}</div>
                  <div className="mt-0.5 text-[10px] text-muted">{l.instrument} · {l.resolution}</div>
                </div>
                <Toggle label={l.name} on={!!layers.trek[l.id]}
                  onChange={(v) => setLayers((s) => ({ ...s, trek: { ...s.trek, [l.id]: v } }))} />
              </div>
              {detailed && (
                <LayerDetails label={l.name}>
                  <CategoryTag c="observation" />
                  <p><span className="text-ink/70">Source: </span>NASA Mars Trek · {l.mission} · {l.instrument}</p>
                  <p>{l.description}</p>
                </LayerDetails>
              )}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Terrain analysis (HiRISE DTM)">
        {!manifest && <div className="text-xs text-muted">Loading layers…</div>}
        <ul className="space-y-3">
          {manifest?.raster_layers.map((r) => {
            const st = layers.raster[r.id] ?? { on: false, opacity: 0.75 }
            return (
              <li key={r.id}>
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm leading-snug">{r.name}</div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-muted"><span>5 m/px</span><CategoryTag c={r.category} /></div>
                  </div>
                  <Toggle label={r.name} on={st.on}
                    onChange={(v) => setLayers((s) => ({ ...s, raster: { ...s.raster, [r.id]: { ...st, on: v } } }))} />
                </div>
                {st.on && (
                  <div className="mt-2 space-y-2">
                    <Legend layer={r} />
                    <label className="flex items-center gap-2 text-[10px] text-muted">
                      Opacity
                      <input type="range" min={0.1} max={1} step={0.05} value={st.opacity} className="flex-1 accent-mars"
                        onChange={(e) => setLayers((s) => ({ ...s, raster: { ...s.raster, [r.id]: { ...st, opacity: +e.target.value } } }))} />
                    </label>
                  </div>
                )}
                {detailed && (
                  <LayerDetails label={r.name}><dl className="space-y-1">
                    <div><dt className="inline text-ink/70">Source: </dt><dd className="inline">{r.source}</dd></div>
                    <div><dt className="inline text-ink/70">Processing: </dt><dd className="inline">{r.processing}</dd></div>
                  </dl></LayerDetails>
                )}
              </li>
            )
          })}
        </ul>
      </Section>

      <Section title="Rover observations (Perseverance)">
        {([['traverse', 'Traverse path'], ['waypoints', 'Localized waypoints'], ['samples', 'Sample sites']] as const).map(([k, label]) => (
          <div key={k} className="flex items-center justify-between py-1">
            <span className="text-sm">{label}</span>
            <Toggle label={label} on={layers[k]} onChange={(v) => setLayers((s) => ({ ...s, [k]: v }))} />
          </div>
        ))}
        {detailed && (
          <LayerDetails label="Rover observations">
            <CategoryTag c="observation" />
            <p>NASA/JPL Mars 2020 MMGIS localization files. Sample positions are <em>derived</em>: last rover fix on/before the sealing sol.</p>
          </LayerDetails>
        )}
      </Section>

      <Section title="Not yet integrated">
        <p className="text-[11px] leading-snug text-muted">
          CRISM mineral maps (no tiled Jezero product found yet; candidate sources under evaluation). Shown here so
          missing data is explicit rather than silently absent.
        </p>
      </Section>
    </div>
  )
}
