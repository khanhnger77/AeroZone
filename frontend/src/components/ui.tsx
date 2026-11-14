import type { ReactNode } from 'react'
import type { DataCategory, RasterLayer } from '../types'

export function Section({ title, children, right, icon }: { title: string; children: ReactNode; right?: ReactNode; icon?: ReactNode }) {
  return (
    <section className="border-b border-line/70 px-5 py-4 last:border-b-0">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="label flex items-center gap-1.5">{icon}{title}</h3>
        {right}
      </div>
      {children}
    </section>
  )
}

const CAT_LABEL: Record<DataCategory, [string, string]> = {
  observation: ['NASA obs', 'tag-obs'],
  derived: ['Derived', 'tag-derived'],
  model: ['Model', 'tag-model'],
  simulated: ['Simulated', 'tag-sim'],
}
export function CategoryTag({ c }: { c: DataCategory }) {
  const [label, cls] = CAT_LABEL[c]
  return <span className={`tag ${cls}`}>{label}</span>
}

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-mars ${on ? 'bg-mars shadow-[0_0_12px_rgba(255,106,61,.45)]' : 'bg-line-2'}`}>
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${on ? 'translate-x-4' : 'translate-x-0.5'}`} />
    </button>
  )
}

export function Legend({ layer, compact }: { layer: RasterLayer; compact?: boolean }) {
  const lg = layer.legend
  if (lg.type === 'ramp') {
    return (
      <div>
        <div className={`${compact ? 'h-1.5' : 'h-2'} rounded-full`} style={{ background: `linear-gradient(90deg, ${lg.colors.join(',')})` }} />
        <div className="mt-1 flex justify-between font-mono text-[10px] text-muted">
          <span>{fmt(lg.min)}</span><span className="text-faint">{layer.units}</span><span>{fmt(lg.max)}</span>
        </div>
      </div>
    )
  }
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1">
      {lg.classes.map((c) => (
        <span key={c.label} className="flex items-center gap-1.5 text-[11px] text-muted">
          <i className="inline-block h-2 w-2 rounded-full" style={{ background: c.color, boxShadow: `0 0 6px ${c.color}` }} />{c.label}
        </span>
      ))}
    </div>
  )
}

const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2).replace(/\.?0+$/, ''))

export function Stat({ label, value, unit, tone, icon }: { label: string; value: string | number; unit?: string; tone?: string; icon?: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-gradient-to-b from-panel-2 to-panel px-3 py-2.5">
      <div className="label flex items-center gap-1 !text-[9px]">{icon}{label}</div>
      <div className={`display mt-0.5 text-xl font-semibold leading-tight ${tone ?? 'text-ink'}`}>
        {value}{unit && <span className="ml-0.5 font-mono text-[11px] font-normal text-muted">{unit}</span>}
      </div>
    </div>
  )
}

export function PanelHeader({ kicker, title, subtitle }: { kicker: string; title: string; subtitle?: string }) {
  return (
    <div className="relative overflow-hidden border-b border-line/70 px-5 pb-4 pt-5">
      <div className="pointer-events-none absolute -right-10 -top-16 h-40 w-40 rounded-full bg-mars/10 blur-3xl" />
      <div className="label !text-mars">{kicker}</div>
      <h2 className="display mt-1 text-[22px] font-semibold leading-tight">{title}</h2>
      {subtitle && <p className="mt-1 text-[12.5px] leading-snug text-muted">{subtitle}</p>}
    </div>
  )
}
