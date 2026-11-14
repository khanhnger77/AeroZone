import { ArrowRight, Database, Mountain, Route as RouteIcon, ShieldAlert } from 'lucide-react'
import type { LayersManifest } from '../types'

/** First-load mission briefing. Numbers come from the processed data, not hard-coded claims. */
export default function Briefing({ manifest, nSamples, onEnter }: { manifest: LayersManifest | null; nSamples: number; onEnter: () => void }) {
  const hz = manifest?.raster_layers.find((l) => l.id === 'hazard')?.model
  const slope = manifest?.raster_layers.find((l) => l.id === 'slope')?.stats
  const pillars = [
    { icon: <Database size={18} />, t: 'NASA data integration', d: 'HiRISE imagery and 1 m terrain, THEMIS thermal IR, CTX, MOLA, plus Perseverance rover data, on one map.' },
    { icon: <ShieldAlert size={18} />, t: 'Terrain intelligence', d: 'Slope, roughness and a transparent prototype hazard model, with its assumptions shown.' },
    { icon: <RouteIcon size={18} />, t: 'Mission planning', d: 'Compare EVA route options and get a recommendation that explains itself.' },
  ]
  return (
    <div className="scroll-thin absolute inset-0 z-[2000] grid place-items-start overflow-y-auto sm:place-items-center bg-[radial-gradient(ellipse_at_center,rgba(4,6,10,.55),rgba(4,6,10,.92))] p-4 backdrop-blur-[2px]">
      <div className="glass hud-corners fade-in mx-auto my-auto w-full max-w-[760px] rounded-2xl p-5 sm:p-8">
        <div className="label !text-mars">Mission briefing · Jezero Crater, Mars</div>
        <h1 className="display mt-2 text-4xl sm:text-5xl font-bold leading-[1.02] tracking-tight">
          MARS <span className="bg-gradient-to-r from-mars to-mars-2 bg-clip-text text-transparent">ODYSSEY</span>
        </h1>
        <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
          Explore smarter. Navigate safer. Discover more. A layered map that turns NASA orbital and rover data into
          EVA planning information for the delta Perseverance has been exploring since 2021.
        </p>

        <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {pillars.map((p) => (
            <div key={p.t} className="rounded-xl border border-line bg-panel-2/60 p-4">
              <div className="grid h-8 w-8 place-items-center rounded-lg bg-mars/15 text-mars">{p.icon}</div>
              <div className="mt-3 text-sm font-semibold">{p.t}</div>
              <div className="mt-1 text-xs leading-snug text-muted">{p.d}</div>
            </div>
          ))}
        </div>

        <div className="mt-6 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4">
          {[
            ['Study area', '9.5 × 5', 'km'],
            ['Terrain grid', '5', 'm'],
            ['Median slope', slope ? slope.median.toFixed(1) : '–', '°'],
            ['Sample sites', String(nSamples || '–'), ''],
          ].map(([l, v, u]) => (
            <div key={l} className="bg-panel px-4 py-3">
              <div className="label !text-[9px]">{l}</div>
              <div className="display text-2xl font-semibold">{v}<span className="ml-1 font-mono text-xs text-muted">{u}</span></div>
            </div>
          ))}
        </div>

        <div className="mt-6 flex flex-wrap items-center gap-4">
          <button onClick={onEnter} className="btn-primary !px-5 !py-2.5 text-[15px]" autoFocus>
            Begin exploration <ArrowRight size={16} />
          </button>
          <span className="flex items-center gap-1.5 text-[11px] text-muted">
            <Mountain size={13} /> {hz ? `${(hz.class_fraction.Low * 100).toFixed(0)}% of the area is low modelled hazard.` : ''} Prototype model, not a safety certification.
          </span>
        </div>
      </div>
    </div>
  )
}
