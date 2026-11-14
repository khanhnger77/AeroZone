import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Crosshair, Minus, Plus } from 'lucide-react'
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
import { loadSimMission, simConfig, simCommand, type SimCommand, type SimControlsConfig, type SimMission, type SimTelemetry } from './services/sim'
import { useMedia } from './useMedia'
import { useConnections } from './useConnections'
import ConnectionStatus from './components/ConnectionStatus'
import PlaceCard, { type PlaceViewTab, type SelectedPlace } from './components/PlaceCard'

// recharts is only needed once a route exists -> load it on demand (separate chunk)
const ElevationProfile = lazy(() => import('./components/ElevationProfile'))
const PlaceViewer = lazy(() => import('./components/PlaceViewer'))
const WeatherPanel = lazy(() => import('./components/WeatherPanel'))

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
  const iframe = useRef<HTMLIFrameElement>(null)
  const demoStarting = useRef(false)
  const command = useCallback(async (cmd: SimCommand) => {
    try {
      await simCommand(cmd)
      if (cmd.type === 'set_speed') setSpeed(cmd.value)
      if (cmd.type === 'set_warp') setWarp(cmd.value)
    } catch (error) { setDemoError(error instanceof Error ? error.message : String(error)) }
  }, [])
  const runDemo = async () => {
    if (demo) {
      try { await simCommand({ type: 'pause' }) } catch (error) { setDemoError(String(error)) }
      setDemo(null); setTelemetry(null); return
    }
    if (!route || demoStarting.current || connections.sim !== 'online') return
    demoStarting.current = true
    setDemoLoading(true); setDemoError(null)
    try {
      const [config, mission] = await Promise.all([simConfig(), loadSimMission(route, selectedRoute ?? route.recommended, { enabled: obstaclesOn, seed: crypto.getRandomValues(new Uint32Array(1))[0] })])
      setControls(config.controls); setSpeed(config.controls.default_speed_m_s); setWarp(1)
      setPickMode(null); setHoverPoint(null); setTelemetry(null); setPlace(null); setPlaceView(null); setDemo(mission)
      if (mobile) setPanelOpen(false)
    } catch (error) { setDemoError(error instanceof Error ? error.message : String(error)) }
    finally { setDemoLoading(false); demoStarting.current = false }
  }
  useEffect(() => {
    if (!demo || !controls) return
    let active = true, initialized = false
    const receive = async (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== iframe.current?.contentWindow || event.data?.source !== 'mo-sim') return
      const data = event.data
      if (data.type === 'ready' && data.mission === demo.id && !initialized) {
        initialized = true
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
    if (demo || demoLoading) return
    if (pickMode === 'start') { setStart(p); setPickMode(end ? null : 'end'); setRoute(null); setPlace(null); setPlaceView(null) }
    else if (pickMode === 'end') { setEnd(p); setPickMode(null); setRoute(null); setPlace(null); setPlaceView(null) }
    else if (!mobile) setPlace(p)
  }, [pickMode, end, demo, demoLoading, mobile])

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
          demoControls={<DemoControls active={!!demo} config={controls} telemetry={telemetry} speed={speed} warp={warp} onCommand={command} />}
          start={start} end={end} pickMode={pickMode}
          setPickMode={(m) => { setPickMode(m); if (m && mobile) setPanelOpen(false) }}
          onPlan={() => plan()} onReset={() => { setStart(null); setEnd(null); setRoute(null); setRouteError(null); setPickMode(null) }}
          onPreset={preset} loading={routeLoading} error={routeError}
          route={route} selected={selectedRoute} onSelect={setSelectedRoute} />
      )}
    </>
  )

  const profile = !demo && !place && !placeView && selRoute && showProfile && tab === 'mission' && (
    <Suspense fallback={null}>
      <ElevationProfile route={selRoute} compact={mobile} onHover={setHoverPoint}
        onClose={() => { setShowProfile(false); setHoverPoint(null) }} />
    </Suspense>
  )

  const pickBanner = !demo && pickMode && (
    <div className="pointer-events-none absolute left-1/2 top-5 z-[1100] w-max max-w-[92vw] -translate-x-1/2">
      <div className="glass fade-in flex items-center gap-2 rounded-full border-mars/60 px-4 py-2 text-sm">
        <span className={`h-2 w-2 animate-pulse rounded-full ${pickMode === 'start' ? 'bg-green-400' : 'bg-mars'}`} />
        {mobile ? 'Tap' : 'Click'} the map to place the <b>{pickMode === 'start' ? 'START' : 'TARGET'}</b> point
      </div>
    </div>
  )

  const map = (
    <div className={demo ? 'mo-tracking glass absolute right-3 bottom-[76px] z-[1000] w-[200px] overflow-hidden rounded-lg md:bottom-[60px] lg:bottom-[207px]' : 'absolute inset-0'}>
      <div className={demo ? 'flex h-7 items-center justify-between px-2 text-[10px]' : 'hidden'}><span>Tracking · {selRoute?.label}</span><span>{((telemetry?.nav.progress ?? 0) * 100).toFixed(0)}%</span></div>
      <div className={demo ? 'h-[110px]' : 'h-full'}>
    <MarsMap place={place} showPlaces={!mobile && !demo && !pickMode}
      onPanoramaClick={(point) => { setPickMode(null); setPlace(point); setPlaceView('panorama') }}
      tracking={!!demo} rover={telemetry} rocks={demo?.obstacles.rocks} craters={demo?.obstacles.craters} manifest={manifest} layers={layers} samples={samples} traverse={traverse} waypoints={waypoints}
      route={route} selectedRoute={selectedRoute} start={start} end={end} pickMode={pickMode}
      onWaypointClick={sol => { if (demo || demoLoading) return; setWeatherSol(sol); setTab('weather'); setPanelOpen(true); setPlace(null); setPlaceView(null) }}
      onViewportChange={setViewport} selectedSample={selectedSample} hoverPoint={hoverPoint} flyTo={flyTo} onCursor={onCursor} onMapClick={onMapClick}
      crisp={zoom >= 16}
      fitPadding={mobile ? [[24, 80], [24, 230]] : [[panelOpen ? 470 : 100, 90], [260, 230]]}
      onSampleClick={(no) => { if (!demo) { setSelectedSample(no); setTab('science'); setPanelOpen(true); const sample = samples?.features.find(f => f.properties.sample_no === no); if (sample && !mobile) { const [lon, lat] = sample.geometry.coordinates as [number, number]; setPlace({ lon, lat, name: sample.properties.name }) } } }} />
      </div>
    </div>
  )

  const connectionStatus = <ConnectionStatus planner={connections.planner} sim={connections.sim} checking={connections.checking} onRetry={connections.refresh} />

  const demoBackground = demo && <iframe ref={iframe} key={demo.id} title="Mars 3D simulation" src={`/sim/?embed=1&m=${encodeURIComponent(demo.id)}`} className="absolute inset-0 h-full w-full border-0" />
  const lidar = demo?.obstacles.enabled && <div className="absolute right-3 bottom-[224px] z-[1000] md:bottom-[207px] lg:bottom-[353px]"><LidarScreen /></div>

  /* ===================== phone layout (< 768 px) ===================== */
  if (mobile) {
    return (
      <div className="relative h-full w-full overflow-hidden">
        {demoBackground}
        {map}
        {lidar}
        <div className="pointer-events-none absolute left-2 right-2 top-2 z-[1000] flex items-start justify-between gap-2">
          <div className="glass hud-corners pointer-events-auto rounded-xl px-3 py-2">
            <div className="display text-[13px] font-bold tracking-[0.2em]">MARS ODYSSEY</div>
            <div className="label !text-[8.5px]">Jezero · EVA planner</div>
            {connectionStatus}
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
      {pointViewer}

      {/* ---- left: icon rail + panel ---- */}
      <div className="pointer-events-none absolute bottom-[60px] left-3 top-3 z-[1000] flex gap-3">
        <div className="pointer-events-auto"><IconRail tab={tab} open={panelOpen} onSelect={selectTab} onBriefing={() => setBriefing(true)} /></div>
        {panelOpen && (
          <aside key={tab} className="glass hud-corners slide-in scroll-thin pointer-events-auto relative w-[340px] overflow-y-auto rounded-2xl xl:w-[380px]">
            {panelContent}
          </aside>
        )}
        <button onClick={() => setPanelOpen((o) => !o)} aria-label={panelOpen ? 'Collapse panel' : 'Expand panel'}
          className="glass pointer-events-auto mt-3 grid h-8 w-6 place-items-center self-start rounded-lg text-muted hover:text-ink">
          {panelOpen ? <ChevronLeft size={14} /> : <ChevronRight size={14} />}
        </button>
      </div>

      {/* ---- top-right: title HUD + map controls ---- */}
      <div className="pointer-events-none absolute right-3 top-3 z-[1000] flex flex-col items-end gap-2">
        <div className="glass hud-corners pointer-events-auto flex items-center gap-4 rounded-xl px-4 py-2.5">
          <div>
            <div className="display text-[15px] font-bold tracking-[0.2em]">MARS ODYSSEY</div>
            <div className="label !text-[9px]">Jezero Crater · EVA planning prototype</div>
            {connectionStatus}
          </div>
          <div className="hidden h-8 w-px bg-line-2 lg:block" />
          <div className="hidden font-mono text-[10.5px] leading-tight text-muted lg:block">
            <div><span className="text-faint">AOI</span> 77.29–77.47°E</div>
            <div className="pl-[26px]">18.42–18.51°N</div>
          </div>
        </div>
        <div className={demo ? 'hidden' : 'glass pointer-events-auto flex flex-col overflow-hidden rounded-xl'}>
          {[
            { l: 'Zoom in', i: <Plus size={16} />, f: () => mapRef.current?.zoomIn() },
            { l: 'Zoom out', i: <Minus size={16} />, f: () => mapRef.current?.zoomOut() },
            { l: 'Reset view', i: <Crosshair size={15} />, f: () => fly(77.385, 18.462, 13) },
          ].map((b, k) => (
            <button key={b.l} title={b.l} aria-label={b.l} onClick={b.f}
              className={`grid h-9 w-9 place-items-center text-muted transition-colors hover:bg-white/5 hover:text-ink ${k ? 'border-t border-line' : ''}`}>{b.i}</button>
          ))}
        </div>
        {!demo && <QuickLayers layers={layers} setLayers={setLayers} manifest={manifest} />}
      </div>

      {/* Fixed dock: selected place sits directly to the left of the locator. */}
      <div className={`pointer-events-none absolute bottom-[60px] right-3 z-[1200] items-end gap-3 ${demo || placeView ? 'hidden' : profile ? 'hidden xl:flex' : 'flex'}`}>
        {placeCard}
        <div className="flex flex-col items-end gap-2">
        {(activeRasters.length > 0 || layers.samples) && (
          <div className="glass pointer-events-auto w-[220px] space-y-2.5 rounded-xl p-3 fade-in">
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
        <div className="pointer-events-auto"><MiniMap aoi={manifest?.aoi_bounds ?? null} viewport={viewport}
          onNavigate={(center, z) => mapRef.current?.setView([center.lat, center.lon], z, { animate: false })} /></div>
        </div>
      </div>

      {/* ---- bottom-centre: elevation profile ---- */}
      {profile && (
        <div className={`absolute bottom-[60px] right-3 z-[1000] xl:right-[244px] ${panelOpen ? 'left-[428px] xl:left-[468px]' : 'left-[92px]'} transition-[left]`}>
          {profile}
        </div>
      )}

      {pickBanner}

      <div className="absolute bottom-3 left-3 right-3 z-[1000]">
        <StatusBar cursor={cursor} info={info} zoom={zoom} apiOk={apiOk} />
      </div>

      {briefing && <Briefing manifest={manifest} nSamples={samples?.features.length ?? 0} onEnter={closeBriefing} />}
    </div>
  )
}

function QuickLayers({ layers, setLayers, manifest }: { layers: MapLayerState; setLayers: (fn: (s: MapLayerState) => MapLayerState) => void; manifest: LayersManifest | null }) {
  const items: [string, string][] = [['hirise', 'HiRISE'], ...(manifest?.raster_layers.map((r) => [r.id, r.id[0].toUpperCase() + r.id.slice(1)] as [string, string]) ?? [])]
  return (
    <div className="glass pointer-events-auto flex flex-col gap-1 rounded-xl p-1.5">
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
