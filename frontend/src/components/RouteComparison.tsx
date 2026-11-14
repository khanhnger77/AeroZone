import { ArrowRight, CheckCircle2, Sparkles } from 'lucide-react'
import type { Route, RouteResponse } from '../types'
import { fmtDist } from '../map/geo'

const CLASSES = [
  ['Low', '#91cf60'], ['Moderate', '#fee08b'], ['Elevated', '#fc8d59'], ['High', '#d73027'], ['Severe', '#7b0041'],
] as const

/** A zero baseline needs an absolute difference, not an infinite percentage. */
function compareMetric(value: number, baseline: number, metric: 'distance' | 'hazard') {
  if (!Number.isFinite(value) || !Number.isFinite(baseline)) return 'Comparison unavailable'
  const delta = value - baseline
  if (Math.abs(delta) < 1e-9) return metric === 'distance' ? 'Same distance' : 'Same mean hazard'
  const direction = metric === 'distance' ? (delta > 0 ? 'longer' : 'shorter') : (delta > 0 ? 'higher mean hazard' : 'lower mean hazard')
  if (baseline <= 0) {
    if (metric === 'hazard') return `Mean hazard ${Math.abs(delta).toFixed(1)} points ${delta > 0 ? 'higher' : 'lower'}`
    return `${fmtDist(Math.abs(delta))} ${direction}`
  }
  const percent = Math.abs(delta / baseline) * 100
  return `${percent < 0.1 ? '<0.1' : percent.toFixed(1)}% ${direction}`
}

function Differences({ route, direct }: { route: Route; direct: Route }) {
  return <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] leading-relaxed">
    <span className="text-muted">{compareMetric(route.length_m, direct.length_m, 'distance')}</span>
    <span className={route.hazard_mean < direct.hazard_mean ? 'text-emerald-300' : route.hazard_mean > direct.hazard_mean ? 'text-amber-200' : 'text-muted'}>{compareMetric(route.hazard_mean, direct.hazard_mean, 'hazard')}</span>
  </div>
}

export default function RouteComparison({ response, selected, disabled, onSelect }: {
  response: RouteResponse; selected: string | null; disabled: boolean; onSelect: (id: string) => void
}) {
  const recommended = response.routes.find(r => r.id === response.recommended)
  const direct = response.routes.find(r => r.id === 'direct')
  const selectedId = selected ?? response.recommended
  return <div className="space-y-3">
    {recommended && <div className="rounded-xl border border-emerald-400/35 bg-gradient-to-br from-emerald-400/10 to-panel p-3">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-300"><Sparkles size={13} aria-hidden="true" />Planner recommendation</div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <h4 className="display text-lg font-semibold">{recommended.label}</h4>
        <span className="font-mono text-sm">{fmtDist(recommended.length_m)}</span>
      </div>
      {direct && recommended.id !== direct.id && <div className="mt-1"><Differences route={recommended} direct={direct} /><div className="mt-1 text-[10px] text-muted">Compared with Direct</div></div>}
      {recommended.id === 'direct' && <p className="mt-1 text-xs leading-relaxed text-muted">The planner’s distance, hazard and slope trade-off favors Direct for these points.</p>}
      {selectedId !== recommended.id && <button type="button" disabled={disabled} onClick={() => onSelect(recommended.id)} className="mt-3 flex min-h-8 w-full items-center justify-between rounded-lg border border-emerald-400/25 px-2.5 py-1.5 text-xs text-emerald-200 hover:bg-emerald-400/10 disabled:opacity-40">Use {recommended.label}<ArrowRight size={13} aria-hidden="true" /></button>}
      <p className="mt-2 border-t border-emerald-400/15 pt-2 text-[10px] leading-relaxed text-muted">Based on a prototype terrain model. Recommendation does not certify safety.</p>
    </div>}

    <p className="text-[11px] leading-relaxed text-muted">Select a route to show it on the map and in the elevation profile.{disabled ? ' Route selection is locked during the 3D demo.' : ''}</p>
    <div className="space-y-2">
      {response.routes.map(route => {
        const active = route.id === selectedId
        const rec = route.id === response.recommended
        const exposure = ((route.hazard_class_fraction.High ?? 0) + (route.hazard_class_fraction.Severe ?? 0)) * 100
        const metrics = [['Distance', fmtDist(route.length_m)], ['Mean hazard', `${route.hazard_mean.toFixed(1)} / 100`], ['Max slope', `${route.slope_max_deg.toFixed(1)}°`], ['Ascent', `${Math.round(route.ascent_m)} m`]]
        return <button type="button" key={route.id} aria-label={`Select ${route.label} route`} aria-pressed={active} disabled={disabled} onClick={() => onSelect(route.id)}
          className={`w-full rounded-xl border p-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-cyan disabled:opacity-60 ${active ? 'border-cyan/60 bg-cyan/5 ring-1 ring-cyan/10' : rec ? 'border-emerald-400/30 bg-emerald-400/5 hover:bg-emerald-400/10' : 'border-line bg-panel-2/30 hover:border-line-2 hover:bg-panel-2'}`}>
          <div className="flex flex-wrap items-center gap-2">
            <i className="h-1 w-5 shrink-0 rounded" style={{ background: route.color }} aria-hidden="true" />
            <span className="text-sm font-semibold">{route.label}</span>
            {rec && <span className="tag bg-emerald-400/15 text-emerald-300">Recommended</span>}
            {active && <span className="ml-auto inline-flex items-center gap-1 text-[10px] text-cyan"><CheckCircle2 size={12} aria-hidden="true" />Selected</span>}
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2">
            {metrics.map(([label, value]) => <div key={label}><dt className="text-[10px] text-muted">{label}</dt><dd className="mt-0.5 font-mono text-[12px] text-ink">{value}</dd></div>)}
          </dl>
          {direct && route.id !== direct.id && <div className="mt-2 border-t border-line pt-2"><Differences route={route} direct={direct} /><span className="text-[10px] text-faint">vs Direct</span></div>}
          <div className="mt-3 flex h-1.5 overflow-hidden rounded-full" aria-label={`Hazard classes along ${route.label}`}>
            {CLASSES.map(([name, color]) => <span key={name} title={`${name} ${((route.hazard_class_fraction[name] ?? 0) * 100).toFixed(1)}%`} style={{ width: `${(route.hazard_class_fraction[name] ?? 0) * 100}%`, background: color }} />)}
          </div>
          <div className={`mt-1.5 text-[10px] ${exposure > 0 ? 'text-amber-200' : 'text-muted'}`}>{exposure.toFixed(1)}% of route in High / Severe model classes</div>
        </button>
      })}
    </div>
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted" aria-label="Hazard class legend">
      {CLASSES.map(([label, color]) => <span key={label} className="inline-flex items-center gap-1"><i className="h-1.5 w-1.5 rounded-full" style={{ background: color }} aria-hidden="true" />{label}</span>)}
    </div>
    <p className="text-[10px] leading-relaxed text-muted">Mean hazard is a distance-weighted model score, not a probability of harm. Class exposure is a share of route distance.</p>
  </div>
}
