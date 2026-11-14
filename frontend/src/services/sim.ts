import type { LonLat, Route, RouteResponse } from '../types'
import type { WorldState } from '../sim/world'

export const SIM_BASE = '/sim/'
export interface SimNav {
  state: 'idle' | 'driving' | 'paused' | 'arrived'
  progress: number; distance_done_m: number; distance_remaining_m: number
  length_m: number; cross_track_m: number; cmd_v: number; cmd_w: number; cruise_m_s: number
}
export interface AvoidStatus {
  mode: 'track' | 'avoid' | 'recover' | 'blocked'
  detours: number; recoveries: number; obstacle_cells: number; blocked_ahead_m: number | null
  planner: string; choice: 'path' | 'turn' | 'reverse' | 'hold' | null
  arc?: [number, number][] | null
  rocks: number; craters: number; scans: number; scan_ms: number; points: number
}
export interface SimRock extends LonLat { id: string; x: number; y: number; radius: number; height: number; s: number; v: number[]; f: number[] }
export interface SimCrater extends LonLat { id: string; x: number; y: number; radius: number; depth: number; rim: number; s: number }
export interface SimTelemetry extends LonLat {
  type: 'telemetry'; yaw_deg: number; speed_m_s: number; nav: SimNav; warp: number
  model: 'wheeled' | 'kinematic'; sim_time: number; pitch_deg: number; roll_deg: number
  tilt_deg: number; elev_m: number; avoid: AvoidStatus | null
}
export interface SimControlsConfig {
  speeds_m_s: number[]; real_max_speed_m_s: number; default_speed_m_s: number
  warps: number[]; models: string[]
}
export type SimCommand = { type: 'start' | 'pause' | 'reset' }
  | { type: 'set_speed'; value: number; agent?: string }
  | { type: 'set_speed' | 'set_warp'; value: number }
  | { type: 'set_model'; value: 'wheeled' | 'kinematic' }
  | { type: 'end' }
  | { type: 'select'; agent: string; manual?: boolean }
  | { type: 'drive'; v: number; w: number }
  | { type: 'goto'; agent?: string; lon: number; lat: number; route?: string }
  | { type: 'intercept'; agent?: string; target: string }
export interface SimConfig { protocol: 'mo-sim/1'; controls: SimControlsConfig; rover?: { name: string } }
export interface SimMission {
  id: string
  kind?: 'world'
  world?: WorldState
  obstacles: { enabled: boolean; seed: number; count: number; rocks: SimRock[]; craters: SimCrater[] }
}
const UNREACHABLE = 'Sim server not reachable on :8100. Start it with `pixi run sim` (or `pixi run demo`).'
async function request<T>(path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(SIM_BASE + path, {
      ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(120000),
    })
  } catch { throw new Error(UNREACHABLE) }
  const text = await response.text()
  if (!text.trim() || response.status === 502 || response.status === 504) throw new Error(UNREACHABLE)
  let json
  try { json = JSON.parse(text) } catch { throw new Error(response.ok ? 'Invalid sim response.' : UNREACHABLE) }
  if (!response.ok || json.ok === false) throw new Error(json.detail ?? json.error ?? `Sim error ${response.status}`)
  return json as T
}
export const simConfig = () => request<SimConfig>('config')
const missionRoute = ({ id, label, color, length_m, coordinates }: Route) => ({ id, label, color, length_m, coordinates })
export function loadSimMission(response: RouteResponse, selectedId: string, obstacles: { enabled: boolean; seed?: number }, object?: 'rover' | 'astronaut') {
  const route = response.routes.find(r => r.id === selectedId)
  if (!route || route.coordinates.length < 2) throw new Error('Select a valid route first.')
  const [lon, lat] = route.coordinates[0]
  const [endLon, endLat] = route.coordinates[route.coordinates.length - 1]
  return request<SimMission>('api/mission', {
    route: missionRoute(route), alternatives: response.routes.filter(r => r.id !== selectedId).map(missionRoute),
    start: { lon, lat }, end: { lon: endLon, lat: endLat }, obstacles, object,
  })
}
/** World demo: several objects on the whole AOI (POST /sim/api/world). */
export const loadSimWorld = (agents: { kind: 'rover' | 'astronaut'; name: string }[], obstacles: { enabled: boolean; seed?: number }) =>
  request<SimMission>('api/world', { agents, obstacles })
export const simCommand = (command: SimCommand) => request<{ ok: boolean }>('api/command', command)
