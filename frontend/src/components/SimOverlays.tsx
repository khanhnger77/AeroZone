/* Overlays for locations and live sim objects: the status-bar legend, the info popups, the object
   selector, the "Navigate to" list and the virtual joystick. */
import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from 'react'
import { Crosshair, Navigation, X } from 'lucide-react'
import { fmtDist, fmtLat, fmtLon } from '../map/geo'
import {
  LOCATION_TYPES, MARKER_STYLE, MODE_LABEL, OBJECT_KINDS, RESOURCE, bearingDeg, compass, distM, energyColor,
  type MapLocation, type MarkerType, type WorldAgent,
} from '../sim/world'

/* ------------------------------------------------------------------ legend */
/** Map legend, fixed in the middle of the bottom status bar: every location type and object type
 *  (always listed, sim running or not) with its icon; click one to show / hide those icons.
 *  Labels appear from 2xl widths; below that the icons carry them as tooltips. */
export function MapLegend({ visible, onToggle }: { visible: Record<MarkerType, boolean>; onToggle: (t: MarkerType) => void }) {
  const types: MarkerType[] = [...LOCATION_TYPES, ...OBJECT_KINDS]
  return (
    <div className="flex items-center gap-1" aria-label="Map legend">
      {types.map((t, i) => {
        const on = visible[t], { label, color, Icon } = MARKER_STYLE[t]
        return <button key={t} type="button" aria-pressed={on} onClick={() => onToggle(t)} title={`${on ? 'Hide' : 'Show'} ${label} icons`}
          className={`flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10.5px] transition-colors hover:bg-white/5 ${on ? 'text-ink' : 'text-faint line-through'} ${i === LOCATION_TYPES.length ? 'ml-2 border-l border-line-2 pl-2.5' : ''}`}>
          <span className="grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full" style={{ background: on ? color : 'transparent', border: `1px solid ${color}` }}>
            <Icon size={11} color={on ? '#05070a' : color} />
          </span>
          <span className="hidden 2xl:inline">{label}</span>
        </button>
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ popups */
const Row = ({ k, v }: { k: string; v: string }) => <><dt className="text-muted">{k}</dt><dd className="truncate font-mono">{v}</dd></>

/** Info panel above the centred (tracked) icon. */
export function FocusPopup({ children, onClose, title, type }: { children: React.ReactNode; onClose: () => void; title: string; type: MarkerType }) {
  const { Icon, color, label } = MARKER_STYLE[type]
  return (
    <div className="pointer-events-none fixed left-1/2 top-1/2 z-[1300] -translate-x-1/2 -translate-y-[calc(100%+34px)]">
      <div className="glass fade-in pointer-events-auto w-[250px] rounded-xl p-3 shadow-2xl">
        <div className="mb-2 flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded-full" style={{ background: color }}><Icon size={13} color="#05070a" /></span>
          <div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold">{title}</div><div className="text-[10px] uppercase tracking-wider text-muted">{label}</div></div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-muted hover:text-ink"><X size={14} /></button>
        </div>
        {children}
      </div>
    </div>
  )
}

export function AgentPopup({ agent, agents, locations, isSelected, arming, speeds, onSpeed, onEnergy, onSelect, onTraverse, onClose }: {
  agent: WorldAgent; agents: WorldAgent[]; locations: MapLocation[]; isSelected: boolean; arming: boolean
  /** demo speeds (m/s) for the speed drop-down, shown while the object is moving on its own */
  speeds: number[]; onSpeed: (v: number) => void
  /** set the oxygen level (astronauts; for testing the low-oxygen alert) */
  onEnergy?: (v: number) => void
  onSelect: () => void; onTraverse: () => void; onClose: () => void
}) {
  const moving = ['wander', 'goto', 'intercept', 'route'].includes(agent.mode)
  const options = speeds.includes(agent.cruise_m_s) ? speeds : [...speeds, agent.cruise_m_s].sort((a, b) => a - b)
  const b = bearingDeg(agent.yaw_deg)
  const target = agent.target ? agents.find(a => a.id === agent.target)?.name : null
  // Mission target: another object (straight-line distance), a location or a map point (path
  // length along the remaining route, and the straight-line distance).
  const tgtAgent = agent.mode === 'intercept' ? agents.find(a => a.id === agent.target) : undefined
  const goal = agent.mode === 'goto' ? agent.goal : null
  const tgtLoc = goal ? locations.find(l => distM(l, goal) < 10) : undefined
  const pathM = agent.path && agent.path.length > 1
    ? agent.path.slice(1).reduce((s, [lon, lat], i) => s + distM({ lon: agent.path![i][0], lat: agent.path![i][1] }, { lon, lat }), 0) : null
  return <FocusPopup title={agent.name} type={agent.kind} onClose={onClose}>
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
      {moving && speeds.length ? <>
        <dt className="text-muted">Speed</dt>
        <dd className="flex items-center gap-1.5 font-mono">{agent.speed_m_s.toFixed(2)} m/s
          <select aria-label={`Set ${agent.name} speed`} value={agent.cruise_m_s} onChange={e => onSpeed(Number(e.target.value))} style={{ colorScheme: 'dark' }}
            className="ml-auto rounded border border-line bg-panel-2 px-1 py-0.5 text-[10.5px] text-ink" title="Cruise speed on flat ground">
            {options.map(v => <option key={v} value={v} className="bg-panel-2">{v < 0.1 ? `${(v * 100).toFixed(1)} cm/s` : `${+v.toFixed(2)} m/s`}</option>)}
          </select></dd>
      </> : <Row k="Speed" v={`${agent.speed_m_s.toFixed(2)} m/s`} />}
      {agent.energy != null && <><dt className="text-muted">{RESOURCE[agent.kind].label}</dt>
        <dd className="flex items-center gap-1.5 font-mono"><span style={{ color: energyColor(agent.energy) }}>{agent.energy.toFixed(1)}%</span>
          {agent.kind === 'astronaut' && onEnergy && <EnergyEditor key={agent.id} onSet={onEnergy} />}</dd></>}
      <Row k="Heading" v={`${b.toFixed(0)}° ${compass(b)}`} />
      <Row k="Status" v={`${MODE_LABEL[agent.mode] ?? agent.mode}${target ? ` · ${target}` : ''}`} />
      <Row k="Position" v={`${fmtLat(agent.lat)} ${fmtLon(agent.lon)}`} />
      {tgtAgent && <><Row k="Target" v={tgtAgent.name} /><Row k="Straight line" v={fmtDist(distM(agent, tgtAgent))} /></>}
      {goal && <>
        <Row k="Target" v={tgtLoc ? tgtLoc.name : 'Map point (Target)'} />
        {pathM != null && <Row k="Path" v={fmtDist(pathM)} />}
        <Row k="Straight line" v={fmtDist(distM(agent, goal))} />
      </>}
    </dl>
    <div className="mt-2.5 flex gap-2">
      <button type="button" disabled={isSelected} onClick={onSelect} className="btn-ghost flex-1 !py-1.5 text-[11px]">{isSelected ? 'Selected' : 'Select object'}</button>
      <button type="button" onClick={onTraverse} className={`flex-1 !py-1.5 text-[11px] ${arming ? 'btn-ghost border-mars text-ink' : 'btn-primary'}`}>{arming ? 'Choosing…' : 'Choose destination'}</button>
    </div>
    {arming && <p className="mt-1.5 text-[10.5px] leading-snug text-muted">Click the map, a location icon (cautious route) or another object (intercept).</p>}
  </FocusPopup>
}

export function LocationPopup({ loc, selected, canTravel, onTravel, onClose }: {
  loc: MapLocation; selected: WorldAgent | null; canTravel: boolean; onTravel: () => void; onClose: () => void
}) {
  return <FocusPopup title={loc.name} type={loc.type} onClose={onClose}>
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
      <Row k="Location" v={`${fmtLat(loc.lat)} ${fmtLon(loc.lon)}`} />
      {loc.description && <><dt className="text-muted">Notes</dt><dd className="whitespace-pre-wrap break-words">{loc.description}</dd></>}
      {selected && <Row k={`From ${selected.name}`} v={fmtDist(distM(selected, loc))} />}
    </dl>
    {canTravel && <button type="button" disabled={!selected} onClick={onTravel} className="btn-primary mt-2.5 w-full !py-1.5 text-[11px]">
      <Navigation size={12} aria-hidden="true" />Travel here{selected ? ` · ${selected.name}` : ''}
    </button>}
  </FocusPopup>
}

/** Small "set oxygen" field in the astronaut popup, to try the low-oxygen alert. */
function EnergyEditor({ onSet }: { onSet: (v: number) => void }) {
  const [v, setV] = useState('25')
  const ok = v !== '' && Number.isFinite(Number(v)) && Number(v) >= 0 && Number(v) <= 100
  return <form className="ml-auto flex items-center gap-1" onSubmit={e => { e.preventDefault(); if (ok) onSet(Number(v)) }} title="Set the oxygen level (testing)">
    <input aria-label="Oxygen level to set (percent)" inputMode="decimal" value={v} onChange={e => setV(e.target.value)}
      className="w-11 rounded border border-line bg-panel-2 px-1 py-0.5 text-right text-[10.5px] text-ink" />
    <span className="text-[10px] text-muted">%</span>
    <button type="submit" disabled={!ok} className="rounded border border-line px-1.5 py-0.5 text-[10px] text-muted hover:text-ink disabled:opacity-40">Set</button>
  </form>
}

/* ------------------------------------------------------------------ low-oxygen alerts */
/**
 * Top-of-screen alert for each astronaut under 30 % oxygen, ending in an investigate (attention)
 * button. It opens a dialog to send a rover: the closest one is preselected, at the highest demo
 * speed, with a straight-line ETA. The alert goes away only once a rover is sent.
 */
export function OxygenAlerts({ people, rovers, maxSpeed, warp, top, onSend }: {
  people: WorldAgent[]; rovers: WorldAgent[]; maxSpeed: number; warp: number; top: string
  onSend: (roverId: string, personId: string) => void
}) {
  const [open, setOpen] = useState<string | null>(null)
  const [rover, setRover] = useState('')
  const person = people.find(p => p.id === open)
  const byDistance = person ? [...rovers].sort((a, b) => distM(a, person) - distM(b, person)) : []
  const chosen = byDistance.find(r => r.id === rover) ?? byDistance[0]
  const Alert = MARKER_STYLE.Attention.Icon
  const fmt = (t: number) => (t >= 3600 ? `${Math.floor(t / 3600)} h ${Math.round((t % 3600) / 60)} min` : t >= 60 ? `${Math.round(t / 60)} min` : `${Math.round(t)} s`)
  const eta = (r: WorldAgent, p: WorldAgent) => {
    const s = distM(r, p) / Math.max(maxSpeed, 0.01)
    return warp > 1 ? `${fmt(s)} sim time (${fmt(s / warp)} at ${warp}×)` : fmt(s)
  }
  if (!people.length) return null
  return (
    <div className={`pointer-events-none fixed left-1/2 z-[1500] flex w-[min(560px,calc(100vw-32px))] -translate-x-1/2 flex-col items-stretch gap-1.5 ${top}`} role="alert">
      {people.map(p => (
        <div key={p.id} className="pointer-events-auto flex items-center gap-2 rounded-xl border border-red-400/60 bg-red-950/85 px-3 py-2 text-xs text-red-100 shadow-lg backdrop-blur">
          <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-red-400" />
          <span className="min-w-0 flex-1"><b>{p.name}</b>: oxygen low, <b className="font-mono">{(p.energy ?? 0).toFixed(1)}%</b></span>
          <button type="button" onClick={() => { setOpen(p.id); setRover('') }} title="Investigate: send a rover" aria-label={`Investigate ${p.name}`}
            className="grid h-7 w-7 shrink-0 place-items-center rounded-full" style={{ background: MARKER_STYLE.Attention.color }}>
            <Alert size={15} color="#05070a" />
          </button>
        </div>
      ))}
      {person && (
        <div className="glass fade-in pointer-events-auto rounded-xl p-3 text-xs shadow-2xl" role="dialog" aria-label={`Send a rover to ${person.name}`}>
          <div className="mb-2 text-sm font-semibold">Send a rover to {person.name}?</div>
          {byDistance.length === 0 ? <p className="text-muted">No rover in this simulation.</p> : <>
            <label className="block text-muted">Rover
              <select value={chosen?.id ?? ''} onChange={e => setRover(e.target.value)} style={{ colorScheme: 'dark' }}
                className="mt-1 block w-full rounded border border-line bg-panel-2 p-1.5 text-ink">
                {byDistance.map((r, i) => <option key={r.id} value={r.id} className="bg-panel-2">{r.name} · {fmtDist(distM(r, person))}{i === 0 ? ' (closest)' : ''}</option>)}
              </select>
            </label>
            {chosen && <p className="mt-2 text-muted">At {maxSpeed} m/s (highest speed). ETA ≈ <b className="text-ink">{eta(chosen, person)}</b>
              <span className="block text-[10.5px] text-faint">Straight line: slopes, rough ground and detours make it longer. The rover intercepts the person.</span></p>}
          </>}
          <div className="mt-3 flex justify-end gap-2">
            <button type="button" onClick={() => setOpen(null)} className="btn-ghost !py-1.5 text-xs">Cancel</button>
            <button type="button" disabled={!chosen} onClick={() => { if (chosen) { onSend(chosen.id, person.id); setOpen(null) } }} className="btn-primary !py-1.5 text-xs">Send {chosen?.name ?? 'rover'}</button>
          </div>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ corner selector */
export function ObjectSelector({ agents, selected, onSelect, onFocus }: {
  agents: WorldAgent[]; selected: string | null; onSelect: (id: string) => void; onFocus: (id: string) => void
}) {
  return (
    <div className="glass pointer-events-auto rounded-xl p-2 fade-in" aria-label="Simulation objects">
      <div className="mb-1 flex items-center justify-between px-1 text-[11px] font-medium">Sim objects<span className="text-[10px] text-muted">{agents.length}</span></div>
      <ul className="scroll-thin max-h-[132px] space-y-0.5 overflow-y-auto">
        {agents.map(a => {
          const { Icon, color } = MARKER_STYLE[a.kind]
          const sel = a.id === selected
          return <li key={a.id} className={`flex items-center gap-1.5 rounded-lg px-1.5 py-1 text-[11px] ${sel ? 'bg-mars/15' : 'hover:bg-white/5'}`}>
            <button type="button" onClick={() => onFocus(a.id)} className="flex min-w-0 flex-1 items-center gap-1.5 text-left">
              <Icon size={12} style={{ color }} /><span className="truncate">{a.name}</span>
              <span className="ml-auto shrink-0 text-[9.5px] text-muted">{MODE_LABEL[a.mode] ?? a.mode}</span>
            </button>
            <input type="radio" name="mo-selected-object" aria-label={`Select ${a.name}`} checked={sel} onChange={() => onSelect(a.id)} />
          </li>
        })}
      </ul>
    </div>
  )
}

/* ------------------------------------------------------------------ navigate to */
type NavItem = { key: string; kind: 'agent' | 'location'; id: string; name: string; type: MarkerType; lon: number; lat: number; d: number }

/** "Navigate to" list in the 3D chase / orbit view: objects and locations by distance. */
export function NavigatePanel({ me, agents, locations, picking, onPickOnMap, onGo, onClose }: {
  me: WorldAgent; agents: WorldAgent[]; locations: MapLocation[]; picking: boolean
  onPickOnMap: () => void; onGo: (item: { kind: 'agent' | 'location'; id: string; lon: number; lat: number }) => void; onClose: () => void
}) {
  const all: MarkerType[] = [...OBJECT_KINDS, ...LOCATION_TYPES]
  const [types, setTypes] = useState<Set<MarkerType>>(new Set(all))
  const [nearest, setNearest] = useState(false)
  const [chosen, setChosen] = useState<string | null>(null)
  const items = useMemo(() => {
    const list: NavItem[] = [
      ...agents.filter(a => a.id !== me.id).map(a => ({ key: `a:${a.id}`, kind: 'agent' as const, id: a.id, name: a.name, type: a.kind, lon: a.lon, lat: a.lat, d: distM(me, a) })),
      ...locations.map(l => ({ key: `l:${l.id}`, kind: 'location' as const, id: l.id, name: l.name, type: l.type, lon: l.lon, lat: l.lat, d: distM(me, l) })),
    ].filter(i => types.has(i.type)).sort((a, b) => a.d - b.d)
    if (!nearest) return list
    const seen = new Set<MarkerType>()
    return list.filter(i => !seen.has(i.type) && (seen.add(i.type), true))
  }, [agents, locations, me, types, nearest])
  const item = items.find(i => i.key === chosen)
  return (
    <div className="glass fade-in pointer-events-auto flex max-h-[min(420px,calc(100vh-180px))] w-[270px] flex-col rounded-xl p-2.5 shadow-2xl" aria-label="Navigate to">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold"><Navigation size={14} className="text-cyan" />Navigate to
        <button type="button" onClick={onClose} aria-label="Close" className="ml-auto text-muted hover:text-ink"><X size={14} /></button></div>
      <div className="mb-1.5 flex flex-wrap gap-1">
        {all.map(t => {
          const on = types.has(t), { Icon, color, label } = MARKER_STYLE[t]
          return <button key={t} type="button" aria-pressed={on} title={label}
            onClick={() => setTypes(s => { const n = new Set(s); if (on) n.delete(t); else n.add(t); return n })}
            className={`flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] ${on ? 'border-line-2 text-ink' : 'border-line text-faint'}`}>
            <Icon size={10} style={{ color: on ? color : undefined }} />{label}
          </button>
        })}
      </div>
      <label className="mb-1.5 flex items-center gap-1.5 text-[10.5px] text-muted"><input type="checkbox" checked={nearest} onChange={e => setNearest(e.target.checked)} />Nearest of each type only</label>
      <ul className="scroll-thin min-h-0 flex-1 space-y-0.5 overflow-y-auto">
        <li><button type="button" onClick={onPickOnMap}
          className={`flex w-full items-center gap-1.5 rounded-lg px-1.5 py-1.5 text-left text-[11px] ${picking ? 'bg-mars/20 text-ink' : 'text-cyan hover:bg-white/5'}`}>
          <Crosshair size={12} />{picking ? 'Choosing on the map…' : 'Select on map (location, object or any point)'}</button></li>
        {items.map(i => {
          const { Icon, color, label } = MARKER_STYLE[i.type]
          return <li key={i.key}><button type="button" onClick={() => setChosen(i.key)}
            className={`flex w-full items-center gap-1.5 rounded-lg px-1.5 py-1 text-left text-[11px] ${chosen === i.key ? 'bg-mars/15' : 'hover:bg-white/5'}`}>
            <Icon size={12} style={{ color }} /><span className="min-w-0 flex-1 truncate">{i.name}</span>
            <span className="text-[9.5px] text-muted">{label}</span><span className="w-14 text-right font-mono text-[10px]">{fmtDist(i.d)}</span>
          </button></li>
        })}
        {items.length === 0 && <li className="px-1.5 py-1 text-[11px] text-muted">Nothing matches the filter.</li>}
      </ul>
      <button type="button" disabled={!item} onClick={() => item && onGo(item)} className="btn-primary mt-2 w-full !py-1.5 text-[11px]">
        <Navigation size={12} aria-hidden="true" />Navigate{item ? ` to ${item.name}` : ''}
      </button>
      {item?.kind === 'agent' && <p className="mt-1 text-[10px] leading-snug text-muted">Moving targets are intercepted: the course aims at where they will be, allowing for the terrain.</p>}
    </div>
  )
}

/* ------------------------------------------------------------------ joystick */
/** Touch joystick: drag the knob; up = forward, left = turn left. Sends (v, w) in -1..1 at 10 Hz. */
export function Joystick({ onDrive }: { onDrive: (v: number, w: number) => void }) {
  const [knob, setKnob] = useState({ x: 0, y: 0 })
  const active = useRef(false)
  const value = useRef({ v: 0, w: 0 })
  const base = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const t = setInterval(() => { if (active.current) onDrive(value.current.v, value.current.w) }, 100)
    return () => clearInterval(t)
  }, [onDrive])
  const R = 44
  const update = (e: RPointerEvent) => {
    const r = base.current!.getBoundingClientRect()
    let x = e.clientX - (r.left + r.width / 2), y = e.clientY - (r.top + r.height / 2)
    const m = Math.hypot(x, y)
    if (m > R) { x *= R / m; y *= R / m }
    setKnob({ x, y })
    value.current = { v: -y / R, w: -x / R }
  }
  const end = () => { active.current = false; setKnob({ x: 0, y: 0 }); value.current = { v: 0, w: 0 }; onDrive(0, 0) }
  return (
    <div ref={base} role="application" aria-label="Virtual joystick"
      onPointerDown={e => { active.current = true; (e.target as Element).setPointerCapture(e.pointerId); update(e) }}
      onPointerMove={e => { if (active.current) update(e) }} onPointerUp={end} onPointerCancel={end}
      className="glass pointer-events-auto relative h-[120px] w-[120px] touch-none select-none rounded-full">
      <span className="absolute left-1/2 top-1/2 h-12 w-12 rounded-full border border-white/40 bg-white/20 shadow-lg"
        style={{ transform: `translate(calc(-50% + ${knob.x}px), calc(-50% + ${knob.y}px))` }} />
    </div>
  )
}
