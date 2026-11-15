import { useEffect, useState } from 'react'
import { Boxes, Building2, Gem, Package, PersonStanding, Sprout, TriangleAlert, Truck, type LucideIcon } from 'lucide-react'
import { haversineM } from '../map/geo'
import { backendWs } from '../services/endpoints'

/* ------------------------------------------------------------------ marker types (map + legend) */
export const LOCATION_TYPES = ['HQ', 'Geological', 'Cargo', 'Supplies', 'Greenhouse', 'Attention'] as const
export type LocationType = typeof LOCATION_TYPES[number]
export type ObjectKind = 'rover' | 'astronaut'
export const OBJECT_KINDS: ObjectKind[] = ['rover', 'astronaut']
/** Every icon category the legend can show or hide. */
export type MarkerType = LocationType | ObjectKind

export const MARKER_STYLE: Record<MarkerType, { label: string; color: string; Icon: LucideIcon }> = {
  HQ: { label: 'HQ', color: '#f8fafc', Icon: Building2 },
  Geological: { label: 'Geological', color: '#a78bfa', Icon: Gem },
  Cargo: { label: 'Cargo', color: '#f59e0b', Icon: Package },
  Supplies: { label: 'Supplies', color: '#34d399', Icon: Boxes },
  Greenhouse: { label: 'Greenhouse', color: '#a3e635', Icon: Sprout },
  Attention: { label: 'Attention', color: '#f87171', Icon: TriangleAlert },
  rover: { label: 'Rover', color: '#5ee6ff', Icon: Truck },
  astronaut: { label: 'Astronaut', color: '#fbbf24', Icon: PersonStanding },
}

/* ------------------------------------------------------------------ user locations */
export interface MapLocation { id: string; name: string; type: LocationType; lon: number; lat: number; description?: string }

export function nextLocationName(type: LocationType, list: MapLocation[]): string {
  const used = list.filter(l => l.type === type).map(l => Number(l.name.slice(type.length + 1)) || 0)
  return `${type}_${(used.length ? Math.max(...used) : 0) + 1}`
}

const LOC_KEY = 'mo.locations'
export function loadLocations(): MapLocation[] {
  try {
    const v = JSON.parse(localStorage.getItem(LOC_KEY) ?? '[]')
    return Array.isArray(v) ? v.filter(l => l && LOCATION_TYPES.includes(l.type) && Number.isFinite(l.lon) && Number.isFinite(l.lat)) : []
  } catch { return [] }
}
export function saveLocations(list: MapLocation[]) {
  try { localStorage.setItem(LOC_KEY, JSON.stringify(list)) } catch { /* storage unavailable */ }
}

/* ------------------------------------------------------------------ live sim objects */
export interface WorldAgent {
  id: string; name: string; kind: ObjectKind; mode: string
  lon: number; lat: number; x: number; y: number; z: number
  yaw_deg: number; speed_m_s: number; cruise_m_s: number
  target: string | null; goal: { lon: number; lat: number } | null
  /** remaining mission path [lon, lat][] while travelling / intercepting */
  path?: [number, number][] | null
  /** rover battery / astronaut suit oxygen, percent */
  energy?: number
}

/** What each object type carries: battery for vehicles, oxygen for people. */
export const RESOURCE: Record<ObjectKind, { label: string; short: string }> = {
  rover: { label: 'Battery', short: '⚡' }, astronaut: { label: 'Oxygen', short: 'O₂' },
}
/** Green above 50 %, yellow from 30 to 50 %, red below 30 % (also the low-oxygen alert level). */
export const LOW_ENERGY_PCT = 30
export const energyColor = (p: number) => (p > 50 ? '#34d399' : p >= LOW_ENERGY_PCT ? '#facc15' : '#f87171')
export interface WorldState { mission: string; selected: string | null; running: boolean; sim_time: number; agents: WorldAgent[] }

export const MODE_LABEL: Record<string, string> = {
  route: 'following route', wander: 'wandering', goto: 'travelling', intercept: 'intercepting',
  manual: 'manual control', stopped: 'stopped (met)', idle: 'idle',
}

/** Distance (m) between two lon/lat points on Mars. */
export const distM = (a: { lon: number; lat: number }, b: { lon: number; lat: number }) => haversineM(a.lon, a.lat, b.lon, b.lat)

/** ENU yaw (deg, CCW from east) → compass bearing (deg, CW from north). */
export const bearingDeg = (yawDeg: number) => ((90 - yawDeg) % 360 + 360) % 360
const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
export const compass = (bearing: number) => COMPASS[Math.round(bearing / 45) % 8]

/**
 * Live world state of the running simulation: a WebSocket to the sim server, kept open while a
 * session exists (the server only steps while a client is connected, so this also keeps the
 * simulation running behind the background map after the 3D view is closed).
 */
export function useSimWorld(active: boolean, onEnded: () => void) {
  const [world, setWorld] = useState<WorldState | null>(null)
  useEffect(() => {
    if (!active) { setWorld(null); return }
    let stopped = false, socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    const connect = () => {
      socket = new WebSocket(backendWs('/sim/ws'))
      socket.onmessage = e => {
        if (typeof e.data !== 'string') return
        try {
          const msg = JSON.parse(e.data)
          if (msg.type === 'world') setWorld(msg as WorldState)
          else if (msg.type === 'mission_ended') onEnded()
        } catch { /* ignore malformed */ }
      }
      socket.onerror = () => socket?.close()
      socket.onclose = () => { if (!stopped) retry = setTimeout(connect, 1500) }
    }
    connect()
    return () => { stopped = true; clearTimeout(retry); socket?.close() }
  }, [active, onEnded])
  return world
}
