import type { LayersManifest } from '../types'
import { Legend, Section, Stat, PanelHeader } from './ui'
import InfoTooltip from './InfoTooltip'

export default function SafetyPanel({ manifest }: { manifest: LayersManifest | null }) {
  const hz = manifest?.raster_layers.find((l) => l.id === 'hazard')
  const slope = manifest?.raster_layers.find((l) => l.id === 'slope')
  const rough = manifest?.raster_layers.find((l) => l.id === 'roughness')
  const m = hz?.model
  if (!hz || !m) return <div className="p-4 text-sm text-muted">Loading hazard model…</div>
  const colors = hz.legend.type === 'classes' ? hz.legend.classes : []
  return (
    <div>
      <PanelHeader kicker="Safety intelligence" title="Terrain hazard" subtitle="Where walking gets harder: slope and metre-scale roughness from the HiRISE DTM, combined in a transparent model." />
      <div className="mx-4 mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-[11px] leading-snug text-amber-200">
        <b>Prototype model.</b> {m.disclaimer}
      </div>

      <Section title="Hazard classes · study area">
        <Legend layer={hz} />
        <div className="mt-3 space-y-1.5">
          {m.classes.map((c, i) => {
            const f = m.class_fraction[c.label] ?? 0
            return (
              <div key={c.label} className="flex items-center gap-2 text-xs">
                <span className="w-16 text-muted">{c.label}</span>
                <div className="h-2 flex-1 overflow-hidden rounded-sm bg-line">
                  <div className="h-full" style={{ width: `${f * 100}%`, background: colors[i]?.color }} />
                </div>
                <span className="w-12 text-right font-mono">{(f * 100).toFixed(1)}%</span>
              </div>
            )
          })}
        </div>
        <p className="mt-2 text-[11px] text-muted">
          No-go (slope ≥ {m.no_go_slope_deg}°): <span className="font-mono text-ink">{(m.no_go_fraction * 100).toFixed(2)}%</span> of area — impassable for the route planner.
        </p>
      </Section>

      <Section title="Terrain statistics (5 m grid)">
        <div className="grid grid-cols-3 gap-2">
          <Stat label="Median slope" value={slope?.stats?.median.toFixed(1) ?? '–'} unit="°" />
          <Stat label="P95 slope" value={slope?.stats?.p95.toFixed(1) ?? '–'} unit="°" />
          <Stat label="Max slope" value={slope?.stats?.max.toFixed(0) ?? '–'} unit="°" />
          <Stat label="Median rough." value={rough?.stats?.median.toFixed(2) ?? '–'} unit="m" />
          <Stat label="P95 rough." value={rough?.stats?.p95.toFixed(2) ?? '–'} unit="m" />
          <Stat label="Max rough." value={rough?.stats?.max.toFixed(1) ?? '–'} unit="m" />
        </div>
      </Section>

      <Section title="Model factors">
        <table className="w-full text-xs">
          <thead className="text-[10px] uppercase text-muted">
            <tr><th className="pb-1 text-left font-medium">Factor</th><th className="text-right font-medium">Weight</th><th className="text-right font-medium">Status</th></tr>
          </thead>
          <tbody>
            {Object.entries(m.weights).map(([k, w]) => (
              <tr key={k} className="border-t border-line">
                <td className="py-1 capitalize">{k}</td>
                <td className="text-right font-mono">{w.toFixed(1)}</td>
                <td className={`text-right ${m.factor_available[k] ? 'text-emerald-300' : 'text-amber-300'}`}>
                  {m.factor_available[k] ? 'integrated' : 'unavailable'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-[11px] leading-snug text-muted">
          Score covers {(m.model_weight_coverage * 100).toFixed(0)}% of model weight and is re-normalised over
          available factors. Missing factors are <em>not</em> treated as zero risk.
        </p>
      </Section>

      <Section title="Thresholds & uncertainty" right={<InfoTooltip label="Thresholds and uncertainty details">
        <div className="mb-2 text-xs font-semibold text-ink">Thresholds & uncertainty</div>
        <ul className="list-disc space-y-2 pl-4">
          <li>Slope risk: {m.slope_stops[0].map((s, i) => `${s}°→${m.slope_stops[1][i]}`).join(', ')}.</li>
          <li>Roughness risk: {m.roughness_stops[0].map((s, i) => `${s} m→${m.roughness_stops[1][i]}`).join(', ')}.</li>
          <li>DTM post spacing 1 m; effective resolution is coarser (~3 m). Boulders below a few metres are not resolved.</li>
          <li>Roughness values below ~0.1 m approach the DTM vertical noise.</li>
          <li>Alignment check: DTM vs rover-reported elevation, median difference ≈ 0.00 m (n=612); rover fixes are tied to HiRISE basemaps, so this confirms consistency, not independent accuracy.</li>
        </ul>
      </InfoTooltip>}>
        <p className="text-xs leading-relaxed text-muted">No-go slope ≥ <span className="text-ink">{m.no_go_slope_deg}°</span> · terrain detail <span className="text-ink">~3 m</span>.</p>
        <p className="mt-1 text-[11px] leading-relaxed text-muted">Small boulders and fine roughness may be unresolved. Hover ⓘ for thresholds and accuracy limits.</p>
      </Section>
    </div>
  )
}
