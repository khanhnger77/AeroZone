import { useCallback, useEffect, useMemo, useRef } from 'react'
import L from 'leaflet'
import {
  CircleMarker, ImageOverlay, MapContainer, Marker, Pane, Polyline, TileLayer, Tooltip, useMap, useMapEvents,
} from 'react-leaflet'
import type { FC, LayersManifest, LonLat, MapViewport, ProfilePoint, RouteResponse, SampleProps } from '../types'
import { TREK_ATTRIB, TREK_LAYERS, trekUrl } from './trek'
import { LANDING_SITE } from './geo'
import type { SelectedPlace } from '../components/PlaceCard'
import { PANORAMAS } from '../data/panoramas'
import type { SimRock, SimCrater, SimTelemetry } from '../services/sim'

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

const pinIcon = (color: string, sel: boolean) =>
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

        {p.rocks?.map(rock => <CircleMarker key={rock.id} center={[rock.lat, rock.lon]} radius={3} pathOptions={{ color: '#f87171', fillOpacity: 1 }} />)}
        {p.craters?.map(crater => <CircleMarker key={crater.id} center={[crater.lat, crater.lon]} radius={5} pathOptions={{ color: '#fb923c', fillOpacity: 0.4 }} />)}
        {p.rover && <Marker position={[p.rover.lat, p.rover.lon]} zIndexOffset={3000} icon={L.divIcon({ className: '', iconSize: [22, 22], iconAnchor: [11, 11], html: `<div style="color:#5ee6ff;font-size:22px;transform:rotate(${90 - p.rover.yaw_deg}deg)">➤</div>` })} />}
        <TrackingMode tracking={!!p.tracking} rover={p.rover} route={p.route?.routes.find(r => r.id === selId)?.coordinates} />
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

function TrackingMode({ tracking, rover, route }: { tracking: boolean; rover?: SimTelemetry | null; route?: [number, number][] }) {
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
    const point = map.latLngToContainerPoint([rover.lat, rover.lon]), size = map.getSize()
    if (point.x < size.x * 0.2 || point.x > size.x * 0.8 || point.y < size.y * 0.2 || point.y > size.y * 0.8) map.panTo([rover.lat, rover.lon], { animate: false })
  }, [tracking, rover, map])
  return null
}
