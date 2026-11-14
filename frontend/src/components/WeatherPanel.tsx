import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, ExternalLink, MapPin, RefreshCw, Thermometer } from 'lucide-react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps } from 'recharts'
import { kelvinToCelsius, loadWeather, type MedaWeather } from '../services/weather'
import type { LonLat } from '../types'
import { CategoryTag, PanelHeader, Section, Stat } from './ui'
import InfoTooltip from './InfoTooltip'

const utc = (value: string) => new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(value))

function ChartTip({ active, payload, label, metric, mode }: TooltipProps<number, string> & { metric: 'temperature' | 'pressure'; mode: 'sols' | 'hours' }) {
  if (!active || !payload?.length) return null
  const value = payload[0].value
  return <div className="rounded-lg border border-line-2 bg-panel px-3 py-2 text-[11px] shadow-xl"><div className="text-muted">{mode === 'sols' ? `Sol ${label}` : `${String(label).padStart(2, '0')}:00 LMST`}</div><div className="mt-1 font-mono text-cyan">{typeof value === 'number' ? `${value.toFixed(1)} ${metric === 'temperature' ? '°C' : 'Pa'}` : 'No observation'}</div></div>
}

export default function WeatherPanel({ requestedSol, onSelectSol, onFly }: {
  requestedSol: number | null; onSelectSol: (sol: number) => void; onFly: (point: LonLat) => void
}) {
  const [weather, setWeather] = useState<MedaWeather | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [metric, setMetric] = useState<'temperature' | 'pressure'>('temperature')
  const [mode, setMode] = useState<'sols' | 'hours'>('sols')
  useEffect(() => {
    const controller = new AbortController()
    setError(null)
    loadWeather(controller.signal).then(setWeather).catch(error => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : 'Weather could not load.') })
    return () => controller.abort()
  }, [revision])
  const selected = weather?.sols.find(row => row.sol === (requestedSol ?? weather.sols[weather.sols.length - 1].sol))
  const index = selected && weather ? weather.sols.indexOf(selected) : -1
  const series = useMemo(() => {
    if (!weather) return []
    if (mode === 'hours') return (selected?.hourly ?? []).map(row => ({ x: row.hour, value: metric === 'temperature' ? (row.t_air_mean_k === null ? null : kelvinToCelsius(row.t_air_mean_k)) : row.p_mean_pa }))
    const bySol = new Map(weather.sols.map(row => [row.sol, row]))
    return Array.from({ length: weather.requested_range[1] - weather.requested_range[0] + 1 }, (_, i) => {
      const sol = weather.requested_range[0] + i, row = bySol.get(sol)
      return { x: sol, value: !row ? null : metric === 'temperature' ? kelvinToCelsius(row.t_air_mean_k) : row.p_mean_pa }
    })
  }, [weather, selected, metric, mode])
  return <div>
    <PanelHeader kicker="Weather · rover observations" title="Jezero weather" subtitle="Recorded conditions at Perseverance. Choose a sol to explore temperature and pressure." />
    {error ? <div role="alert" className="space-y-3 p-5"><p className="text-sm text-muted">{error}</p><button type="button" onClick={() => setRevision(r => r + 1)} className="btn-ghost"><RefreshCw size={13} />Retry weather</button></div> : !weather ? <div role="status" className="p-5 text-sm text-muted">Loading MEDA observations…</div> : <>
      <Section title="Observation archive" right={<CategoryTag c="observation" />}>
        <div className="rounded-xl border border-cyan/20 bg-cyan/5 p-3">
          <div className="flex items-center gap-2 text-xs font-semibold text-cyan"><Thermometer size={15} />Measured at the rover</div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted">Historical observations, not live weather or a forecast. This snapshot covers {weather.sols.length} sols: {weather.sols[0].sol}–{weather.sols[weather.sols.length - 1].sol}.</p>
          <p className="mt-1 text-[10px] text-muted">{utc(weather.sols[0].start_utc)} – {utc(weather.sols[weather.sols.length - 1].end_utc)} UTC</p>
        </div>
      </Section>
      <Section title="Choose a sol">
        <div className="flex items-center gap-2">
          <button type="button" aria-label="Previous weather sol" disabled={index <= 0} onClick={() => onSelectSol(weather.sols[index - 1].sol)} className="btn-ghost !px-2 disabled:opacity-30"><ChevronLeft size={15} /></button>
          <label className="flex min-w-0 flex-1 items-center gap-2 text-xs text-muted">Sol
            <select aria-label="Weather sol" value={selected?.sol ?? ''} onChange={e => onSelectSol(Number(e.target.value))} className="min-w-0 flex-1 rounded-lg border border-line-2 bg-panel-2 px-2 py-2 text-ink">
              {!selected && <option value="" disabled>{requestedSol !== null ? `${requestedSol} · no observation` : 'Select sol'}</option>}
              {weather.sols.map(row => <option key={row.sol} value={row.sol}>{row.sol} · {utc(row.start_utc)}</option>)}
            </select>
          </label>
          <button type="button" aria-label="Next weather sol" disabled={index < 0 || index >= weather.sols.length - 1} onClick={() => onSelectSol(weather.sols[index + 1].sol)} className="btn-ghost !px-2 disabled:opacity-30"><ChevronRight size={15} /></button>
        </div>
        {!selected ? <div className="mt-3 space-y-2 text-xs text-muted"><p>No MEDA observation for sol {requestedSol} in this snapshot. Another day’s values are not substituted.</p><button type="button" onClick={() => onSelectSol(weather.sols[weather.sols.length - 1].sol)} className="btn-ghost">Show last available sol</button></div> : <>
          <p className="mt-2 text-[10px] text-muted">Observation window: {utc(selected.start_utc)} – {utc(selected.end_utc)} UTC</p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Stat label="Mean air temp." value={kelvinToCelsius(selected.t_air_mean_k).toFixed(1)} unit="°C" tone="text-cyan" />
            <Stat label="Mean pressure" value={selected.p_mean_pa.toFixed(1)} unit="Pa" />
            <Stat label="Min air temp." value={kelvinToCelsius(selected.t_air_min_k).toFixed(1)} unit="°C" />
            <Stat label="Max air temp." value={kelvinToCelsius(selected.t_air_max_k).toFixed(1)} unit="°C" />
          </div>
          <p className="mt-2 text-[10px] leading-relaxed text-muted">Calibrated local air temperature at ATS1. Means and extrema use the accepted observations, which may not cover the full sol.</p>
          {selected.location && <div className="mt-3 rounded-lg border border-line px-3 py-2">
            <button type="button" onClick={() => onFly(selected.location!)} className="flex items-center gap-1.5 text-xs text-cyan hover:underline"><MapPin size={13} />Show approximate rover location</button>
            <p className="mt-1 text-[10px] text-muted">Last rover fix on/before this sol: {selected.location.waypoint_sol}. These readings do not describe every point on the map.</p>
          </div>}
        </>}
      </Section>
      <Section title={mode === 'sols' ? 'Across the loaded sols' : 'Within the selected sol'}>
        <div className="mb-2 grid grid-cols-2 gap-2">
          {(['temperature', 'pressure'] as const).map(value => <button type="button" key={value} aria-pressed={metric === value} onClick={() => setMetric(value)} className={`rounded-lg border px-2 py-1.5 text-xs ${metric === value ? 'border-cyan/40 bg-cyan/10 text-cyan' : 'border-line text-muted'}`}>{value === 'temperature' ? 'Temperature · °C' : 'Pressure · Pa'}</button>)}
        </div>
        <div className="mb-2 flex gap-3 text-[10px] text-muted">{(['sols', 'hours'] as const).map(value => <button type="button" key={value} disabled={value === 'hours' && !selected} aria-pressed={mode === value} onClick={() => setMode(value)} className={mode === value ? 'text-cyan underline underline-offset-4' : 'hover:text-ink disabled:opacity-30'}>{value === 'sols' ? 'By sol' : 'By local hour'}</button>)}</div>
        <div className="h-[180px]" aria-label={`${metric} chart by ${mode === 'sols' ? 'sol' : 'local hour'}`}>
          {series.length ? <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series} margin={{ top: 8, right: 10, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="#1c2633" strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="x" type="number" domain={['dataMin', 'dataMax']} allowDecimals={false} tick={{ fontSize: 9 }} stroke="#8494a7" tickLine={false} />
              <YAxis domain={['auto', 'auto']} tick={{ fontSize: 9 }} stroke="#8494a7" tickLine={false} tickFormatter={v => Number(v).toFixed(0)} />
              <Tooltip content={<ChartTip metric={metric} mode={mode} />} />
              <Line name={metric} dataKey="value" type="linear" stroke={metric === 'temperature' ? '#5ee6ff' : '#ff9a6b'} strokeWidth={2} dot={{ r: 2 }} connectNulls={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer> : <p className="p-3 text-xs text-muted">Choose a sol with observations to see hourly values.</p>}
        </div>
        <p className="mt-2 text-[10px] leading-relaxed text-muted">{mode === 'hours' ? 'Hourly sample means in local mean solar time (LMST), not Earth clock time.' : 'Each point is a mean of the observations in one sol.'} Missing observations leave gaps.</p>
      </Section>
      <Section title="Source & quality" right={<InfoTooltip label="Weather processing details"><p className="mb-2 font-semibold text-ink">How the snapshot was built</p>{Object.entries(weather.method).map(([key, value]) => <p key={key} className="mt-2">{value}</p>)}</InfoTooltip>}>
        <div className="space-y-2 text-[11px] leading-relaxed text-muted">
          {selected && <p>{selected.n_samples.toLocaleString()} accepted ATS1 samples · {selected.n_pressure_samples.toLocaleString()} accepted pressure samples.</p>}
          <p>NASA PDS · Perseverance MEDA<br />Retrieved {utc(weather.retrieved_utc)} UTC</p>
          <a href={weather.source} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-cyan hover:underline">MEDA archive<ExternalLink size={11} /></a>
          {selected && <div className="flex gap-3"><a href={selected.temperature_source} target="_blank" rel="noreferrer" className="text-cyan hover:underline">Temperature CSV</a><a href={selected.pressure_source} target="_blank" rel="noreferrer" className="text-cyan hover:underline">Pressure CSV</a></div>}
          {weather.skipped.length > 0 && <details><summary className="cursor-pointer">Missing sols ({weather.skipped.length})</summary><ul className="mt-1 list-disc pl-4">{weather.skipped.map(text => <li key={text}>{text}</li>)}</ul></details>}
        </div>
      </Section>
    </>}
  </div>
}
