import { ArrowUpRight, Flag, FlaskConical, Mountain, Satellite, Waves } from 'lucide-react'
import type { LayersManifest } from '../types'
import { PanelHeader, Section, Stat } from './ui'

const PLACES = [
  { name: 'Octavia E. Butler landing', sub: 'Sol 0 · crater floor', lon: 77.4509, lat: 18.4446, zoom: 16, Icon: Flag },
  { name: 'Séítah', sub: 'Igneous floor samples #2–#9', lon: 77.4440, lat: 18.4320, zoom: 15, Icon: Mountain },
  { name: 'Delta front', sub: 'Skinner & Wildcat Ridge', lon: 77.4060, lat: 18.4588, zoom: 16, Icon: Waves },
  { name: 'Upper fan', sub: 'Otis Peak to Lefroy Bay', lon: 77.3600, lat: 18.4850, zoom: 15, Icon: Mountain },
  { name: 'Cheyava Falls', sub: 'Sample #25 Sapphire Canyon', lon: 77.3052, lat: 18.4975, zoom: 17, Icon: FlaskConical },
]

const SOURCES: [string, string, string][] = [
  ['HiRISE', 'MRO', 'Ortho 25 cm · DTM 1 m'],
  ['CTX', 'MRO', 'Ortho 6 m'],
  ['THEMIS', 'Mars Odyssey', 'Day/night IR 100 m'],
  ['MOLA', 'MGS', 'Shaded relief 463 m'],
  ['Perseverance', 'Mars 2020', 'Traverse · samples · images'],
]

export default function ExplorerPanel({ manifest, onFly }: { manifest: LayersManifest | null; onFly: (lon: number, lat: number, z: number) => void }) {
  const hz = manifest?.raster_layers.find((l) => l.id === 'hazard')?.model
  const slope = manifest?.raster_layers.find((l) => l.id === 'slope')?.stats
  return (
    <div>
      <PanelHeader kicker="Explorer" title="Jezero delta"
        subtitle="A 9.5 × 5 km window along Perseverance's traverse, sols 0–1215, from the crater floor up the ancient river delta." />
      <Section title="At a glance">
        <div className="grid grid-cols-3 gap-2">
          <Stat label="Grid" value="5" unit="m" />
          <Stat label="Med. slope" value={slope ? slope.median.toFixed(1) : '–'} unit="°" />
          <Stat label="Low hazard" value={hz ? (hz.class_fraction.Low * 100).toFixed(0) : '–'} unit="%" tone="text-emerald-300" />
        </div>
      </Section>
      <Section title="Fly to">
        <ul className="space-y-1.5">
          {PLACES.map(({ name, sub, lon, lat, zoom, Icon }) => (
            <li key={name}>
              <button onClick={() => onFly(lon, lat, zoom)}
                className="group flex w-full items-center gap-3 rounded-lg border border-transparent px-2 py-2 text-left transition-all hover:border-line-2 hover:bg-white/[0.03]">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-panel-2 text-mars ring-1 ring-line"><Icon size={15} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{name}</span>
                  <span className="block truncate text-[11px] text-muted">{sub}</span>
                </span>
                <ArrowUpRight size={15} className="text-faint transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-mars" />
              </button>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Integrated NASA data" icon={<Satellite size={11} />}>
        <ul className="divide-y divide-line/70 text-xs">
          {SOURCES.map(([a, m, b]) => (
            <li key={a} className="flex items-center justify-between py-1.5">
              <span><span className="font-medium">{a}</span> <span className="text-faint">· {m}</span></span>
              <span className="text-muted">{b}</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  )
}
