import { Compass, FlaskConical, Info, Layers, Route, ShieldAlert, CloudSun } from 'lucide-react'
import type { Tab } from '../types'

const ITEMS: { id: Tab; label: string; Icon: typeof Compass }[] = [
  { id: 'explorer', label: 'Explorer', Icon: Compass },
  { id: 'layers', label: 'Data Layers', Icon: Layers },
  { id: 'weather', label: 'Weather', Icon: CloudSun },
  { id: 'safety', label: 'Safety', Icon: ShieldAlert },
  { id: 'science', label: 'Science', Icon: FlaskConical },
  { id: 'mission', label: 'Mission Planner', Icon: Route },
]

interface Props {
  tab: Tab
  open: boolean
  onSelect: (t: Tab) => void
  onBriefing: () => void
  /** 'vertical' = desktop side rail, 'horizontal' = phone bottom navigation */
  orientation?: 'vertical' | 'horizontal'
}

export default function IconRail({ tab, open, onSelect, onBriefing, orientation = 'vertical' }: Props) {
  const horiz = orientation === 'horizontal'
  return (
    <nav aria-label="Main"
      className={`glass flex items-center rounded-2xl ${horiz ? 'h-[60px] w-full justify-around px-1' : 'w-[64px] flex-col gap-1 py-3'}`}>
      <button onClick={onBriefing} title="Mission briefing" aria-label="Mission briefing"
        className={`grid shrink-0 place-items-center rounded-full bg-gradient-to-br from-mars to-[#7a2a10] shadow-[0_0_18px_rgba(255,106,61,.5)] ${horiz ? 'h-8 w-8' : 'mb-2 h-10 w-10'}`}>
        <span className="h-2.5 w-2.5 -translate-x-1 -translate-y-1 rounded-full bg-[#ffb08f]/70" />
      </button>
      {ITEMS.map(({ id, label, Icon }) => {
        const active = tab === id && open
        return (
          <button key={id} onClick={() => onSelect(id)} aria-label={label} aria-current={active ? 'page' : undefined}
            className={`group relative flex flex-col items-center gap-1 rounded-xl transition-colors ${horiz ? 'min-w-[52px] px-1 py-1.5' : 'w-14 py-2'} ${active ? 'bg-mars/15 text-mars' : 'text-muted hover:bg-white/5 hover:text-ink'}`}>
            {active && (
              <span className={`absolute rounded-full bg-mars shadow-[0_0_10px_#ff6a3d] ${horiz ? '-top-[5px] left-3 right-3 h-[3px]' : '-left-[5px] top-2 bottom-2 w-[3px]'}`} />
            )}
            <Icon size={horiz ? 18 : 19} strokeWidth={1.8} />
            <span className="text-[9px] font-medium leading-none">{label.split(' ')[0]}</span>
          </button>
        )
      })}
      {!horiz && <div className="mt-auto pt-2 text-faint" title="Space Apps 2026"><Info size={14} /></div>}
    </nav>
  )
}
