import { useCallback, useEffect, useMemo, useRef } from 'react'
import L from 'leaflet'
import {
  CircleMarker, ImageOverlay, MapContainer, Marker, Pane, Polyline, TileLayer, Tooltip, useMap, useMapEvents,
} from 'react-leaflet'
import type { FC, LayersManifest, LonLat, MapViewport, ProfilePoint, RouteResponse, SampleProps } from '../types'
import { TREK_ATTRIB, TREK_LAYERS, trekUrl } from './trek'
import { LANDING_SITE, haversineM } from './geo'
import type { SelectedPlace } from '../components/PlaceCard'
import { PANORAMAS } from '../data/panoramas'
import type { SimRock, SimCrater, SimTelemetry } from '../services/sim'
import type { MapLocation, MarkerType, WorldAgent } from '../sim/world'
import { markerIcon } from './markers'

export interface MapLayerState {
  base: string
  trek: Record<string, boolean>
  raster: Record<string, { on: boolean; opacity: number }>
  traverse: boolean
  samples: boolean
  waypoints: boolean
}

interface Props {
  place?: SelectedPlace | null
  showPlaces?: boolean
  onPanoramaClick?: (point: SelectedPlace) => void
  tracking?: boolean
  rover?: SimTelemetry | null
  rocks?: SimRock[]
  craters?: SimCrater[]
  manifest: LayersManifest | null
  layers: MapLayerState
  samples: FC<SampleProps> | null
  traverse: FC<{ sol: number }> | null
  waypoints: FC<{ sol: number; lon: number; lat: number }> | null
  route: RouteResponse | null
  selectedRoute: string | null
  start: LonLat | null
  end: LonLat | null
  pickMode: 'start' | 'end' | null
  selectedSample: number | null
  hoverPoint: ProfilePoint | null
  /** render 5 m analysis cells with hard edges (true data resolution) when zoomed in */
  crisp?: boolean
  /** extra padding [top-left, bottom-right] in px when fitting a route, so HUD panels don't hide it */
  fitPadding?: [[number, number], [number, number]]
  flyTo: { lon: number; lat: number; zoom: number; key: number } | null
  onViewportChange: (viewport: MapViewport) => void
  onCursor: (p: LonLat | null, zoom: number) => void
  onMapClick: (p: LonLat) => void
  onSampleClick: (no: number) => void
  onWaypointClick?: (sol: number) => void
  /** user locations (Location tab) and live sim objects, filtered by the legend toggles */
  locations?: MapLocation[]
  agents?: WorldAgent[] | null
  visibleTypes?: Record<MarkerType, boolean>
  highlight?: { kind: 'location' | 'agent'; id: string } | null
  selectedAgent?: string | null
  onLocationClick?: (id: string) => void
  onLocationMove?: (id: string, p: LonLat) => void
  onAgentClick?: (id: string) => void
  /** keep this point centred (a tracked object) */
  follow?: LonLat | null
  /** tracking minimap: keep the rover arrow centred (button on the minimap) */
  trackLock?: boolean
}

/**
 * CSS rotation (deg, clockwise) for the east-pointing ➤ rover glyph.
 *
 * `yaw_deg` is the sim's ENU heading: counter-clockwise from east (C1). On this plate-carrée map
 * (EPSG:4326) screen x is proportional to longitude, so a true east step of 1 m is drawn
 * 1/cos(lat) wider than a north step; the drawn direction is (cos ψ / cos φ, sin ψ). Screen y
 * grows downwards, hence the minus sign.
 */
export function roverScreenAngleDeg(yawDeg: number, latDeg: number): number {
  const psi = (yawDeg * Math.PI) / 180
  const k = Math.cos((latDeg * Math.PI) / 180)
  return (-Math.atan2(Math.sin(psi), Math.cos(psi) / k) * 180) / Math.PI
}

