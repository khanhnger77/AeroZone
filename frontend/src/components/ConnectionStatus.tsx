import { RefreshCw } from 'lucide-react'
import type { ConnectionState } from '../useConnections'

const TONES = {
  checking: 'border-amber-400/20 bg-amber-400/5 text-amber-200',
  online: 'border-emerald-400/20 bg-emerald-400/5 text-emerald-300',
  offline: 'border-line-2 bg-panel-2 text-muted',
}
export default function ConnectionStatus({ planner, sim, checking, onRetry }: {
  planner: ConnectionState; sim: ConnectionState; checking: boolean; onRetry: () => void
}) {
  return <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]" aria-label="Service connections">
    <div role="status" aria-live="polite" className="flex flex-wrap gap-1.5">
      {([['Planner', planner], ['Rover sim', sim]] as const).map(([name, state]) => <span key={name} className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-1 ${TONES[state]}`}>
        <i aria-hidden="true" className={`h-1.5 w-1.5 rounded-full bg-current ${state === 'checking' ? 'animate-pulse' : ''}`} />
        {name} · {state === 'online' ? 'Connected' : state === 'checking' ? 'Checking…' : name === 'Planner' ? 'Offline' : 'Unavailable'}
      </span>)}
    </div>
    <button type="button" aria-label="Retry connections" title="Retry connections" disabled={checking} onClick={onRetry}
      className="grid h-7 w-7 place-items-center rounded-full border border-line-2 text-muted hover:text-ink focus-visible:outline-2 focus-visible:outline-cyan disabled:opacity-40">
      <RefreshCw aria-hidden="true" size={12} className={checking ? 'animate-spin' : ''} />
    </button>
  </div>
}
