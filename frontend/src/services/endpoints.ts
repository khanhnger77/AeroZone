/**
 * Where the planner API (/api) and the sim server (/sim) live.
 *
 * Local runs (`pixi run demo`, Vite dev) serve everything from one origin, so the paths stay
 * relative. A static deployment (GitHub Pages) has no server of its own: it is built with
 * VITE_BACKEND_URL set to the hosted backend (the Hugging Face Space), which serves both
 * /api and /sim on a single origin.
 */
const configured = (import.meta.env.VITE_BACKEND_URL ?? '').trim().replace(/\/+$/, '')

/** Origin of the backend, e.g. "https://user-aerozone.hf.space"; '' when same-origin. */
export const BACKEND = configured

/** Origin the sim iframe runs on: the backend's origin, or this page's own. */
export const SIM_ORIGIN = BACKEND ? new URL(BACKEND).origin : location.origin

/** Absolute or same-origin URL of a backend path such as "/api/route" or "/sim/config". */
export const backendUrl = (path: string) => BACKEND + path

/** WebSocket URL of a backend path ("/sim/ws"), ws: or wss: to match the backend's scheme. */
export function backendWs(path: string): string {
  const base = BACKEND ? new URL(BACKEND) : location
  return `${base.protocol === 'https:' ? 'wss:' : 'ws:'}//${base.host}${path}`
}
