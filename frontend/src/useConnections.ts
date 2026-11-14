import { useCallback, useEffect, useRef, useState } from 'react'

export type ConnectionState = 'checking' | 'online' | 'offline'

/** Poll independently so a missing sim never disables an available planner. */
export function useConnections() {
  const [planner, setPlanner] = useState<ConnectionState>('checking')
  const [sim, setSim] = useState<ConnectionState>('checking')
  const [checking, setChecking] = useState(false)
  const [revision, setRevision] = useState(0)
  const pending = useRef(false)
  const refresh = useCallback(() => setRevision(v => v + 1), [])

  useEffect(() => {
    let active = true
    const controllers = new Set<AbortController>()
    pending.current = false
    async function check(url: string, valid: (body: Record<string, unknown>) => boolean, update: (state: ConnectionState) => void) {
      const controller = new AbortController()
      controllers.add(controller)
      const timer = setTimeout(() => controller.abort(), 4000)
      try {
        const response = await fetch(url, { signal: controller.signal, cache: 'no-store' })
        const body = response.ok ? await response.json() : null
        if (active) update(body && valid(body) ? 'online' : 'offline')
      } catch { if (active) update('offline') }
      finally { clearTimeout(timer); controllers.delete(controller) }
    }
    async function poll() {
      if (pending.current) return
      pending.current = true
      setChecking(true)
      await Promise.all([
        check('/api/health', body => body.status === 'ok', setPlanner),
        check('/sim/config', body => body.protocol === 'mo-sim/1', setSim),
      ])
      if (active) { pending.current = false; setChecking(false) }
    }
    void poll()
    const timer = setInterval(() => { void poll() }, 15000)
    return () => { active = false; clearInterval(timer); controllers.forEach(c => c.abort()) }
  }, [revision])

  return { planner, sim, checking, refresh }
}
