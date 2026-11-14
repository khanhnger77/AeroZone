import { createElement } from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import L from 'leaflet'
import { MARKER_STYLE, type MarkerType } from '../sim/world'

const svgCache = new Map<MarkerType, string>()
/** The type's lucide icon as an SVG string (rendered once with the client renderer, then cached;
 *  react-dom/server would add ~270 kB to the bundle for this). */
function svg(type: MarkerType): string {
  let s = svgCache.get(type)
  if (!s) {
    const host = document.createElement('div')
    const root = createRoot(host)
    flushSync(() => root.render(createElement(MARKER_STYLE[type].Icon, { size: 14, strokeWidth: 2.2, color: '#05070a' })))
    s = host.innerHTML
    root.unmount()
    svgCache.set(type, s)
  }
  return s
}

// Fill the cache at import time: flushSync must not run inside a React render.
for (const t of Object.keys(MARKER_STYLE) as MarkerType[]) svg(t)

const esc =(t: string) => t.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!))

const iconCache = new Map<string, L.DivIcon>()
/**
 * Icons are cached by their content: react-leaflet swaps the DOM element whenever a marker gets a
 * new icon object, which closes nothing on mouse-out (stuck tooltips) and breaks a drag in progress.
 */
export function markerIcon(type: MarkerType, opts: { name: string; highlight?: boolean; selected?: boolean; arrowDeg?: number | null; speed?: number }) {
  const deg = opts.arrowDeg == null ? null : Math.round(opts.arrowDeg / 3) * 3
  const key = `${type}|${opts.name}|${!!opts.highlight}|${!!opts.selected}|${deg}|${deg == null ? 0 : Math.round((opts.speed ?? 0) * 10)}`
  let icon = iconCache.get(key)
  if (!icon) {
    if (iconCache.size > 600) iconCache.clear()
    icon = makeIcon(type, { ...opts, arrowDeg: deg })
    iconCache.set(key, icon)
  }
  return icon
}

/**
 * Leaflet icon for a location or a sim object: a round badge with the type's icon, the name above
 * it, a glow when highlighted, and optionally a heading arrow (screen angle in degrees, clockwise
 * from east) whose length grows with speed.
 */
function makeIcon(type: MarkerType, opts: { name: string; highlight?: boolean; selected?: boolean; arrowDeg?: number | null; speed?: number }) {
  const { color } = MARKER_STYLE[type]
  const arrow = opts.arrowDeg == null ? ''
    : `<span class="mo-mk-arrow" style="width:${Math.round(22 + Math.min(40, (opts.speed ?? 0) * 25))}px;transform:rotate(${opts.arrowDeg}deg)"></span>`
  return L.divIcon({
    className: '',
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    html: `<div class="mo-mk${opts.highlight ? ' hl' : ''}${opts.selected ? ' sel' : ''}" style="--c:${color}">${arrow}`
      + `<span class="mo-mk-body">${svg(type)}</span><span class="mo-mk-name">${esc(opts.name)}</span></div>`,
  })
}
