import type { SimCommand, SimControlsConfig, SimTelemetry } from '../services/sim'

interface Props {
  active: boolean; config: SimControlsConfig | null; telemetry: SimTelemetry | null
  speed: number; warp: number; onCommand: (command: SimCommand) => void
}
export default function DemoControls({ active, config, telemetry: t, speed, warp, onCommand }: Props) {
  const DEMO_CONTROLS = [
    { label: 'Vehicle speed', type: 'set_speed' as const, values: config?.speeds_m_s ?? [], value: speed,
      format: (v: number) => v <= (config?.real_max_speed_m_s ?? 0) ? `${(v * 100).toFixed(1)} cm/s · real` : `${v} m/s` },
    { label: 'Simulation speed', type: 'set_warp' as const, values: config?.warps ?? [], value: warp, format: (v: number) => `${v}×` },
  ]
  return <fieldset disabled={!active} className={`my-3 space-y-3 rounded-lg border border-line p-3 ${active ? '' : 'opacity-40'}`}>
    <legend className="label px-1">Demo controls</legend>
    <div className="flex gap-2">
      <button className="btn-primary flex-1" onClick={() => onCommand({ type: t?.nav.state === 'driving' ? 'pause' : 'start' })}>
        {t?.nav.state === 'driving' ? 'Pause' : t?.nav.state === 'paused' ? 'Resume' : 'Drive'}
      </button>
      <button className="btn-ghost" onClick={() => onCommand({ type: 'reset' })}>Reset</button>
    </div>
    {DEMO_CONTROLS.map(control => <label key={control.type} className="block text-xs text-muted">{control.label}
      <select className="mt-1 block w-full rounded border border-line bg-panel-2 p-2 text-ink" value={control.value}
        onChange={e => onCommand({ type: control.type, value: Number(e.target.value) })}>
        {(control.values.includes(control.value) ? control.values : [control.value, ...control.values]).map(v => <option key={v} value={v}>{control.format(v)}</option>)}
      </select>
    </label>)}
    {t && <div className="space-y-2 font-mono text-[11px]" aria-live="polite">
      <progress className="h-2 w-full" max={1} value={t.nav.progress} />
      <div>{(t.nav.progress * 100).toFixed(1)}% · {t.nav.state} · {t.speed_m_s.toFixed(3)} m/s</div>
      <div>Remaining {t.nav.distance_remaining_m.toFixed(0)} m</div>
      <div>Pitch {t.pitch_deg.toFixed(1)}° · Roll {t.roll_deg.toFixed(1)}°</div>
      <div>Elevation {t.elev_m.toFixed(1)} m</div>
      <div>Cross-track {t.nav.cross_track_m.toFixed(1)} m · Time {t.sim_time.toFixed(1)} s</div>
      {t.avoid && <div className="space-y-1 border-t border-line pt-2">
        <div className="text-cyan">Obstacle avoidance · {{ track: 'on route', avoid: 'detouring', recover: 'backing up', blocked: 'blocked' }[t.avoid.mode]}</div>
        <div>{t.avoid.rocks} boulders · {t.avoid.craters} craters</div>
        <div>{t.avoid.detours} detours · {t.avoid.recoveries} recoveries</div>
        <div>Ahead {t.avoid.blocked_ahead_m?.toFixed(1) ?? '—'} m · Scan + plan {t.avoid.scan_ms.toFixed(1)} ms</div>
        <div className="break-words text-muted">{t.avoid.planner}</div>
      </div>}
    </div>}
  </fieldset>
}