function Events({ onCursor, onMapClick, onViewportChange }: Pick<Props, 'onCursor' | 'onMapClick' | 'onViewportChange'>) {
  const map = useMap()
  const viewportFrame = useRef<number | null>(null)
  const reportViewport = useCallback(() => {
    if (viewportFrame.current !== null) return
    viewportFrame.current = requestAnimationFrame(() => {
      viewportFrame.current = null
      const center = map.getCenter(), bounds = map.getBounds()
      onViewportChange({ center: { lon: center.lng, lat: center.lat }, zoom: map.getZoom(), bounds: [[bounds.getSouth(), bounds.getWest()], [bounds.getNorth(), bounds.getEast()]] })
    })
  }, [map, onViewportChange])
  useMapEvents({
    mousemove: (e) => onCursor({ lon: e.latlng.lng, lat: e.latlng.lat }, map.getZoom()),
    mouseout: () => onCursor(null, map.getZoom()),
    zoomend: () => onCursor(null, map.getZoom()),
    move: reportViewport,
    moveend: reportViewport,
    click: (e) => onMapClick({ lon: e.latlng.lng, lat: e.latlng.lat }),
  })
  useEffect(() => { reportViewport(); return () => { if (viewportFrame.current !== null) cancelAnimationFrame(viewportFrame.current); viewportFrame.current = null } }, [reportViewport])
  return null
}

function FlyTo({ flyTo }: { flyTo: Props['flyTo'] }) {
  const map = useMap()
  useEffect(() => {
    if (flyTo) map.flyTo([flyTo.lat, flyTo.lon], flyTo.zoom, { duration: 1.4 })
  }, [flyTo, map])
  return null
}

function FitRoute({ route, pad }: { route: RouteResponse | null; pad: [[number, number], [number, number]] }) {
  const map = useMap()
  const last = useRef<RouteResponse | null>(null)
  useEffect(() => {
    if (route && route !== last.current) {
      last.current = route
      const pts = route.routes.flatMap((r) => r.coordinates.map(([lon, lat]) => [lat, lon] as [number, number]))
      // leave room for the side panel (left) and the elevation profile (bottom)
      map.flyToBounds(L.latLngBounds(pts), { paddingTopLeft: pad[0], paddingBottomRight: pad[1], duration: 1.2 })
    }
  }, [route, map, pad])
  return null
}

// Draw order of imagery overlays: sharper data on top (CTX 6 m < HiRISE 25 cm < THEMIS when explicitly enabled).
const OVERLAY_Z: Record<string, number> = { ctx: 10, hirise: 20, themis_day: 30, themis_night: 31 }

export const ROCK_COLORS: [string, string][] = [
  ['Igneous', '#f472b6'], ['Sedimentary', '#38bdf8'], ['Regolith', '#cbd5e1'], ['Silica', '#a78bfa'],
]
export const sampleColor = (rock: string) => ROCK_COLORS.find(([k]) => rock.startsWith(k))?.[1] ?? '#facc15'

/** Cached: a new icon object per render would replace the pin element and leave its tooltip open. */
const pinCache = new Map<string, L.DivIcon>()
const pinIcon = (color: string, sel: boolean) => {
  const key = color + sel
  if (!pinCache.has(key)) pinCache.set(key, makePin(color, sel))
  return pinCache.get(key)!
}
const makePin = (color: string, sel: boolean) =>
  L.divIcon({
    className: '',
    iconSize: [22, 22],
    iconAnchor: [11, 11],
    html: `<div class="mo-pin ${sel ? 'sel' : ''}" style="color:${color}"><span class="ring"></span><span class="core" style="background:${color}"></span></div>`,
  })
const flagIcon = (color: string) =>
  L.divIcon({
    className: '',
    iconSize: [26, 26],
    iconAnchor: [13, 24],
    html: `<div class="mo-flagpin"><div style="background:${color}"></div></div>`,
  })

