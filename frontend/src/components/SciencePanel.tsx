import { useEffect, useState } from 'react'
import type { FC, SampleProps } from '../types'
import { rawImagesForSol, type RawImage } from '../services/api'
import { fmtLat, fmtLon } from '../map/geo'
import { CategoryTag, Section, PanelHeader } from './ui'
import { sampleColor } from '../map/MarsMap'

interface Props {
  samples: FC<SampleProps> | null
  selected: number | null
  onSelect: (no: number) => void
  onFly: (lon: number, lat: number) => void
  onSetTarget: (lon: number, lat: number) => void
}

export default function SciencePanel({ samples, selected, onSelect, onFly, onSetTarget }: Props) {
  const feat = samples?.features.find((f) => f.properties.sample_no === selected)
  return (
    <div>
      <PanelHeader kicker="Science discovery" title="Sample sites" subtitle="Where Perseverance cored rock and regolith, with NASA facts and the rover images from that sol." />
      <Section title={`Perseverance sample sites · ${samples?.features.length ?? 0}`}>
        <ul className="scroll-thin max-h-64 space-y-0.5 overflow-y-auto pr-1">
          {samples?.features.map((f) => {
            const s = f.properties
            const [lon, lat] = f.geometry.coordinates as [number, number]
            return (
              <li key={s.sample_no}>
                <button onClick={() => { onSelect(s.sample_no); onFly(lon, lat) }}
                  className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs transition-colors ${selected === s.sample_no ? 'bg-mars/15 text-ink' : 'text-muted hover:bg-panel-2 hover:text-ink'}`}>
                  <i className="h-2 w-2 shrink-0 rounded-full" style={{ background: sampleColor(s.rock_class) }} />
                  <span className="w-6 font-mono text-[10px]">#{s.sample_no}</span>
                  <span className="flex-1 truncate">{s.name}</span>
                  <span className="truncate text-[10px]">{s.rock_class.split(' ')[0]}</span>
                </button>
              </li>
            )
          })}
        </ul>
      </Section>
      {feat ? <SampleDetail f={feat} onSetTarget={onSetTarget} /> :
        <p className="p-4 text-xs text-muted">Select a sample site on the map or in the list.</p>}
    </div>
  )
}

function SampleDetail({ f, onSetTarget }: { f: FC<SampleProps>['features'][number]; onSetTarget: Props['onSetTarget'] }) {
  const s = f.properties
  const [lon, lat] = f.geometry.coordinates as [number, number]
  const [imgs, setImgs] = useState<RawImage[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setImgs(null); setErr(null)
    rawImagesForSol(s.sol_sealed, 9).then((r) => live && setImgs(r)).catch((e) => live && setErr(String(e.message ?? e)))
    return () => { live = false }
  }, [s.sol_sealed])
  return (
    <>
      <Section title={`Sample #${s.sample_no}`}>
        <h2 className="text-lg font-semibold leading-tight">{s.name}</h2>
        <div className="text-xs text-muted">Target / feature: {s.feature}</div>
        <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
          <dt className="text-muted">Type</dt><dd>{s.type}</dd>
          <dt className="text-muted">Rock class</dt><dd>{s.rock_class}</dd>
          <dt className="text-muted">Sol sealed</dt><dd className="font-mono">{s.sol_sealed}</dd>
          <dt className="text-muted">Elevation</dt><dd className="font-mono">{s.elev_m.toFixed(1)} m</dd>
          <dt className="text-muted">Position</dt><dd className="font-mono text-[11px]">{fmtLat(lat)}<br />{fmtLon(lon)}</dd>
        </dl>
        <div className="mt-3 rounded-md border border-line bg-panel-2 p-2 text-[11px] leading-snug text-muted">
          <CategoryTag c="observation" /> Sample facts: <a className="underline" href={s.source} target="_blank" rel="noreferrer">NASA Mars Rock Samples</a>.<br />
          <CategoryTag c="derived" /> Position = rover fix on sol {s.location_sol}
          {s.location_sol_gap > 0 && <> ({s.location_sol_gap} sols before sealing — position uncertain at the 10–100 m level)</>}.
        </div>
        <button onClick={() => onSetTarget(lon, lat)}
          className="btn-primary mt-3 w-full">
          Plan EVA to this site →
        </button>
      </Section>
      <Section title={`Rover images · sol ${s.sol_sealed}`}>
        {err && <p className="text-xs text-red-300">Could not load NASA raw images: {err}</p>}
        {!imgs && !err && <p className="text-xs text-muted">Loading from NASA raw-image feed…</p>}
        {imgs && imgs.length === 0 && <p className="text-xs text-muted">No raw images listed for this sol.</p>}
        {imgs && imgs.length > 0 && (
          <>
            <div className="grid grid-cols-3 gap-1.5">
              {imgs.map((im) => (
                <a key={im.imageid} href={im.image_files.large} target="_blank" rel="noreferrer" title={im.camera.instrument}
                  className="group relative block aspect-square overflow-hidden rounded bg-panel-2">
                  <img src={im.image_files.small} alt={`${im.camera.instrument} sol ${im.sol}`} loading="lazy"
                    className="h-full w-full object-cover transition-transform group-hover:scale-105" />
                  <span className="absolute inset-x-0 bottom-0 truncate bg-black/60 px-1 text-[9px]">{im.camera.instrument}</span>
                </a>
              ))}
            </div>
            <p className="mt-2 text-[10px] leading-snug text-muted">
              Raw, unprocessed images from NASA/JPL-Caltech, matched by sol only — not guaranteed to show the sampled rock.
            </p>
          </>
        )}
      </Section>
    </>
  )
}
