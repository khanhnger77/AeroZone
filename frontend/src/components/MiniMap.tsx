import L from 'leaflet'
import { MapContainer, Rectangle, TileLayer } from 'react-leaflet'
import { TREK_LAYERS, trekUrl } from '../map/trek'

/** Locator inset: where the study area sits on Mars (MOLA colour shaded relief). */
export default function MiniMap({ aoi }: { aoi: [[number, number], [number, number]] | null }) {
  const mola = TREK_LAYERS.find((l) => l.id === 'mola')!
  return (
    <div className="glass hud-corners overflow-hidden rounded-xl">
      <div className="flex items-center justify-between px-2.5 py-1.5">
        <span className="label !text-[9px]">Locator · MOLA</span>
        <span className="font-mono text-[9px] text-faint">18.5°N 77.4°E</span>
      </div>
      <div className="h-[110px] w-[200px]">
        <MapContainer crs={L.CRS.EPSG4326} center={[18.46, 77.4]} zoom={4} zoomControl={false} attributionControl={false}
          dragging={false} scrollWheelZoom={false} doubleClickZoom={false} touchZoom={false} keyboard={false}
          boxZoom={false} className="h-full w-full">
          <TileLayer url={trekUrl(mola)} maxNativeZoom={mola.maxNativeZoom} />
          {aoi && <Rectangle bounds={L.latLngBounds(aoi).pad(2.5)} pathOptions={{ color: '#ff6a3d', weight: 1.5, fillOpacity: 0.15 }} />}
        </MapContainer>
      </div>
    </div>
  )
}
