import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Crosshair, Minus, Plus } from 'lucide-react'
import MarsMap, { mapRef, ROCK_COLORS, type MapLayerState } from './map/MarsMap'
import { LANDING_SITE } from './map/geo'
import { getPoint, loadManifest, loadSamples, loadTraverse, loadWaypoints, planRoute } from './services/api'
import type { FC, LayersManifest, LonLat, MapViewport, PointInfo, ProfilePoint, RouteResponse, SampleProps, Tab } from './types'
import ExplorerPanel from './components/ExplorerPanel'
import LayerPanel from './components/LayerPanel'
import SafetyPanel from './components/SafetyPanel'
import SciencePanel from './components/SciencePanel'
import MissionPanel from './components/MissionPanel'
import StatusBar from './components/StatusBar'
import IconRail from './components/IconRail'
import Briefing from './components/Briefing'
import MiniMap from './components/MiniMap'
import { Legend } from './components/ui'
import DemoControls from './components/DemoControls'
import LidarScreen from './components/LidarScreen'
import { loadSimMission, loadSimWorld, simConfig, simCommand, type SimCommand, type SimControlsConfig, type SimMission, type SimTelemetry } from './services/sim'
import LocationPanel from './components/LocationPanel'
import TargetPicker from './components/TargetPicker'
import { AgentPopup, Joystick, LocationPopup, MapLegend, NavigatePanel, ObjectSelector, OxygenAlerts } from './components/SimOverlays'
import {
  LOCATION_TYPES, LOW_ENERGY_PCT, MARKER_STYLE, OBJECT_KINDS, RESOURCE, energyColor, loadLocations, nextLocationName, saveLocations, useSimWorld,
  type LocationType, type MapLocation, type MarkerType, type ObjectKind,
} from './sim/world'
import { BatteryMedium, Gamepad2, Navigation, Power, Wind } from 'lucide-react'
import { useMedia } from './useMedia'
import { useConnections } from './useConnections'
import ConnectionStatus from './components/ConnectionStatus'
import PlaceCard, { type PlaceViewTab, type SelectedPlace } from './components/PlaceCard'
import OdysseyCopilot, { type CopilotAction } from './components/OdysseyCopilot'

// recharts is only needed once a route exists -> load it on demand (separate chunk)
const ElevationProfile = lazy(() => import('./components/ElevationProfile'))
const PlaceViewer = lazy(() => import('./components/PlaceViewer'))
const WeatherPanel = lazy(() => import('./components/WeatherPanel'))

/** Width of the expanded top-centre info box (title, sim status + speed, AOI, collapse), measured 570 px. */
const HUD_BOX_W = 572

const INITIAL_LAYERS: MapLayerState = {
  base: 'viking',
  trek: { ctx: true, hirise: true },
  raster: {},
  traverse: true,
  samples: true,
  waypoints: false,
}

const BRIEFING_KEY = 'mo.briefing.seen'
const seenBriefing = () => { try { return localStorage.getItem(BRIEFING_KEY) === '1' } catch { return false } }

