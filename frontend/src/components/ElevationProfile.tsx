import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ProfilePoint, Route } from '../types'

const hazardColor = (h: number) =>
  h < 20 ? '#4ade80' : h < 40 ? '#facc15' : h < 60 ? '#fb923c' : h < 80 ? '#ef4444' : '#a21caf'

interface Props {
  route: Route
  onHover: (p: ProfilePoint | null) => void
  onClose: () => void
  /** phone layout: shorter chart, fewer header details */
  compact?: boolean
}

/** Elevation profile of the selected route; the fill is coloured by modelled hazard along the path. */
export default function ElevationProfile({ route, onHover, onClose, compact }: Props) {
  const data = route.profile
  const dmax = data[data.length - 1]?.d || 1
  const zs = data.map((p) => p.z)
  const zmin = Math.min(...zs), zmax = Math.max(...zs)
  const pad = Math.max(5, (zmax - zmin) * 0.12)
  const gid = `hz-${route.id}`
  return (
    <div className="glass hud-corners fade-in rounded-xl px-4 pb-2 pt-3">
      <div className="mb-1 flex items-center gap-3 sm:gap-4">
        <span className="label whitespace-nowrap">Elevation profile</span>
        <span className="flex items-center gap-1.5 text-xs">
          <i className="h-1 w-4 rounded" style={{ background: route.color }} />{route.label}
        </span>
        <span className={`min-w-0 truncate whitespace-nowrap font-mono text-[11px] text-muted ${compact ? 'hidden' : 'hidden lg:inline'}`}>
          {(dmax / 1000).toFixed(2)} km · ↑{Math.round(route.ascent_m)} m ↓{Math.round(route.descent_m)} m · max ∠{route.slope_max_deg}°
        </span>
        <span className="ml-auto flex items-center gap-3 text-[10px] text-muted">
          {compact && <span className="whitespace-nowrap font-mono">{(dmax / 1000).toFixed(1)} km · ↑{Math.round(route.ascent_m)} m</span>}
          {!compact && [['Low', '#4ade80'], ['Mod', '#facc15'], ['Elev', '#fb923c'], ['High', '#ef4444']].map(([l, c]) => (
            <span key={l} className="flex items-center gap-1"><i className="h-1.5 w-1.5 rounded-full" style={{ background: c }} />{l}</span>
          ))}
          <button onClick={onClose} aria-label="Hide profile" className="ml-1 rounded px-1.5 text-base leading-none text-muted hover:text-ink">×</button>
        </span>
      </div>
      <div className={compact ? 'h-[96px]' : 'h-[132px]'}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}
            onMouseMove={(s) => { const i = s?.activeTooltipIndex; onHover(i != null ? data[i] : null) }}
            onMouseLeave={() => onHover(null)}>
            <defs>
              <linearGradient id={gid} x1="0" y1="0" x2="1" y2="0">
                {data.map((p, i) => (
                  <stop key={i} offset={`${(p.d / dmax) * 100}%`} stopColor={hazardColor(p.hazard)} />
                ))}
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#1c2633" strokeDasharray="2 4" vertical={false} />
            <XAxis dataKey="d" type="number" domain={[0, dmax]} tickFormatter={(v) => `${(v / 1000).toFixed(1)}`}
              stroke="#4f5d6e" fontSize={10} tickLine={false} axisLine={false} unit=" km" />
            <YAxis domain={[Math.floor(zmin - pad), Math.ceil(zmax + pad)]} stroke="#4f5d6e" fontSize={10}
              tickLine={false} axisLine={false} width={44} tickFormatter={(v) => `${v}`} />
            <Tooltip cursor={{ stroke: '#5ee6ff', strokeWidth: 1 }} content={<Tip />} />
            <Area type="monotone" dataKey="z" baseValue="dataMin" stroke={`url(#${gid})`} strokeWidth={2.2}
              fill={`url(#${gid})`} fillOpacity={0.22} isAnimationActive />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function Tip({ active, payload }: { active?: boolean; payload?: { payload: ProfilePoint }[] }) {
  if (!active || !payload?.length) return null
  const p: ProfilePoint = payload[0].payload
  return (
    <div className="rounded-md border border-line-2 bg-panel/95 px-2.5 py-1.5 font-mono text-[11px] shadow-xl">
      <div className="text-cyan">{(p.d / 1000).toFixed(2)} km</div>
      <div>Z {p.z.toFixed(1)} m</div>
      <div>∠ {p.slope.toFixed(1)}°</div>
      <div style={{ color: hazardColor(p.hazard) }}>Hazard {p.hazard.toFixed(0)}</div>
    </div>
  )
}