export default function MarsMap(p: Props) {
  const base = TREK_LAYERS.find((l) => l.id === p.layers.base) ?? TREK_LAYERS[5]
  const selId = p.route ? (p.selectedRoute ?? p.route.recommended) : null
  const traverseLines = useMemo(
    () => p.traverse?.features.map((f) =>
      (f.geometry.coordinates as [number, number][]).map(([lon, lat]) => [lat, lon] as [number, number])) ?? [],
    [p.traverse],
  )
  const startIcon = useMemo(() => flagIcon('#22c55e'), [])
  const endIcon = useMemo(() => flagIcon('#ff6a3d'), [])
  const panoramaIcon = useMemo(() => L.divIcon({ className: '', iconSize: [36, 24], iconAnchor: [18, 12], html: '<span class="mo-panorama-pin">360°</span>' }), [])

  return (
    <div className={`relative h-full w-full ${p.pickMode ? 'mo-crosshair' : ''}`}>
      <MapContainer crs={L.CRS.EPSG4326} center={[18.462, 77.385]} zoom={13} minZoom={3} maxZoom={19}
        zoomControl={false} className="h-full w-full" attributionControl>
        <TileLayer key={base.id} url={trekUrl(base)} maxNativeZoom={base.maxNativeZoom} maxZoom={19}
          attribution={`${TREK_ATTRIB} · ${base.mission} ${base.instrument}`} />

        {TREK_LAYERS.filter((l) => l.kind === 'overlay' && p.layers.trek[l.id]).map((l) => (
          <TileLayer key={l.id} url={trekUrl(l)} maxNativeZoom={l.maxNativeZoom} maxZoom={19} minZoom={l.minZoom ?? 0}
            zIndex={OVERLAY_Z[l.id] ?? 10}
            bounds={l.bounds ? L.latLngBounds(l.bounds) : undefined} attribution={`${l.mission} ${l.instrument}`} />
        ))}

        <Pane name="analysis" style={{ zIndex: 350 }}>
          {p.manifest?.raster_layers.filter((r) => p.layers.raster[r.id]?.on).map((r) => (
            <ImageOverlay key={r.id} url={r.url} bounds={r.bounds} opacity={p.layers.raster[r.id].opacity}
              attribution="HiRISE DTM: USGS/NASA doi:10.5066/P9REJ9JN" />
          ))}
        </Pane>

        {p.layers.traverse && traverseLines.length > 0 && (
          <>
            <Polyline positions={traverseLines} pathOptions={{ color: '#000', weight: 4, opacity: 0.35 }} interactive={false} />
            <Polyline positions={traverseLines} pathOptions={{ color: '#f1f5f9', weight: 1.6, opacity: 0.9, dashArray: '5 5' }}>
              <Tooltip className="mo-tip" sticky>Perseverance traverse (NASA/JPL)</Tooltip>
            </Polyline>
          </>
        )}
        {p.layers.waypoints && p.waypoints?.features.map((f, i) => (
          <CircleMarker key={i} center={[f.properties.lat, f.properties.lon]} radius={2}
            pathOptions={{ color: '#cbd5e1', weight: 0, fillOpacity: 0.75 }}
            eventHandlers={{ click: e => { L.DomEvent.stopPropagation(e); p.onWaypointClick?.(f.properties.sol) } }}>
            <Tooltip className="mo-tip">Sol {f.properties.sol} · click for recorded weather</Tooltip>
          </CircleMarker>
        ))}

        {/* alternative routes underneath, selected route on top with glow + marching dashes */}
        {p.route?.routes.filter((r) => r.id !== selId).map((r) => {
          const pos = r.coordinates.map(([lon, lat]) => [lat, lon] as [number, number])
          return (
            <Polyline key={r.id} positions={pos} pathOptions={{ color: r.color, weight: 3, opacity: 0.45 }}>
              <Tooltip className="mo-tip" sticky>{r.label} · {(r.length_m / 1000).toFixed(2)} km</Tooltip>
            </Polyline>
          )
        })}
        {p.route?.routes.filter((r) => r.id === selId).map((r) => {
          const pos = r.coordinates.map(([lon, lat]) => [lat, lon] as [number, number])
          return (
            <span key={r.id + '-sel'}>
              <Polyline positions={pos} pathOptions={{ color: r.color, weight: 12, opacity: 0.18 }} interactive={false} />
              <Polyline positions={pos} pathOptions={{ color: '#05070a', weight: 6, opacity: 0.8 }} interactive={false} />
              <Polyline positions={pos} pathOptions={{ color: r.color, weight: 4, opacity: 1 }}>
                <Tooltip className="mo-tip" sticky>{r.label} · {(r.length_m / 1000).toFixed(2)} km</Tooltip>
              </Polyline>
              <Polyline positions={pos} pathOptions={{ color: '#fff', weight: 2, opacity: 0.9, className: 'route-flow' }} interactive={false} />
            </span>
          )
        })}

        {p.layers.samples && p.samples?.features.map((f) => {
          const [lon, lat] = f.geometry.coordinates as [number, number]
          const s = f.properties
          const sel = p.selectedSample === s.sample_no
          return (
            <Marker key={`${s.sample_no}-${sel}`} position={[lat, lon]} icon={pinIcon(sampleColor(s.rock_class), sel)}
              zIndexOffset={sel ? 1000 : 0}
              eventHandlers={{ click: (e) => { L.DomEvent.stopPropagation(e); p.onSampleClick(s.sample_no) } }}>
              <Tooltip className="mo-tip" direction="top" offset={[0, -10]}>
                <b>#{s.sample_no} {s.name}</b> · {s.feature}<br /><span style={{ opacity: 0.7 }}>{s.rock_class} · sol {s.sol_sealed}</span>
              </Tooltip>
            </Marker>
          )
        })}

        <CircleMarker center={[LANDING_SITE.lat, LANDING_SITE.lon]} radius={9}
          pathOptions={{ color: '#ff6a3d', weight: 2, fillColor: '#ff6a3d', fillOpacity: 0.15, dashArray: '3 3' }}>
          <Tooltip className="mo-flag" permanent direction="right" offset={[10, 0]}>LANDING · SOL 0</Tooltip>
        </CircleMarker>

        {p.start && (
          <Marker position={[p.start.lat, p.start.lon]} icon={startIcon} zIndexOffset={2000}>
            <Tooltip className="mo-flag" permanent direction="left" offset={[-10, -12]}>START</Tooltip>
          </Marker>
        )}
        {p.end && (
          <Marker position={[p.end.lat, p.end.lon]} icon={endIcon} zIndexOffset={2000}>
            <Tooltip className="mo-flag" permanent direction="left" offset={[-10, -12]}>TARGET</Tooltip>
          </Marker>
        )}

        {p.hoverPoint && (
          <CircleMarker center={[p.hoverPoint.lat, p.hoverPoint.lon]} radius={7}
            pathOptions={{ color: '#5ee6ff', weight: 2, fillColor: '#5ee6ff', fillOpacity: 0.35 }} interactive={false} />
        )}

        {p.rover && <Marker position={[p.rover.lat, p.rover.lon]} zIndexOffset={3000} icon={L.divIcon({ className: '', iconSize: [22, 22], iconAnchor: [11, 11], html: `<div style="color:#5ee6ff;font-size:22px;line-height:22px;text-align:center;transform:rotate(${roverScreenAngleDeg(p.rover.yaw_deg, p.rover.lat)}deg)">➤</div>` })} />}
        {p.locations?.filter(l => p.visibleTypes?.[l.type] !== false).map(l => {
          const hl = p.highlight?.kind === 'location' && p.highlight.id === l.id
          return <LocationMarker key={l.id} loc={l} highlight={hl} draggable={!p.tracking} onClick={p.onLocationClick} onMove={p.onLocationMove} />
        })}
        {/* mission path of the focused / selected object, dotted */}
        {p.agents?.filter(a => a.path && a.path.length > 1 && (a.id === p.selectedAgent || (p.highlight?.kind === 'agent' && p.highlight.id === a.id))).map(a =>
          <Polyline key={`path-${a.id}`} positions={a.path!.map(([lon, lat]) => [lat, lon] as [number, number])} interactive={false}
            pathOptions={{ color: '#ffffff', weight: 2.5, opacity: 0.9, dashArray: '2 7', lineCap: 'round' }} />)}
        {/* a travel target that is a plain map point (not a location): green dot named Target */}
        {p.agents?.filter(a => a.mode === 'goto' && a.goal && a.path && a.path.length > 1
          && (a.id === p.selectedAgent || (p.highlight?.kind === 'agent' && p.highlight.id === a.id))
          && !p.locations?.some(l => haversineM(l.lon, l.lat, a.goal!.lon, a.goal!.lat) < 10)).map(a =>
          <CircleMarker key={`target-${a.id}`} center={[a.goal!.lat, a.goal!.lon]} radius={6} interactive={false}
            pathOptions={{ color: '#ffffff', weight: 2, fillColor: '#22c55e', fillOpacity: 1 }}>
            <Tooltip className="mo-flag" permanent direction="top" offset={[0, -8]}>Target</Tooltip>
          </CircleMarker>)}
        {/* in the 3D view the selected object is the arrow; its type icon is not repeated */}
        {p.agents?.filter(a => p.visibleTypes?.[a.kind] !== false && !(p.tracking && a.id === p.selectedAgent)).map(a => {
          const hl = p.highlight?.kind === 'agent' && p.highlight.id === a.id
          return <Marker key={a.id} position={[a.lat, a.lon]} zIndexOffset={hl ? 3200 : 2900}
            icon={markerIcon(a.kind, { name: a.name, highlight: hl, selected: a.id === p.selectedAgent,
              arrowDeg: hl ? roverScreenAngleDeg(a.yaw_deg, a.lat) : null, speed: a.speed_m_s })}
            eventHandlers={{ click: e => { L.DomEvent.stopPropagation(e); p.onAgentClick?.(a.id) } }} />
        })}
        {!p.tracking && <Follow point={p.follow ?? null} />}
        <TrackingMode tracking={!!p.tracking} lock={!!p.trackLock} rover={p.rover} route={p.route?.routes.find(r => r.id === selId)?.coordinates} />
        {p.showPlaces && !p.tracking && PANORAMAS.filter(site => site.id !== 'van-zyl' || p.crisp).map(site => <Marker key={site.id} position={[site.lat, site.lon]} icon={panoramaIcon} zIndexOffset={2500} title={`360° panorama: ${site.name}`} alt={`360° panorama: ${site.name}`} eventHandlers={{ click: () => p.onPanoramaClick?.(site) }}>
          <Tooltip className="mo-tip">{site.name} · panorama sol {site.sols}</Tooltip>
        </Marker>)}
        {p.place && p.showPlaces && <CircleMarker center={[p.place.lat, p.place.lon]} radius={7} pathOptions={{ color: '#5ee6ff', fillOpacity: .3, weight: 2 }} interactive={false} />}
        <Events onViewportChange={p.onViewportChange} onCursor={p.onCursor} onMapClick={p.onMapClick} />
        <FlyTo flyTo={p.tracking ? null : p.flyTo} />
        <FitRoute route={p.route} pad={p.fitPadding ?? [[470, 90], [260, 230]]} />
        <MapApi />
        <CrispPane crisp={!!p.crisp} />
      </MapContainer>
      <div className="map-vignette" />
    </div>
  )
}

