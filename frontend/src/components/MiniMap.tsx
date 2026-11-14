import { useCallback, useEffect, useRef } from 'react'
import L from 'leaflet'
import { CircleMarker, MapContainer, Rectangle, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import { TREK_LAYERS, trekUrl } from '../map/trek'
import type { LonLat, MapViewport } from '../types'

const ZOOM_OFFSET = 4
const overviewZoom = (mainZoom: number) => Math.max(2, Math.min(12, mainZoom - ZOOM_OFFSET))

function LocatorSync({ viewport, onNavigate }: { viewport: MapViewport | null; onNavigate: (center: LonLat, zoom: number) => void }) {
  const map = useMap()
  const syncing = useRef(false)
  const latest = useRef({ viewport, onNavigate })
  latest.current = { viewport, onNavigate }
  const followMainView = useCallback(() => {
    const current = latest.current.viewport
    if (!current || !map.getContainer().clientWidth) return
    syncing.current = true
    try {
      map.invalidateSize({ pan: false, animate: false })
      map.setView([current.center.lat, current.center.lon], overviewZoom(current.zoom), { animate: false })
    } finally { syncing.current = false }
  }, [map])
  useEffect(followMainView, [viewport, followMainView])
  useEffect(() => {
    const observer = new ResizeObserver(followMainView)
    observer.observe(map.getContainer())
    return () => observer.disconnect()
  }, [map, followMainView])
  useMapEvents({
    moveend: () => {
      if (syncing.current || !latest.current.viewport || !map.getContainer().clientWidth) return
      const center = map.getCenter(), previous = latest.current.viewport
      const zoomDelta = map.getZoom() - overviewZoom(previous.zoom)
      const mainZoom = Math.max(3, Math.min(19, previous.zoom + zoomDelta))
      if (Math.abs(center.lat - previous.center.lat) < 1e-9 && Math.abs(center.lng - previous.center.lon) < 1e-9 && mainZoom === previous.zoom) return
      latest.current.onNavigate({ lon: center.lng, lat: center.lat }, mainZoom)
    },
    click: event => {
      const previous = latest.current.viewport
      if (previous) latest.current.onNavigate({ lon: event.latlng.lng, lat: event.latlng.lat }, previous.zoom)
    },
  })
  return null
}

/** A live locator: follows the main view; drag/click/zoom here to navigate it. */
export default function MiniMap({ aoi, viewport, onNavigate, embedded = false }: {
  aoi: [[number, number], [number, number]] | null
  viewport: MapViewport | null
  onNavigate: (center: LonLat, zoom: number) => void
  /** Inside a dock that supplies the frame (glass, corners, rounding): render without its own. */
  embedded?: boolean
}) {
  const mola = TREK_LAYERS.find(l => l.id === 'mola')!
  const center = viewport?.center ?? { lon: 77.385, lat: 18.462 }
  // Scales with the window: 180-280 px wide, map ~55 % of the width tall. The locator re-syncs on
  // resize (ResizeObserver in LocatorSync), so a size change never desynchronises it.
  return <section aria-label="Locator map" className={`w-[clamp(180px,16vw,280px)] overflow-hidden ${embedded ? '' : 'glass hud-corners rounded-xl'}`}>
    <div className="flex items-center justify-between px-2.5 py-1.5">
      <span className="label !text-[9px]">Locator · MOLA</span>
      <span className="font-mono text-[9px] text-muted">{Math.abs(center.lat).toFixed(2)}°{center.lat >= 0 ? 'N' : 'S'} {Math.abs(center.lon).toFixed(2)}°{center.lon >= 0 ? 'E' : 'W'}</span>
    </div>
    <div className="h-[clamp(96px,8.8vw,154px)] w-full" title="Drag or click to move the main map. Scroll to zoom.">
      <MapContainer crs={L.CRS.EPSG4326} center={[center.lat, center.lon]} zoom={overviewZoom(viewport?.zoom ?? 13)} minZoom={2} maxZoom={12} zoomControl={false} attributionControl={false}
        dragging scrollWheelZoom doubleClickZoom touchZoom keyboard boxZoom={false} className="h-full w-full" aria-label="Interactive locator">
        <TileLayer url={trekUrl(mola)} maxNativeZoom={mola.maxNativeZoom} maxZoom={12} />
        {aoi && <Rectangle bounds={aoi} interactive={false} pathOptions={{ color: '#ff6a3d', weight: 1, fillOpacity: 0 }} />}
        {viewport && <Rectangle bounds={viewport.bounds} interactive={false} pathOptions={{ color: '#5ee6ff', weight: 1.5, fillOpacity: .1 }} />}
        <CircleMarker center={[center.lat, center.lon]} radius={2} interactive={false} pathOptions={{ color: '#ffffff', weight: 1, fillOpacity: 1 }} />
        <LocatorSync viewport={viewport} onNavigate={onNavigate} />
      </MapContainer>
    </div>
    <div className="space-y-1 border-t border-line px-2.5 py-1.5 text-[9px] text-muted">
      <div className="flex gap-3"><span className="inline-flex items-center gap-1"><i className="h-1.5 w-1.5 border border-cyan" />Current view</span><span className="inline-flex items-center gap-1"><i className="h-1.5 w-1.5 border border-mars" />Study area</span></div>
      <p>Drag / click to navigate · scroll to zoom</p>
    </div>
  </section>
}
