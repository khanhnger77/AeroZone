import type { ReactNode } from 'react'
import type { LonLat, RouteResponse } from '../types'
import { fmtDist, fmtLat, fmtLon } from '../map/geo'
import { Section, PanelHeader } from './ui'

interface Props {
  demoActive: boolean
  demoLoading: boolean
  obstaclesOn: boolean
  onObstacles: (enabled: boolean) => void
  onDemo: () => void
  demoError: string | null
  demoControls: ReactNode
  start: LonLat | null
  end: LonLat | null
  pickMode: 'start' | 'end' | null
  setPickMode: (m: 'start' | 'end' | null) => void
  onPlan: () => void
  onReset: () => void
  onPreset: () => void
  loading: boolean
  error: string | null
  route: RouteResponse | null
  selected: string | null
  onSelect: (id: string) => void
}

const CLASS_COLORS: Record<string, string> = {
  Low: '#91cf60', Moderate: '#fee08b', Elevated: '#fc8d59', High: '#d73027', Severe: '#7b0041',
}

export default function MissionPanel(p: Props) {
  const pt = (v: LonLat | null) => (v ? `${fmtLat(v.lat)} ${fmtLon(v.lon)}` : 'not set')
  const sel = p.route?.routes.find((r) => r.id === (p.selected ?? p.route?.recommended))
  return (
    <div>
      <PanelHeader kicker="Mission planner" title="Plan an EVA" subtitle="Set a start and a target. Three cost profiles trade distance against modelled hazard and slope effort." />
      <Section title="1 · Waypoints">
        {(['start', 'end'] as const).map((k) => (
          <div key={k} className="mb-2 flex items-center gap-2">
            <span className={`h-3 w-3 rounded-full ${k === 'start' ? 'bg-green-500' : 'bg-red-500'}`} />
            <div className="min-w-0 flex-1">
              <div className="text-[10px] uppercase tracking-wider text-muted">{k === 'start' ? 'Start' : 'Target'}</div>
              <div className="truncate font-mono text-xs">{pt(k === 'start' ? p.start : p.end)}</div>
            </div>
            <button disabled={p.demoActive || p.demoLoading} onClick={() => p.setPickMode(p.pickMode === k ? null : k)}
              className={`rounded border px-2 py-1 text-xs ${p.pickMode === k ? 'border-mars bg-mars/20 text-ink' : 'border-line text-muted hover:text-ink'}`}>
              {p.pickMode === k ? 'Click map…' : 'Pick'}
            </button>
          </div>
        ))}
        <div className="mt-2 flex gap-2">
          <button disabled={!p.start || !p.end || p.loading || p.demoActive || p.demoLoading} onClick={p.onPlan}
            className="btn-primary flex-1">
            {p.loading ? 'Computing routes…' : 'Compute routes'}
          </button>
          <button disabled={p.demoActive || p.demoLoading} onClick={p.onReset} className="btn-ghost">Reset</button>
        </div>
        <label className="mt-3 flex items-center justify-between gap-2 text-xs">
          Randomly generate obstacles
          <input type="checkbox" role="switch" aria-label="Randomly generate obstacles" checked={p.obstaclesOn} disabled={p.demoActive || p.demoLoading} onChange={e => p.onObstacles(e.target.checked)} />
        </label>
        <button aria-label={p.demoActive ? 'Exit 3D demo' : 'Run demo'} disabled={!p.route || p.demoLoading || p.loading} onClick={p.onDemo} className="btn-primary mt-3 w-full shadow-lg shadow-orange-500/20">
          {p.demoLoading ? 'Loading demo…' : p.demoActive ? 'Exit 3D demo' : 'Run demo'}
        </button>
        {p.demoError && <p role="alert" className="mt-2 text-xs text-red-300">{p.demoError}</p>}
        {p.demoControls}
        <button disabled={p.demoActive || p.demoLoading} onClick={p.onPreset} className="mt-3 w-full rounded-lg border border-dashed border-cyan/30 bg-cyan/5 px-3 py-2 text-left text-[12px] text-cyan transition-colors hover:bg-cyan/10">
          ▶ Demo scenario: landing site → Cheyava Falls (sample #25)
        </button>
        {p.error && <p className="mt-2 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-200">{p.error}</p>}
      </Section>

      {p.route && (
        <>
          <Section title="2 · Compare alternatives" right={<span className="text-[10px] text-muted">straight line {fmtDist(p.route.straight_line_m)}</span>}>
            <div className="space-y-2">
              {p.route.routes.map((r) => {
                const isSel = r.id === (p.selected ?? p.route!.recommended)
                const rec = r.id === p.route!.recommended
                return (
                  <button disabled={p.demoActive || p.demoLoading} key={r.id} onClick={() => p.onSelect(r.id)}
                    className={`w-full rounded-md border p-2.5 text-left transition-colors ${isSel ? 'border-ink/50 bg-panel-2' : 'border-line hover:bg-panel-2'}`}>
                    <div className="flex items-center gap-2">
                      <i className="h-1 w-5 rounded" style={{ background: r.color }} />
                      <span className="text-sm font-medium">{r.label}</span>
                      {rec && <span className="tag bg-emerald-500/20 text-emerald-300">Recommended</span>}
                    </div>
                    <div className="mt-1.5 grid grid-cols-4 gap-1 font-mono text-[11px]">
                      <span>{fmtDist(r.length_m)}</span>
                      <span title="distance-weighted mean hazard">hz {r.hazard_mean}</span>
                      <span title="maximum slope">∠ {r.slope_max_deg}°</span>
                      <span title="total ascent">↑ {Math.round(r.ascent_m)} m</span>
                    </div>
                    <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-sm">
                      {Object.entries(r.hazard_class_fraction).map(([k, f]) => (
                        <span key={k} title={`${k} ${(f * 100).toFixed(1)}%`} style={{ width: `${f * 100}%`, background: CLASS_COLORS[k] }} />
                      ))}
                    </div>
                  </button>
                )
              })}
            </div>
          </Section>

          <Section title="3 · Why this route">
            <ul className="list-disc space-y-1 pl-4 text-xs leading-snug">
              {p.route.rationale.map((t, i) => <li key={i}>{t}</li>)}
            </ul>
          </Section>

          {sel && (
            <Section title={`Details · ${sel.label}`}>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                <dt className="text-muted">Distance</dt><dd className="font-mono">{fmtDist(sel.length_m)}</dd>
                <dt className="text-muted">Mean / max hazard</dt><dd className="font-mono">{sel.hazard_mean} / {sel.hazard_max}</dd>
                <dt className="text-muted">Mean / max slope</dt><dd className="font-mono">{sel.slope_mean_deg}° / {sel.slope_max_deg}°</dd>
                <dt className="text-muted">Ascent / descent</dt><dd className="font-mono">{Math.round(sel.ascent_m)} / {Math.round(sel.descent_m)} m</dd>
                <dt className="text-muted">Effort index</dt><dd className="font-mono" title="relative, not energy">{sel.effort_index}</dd>
                <dt className="text-muted">Walk time*</dt><dd className="font-mono">{sel.time_h_at_assumed_pace.toFixed(1)} h</dd>
              </dl>
              <div className="mt-3 text-[10px] uppercase tracking-wider text-muted">Sample sites within 150 m</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {sel.samples_within_150m.length === 0 && <span className="text-xs text-muted">none</span>}
                {sel.samples_within_150m.map((s) => (
                  <span key={s.name} className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[11px] text-sky-200">{s.name}</span>
                ))}
              </div>
              <p className="mt-3 text-[10px] leading-snug text-muted">
                * At an assumed {p.route.assumptions.pace_kmh} km/h. {p.route.assumptions.note}
              </p>
            </Section>
          )}
        </>
      )}
    </div>
  )
}