/**
 * A draggable location marker. The position array is memoised on the coordinates: react-leaflet
 * moves the marker back to `position` whenever it receives a new array, which happened on every
 * re-render during a drag (cursor read-out, sim updates) and snapped the marker back.
 */
function LocationMarker({ loc, highlight, draggable, onClick, onMove }: {
  loc: MapLocation; highlight: boolean; draggable: boolean
  onClick?: (id: string) => void; onMove?: (id: string, p: LonLat) => void
}) {
  const position = useMemo<[number, number]>(() => [loc.lat, loc.lon], [loc.lat, loc.lon])
  const handlers = useMemo(() => ({
    click: (e: L.LeafletMouseEvent) => { L.DomEvent.stopPropagation(e); onClick?.(loc.id) },
    dragend: (e: L.DragEndEvent) => { const ll = (e.target as L.Marker).getLatLng(); onMove?.(loc.id, { lon: ll.lng, lat: ll.lat }) },
  }), [loc.id, onClick, onMove])
  return <Marker position={position} draggable={draggable} zIndexOffset={highlight ? 2600 : 2200}
    icon={markerIcon(loc.type, { name: loc.name, highlight })} eventHandlers={handlers} />
}

/** Keeps a tracked object centred (pans only when it drifts from the centre). */
function Follow({ point }: { point: LonLat | null }) {
  const map = useMap()
  useEffect(() => {
    if (!point) return
    const c = map.latLngToContainerPoint([point.lat, point.lon]), size = map.getSize()
    if (Math.hypot(c.x - size.x / 2, c.y - size.y / 2) > 2) map.panTo([point.lat, point.lon], { animate: true, duration: 0.25 })
  }, [point?.lat, point?.lon, map])  // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

/** Exposes zoom controls to the HUD outside the MapContainer. */
export const mapRef: { current: L.Map | null } = { current: null }
function MapApi() {
  const map = useMap()
  useEffect(() => { mapRef.current = map; return () => { mapRef.current = null } }, [map])
  return null
}

/** Toggle hard-edged rendering of the 5 m analysis rasters (Pane ignores className updates after mount). */
function CrispPane({ crisp }: { crisp: boolean }) {
  const map = useMap()
  useEffect(() => {
    map.getPane('analysis')?.classList.toggle('mo-crisp', crisp)
  }, [map, crisp])
  return null
}

function TrackingMode({ tracking, lock, rover, route }: { tracking: boolean; lock: boolean; rover?: SimTelemetry | null; route?: [number, number][] }) {
  const map = useMap()
  useEffect(() => {
    const timer = setTimeout(() => {
      map.invalidateSize()
      if (tracking && route?.length) map.fitBounds(L.latLngBounds(route.map(([lon, lat]) => [lat, lon])), { padding: [8, 8], animate: false })
    }, 0)
    return () => clearTimeout(timer)
  }, [tracking, map, route])
  useEffect(() => {
    if (!tracking || !rover) return
    // Tracking button on: keep the arrow centred. Off: leave the map alone (free to drag).
    if (lock) map.setView([rover.lat, rover.lon], map.getZoom(), { animate: false })
  }, [tracking, lock, rover, map])
  return null
}
