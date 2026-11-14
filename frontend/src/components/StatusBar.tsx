import type { ReactNode } from 'react'
import type { LonLat, PointInfo } from '../types'
import { MARS_RADIUS_M, fmtLat, fmtLon } from '../map/geo'

/** Ground metres per screen pixel at a Leaflet EPSG4326 zoom level (north-south, Mars sphere). */
const mPerPx = (z: number) => (180 / (256 * 2 ** z)) * (Math.PI / 180) * MARS_RADIUS_M

function ScaleBar({ zoom }: { zoom: number }) {
  const m = mPerPx(zoom) * 100
  const nice = [1, 2, 5].flatMap((b) => [1, 10, 100, 1000, 10000, 100000].map((e) => b * e)).sort((a, b) => a - b)
  const len = nice.filter((n) => n <= m).pop() ?? 1
  const px = (len / m) * 100
  return (
    <div className="flex items-center gap-2" title="Scale computed on the Mars sphere (R = 3,396.19 km)">
      <div className="h-[7px] border-x border-b border-ink/70" style={{ width: px }} />
      <span className="text-ink">{len >= 1000 ? `${len / 1000} km` : `${len} m`}</span>
    </div>
  )
}

const hazardTone = (h?: number | null) =>
  h == null ? 'text-muted' : h < 20 ? 'text-emerald-300' : h < 40 ? 'text-yellow-300' : h < 60 ? 'text-orange-300' : 'text-red-400'

function Cell({ k, children, w }: { k: string; children: ReactNode; w?: string }) {
  return (
    <div className={`flex items-baseline gap-1.5 ${w ?? ''}`}>
      <span className="text-[9px] tracking-[0.14em] text-faint">{k}</span>
      <span className="text-ink">{children}</span>
    </div>
  )
}

export default function StatusBar({ cursor, info, zoom, apiOk, center, vehicle = false }: { cursor: LonLat | null; info: PointInfo | null; zoom: number; apiOk: boolean | null; center?: ReactNode; vehicle?: boolean }) {
  const v = (x: number | null | undefined, d = 1) => (x == null ? '—' : x.toFixed(d))
  return (
    <footer className="glass relative flex h-10 items-center gap-6 rounded-xl px-4 font-mono text-[11px]">
      <Cell k="LAT" w="w-[118px]">{cursor ? fmtLat(cursor.lat) : '—'}</Cell>
      <Cell k="LON" w="w-[118px]">{cursor ? fmtLon(cursor.lon) : '—'}</Cell>
      <div className="h-5 w-px bg-line-2" />
      {info?.inside ? (
        <>
          <Cell k="ELEV">{v(info.elevation_m)} m</Cell>
          <Cell k="SLOPE">{v(info.slope_deg)}°</Cell>
          <Cell k="ROUGH">{v(info.roughness_m, 2)} m</Cell>
          <div className="flex items-baseline gap-1.5">
            <span className="text-[9px] tracking-[0.14em] text-faint">HAZARD</span>
            <span className={hazardTone(info.hazard)}>{v(info.hazard, 0)}</span>
          </div>
        </>
      ) : (
        <span className="min-w-0 truncate text-faint">{vehicle ? 'vehicle position' : apiOk === false ? 'terrain readout unavailable · planner offline' : apiOk === null ? 'connecting to planner…' : cursor ? 'outside analysed terrain' : 'hover for terrain · click for 3D / 360°'}</span>
      )}
      {/* map legend sits in the free middle of the bar */}
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center font-sans"><div className="pointer-events-auto rounded-full bg-[#05070a]/70 px-1">{center}</div></div>
      <div className="ml-auto flex shrink-0 items-center gap-5 text-muted">
        <ScaleBar zoom={zoom} />
        <span>Z{zoom}</span>
        <span className="flex items-center gap-1.5">
          <i className={`h-1.5 w-1.5 rounded-full ${apiOk ? 'bg-emerald-400 shadow-[0_0_8px_#34d399]' : apiOk === false ? 'bg-red-500' : 'bg-amber-400'}`} />
          {apiOk ? 'Planner online' : apiOk === false ? 'Planner offline' : 'Checking…'}
        </span>
        <span className="hidden text-faint 2xl:inline">IAU Mars 2000 · planetocentric · E+</span>
      </div>
    </footer>
  )
}
