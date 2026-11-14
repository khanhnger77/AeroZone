import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowRight, Plus, Route as RouteIcon, X } from 'lucide-react'
import { MARKER_STYLE, OBJECT_KINDS, type ObjectKind } from '../sim/world'
import type { ConnectionState } from '../useConnections'
import type { LonLat, RouteResponse } from '../types'
import { fmtDist, fmtLat, fmtLon } from '../map/geo'
import { Section, PanelHeader } from './ui'
import RouteComparison from './RouteComparison'

interface Props {
  plannerState: ConnectionState
  simState: ConnectionState
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
  /** the object driven in the path-traverse demo (fixed for the run) */
  object: ObjectKind
  onObject: (k: ObjectKind) => void
  roverName: string
  /** a simulation exists (possibly with the 3D view closed): which demo */
  session: 'path' | 'world' | null
  onReopen: () => void
  worldObjects: { kind: ObjectKind; name: string }[]
  onWorldAdd: (k: ObjectKind) => void
  onWorldRemove: (index: number) => void
  worldObstacles: boolean
  onWorldObstacles: (on: boolean) => void
  onWorldDemo: () => void
}

export default function MissionPanel(p: Props) {
  const comparison = useRef<HTMLDivElement>(null)
  const previousRoute = useRef(p.route)
  useEffect(() => {
    if (p.route && p.route !== previousRoute.current) {
      comparison.current?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
    }
    previousRoute.current = p.route
  }, [p.route])
  const locked = p.demoActive || p.demoLoading
  const plannerReason = p.plannerState === 'checking' ? 'Checking the planner connection…'
    : p.plannerState === 'offline' ? 'Planner is offline. Retry the connection in the header; map layers are still available.' : null
  const planReason = locked ? 'Exit the 3D demo to change or compute a route.'
    : p.loading ? 'Calculating your route alternatives…'
    : plannerReason ?? (!p.start || !p.end ? 'Pick both a start and a target, or try the sample route above.' : null)
  const sampleReason = locked ? 'Exit the 3D demo to try another route.' : p.loading ? 'Calculating routes…' : plannerReason
  const demoReason = p.demoActive ? null : p.demoLoading ? 'Preparing the 3D scene…'
    : p.simState === 'checking' ? 'Checking the 3D connection…'
    : p.simState === 'offline' ? 'Rover simulation is unavailable. Click the map for Terrain 3D and panorama views.'
    : p.loading ? 'Wait for route calculation to finish.'
    : !p.route ? 'Compute a route before starting the 3D demo.' : null
  const objectLabel = (k: ObjectKind) => (k === 'rover' ? `${p.roverName} Rover` : 'Astronaut')
  const pathRunning = p.session === 'path', worldRunning = p.session === 'world'
  // The two demo sections are collapsed by default; opening one closes the other. A running demo opens.
  const [openDemo, setOpenDemo] = useState<'path' | 'world' | null>(p.session)
  useEffect(() => { if (p.session) setOpenDemo(p.session) }, [p.session])
  const toggleDemo = (k: 'path' | 'world') => setOpenDemo(o => (o === k ? null : k))
  // Once the path demo has started, only the chosen route is listed.
  const shown = p.route && pathRunning ? { ...p.route, routes: p.route.routes.filter(r => r.id === (p.selected ?? p.route!.recommended)) } : p.route
  const worldReason = p.demoLoading ? 'Preparing the 3D scene…'
    : p.simState !== 'online' ? 'The 3D simulation is unavailable.'
    : p.worldObjects.length === 0 ? 'Add at least one object.' : null
  const pt = (v: LonLat | null) => (v ? `${fmtLat(v.lat)} ${fmtLon(v.lon)}` : 'not set')
  const sel = p.route?.routes.find((r) => r.id === (p.selected ?? p.route?.recommended))
  return (
    <div>
      <PanelHeader kicker="Mission planner" title="Plan an EVA" subtitle="Choose where to explore. Compare routes by distance, terrain hazard and slope." />
      <Section title="Start here">
        <div className="rounded-xl border border-mars/25 bg-gradient-to-br from-mars/10 to-transparent p-3">
          <div className="mb-1 flex items-center gap-2 text-sm font-semibold"><RouteIcon size={16} className="text-mars" />Your first Mars route</div>
          <p className="mb-3 text-xs leading-relaxed text-muted">Explore the journey from the landing site to Cheyava Falls. We’ll set both points and calculate three alternatives for you.</p>
          <button type="button" aria-label="Try sample route" aria-describedby={sampleReason ? 'sample-route-help' : undefined} disabled={!!sampleReason} onClick={p.onPreset} className="btn-primary w-full justify-between">
            {p.loading ? 'Calculating routes…' : 'Try sample route'}<ArrowRight size={15} aria-hidden="true" />
          </button>
          {sampleReason && <p id="sample-route-help" role="status" className="mt-2 text-[11px] leading-relaxed text-muted">{sampleReason}</p>}
          {p.route && <button type="button" onClick={() => comparison.current?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })} className="mt-2 flex w-full items-center justify-between rounded-lg px-1 py-2 text-xs text-cyan hover:bg-cyan/5">View route comparison<ArrowRight size={13} aria-hidden="true" /></button>}
        </div>
        <ol aria-label="Planning steps" className="mt-3 grid grid-cols-3 gap-2 text-[10px] text-muted">
          {['Choose points', 'Compute routes', 'Compare & explore'].map((label, i) => <li key={label} className="flex items-start gap-1.5"><span className="grid h-4 w-4 shrink-0 place-items-center rounded-full border border-line-2 font-mono text-cyan">{i + 1}</span><span>{label}</span></li>)}
        </ol>
      </Section>
      <Section title="1 · Choose your own points">
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
          <button aria-describedby={planReason ? 'compute-route-help' : undefined} disabled={!!planReason} onClick={p.onPlan}
            className="btn-primary flex-1">
            {p.loading ? 'Computing routes…' : 'Compute routes'}
          </button>
          <button disabled={p.demoActive || p.demoLoading} onClick={p.onReset} className="btn-ghost">Reset</button>
        </div>
        {planReason && <p id="compute-route-help" className="mt-2 text-[11px] leading-relaxed text-muted">{planReason}</p>}
        {p.error && <p role="alert" className="mt-2 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-200">{p.error}</p>}
      </Section>


      {p.route && (
        <>
          <div ref={comparison} className="scroll-mt-3" aria-label="Route comparison">
          <Section title="2 · Compare alternatives" right={<span className="text-[10px] text-muted">straight line {fmtDist(p.route.straight_line_m)}</span>}>
            <RouteComparison response={shown!} selected={p.selected} disabled={locked || p.loading} onSelect={p.onSelect} />
          </Section>
          </div>

          <Section title={`3 · Why ${p.route.routes.find(r => r.id === p.route!.recommended)?.label ?? p.route.recommended} is recommended`}>
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

      <Section title="Path traverse demo" open={openDemo === 'path'} onToggle={() => toggleDemo('path')} right={<span className={`tag ${pathRunning || p.simState === 'online' ? 'bg-emerald-400/10 text-emerald-300' : 'bg-panel-2 text-muted'}`}>{pathRunning ? 'Running' : p.simState === 'online' ? 'Available' : p.simState === 'checking' ? 'Checking' : 'Unavailable'}</span>}>
        <label className="mt-1 flex items-center justify-between gap-2 text-xs">
          Randomly generate obstacles
          <input type="checkbox" role="switch" aria-label="Randomly generate obstacles" checked={p.obstaclesOn} disabled={locked || p.simState !== 'online'} onChange={e => p.onObstacles(e.target.checked)} />
        </label>
        <label className="mt-2 block text-xs text-muted">Object
          <select aria-label="Object" value={p.object} disabled={locked || pathRunning} onChange={e => p.onObject(e.target.value as ObjectKind)}
            className="mt-1 block w-full rounded border border-line bg-panel-2 p-2 text-ink">
            {OBJECT_KINDS.map(k => <option key={k} value={k}>{objectLabel(k)}</option>)}
          </select>
        </label>
        {pathRunning && <p className="mt-1 text-[10.5px] text-muted">The object is fixed while the simulation runs. End the simulation to change it.</p>}
        <button aria-label={p.demoActive && pathRunning ? 'Exit simulation' : pathRunning ? 'Open 3D view' : 'Run demo'} aria-describedby={demoReason && !pathRunning ? 'demo-help' : undefined}
          disabled={!!demoReason && !pathRunning} onClick={pathRunning && !p.demoActive ? p.onReopen : p.onDemo} className="btn-primary mt-3 w-full shadow-lg shadow-orange-500/20">
          {p.demoLoading ? 'Loading demo…' : p.demoActive && pathRunning ? 'Exit simulation' : pathRunning ? 'Open 3D view' : 'Run demo'}
        </button>
        {demoReason && !pathRunning && <p id="demo-help" role="status" className="mt-2 text-[11px] leading-relaxed text-muted">{demoReason}</p>}
        {p.demoError && <p role="alert" className="mt-2 text-xs text-red-300">{p.demoError}</p>}
        {p.demoActive && pathRunning ? p.demoControls : <details className="mt-3 rounded-lg border border-line px-3">
          <summary className="cursor-pointer py-2 text-xs text-muted">Preview demo controls</summary>
          <p className="text-[11px] leading-relaxed text-muted">Drive, pause, speed and live telemetry become available after the 3D demo starts.</p>
          {p.demoControls}
        </details>}
      </Section>

      <Section title="World demo" open={openDemo === 'world'} onToggle={() => toggleDemo('world')} right={<span className={`tag ${worldRunning ? 'bg-emerald-400/10 text-emerald-300' : 'bg-panel-2 text-muted'}`}>{worldRunning ? 'Running' : 'Not running'}</span>}>
        <p className="text-[11px] leading-relaxed text-muted">Several objects roam the whole crater at their own speeds. The demo opens on the whole-AOI view: pick an object to ride along and drive it.</p>
        <div className="mt-2 flex gap-2">
          {OBJECT_KINDS.map(k => {
            const { Icon, color } = MARKER_STYLE[k]
            return <button key={k} type="button" disabled={worldRunning || p.demoLoading || p.worldObjects.length >= 40} onClick={() => p.onWorldAdd(k)} className="btn-ghost flex-1 !py-1.5 text-[11px]">
              <Plus size={12} aria-hidden="true" /><Icon size={12} style={{ color }} />{k === 'rover' ? 'Rover' : 'Astronaut'}
            </button>
          })}
        </div>
        <ul className="mt-2 flex flex-wrap gap-1" aria-label="World objects">
          {p.worldObjects.map((o, i) => {
            const { Icon, color } = MARKER_STYLE[o.kind]
            return <li key={o.name} className="flex items-center gap-1 rounded-full border border-line py-0.5 pl-2 pr-1 text-[10.5px]">
              <Icon size={11} style={{ color }} />{o.name}
              <button type="button" disabled={worldRunning} onClick={() => p.onWorldRemove(i)} aria-label={`Remove ${o.name}`} className="text-muted hover:text-red-300 disabled:opacity-30"><X size={11} /></button>
            </li>
          })}
          {p.worldObjects.length === 0 && <li className="text-[11px] text-muted">No objects yet.</li>}
        </ul>
        <label className="mt-2 flex items-center justify-between gap-2 text-xs">
          Random obstacles (sparse, whole map)
          <input type="checkbox" role="switch" aria-label="World obstacles" checked={p.worldObstacles} disabled={worldRunning || p.demoLoading} onChange={e => p.onWorldObstacles(e.target.checked)} />
        </label>
        <button type="button" disabled={!worldRunning && !!worldReason} onClick={worldRunning && !p.demoActive ? p.onReopen : p.onWorldDemo} className="btn-primary mt-3 w-full">
          {p.demoLoading ? 'Loading…' : p.demoActive && worldRunning ? 'Exit simulation' : worldRunning ? 'Open 3D view' : 'Run world demo'}
        </button>
        {!worldRunning && worldReason && <p role="status" className="mt-2 text-[11px] text-muted">{worldReason}</p>}
        {worldRunning && p.demoActive && p.demoControls}
      </Section>

    </div>
  )
}