export default function App() {
  const mobile = useMedia('(max-width: 767px)')
  const [tab, setTab] = useState<Tab>('explorer')
  const [panelOpen, setPanelOpen] = useState(true)
  // Bottom-centre title/status bar: collapsible, remembered per browser (a per-viewer convenience).
  const [hudOpen, setHudOpen] = useState<boolean>(() => {
    try { return localStorage.getItem('mo.hudOpen') !== '0' } catch { return true }
  })
  useEffect(() => {
    try { localStorage.setItem('mo.hudOpen', hudOpen ? '1' : '0') } catch { /* storage unavailable */ }
  }, [hudOpen])
  // Set when the user expands the box from the pill: it then shows even where the window is too
  // narrow for it to sit clear of the side panel and Copilot (it layers above them). Collapsing
  // clears it, so the automatic "pill when there is no room" behaviour returns.
  const [hudForced, setHudForced] = useState(false)
  // Responsive layout: the overlays size themselves with the window (clamp), and their positions
  // come from live measurements published as CSS variables, so the bottom-centre bar and the
  // elevation profile always centre in the real gap and the Copilot always stops above the dock:
  //   --mo-left-w  right edge of the left rail + panel + toggle (px from the left edge)
  //   --mo-dock-w  width of the bottom-right dock row (place card + legend/dock column)
  //   --mo-dock-h  height of the legend + map dock column (the legend grows with active layers)
  //   --mo-right-w width of the map dock panel itself: the legend and the top-right Copilot use
  //                it, so the three right-hand panels form one column of equal width. Unset while
  //                the dock is hidden, so the Copilot falls back to its own responsive width.
  const [hudRoom, setHudRoom] = useState<number>(Number.POSITIVE_INFINITY)
  const leftColRef = useRef<HTMLDivElement>(null)
  const rightRowRef = useRef<HTMLDivElement>(null)
  const rightDockRef = useRef<HTMLDivElement>(null)
  const dockPanelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const root = document.documentElement
    const left = leftColRef.current, row = rightRowRef.current, col = rightDockRef.current
    const panel = dockPanelRef.current
    if (!left || !row || !col || !panel) return
    const px = (v: number) => `${Math.round(v)}px`
    const publish = () => {
      root.style.setProperty('--mo-left-w', px(left.getBoundingClientRect().right))
      // Hidden dock (demo, place viewer, profile on small screens): no width to avoid; its corner
      // then holds the lidar/tracking panels or the viewer, ~520 px tall with their offsets.
      const hidden = row.offsetParent === null
      root.style.setProperty('--mo-dock-w', hidden ? '0px' : px(row.getBoundingClientRect().width))
      root.style.setProperty('--mo-dock-h', hidden ? '520px' : px(col.getBoundingClientRect().height))
      if (hidden) root.style.removeProperty('--mo-right-w')
      else root.style.setProperty('--mo-right-w', px(panel.getBoundingClientRect().width))
      // Room for the top-centre info box: twice the smaller distance from the screen centre to the
      // left panel's edge or to the Copilot's left edge (it matches the dock width, or its own
      // responsive width while the dock is hidden), minus a 16 px margin each side.
      const w = window.innerWidth, half = w / 2
      const copilotW = hidden ? Math.min(460, Math.max(320, 0.3 * w)) : panel.getBoundingClientRect().width
      const copilotLeft = w - 12 - copilotW
      setHudRoom(Math.round(2 * Math.min(half - left.getBoundingClientRect().right, copilotLeft - half) - 32))
    }
    publish()
    const observer = new ResizeObserver(publish)
    for (const el of [left, row, col, panel]) observer.observe(el)
    window.addEventListener('resize', publish)
    return () => { observer.disconnect(); window.removeEventListener('resize', publish) }
  }, [mobile])                         // these overlays exist only in the desktop layout
  const [briefing, setBriefing] = useState(!seenBriefing())
  const [layers, setLayersState] = useState<MapLayerState>(INITIAL_LAYERS)
  const setLayers = useCallback((fn: (s: MapLayerState) => MapLayerState) => setLayersState(fn), [])

  const [manifest, setManifest] = useState<LayersManifest | null>(null)
  const [samples, setSamples] = useState<FC<SampleProps> | null>(null)
  const [traverse, setTraverse] = useState<FC<{ sol: number; length_m: number }> | null>(null)
  const [waypoints, setWaypoints] = useState<Awaited<ReturnType<typeof loadWaypoints>> | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [cursor, setCursor] = useState<LonLat | null>(null)
  const [zoom, setZoom] = useState(13)
  const [viewport, setViewport] = useState<MapViewport | null>(null)
  const [info, setInfo] = useState<PointInfo | null>(null)
  const connections = useConnections()
  const apiOk = connections.planner === 'checking' ? null : connections.planner === 'online'

  const [place, setPlace] = useState<SelectedPlace | null>(null)
  const [placeView, setPlaceView] = useState<PlaceViewTab | null>(null)

  const [weatherSol, setWeatherSol] = useState<number | null>(null)
  const [selectedSample, setSelectedSample] = useState<number | null>(null)
  const [flyTo, setFlyTo] = useState<{ lon: number; lat: number; zoom: number; key: number } | null>(null)

  const [start, setStart] = useState<LonLat | null>(null)
  const [end, setEnd] = useState<LonLat | null>(null)
  const [pickMode, setPickMode] = useState<'start' | 'end' | null>(null)
  const [route, setRoute] = useState<RouteResponse | null>(null)
  const [selectedRoute, setSelectedRoute] = useState<string | null>(null)
  const [routeLoading, setRouteLoading] = useState(false)
  const [routeError, setRouteError] = useState<string | null>(null)
  const [hoverPoint, setHoverPoint] = useState<ProfilePoint | null>(null)
  const [showProfile, setShowProfile] = useState(true)

  const [demo, setDemo] = useState<SimMission | null>(null)
  const [demoLoading, setDemoLoading] = useState(false)
  const [demoError, setDemoError] = useState<string | null>(null)
  const [obstaclesOn, setObstaclesOn] = useState(false)
  const [controls, setControls] = useState<SimControlsConfig | null>(null)
  const [telemetry, setTelemetry] = useState<SimTelemetry | null>(null)
  const [speed, setSpeed] = useState(0.042)
  const [warp, setWarp] = useState(1)

  // ---- user locations (Location tab), legend toggles, and what the map is focused on
  const [locations, setLocations] = useState<MapLocation[]>(loadLocations)
  useEffect(() => saveLocations(locations), [locations])
  const [visibleTypes, setVisibleTypes] = useState<Record<MarkerType, boolean>>(
    () => Object.fromEntries([...LOCATION_TYPES, ...OBJECT_KINDS].map(t => [t, true])) as Record<MarkerType, boolean>)
  const toggleType = useCallback((t: MarkerType) => setVisibleTypes(v => ({ ...v, [t]: !v[t] })), [])
  const [placing, setPlacing] = useState<LocationType | null>(null)
  const [focus, setFocus] = useState<{ kind: 'location' | 'agent'; id: string } | null>(null)
  /** object armed by "Traverse here": the next map / location / object click gives it a target */
  const [traverseFor, setTraverseFor] = useState<string | null>(null)
  const iframe = useRef<HTMLIFrameElement>(null)
  const demoStarting = useRef(false)
  const command = useCallback(async (cmd: SimCommand) => {
    try {
      await simCommand(cmd)
      if (cmd.type === 'set_speed') setSpeed(cmd.value)
      if (cmd.type === 'set_warp') setWarp(cmd.value)
    } catch (error) { setDemoError(error instanceof Error ? error.message : String(error)) }
  }, [])

  // ---- simulation session: survives closing the 3D view; "End simulation" ends it.
  const [session, setSession] = useState<{ kind: 'path' | 'world'; mission: SimMission } | null>(null)
  const startedMissions = useRef(new Set<string>())   // missions already started (re-opening the 3D view must not restart)
  const [object, setObject] = useState<ObjectKind>('rover')
  const [roverName, setRoverName] = useState('Perseverance')
  const [worldObjects, setWorldObjects] = useState<{ kind: ObjectKind; name: string }[]>([
    { kind: 'rover', name: 'Rover_1' }, { kind: 'astronaut', name: 'Astronaut_1' }, { kind: 'astronaut', name: 'Astronaut_2' }])
  const [worldObstacles, setWorldObstacles] = useState(true)
  const [simCamera, setSimCamera] = useState<'chase' | 'orbit' | 'overview'>('chase')
  const [navOpen, setNavOpen] = useState(false)
  const [navPicking, setNavPicking] = useState(false)
  const [joystickOn, setJoystickOn] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)   // pop-up map for Navigate to → Select on map
  const [trackLock, setTrackLock] = useState(true)      // tracking minimap keeps the arrow centred
  const clearSession = useCallback(() => {
    setSession(null); setDemo(null); setTelemetry(null); setFocus(null); setTraverseFor(null); setNavOpen(false); setNavPicking(false)
  }, [])
  const world = useSimWorld(!!session, clearSession)
  const liveWorld = world && session && world.mission === session.mission.id ? world : null
  const agents = liveWorld?.agents ?? null
  const selectedAgent = agents?.find(a => a.id === liveWorld?.selected) ?? null
  useEffect(() => {
    if (connections.sim !== 'online') return
    simConfig().then(c => { setRoverName(c.rover?.name ?? 'Perseverance'); setControls(cur => cur ?? c.controls) }).catch(() => {})
  }, [connections.sim])

  const startDemo = async (kind: 'path' | 'world') => {
    if (demoStarting.current || connections.sim !== 'online' || (kind === 'path' && !route)) return
    demoStarting.current = true
    setDemoLoading(true); setDemoError(null)
    try {
      const seed = crypto.getRandomValues(new Uint32Array(1))[0]
      const [config, mission] = await Promise.all([simConfig(), kind === 'path'
        ? loadSimMission(route!, selectedRoute ?? route!.recommended, { enabled: obstaclesOn, seed }, object)
        : loadSimWorld(worldObjects, { enabled: worldObstacles, seed })])
      setControls(config.controls); setSpeed(config.controls.default_speed_m_s); setWarp(1)
      setPickMode(null); setHoverPoint(null); setTelemetry(null); setPlace(null); setPlaceView(null)
      setFocus(null); setTraverseFor(null); setNavOpen(false); setSimCamera(kind === 'world' ? 'overview' : 'chase')
      setSession({ kind, mission }); setDemo(mission)
      if (mobile) setPanelOpen(false)
    } catch (error) { setDemoError(error instanceof Error ? error.message : String(error)) }
    finally { setDemoLoading(false); demoStarting.current = false }
  }
  /** Exit simulation: back to the background map; the simulation keeps running there. */
  const exitDemo = () => {
    setDemo(null); setTelemetry(null); setNavOpen(false); setNavPicking(false); setJoystickOn(false)
    if (liveWorld?.selected) setFocus({ kind: 'agent', id: liveWorld.selected })   // centre + track + info panel
  }
  const reopenDemo = () => { if (session) { setSimCamera(session.kind === 'world' ? 'overview' : 'chase'); setDemo(session.mission) } }
  /** End simulation: stops everything on the server; the object icons leave the map. */
  const endSimulation = async () => {
    try { await simCommand({ type: 'end' }) } catch { /* server gone: end locally anyway */ }
    clearSession()
  }
  const runDemo = () => (demo && session?.kind === 'path' ? exitDemo() : startDemo('path'))
  const goto = useCallback((agent: string | undefined, p: { lon: number; lat: number }, route?: string) => command({ type: 'goto', agent, lon: p.lon, lat: p.lat, route }), [command])
  const intercept = useCallback((agent: string | undefined, target: string) => command({ type: 'intercept', agent, target }), [command])
  const postToSim = useCallback((msg: Record<string, unknown>) => {
    iframe.current?.contentWindow?.postMessage({ source: 'mo-hub', ...msg }, location.origin)
  }, [])
  // Locations for the 3D page's ground guide arrows (sent on change and when the page is ready).
  const sendLocations = useRef(() => {})
  sendLocations.current = () => postToSim({ type: 'locations', list: locations.map(({ name, type, lon, lat }) => ({ name, type, lon, lat })) })
  useEffect(() => { if (demo) sendLocations.current() }, [demo, locations])
  const drive = useCallback((v: number, w: number) => postToSim({ type: 'command', command: { type: 'drive', v, w } }), [postToSim])
  // WASD while the planner (not the 3D frame) has keyboard focus, in the chase / orbit views.
  useEffect(() => {
    if (!demo || session?.kind !== 'world' || simCamera === 'overview') return   // no manual control in the path demo
    const keys = new Set<string>()
    const MAP: Record<string, string> = { KeyW: 'f', ArrowUp: 'f', KeyS: 'b', ArrowDown: 'b', KeyA: 'l', ArrowLeft: 'l', KeyD: 'r', ArrowRight: 'r' }
    let timer: ReturnType<typeof setInterval> | undefined
    const send = () => drive((keys.has('f') ? 1 : 0) - (keys.has('b') ? 1 : 0), (keys.has('l') ? 1 : 0) - (keys.has('r') ? 1 : 0))
    const on = (e: KeyboardEvent, down: boolean) => {
      const k = MAP[e.code]
      if (!k || (e.target instanceof HTMLElement && e.target.closest('input, select, textarea'))) return
      if (down && e.repeat) return
      e.preventDefault()
      if (down) keys.add(k); else keys.delete(k)
      send(); clearInterval(timer)
      if (keys.size) timer = setInterval(send, 100)
    }
    const kd = (e: KeyboardEvent) => on(e, true), ku = (e: KeyboardEvent) => on(e, false)
    window.addEventListener('keydown', kd); window.addEventListener('keyup', ku)
    return () => { window.removeEventListener('keydown', kd); window.removeEventListener('keyup', ku); clearInterval(timer) }
  }, [demo, session?.kind, simCamera, drive])

  useEffect(() => {
    if (!demo || !controls) return
    let active = true, initialized = startedMissions.current.has(demo.id)
    const receive = async (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== iframe.current?.contentWindow || event.data?.source !== 'mo-sim') return
      const data = event.data
      if (data.type === 'camera') setSimCamera(data.mode)
      if (data.type === 'ready') sendLocations.current()     // the 3D page's ground arrows point at these
      if (data.type === 'ready' && data.mission === demo.id && !initialized) {
        initialized = true
        startedMissions.current.add(demo.id)
        try {
          await simCommand({ type: 'set_speed', value: controls.default_speed_m_s })
          if (!active) return
          await simCommand({ type: 'set_warp', value: 1 })
          if (!active) return
          await simCommand({ type: 'start' })
        } catch (error) { if (active) setDemoError(String(error)) }
      } else if (data.type === 'telemetry') setTelemetry(data as SimTelemetry)
      else if (data.type === 'error') setDemoError(String(data.message))
    }
    window.addEventListener('message', receive)
    return () => { active = false; window.removeEventListener('message', receive) }
  }, [demo, controls])

  useEffect(() => {
    Promise.all([loadManifest(), loadSamples(), loadTraverse()])
      .then(([m, s, t]) => { setManifest(m); setSamples(s); setTraverse(t) })
      .catch((e) => setLoadError(String(e.message ?? e)))
    loadWaypoints().then(setWaypoints).catch(() => {})
  }, [])

  // Debounced terrain readout under the cursor.
  const ctl = useRef<AbortController | null>(null)
  useEffect(() => {
    ctl.current?.abort()
    if (!cursor || !apiOk) { setInfo(null); return }
    const t = setTimeout(() => {
      ctl.current?.abort()
      ctl.current = new AbortController()
      getPoint(cursor, ctl.current.signal).then(setInfo).catch(() => {})
    }, 120)
    return () => { clearTimeout(t); ctl.current?.abort() }
  }, [cursor, apiOk])

  const onCursor = useCallback((p: LonLat | null, z: number) => { setCursor(p); setZoom(z) }, [])
  const fly = useCallback((lon: number, lat: number, z = 16) => setFlyTo({ lon, lat, zoom: z, key: Date.now() }), [])

  const onMapClick = useCallback((p: LonLat) => {
    if (demo && navPicking) { goto(undefined, p); setNavPicking(false); setNavOpen(false); return }   // "Select on map"
    if (demo || demoLoading) return
    if (placing) {
      setLocations(list => {
        const loc = { id: crypto.randomUUID(), name: nextLocationName(placing, list), type: placing, lon: p.lon, lat: p.lat }
        setFocus({ kind: 'location', id: loc.id })
        return [...list, loc]
      })
      setPlacing(null); return
    }
    if (traverseFor) { goto(traverseFor, p); setFocus({ kind: 'agent', id: traverseFor }); setTraverseFor(null); return }
    if (pickMode === 'start') { setStart(p); setPickMode(end ? null : 'end'); setRoute(null); setPlace(null); setPlaceView(null) }
    else if (pickMode === 'end') { setEnd(p); setPickMode(null); setRoute(null); setPlace(null); setPlaceView(null) }
    else if (focus) setFocus(null)                    // a plain click closes the object / location popup
    else if (!mobile) setPlace(p)
  }, [pickMode, end, demo, demoLoading, mobile, navPicking, placing, traverseFor, focus, goto])

  const moveLocation = useCallback((id: string, pt: Partial<Pick<MapLocation, 'lon' | 'lat' | 'description'>>) => setLocations(list => list.map(l => (l.id === id ? { ...l, ...pt } : l))), [])
  const focusLocation = useCallback((id: string) => {
    const loc = locations.find(l => l.id === id)
    if (!loc) return
    if (traverseFor && !demo) { goto(traverseFor, loc, 'cautious'); setTraverseFor(null); setFocus({ kind: 'agent', id: traverseFor }); return }   // locations: cautious route
    setFocus({ kind: 'location', id }); setPlace(null); setPlaceView(null)
    fly(loc.lon, loc.lat, Math.max(zoom, 15))
  }, [locations, traverseFor, demo, goto, fly, zoom])
  const focusAgent = useCallback((id: string) => {
    if (traverseFor && !demo && id !== traverseFor) { intercept(traverseFor, id); setFocus({ kind: 'agent', id: traverseFor }); setTraverseFor(null); return }
    setFocus({ kind: 'agent', id }); setPlace(null); setPlaceView(null)
  }, [traverseFor, demo, intercept])
  const selectAgent = useCallback((id: string) => { command({ type: 'select', agent: id }); setFocus({ kind: 'agent', id }) }, [command])
  // A newly selected object is centred, tracked and its info panel opens.
  const lastSelected = useRef<string | null>(null)
  useEffect(() => {
    const id = liveWorld?.selected ?? null
    if (id && id !== lastSelected.current && !demo) setFocus({ kind: 'agent', id })
    lastSelected.current = id
  }, [liveWorld?.selected, demo])
  // Low-oxygen alerts: an astronaut under 30 % stays listed until a rover is sent to them; the
  // record clears when their oxygen is back at 30 % or more (or the person leaves the simulation).
  const [rescues, setRescues] = useState<Record<string, string>>({})   // astronaut id -> rover id sent
  useEffect(() => {
    setRescues(r => {
      const keep = Object.fromEntries(Object.entries(r).filter(([id]) => (agents?.find(a => a.id === id)?.energy ?? 100) < LOW_ENERGY_PCT))
      return Object.keys(keep).length === Object.keys(r).length ? r : keep
    })
  }, [agents])
  const lowOxygen = agents?.filter(a => a.kind === 'astronaut' && (a.energy ?? 100) < LOW_ENERGY_PCT && !rescues[a.id]) ?? []
  const sendRover = useCallback(async (roverId: string, personId: string) => {
    const top = Math.max(...(controls?.speeds_m_s ?? [2]))
    await command({ type: 'set_speed', value: top, agent: roverId })
    await command({ type: 'intercept', agent: roverId, target: personId })
    setRescues(r => ({ ...r, [personId]: roverId }))
  }, [command, controls])
  const focusedAgent = focus?.kind === 'agent' ? agents?.find(a => a.id === focus.id) ?? null : null
  const focusedLocation = focus?.kind === 'location' ? locations.find(l => l.id === focus.id) ?? null : null

  const plan = useCallback(async (s = start, e = end) => {
    if (!s || !e || connections.planner !== 'online' || routeLoading || demo || demoLoading) return
    setRouteLoading(true); setRouteError(null)
    try {
      const r = await planRoute(s, e)
      setRoute(r); setSelectedRoute(null); setShowProfile(true)
      if (mobile) setPanelOpen(false)  // reveal the route + profile on small screens
    } catch (err) {
      setRouteError(err instanceof Error ? err.message : String(err)); setRoute(null)
    } finally { setRouteLoading(false) }
  }, [start, end, mobile, connections.planner, routeLoading, demo, demoLoading])

  const handleCopilotAction = useCallback((action: CopilotAction) => {
    const { action_type, payload } = action
    if (action_type === 'PLAN_ROUTE') {
      const s = payload.start
        ? { lon: Number(payload.start.lon), lat: Number(payload.start.lat) }
        : (start ?? { lon: LANDING_SITE.lon, lat: LANDING_SITE.lat })
      const e = { lon: Number(payload.end.lon), lat: Number(payload.end.lat) }
      setStart(s)
      setEnd(e)
      setRoute(null)
      setRouteError(null)
      setTab('mission')
      setPanelOpen(true)
      plan(s, e)
      fly(e.lon, e.lat, 15)
    } else if (action_type === 'FOCUS_SAMPLE') {
      const sampleNo = payload.sample_no != null ? Number(payload.sample_no) : null
      const lon = Number(payload.lon ?? 77.3052)
      const lat = Number(payload.lat ?? 18.4975)
      const name = payload.name ? String(payload.name) : (sampleNo ? `Sample #${sampleNo}` : 'Sample')
      if (sampleNo != null) {
        setSelectedSample(sampleNo)
      }
      setTab('science')
      setPanelOpen(true)
      fly(lon, lat, 16)
      setPlace({ lon, lat, name })
    } else if (action_type === 'INSPECT_TERRAIN') {
      const lon = Number(payload.lon ?? cursor?.lon ?? LANDING_SITE.lon)
      const lat = Number(payload.lat ?? cursor?.lat ?? LANDING_SITE.lat)
      fly(lon, lat, 16)
      setPlace({ lon, lat, name: payload.name ? String(payload.name) : 'Terrain Inspection' })
    } else if (action_type === 'TOGGLE_LAYER') {
      const layerId = String(payload.layer_id || payload.layer || '').toLowerCase()
      const desiredState = typeof payload.state === 'boolean' ? payload.state : undefined
      setLayers((prev) => {
        if (layerId === 'hirise') {
          const nextVal = desiredState ?? !prev.trek.hirise
          return { ...prev, trek: { ...prev.trek, hirise: nextVal } }
        }
        if (layerId === 'samples' || layerId === 'traverse' || layerId === 'waypoints') {
          const key = layerId as 'samples' | 'traverse' | 'waypoints'
          const nextVal = desiredState ?? !prev[key]
          return { ...prev, [key]: nextVal }
        }
        if (layerId) {
          const currentOn = !!prev.raster[layerId]?.on
          const nextVal = desiredState ?? !currentOn
          return {
            ...prev,
            raster: {
              ...prev.raster,
              [layerId]: {
                on: nextVal,
                opacity: prev.raster[layerId]?.opacity ?? 0.7,
              },
            },
          }
        }
        return prev
      })
      if (payload.lon != null && payload.lat != null) {
        fly(Number(payload.lon), Number(payload.lat), 15)
      }
    } else if (action_type === 'SHOW_WEATHER') {
      // Open the Weather tab at the sol the Copilot answered about (same path as a waypoint click).
      const sol = Number(payload.sol)
      if (Number.isInteger(sol)) setWeatherSol(sol)
      setTab('weather')
      setPanelOpen(true)
    } else if (action_type === 'SHOW_SPACE_WEATHER') {
      setTab('safety')
      setPanelOpen(true)
    }
  }, [cursor, fly, plan, setLayers, start])

  const selectTab = (t: Tab) => {
    if (t === tab) setPanelOpen((o) => !o)
    else { setTab(t); setPanelOpen(true) }
  }
  const setTarget = (lon: number, lat: number) => {
    if (demo || demoLoading) return
    setPlace(null); setPlaceView(null)
    setEnd({ lon, lat }); setRoute(null); setRouteError(null); setTab('mission'); setPanelOpen(true)
    if (!start) setStart({ lon: LANDING_SITE.lon, lat: LANDING_SITE.lat })
  }
  const preset = () => {
    const s = { lon: LANDING_SITE.lon, lat: LANDING_SITE.lat }
    const f = samples?.features.find((x) => x.properties.sample_no === 25)
    const [lon, lat] = (f?.geometry.coordinates as [number, number]) ?? [77.3052, 18.4975]
    const e = { lon, lat }
    setStart(s); setEnd(e); plan(s, e)
  }
  const closeBriefing = () => {
    setBriefing(false)
    try { localStorage.setItem(BRIEFING_KEY, '1') } catch { /* private mode */ }
  }

  // Show the hazard overlay automatically when entering the Safety tab.
  useEffect(() => {
    if (tab === 'safety' && manifest) setLayers((s) => ({ ...s, raster: { ...s.raster, hazard: { on: true, opacity: s.raster.hazard?.opacity ?? 0.7 } } }))
  }, [tab, manifest, setLayers])

  const selRoute = route?.routes.find((r) => r.id === (selectedRoute ?? route.recommended)) ?? null
  const activeRasters = manifest?.raster_layers.filter((r) => layers.raster[r.id]?.on) ?? []
  const panelContent = (
    <>
      {loadError && <div className="m-3 rounded-lg border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-200">Data failed to load: {loadError}. Run <code>pixi run data</code>.</div>}
      {tab === 'explorer' && <ExplorerPanel manifest={manifest} onFly={(lon, lat, z) => { fly(lon, lat, z); if (mobile) setPanelOpen(false) }} />}
      {tab === 'location' && <LocationPanel locations={locations} visible={visibleTypes} onToggle={toggleType}
        highlight={focus?.kind === 'location' ? focus.id : null} placing={placing}
        onPlace={t => { setPlacing(t); setTraverseFor(null); if (t && mobile) setPanelOpen(false) }}
        onFocus={focusLocation} selected={selectedAgent}
        onMove={(id, pt) => setLocations(list => list.map(l => (l.id === id ? { ...l, ...pt } : l)))}
        onDelete={id => { setLocations(list => list.filter(l => l.id !== id)); if (focus?.id === id) setFocus(null) }} />}
      {tab === 'layers' && <LayerPanel manifest={manifest} layers={layers} setLayers={setLayers} detailed />}
      {tab === 'safety' && <SafetyPanel manifest={manifest} />}
      {tab === 'weather' && <Suspense fallback={<div className="p-5 text-sm text-muted">Loading weather…</div>}><WeatherPanel requestedSol={weatherSol} onSelectSol={setWeatherSol} onFly={point => fly(point.lon, point.lat, 16)} /></Suspense>}
      {tab === 'science' && (
        <SciencePanel samples={samples} selected={selectedSample} onSelect={setSelectedSample}
          onFly={(lon, lat) => fly(lon, lat, mobile ? 16 : 17)} onSetTarget={setTarget} />
      )}
      {tab === 'mission' && (
        <MissionPanel plannerState={connections.planner} simState={connections.sim} demoActive={!!demo} demoLoading={demoLoading} obstaclesOn={obstaclesOn} onObstacles={setObstaclesOn}
          onDemo={runDemo} demoError={demoError}
          demoControls={<DemoControls active={!!demo} config={controls} telemetry={telemetry} speed={session?.kind === 'world' ? (selectedAgent?.cruise_m_s ?? speed) : speed} warp={warp} onCommand={command} />}
          start={start} end={end} pickMode={pickMode}
          setPickMode={(m) => { setPickMode(m); if (m && mobile) setPanelOpen(false) }}
          onPlan={() => plan()} onReset={() => { setStart(null); setEnd(null); setRoute(null); setRouteError(null); setPickMode(null) }}
          onPreset={preset} loading={routeLoading} error={routeError}
          route={route} selected={selectedRoute} onSelect={setSelectedRoute}
          object={object} onObject={setObject} roverName={roverName} session={session?.kind ?? null} onReopen={reopenDemo}
          worldObjects={worldObjects}
          onWorldAdd={k => setWorldObjects(list => {
            const n = Math.max(0, ...list.filter(o => o.kind === k).map(o => Number(o.name.split('_')[1]) || 0)) + 1
            return [...list, { kind: k, name: `${k === 'rover' ? 'Rover' : 'Astronaut'}_${n}` }]
          })}
          onWorldRemove={i => setWorldObjects(list => list.filter((_, j) => j !== i))}
          worldObstacles={worldObstacles} onWorldObstacles={setWorldObstacles}
          onWorldDemo={() => (demo && session?.kind === 'world' ? exitDemo() : startDemo('world'))} />
      )}
    </>
  )

  const profile = !demo && !place && !placeView && selRoute && showProfile && tab === 'mission' && (
    <Suspense fallback={null}>
      <ElevationProfile route={selRoute} compact={mobile} onHover={setHoverPoint}
        onClose={() => { setShowProfile(false); setHoverPoint(null) }} />
    </Suspense>
  )

  const armedName = traverseFor ? agents?.find(a => a.id === traverseFor)?.name : null
  const pickBanner = !demo && (pickMode || placing || traverseFor) && (
    <div className="pointer-events-none absolute bottom-[200px] left-1/2 z-[1100] w-max max-w-[92vw] -translate-x-1/2">
      <div className="glass fade-in pointer-events-auto flex items-center gap-2 rounded-full border-mars/60 px-4 py-2 text-sm">
        <span className={`h-2 w-2 animate-pulse rounded-full ${pickMode === 'start' ? 'bg-green-400' : 'bg-mars'}`} />
        {placing ? <>{mobile ? 'Tap' : 'Click'} the map to add a <b>{placing}</b> location</>
          : traverseFor ? <>{mobile ? 'Tap' : 'Click'} the map or a location for <b>{armedName}</b> to travel to, or an object to intercept</>
          : <>{mobile ? 'Tap' : 'Click'} the map to place the <b>{pickMode === 'start' ? 'START' : 'TARGET'}</b> point</>}
        {(placing || traverseFor) && <button type="button" onClick={() => { setPlacing(null); setTraverseFor(null) }} className="ml-1 text-xs text-muted hover:text-ink">Cancel</button>}
      </div>
    </div>
  )

  const map = (
    <div className={demo ? 'mo-tracking glass absolute right-3 bottom-[76px] z-[1000] w-[200px] overflow-hidden rounded-lg md:bottom-[60px]' : 'absolute inset-0'}>
      <div className={demo ? 'flex h-7 items-center justify-between px-2 text-[10px]' : 'hidden'}><span>Tracking · {session?.kind === 'world' ? selectedAgent?.name : selRoute?.label}{navPicking ? ' · click a target' : ''}</span><span className="flex items-center gap-1.5">{session?.kind === 'world' ? '' : `${((telemetry?.nav.progress ?? 0) * 100).toFixed(0)}%`}<button type="button" onClick={() => setTrackLock(v => !v)} aria-pressed={trackLock} title={trackLock ? 'Centred on the object (click to free the map)' : 'Track and centre the object'} className={`grid h-5 w-5 place-items-center rounded ${trackLock ? 'bg-cyan/20 text-cyan' : 'text-muted hover:text-ink'}`}><Crosshair size={12} /></button></span></div>
      <div className={demo ? 'h-[110px]' : 'h-full'}>
    <MarsMap place={place} showPlaces={!mobile && !demo && !pickMode}
      onPanoramaClick={(point) => { setPickMode(null); setPlace(point); setPlaceView('panorama') }}
      tracking={!!demo} rover={telemetry} manifest={manifest} layers={layers} samples={samples} traverse={traverse} waypoints={waypoints}
      route={route} selectedRoute={selectedRoute} start={start} end={end}
      pickMode={pickMode ?? (placing || traverseFor || navPicking ? 'end' : null)}
      locations={locations} agents={agents} visibleTypes={visibleTypes} highlight={focus} selectedAgent={liveWorld?.selected ?? null}
      onLocationClick={focusLocation} onAgentClick={focusAgent}
      onLocationMove={moveLocation}
      follow={focusedAgent && !traverseFor ? { lon: focusedAgent.lon, lat: focusedAgent.lat } : null}   // free map while choosing
      trackLock={trackLock}
      onWaypointClick={sol => { if (demo || demoLoading) return; setWeatherSol(sol); setTab('weather'); setPanelOpen(true); setPlace(null); setPlaceView(null) }}
      onViewportChange={setViewport} selectedSample={selectedSample} hoverPoint={hoverPoint} flyTo={flyTo} onCursor={onCursor} onMapClick={onMapClick}
      crisp={zoom >= 16}
      fitPadding={mobile ? [[24, 80], [24, 230]] : [[panelOpen ? 470 : 100, 90], [260, 230]]}
      onSampleClick={(no) => { if (!demo) { setSelectedSample(no); setTab('science'); setPanelOpen(true); const sample = samples?.features.find(f => f.properties.sample_no === no); if (sample && !mobile) { const [lon, lat] = sample.geometry.coordinates as [number, number]; setPlace({ lon, lat, name: sample.properties.name }) } } }} />
      </div>
    </div>
  )

  // Info box: one light for the simulation engine (running or not), plus "End simulation".
  const simStatus = (
    <div className="mt-2 flex max-w-[360px] flex-wrap items-center gap-x-2 gap-y-1 text-[10.5px]" role="status" aria-live="polite">
      <span className="flex items-center gap-1.5" title={`Simulation ${session ? 'running' : 'not running'} · 3D server ${connections.sim}`}>
        <i aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${session ? 'bg-emerald-400 shadow-[0_0_8px_#34d399]' : connections.sim === 'checking' ? 'animate-pulse bg-amber-300' : 'bg-faint'}`} />
        Simulation · {session ? `running (${session.kind === 'world' ? 'world demo' : 'path traverse'})` : 'not running'}
      </span>
      {session && <button type="button" onClick={endSimulation} title="Stop the simulation completely; the object icons leave the map"
        className="flex items-center gap-1 rounded-full border border-red-400/40 px-2 py-0.5 text-red-200 hover:bg-red-500/15">
        <Power size={11} aria-hidden="true" />End simulation</button>}
      {session && controls && (
        <label className="flex items-center gap-1 text-muted" title="Simulation speed (time warp)">Sim speed
          <select aria-label="Simulation speed" value={warp} onChange={e => command({ type: 'set_warp', value: Number(e.target.value) })} style={{ colorScheme: 'dark' }}
            className="rounded-full border border-line-2 bg-panel-2 px-1.5 py-0.5 text-ink">
            {controls.warps.map(v => <option key={v} value={v} className="bg-panel-2">{v}×</option>)}
          </select>
        </label>
      )}
    </div>
  )
  void ConnectionStatus  // the per-service chips moved to the Mission tab's status tags

  const demoOverlay = demo && session && (
    <>
      {/* Object choice: bottom centre, just above the status bar. Dark native list (colour-scheme). */}
      {session.kind === 'world' && agents && (
        <div className="pointer-events-none fixed bottom-[60px] left-1/2 z-[1350] -translate-x-1/2">
          <select aria-label="Ride along with an object" value="" style={{ colorScheme: 'dark' }}
            onChange={e => { const id = e.target.value; if (id) { postToSim({ type: 'camera', mode: 'chase', agent: id }); setSimCamera('chase') } }}
            className="pointer-events-auto rounded-lg border border-line-2 bg-panel-2 px-3 py-1.5 text-xs text-ink shadow-lg">
            <option value="" className="bg-panel-2 text-ink">{simCamera === 'overview' ? 'Become an object…' : `Switch object · ${selectedAgent?.name ?? ''}`}</option>
            {agents.map(a => <option key={a.id} value={a.id} className="bg-panel-2 text-ink">{a.name} · {MARKER_STYLE[a.kind].label}</option>)}
          </select>
        </div>
      )}
      {/* Chase / orbit: the ridden object's battery (vehicle) or suit oxygen (astronaut), top-right. */}
      {simCamera !== 'overview' && selectedAgent?.energy != null && (() => {
        const pct = selectedAgent.energy, c = energyColor(pct), Icon = selectedAgent.kind === 'rover' ? BatteryMedium : Wind
        return <div className="glass absolute right-3 top-3 z-[1000] w-[200px] rounded-xl px-3 py-2" aria-label={`${RESOURCE[selectedAgent.kind].label} ${pct.toFixed(0)} percent`}>
          <div className="flex items-center gap-2 text-[11px]">
            <Icon size={16} style={{ color: c }} aria-hidden="true" />
            <span className="text-muted">{RESOURCE[selectedAgent.kind].label}</span>
            <span className="ml-auto font-mono text-sm font-semibold" style={{ color: c }}>{pct.toFixed(1)}%</span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full transition-[width]" style={{ width: `${pct}%`, background: c }} /></div>
          <div className="mt-1 truncate text-[10px] text-faint">{selectedAgent.name}</div>
        </div>
      })()}
      {simCamera !== 'overview' && selectedAgent && (
        <div className="absolute bottom-[60px] right-[220px] z-[1000] flex flex-col items-end gap-2">
          {navOpen && <NavigatePanel me={selectedAgent} agents={agents ?? []} locations={locations} picking={pickerOpen}
            onPickOnMap={() => setPickerOpen(true)} onClose={() => { setNavOpen(false); setNavPicking(false) }}
            onGo={item => { if (item.kind === 'agent') intercept(undefined, item.id); else goto(undefined, item, 'cautious'); setNavOpen(false); setNavPicking(false) }} />}
          <div className="flex gap-2">
            {session.kind === 'world' && <button type="button" onClick={() => setJoystickOn(v => !v)} aria-pressed={joystickOn} title="Touch controls (virtual joystick)"
              className={`glass pointer-events-auto grid h-9 w-9 place-items-center rounded-lg ${joystickOn ? 'text-mars' : 'text-muted hover:text-ink'}`}><Gamepad2 size={16} /></button>}
            <button type="button" onClick={() => { setNavOpen(v => !v); setNavPicking(false) }} aria-expanded={navOpen}
              className="glass pointer-events-auto flex h-9 items-center gap-1.5 rounded-lg px-3 text-xs font-medium hover:text-cyan">
              <Navigation size={14} aria-hidden="true" />Navigate to</button>
          </div>
        </div>
      )}
      {pickerOpen && selectedAgent && (
        <TargetPicker me={selectedAgent} agents={agents ?? []} locations={locations} onCancel={() => setPickerOpen(false)}
          onConfirm={t => {
            if (t.kind === 'agent') intercept(undefined, t.id)
            else goto(undefined, t, t.kind === 'location' ? 'cautious' : undefined)
            setPickerOpen(false); setNavOpen(false)
          }} />
      )}
      {session.kind === 'world' && simCamera !== 'overview' && joystickOn && (
        <div className="absolute bottom-[72px] left-[calc(var(--mo-left-w,92px)+24px)] z-[1100]"><Joystick onDrive={drive} /></div>
      )}
      {session.kind === 'world' && simCamera !== 'overview' && <p className="pointer-events-none absolute bottom-[100px] left-1/2 z-[900] -translate-x-1/2 rounded-full bg-black/40 px-3 py-1 text-[10.5px] text-white/80">
        W A S D / arrows to drive{selectedAgent?.mode === 'manual' ? '' : ' · autonomous: ' + (selectedAgent?.mode ?? '')}</p>}
    </>
  )

  const objectPopup = !demo && !traverseFor && (focusedAgent && agents ? (
    <AgentPopup agent={focusedAgent} agents={agents} locations={locations} speeds={controls?.speeds_m_s ?? []}
      onSpeed={v => command({ type: 'set_speed', value: v, agent: focusedAgent.id })}
      onEnergy={v => command({ type: 'set_energy', agent: focusedAgent.id, value: v })} isSelected={focusedAgent.id === liveWorld?.selected} arming={traverseFor === focusedAgent.id}
      onSelect={() => selectAgent(focusedAgent.id)} onClose={() => { setFocus(null); setTraverseFor(null) }}
      onTraverse={() => setTraverseFor(traverseFor === focusedAgent.id ? null : focusedAgent.id)} />
  ) : focusedLocation ? (
    <LocationPopup loc={focusedLocation} selected={selectedAgent} canTravel={!!liveWorld}
      onTravel={() => { goto(undefined, focusedLocation, 'cautious') }} onClose={() => setFocus(null)} />
  ) : null)

  const demoBackground = demo && <iframe ref={iframe} key={demo.id} title="Mars 3D simulation" src={`/sim/?embed=1&m=${encodeURIComponent(demo.id)}`} className="absolute inset-0 h-full w-full border-0" />
  // Right column above the tracking minimap: the 3D view selector (moved out of the 3D page) and the lidar.
  const setView = (mode: 'chase' | 'orbit' | 'overview') => { postToSim({ type: 'camera', mode }); setSimCamera(mode) }
  const lidar = demo && (
    <div className="absolute right-3 bottom-[222px] z-[1000] flex w-[200px] flex-col gap-2 md:bottom-[206px]">
      <div role="group" aria-label="3D view" className="glass flex gap-0.5 rounded-lg p-0.5 text-[10.5px]">
        {(['chase', 'orbit', 'overview'] as const).map(m => (
          <button key={m} type="button" aria-pressed={simCamera === m} onClick={() => setView(m)}
            className={`flex-1 rounded-md py-1 transition-colors ${simCamera === m ? 'bg-mars/20 text-ink' : 'text-muted hover:bg-white/5 hover:text-ink'}`}>
            {m === 'overview' ? 'Whole AOI' : m === 'chase' ? 'Chase' : 'Orbit'}
          </button>
        ))}
      </div>
      {demo.obstacles.enabled && !(session?.kind === 'world' && simCamera === 'overview') && <LidarScreen />}
    </div>
  )

  /* ===================== phone layout (< 768 px) ===================== */
  if (mobile) {
    return (
      <div className="relative h-full w-full overflow-hidden">
        {demoBackground}
        {map}
        {lidar}
        {demoOverlay}
        {objectPopup}
        <div className="pointer-events-none absolute left-2 right-2 top-2 z-[1000] flex items-start justify-between gap-2">
          <div className={`glass hud-corners pointer-events-auto rounded-xl px-3 py-2 ${demo ? 'hidden' : ''}`}>
            <div className="display text-[13px] font-bold tracking-[0.2em]">MARS ODYSSEY</div>
            <div className="label !text-[8.5px]">Jezero · EVA planner</div>
            {simStatus}
          </div>
          {info?.inside && (
            <div className="glass pointer-events-auto rounded-xl px-3 py-1.5 font-mono text-[10.5px] leading-snug">
              <div>Z {info.elevation_m?.toFixed(0)} m · ∠ {info.slope_deg?.toFixed(1)}°</div>
              <div className="text-muted">Hazard {info.hazard?.toFixed(0)}</div>
            </div>
          )}
        </div>
        {pickBanner}

        {panelOpen ? (
          <aside key={tab} className="glass hud-corners fade-in scroll-thin absolute inset-x-2 bottom-[72px] z-[1000] max-h-[56vh] overflow-y-auto rounded-2xl">
            <div className="sticky top-0 z-10 flex justify-center bg-gradient-to-b from-panel to-transparent pb-1 pt-2">
              <button onClick={() => setPanelOpen(false)} aria-label="Close panel" className="h-1.5 w-12 rounded-full bg-line-2" />
            </div>
            {panelContent}
          </aside>
        ) : (
          profile && <div className="absolute inset-x-2 bottom-[72px] z-[1000]">{profile}</div>
        )}

        <div className="absolute inset-x-2 bottom-2 z-[1000]">
          <IconRail orientation="horizontal" tab={tab} open={panelOpen} onSelect={selectTab} onBriefing={() => setBriefing(true)} />
        </div>
        {briefing && <Briefing manifest={manifest} nSamples={samples?.features.length ?? 0} onEnter={closeBriefing} />}
      </div>
    )
  }

  const placeCard = place && !placeView && !demo && !pickMode && <aside aria-label="Selected location" className="mo-place-card glass pointer-events-auto w-[286px] shrink-0 rounded-2xl shadow-2xl">
    <PlaceCard point={place} plannerOnline={apiOk === true} onView={setPlaceView}
      onStart={() => { setStart({ lon: place.lon, lat: place.lat }); setRoute(null); setRouteError(null); setPickMode(null); setPlace(null); setTab('mission'); setPanelOpen(true) }}
      onTarget={() => setTarget(place.lon, place.lat)} onClose={() => setPlace(null)} />
  </aside>
  const pointViewer = place && placeView && !demo && !pickMode && <Suspense fallback={<div className="glass absolute bottom-[60px] right-3 z-[1200] rounded-xl p-4 text-xs">Loading place viewer…</div>}>
    <PlaceViewer point={place} tab={placeView} online={apiOk === true} onTab={setPlaceView} onClose={() => setPlaceView(null)} onVisit={point => { setPlace(point); fly(point.lon, point.lat, 17) }} />
  </Suspense>

  /* ===================== desktop / laptop layout ===================== */
  return (
    <div className="relative h-full w-full overflow-hidden">
      {demoBackground}
      {map}
      {lidar}
      {demoOverlay}
      {objectPopup}
      <OxygenAlerts people={lowOxygen} rovers={agents?.filter(a => a.kind === 'rover') ?? []} warp={warp}
        maxSpeed={Math.max(...(controls?.speeds_m_s ?? [2]))} top={demo ? 'top-3' : 'top-[100px]'} onSend={sendRover} />
      {pointViewer}

      {/* ---- left: icon rail + panel ---- */}
      <div ref={leftColRef} className="pointer-events-none absolute bottom-[60px] left-3 top-3 z-[1000] flex gap-3">
        <div className="pointer-events-auto"><IconRail tab={tab} open={panelOpen} onSelect={selectTab} onBriefing={() => setBriefing(true)} /></div>
        {panelOpen && (
          <aside key={tab} className="glass hud-corners slide-in scroll-thin pointer-events-auto relative w-[clamp(300px,26vw,420px)] overflow-y-auto rounded-2xl">
            {panelContent}
          </aside>
        )}
        <button onClick={() => setPanelOpen((o) => !o)} aria-label={panelOpen ? 'Collapse panel' : 'Expand panel'}
          className="glass pointer-events-auto grid h-8 w-6 place-items-center self-center rounded-lg text-muted hover:text-ink">
          {panelOpen ? <ChevronLeft size={14} /> : <ChevronRight size={14} />}
        </button>
      </div>

      {/* ---- top-centre: title + connection status (collapsible) ----
           Fixed to the middle of the screen, above the other overlays: opening or resizing the side
           panel, the Copilot or the dock never moves it. When the full box would run into the side
           panel or the Copilot (narrow windows), the compact pill shows in the same spot. */}
      <div className={`pointer-events-none fixed left-1/2 top-3 z-[1400] -translate-x-1/2 ${demo ? 'hidden' : ''}`}>
        {hudOpen && (hudForced || hudRoom >= HUD_BOX_W) && (
          <div className="glass hud-corners pointer-events-auto flex items-center gap-4 rounded-xl py-2.5 pl-4 pr-2 fade-in">
            <div className="shrink-0">
              <div className="display text-[15px] font-bold tracking-[0.2em]">MARS ODYSSEY</div>
              <div className="label !text-[9px]">Jezero Crater · EVA planning prototype</div>
              {simStatus}
            </div>
            <div className="hidden h-8 w-px shrink-0 bg-line-2 lg:block" />
            <div className="hidden shrink-0 font-mono text-[10.5px] leading-tight text-muted lg:block">
              <div><span className="text-faint">AOI</span> 77.29–77.47°E</div>
              <div className="pl-[26px]">18.42–18.51°N</div>
            </div>
            <button type="button" onClick={() => { setHudOpen(false); setHudForced(false) }} title="Collapse" aria-label="Collapse mission info" aria-expanded
              className="grid h-7 w-7 shrink-0 place-items-center self-start rounded-full text-muted hover:bg-white/5 hover:text-ink">
              <ChevronUp size={15} />
            </button>
          </div>
        )}
        {/* Compact pill: when collapsed by the user, or when the expanded box would not fit.
            Clicking it always expands the box (forced above the other panels if space is tight). */}
        {!(hudOpen && (hudForced || hudRoom >= HUD_BOX_W)) && (
          <button type="button" onClick={() => { setHudOpen(true); setHudForced(true) }} title="Expand mission info"
            aria-label="Show mission info" aria-expanded={false}
            className="glass hud-corners pointer-events-auto flex items-center gap-3 rounded-full py-2.5 pl-5 pr-3 text-muted hover:text-ink fade-in">
            <span className="display text-[16px] font-bold tracking-[0.2em] text-ink">MARS ODYSSEY</span>
            <i title={`Simulation ${session ? 'running' : 'not running'}`} aria-label={`Simulation ${session ? 'running' : 'not running'}`}
              className={`h-2.5 w-2.5 rounded-full ${session ? 'bg-emerald-400 shadow-[0_0_8px_#34d399]' : 'bg-faint'}`} />
            <span className="grid h-7 w-7 place-items-center rounded-full border border-line-2 text-ink" aria-hidden="true">
              <ChevronDown size={16} />
            </span>
          </button>
        )}
      </div>

      {/* Fixed dock: selected place sits directly to the left of the locator. */}
      {/* Stays visible with the elevation profile: it used to be hidden below xl whenever a route
          (and so the profile) was shown, which made the legend vanish e.g. after a demo. The profile
          now sits between the left panel and this dock using the measured --mo-dock-w. */}
      <div ref={rightRowRef} className={`pointer-events-none absolute bottom-[60px] right-3 z-[1200] items-end gap-3 ${demo || placeView ? 'hidden' : 'flex'}`}>
        {placeCard}
        {/* items-stretch: the legend takes the dock's width (one right-hand column, with the Copilot). */}
        <div ref={rightDockRef} className="flex flex-col items-stretch gap-2">
        {agents && <ObjectSelector agents={agents} selected={liveWorld?.selected ?? null} onSelect={selectAgent} onFocus={id => { setTraverseFor(null); selectAgent(id) }} />}
        {(activeRasters.length > 0 || layers.samples) && (
          <div className="glass pointer-events-auto space-y-2.5 rounded-xl p-3 fade-in">
            {activeRasters.slice(-2).map((r) => (
              <div key={r.id}>
                <div className="mb-1 text-[11px] font-medium">{r.name}</div>
                <Legend layer={r} compact />
              </div>
            ))}
            {layers.samples && (
              <div>
                <div className="mb-1 text-[11px] font-medium">Sample sites · rock class</div>
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  {ROCK_COLORS.map(([k, c]) => (
                    <span key={k} className="flex items-center gap-1.5 text-[10.5px] text-muted">
                      <i className="h-2 w-2 rounded-full ring-1 ring-white/60" style={{ background: c }} />{k}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
        {/* Map dock: layer toggles | zoom bar | locator, one seamless panel. */}
        <div ref={dockPanelRef} className="glass hud-corners pointer-events-auto flex items-stretch overflow-hidden rounded-xl">
          <QuickLayers layers={layers} setLayers={setLayers} manifest={manifest} embedded />
          <div className="flex w-9 shrink-0 flex-col justify-end border-l border-line">
            {[
              { l: 'Zoom in', i: <Plus size={16} />, f: () => mapRef.current?.zoomIn() },
              { l: 'Zoom out', i: <Minus size={16} />, f: () => mapRef.current?.zoomOut() },
              { l: 'Reset view', i: <Crosshair size={15} />, f: () => fly(77.385, 18.462, 13) },
            ].map((b, k) => (
              <button key={b.l} title={b.l} aria-label={b.l} onClick={b.f}
                className={`grid h-9 w-9 place-items-center text-muted transition-colors hover:bg-white/5 hover:text-ink ${k ? 'border-t border-line' : ''}`}>{b.i}</button>
            ))}
          </div>
          <div className="border-l border-line">
            <MiniMap aoi={manifest?.aoi_bounds ?? null} viewport={viewport} embedded
              onNavigate={(center, z) => mapRef.current?.setView([center.lat, center.lon], z, { animate: false })} />
          </div>
        </div>
        </div>
      </div>

      {/* ---- bottom-centre: elevation profile, above the mission info bar ---- */}
      {profile && (
        <div className="absolute bottom-[60px] left-[calc(var(--mo-left-w,92px)+12px)] right-[calc(var(--mo-dock-w,0px)+24px)] z-[1000]">
          {profile}
        </div>
      )}

      {pickBanner}

      <div className="absolute bottom-3 left-3 right-3 z-[1000]">
        <StatusBar cursor={demo && telemetry ? { lon: telemetry.lon, lat: telemetry.lat } : cursor} info={demo ? null : info} vehicle={!!(demo && telemetry)} zoom={zoom} apiOk={apiOk} center={<MapLegend visible={visibleTypes} onToggle={toggleType} />} />
      </div>

      {!demo && <OdysseyCopilot onAction={handleCopilotAction} currentLocation={cursor ?? start ?? LANDING_SITE} />}

      {briefing && <Briefing manifest={manifest} nSamples={samples?.features.length ?? 0} onEnter={closeBriefing} />}
    </div>
  )
}

function QuickLayers({ layers, setLayers, manifest, embedded = false }: { layers: MapLayerState; setLayers: (fn: (s: MapLayerState) => MapLayerState) => void; manifest: LayersManifest | null; embedded?: boolean }) {
  const items: [string, string][] = [['hirise', 'HiRISE'], ...(manifest?.raster_layers.map((r) => [r.id, r.id[0].toUpperCase() + r.id.slice(1)] as [string, string]) ?? [])]
  return (
    // `embedded`: inside the bottom-right map dock, which supplies the frame (no own glass/rounding).
    <div className={embedded ? 'flex w-[clamp(92px,7.5vw,120px)] shrink-0 flex-col justify-end gap-1 p-1.5' : 'glass pointer-events-auto flex flex-col gap-1 rounded-xl p-1.5'}>
      {items.map(([id, label]) => {
        const on = id === 'hirise' ? !!layers.trek.hirise : !!layers.raster[id]?.on
        return (
          <button key={id} aria-pressed={on}
            onClick={() => setLayers((s) => id === 'hirise'
              ? { ...s, trek: { ...s.trek, hirise: !on } }
              : { ...s, raster: { ...s.raster, [id]: { on: !on, opacity: s.raster[id]?.opacity ?? 0.7 } } })}
            className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[11.5px] transition-colors ${on ? 'bg-mars/15 text-ink' : 'text-muted hover:bg-white/5 hover:text-ink'}`}>
            <i className={`h-1.5 w-1.5 rounded-full ${on ? 'bg-mars shadow-[0_0_8px_#ff6a3d]' : 'bg-line-2'}`} />{label}
          </button>
        )
      })}
    </div>
  )
}
