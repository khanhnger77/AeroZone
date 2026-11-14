import { useEffect, useRef, useState } from 'react'
import { parseLidar, type LidarFrame } from '../services/lidar'

const COLORS = { track: '#34d399', avoid: '#5ee6ff', recover: '#fbbf24', blocked: '#f87171' }
export default function LidarScreen() {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [frame, setFrame] = useState<LidarFrame | null>(null)
  const [view, setView] = useState<'cloud' | 'depth'>('cloud')
  const [status, setStatus] = useState('connecting')
  useEffect(() => {
    let stopped = false, socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    function connect() {
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/sim/ws`)
      socket.binaryType = 'arraybuffer'
      socket.onopen = () => { setStatus('waiting for scan'); socket?.send(JSON.stringify({ type: 'subscribe', topics: ['lidar'] })) }
      socket.onmessage = e => {
        if (e.data instanceof ArrayBuffer) {
          try { setFrame(parseLidar(e.data)); setStatus('live') } catch { setStatus('invalid scan') }
        }
      }
      socket.onerror = () => socket?.close()
      socket.onclose = () => { if (!stopped) { setStatus('reconnecting'); retry = setTimeout(connect, 1500) } }
    }
    connect()
    return () => { stopped = true; clearTimeout(retry); socket?.close() }
  }, [])
  useEffect(() => {
    const el = canvas.current, ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const dpr = window.devicePixelRatio || 1
    el.width = 200 * dpr; el.height = 110 * dpr
    ctx.scale(dpr, dpr); ctx.fillStyle = '#04060a'; ctx.fillRect(0, 0, 200, 110)
    if (!frame || status !== 'live') { ctx.fillStyle = '#8494a7'; ctx.font = '11px monospace'; ctx.fillText(status, 10, 55); return }
    const { header: h, points, hazards, depth } = frame
    if (view === 'depth') {
      const colors = [[255,90,60], [255,190,80], [120,220,140], [60,160,230], [40,60,160]]
      // Rotate azimuth so front is in the centre, left-of-rover is on the left.
      for (let row = 0; row < h.h; row++) for (let col = 0; col < h.w; col++) {
        const sourceCol = h.w - 1 - col
        const value = depth[row * h.w + sourceCol]
        if (!value) continue
        const t = Math.min(3.999, value / (h.range * 100) * 4), index = Math.floor(t), f = t - index
        const color = colors[index].map((v, i) => Math.round(v + (colors[index + 1][i] - v) * f))
        ctx.fillStyle = `rgb(${color.join(',')})`
        ctx.fillRect(col * 200 / h.w, (h.h - 1 - row) * 90 / h.h, Math.ceil(200 / h.w), Math.ceil(90 / h.h))
      }
      ctx.fillStyle = '#e8eef5'; ctx.font = '9px monospace'
      ctx.fillText('rear       front       rear', 4, 100)
      ctx.fillText(`${Math.round(h.v_fov[0])}°…${Math.round(h.v_fov[1])}°`, 4, 109)
      const gradient = ctx.createLinearGradient(120, 0, 195, 0)
      colors.forEach((c, i) => gradient.addColorStop(i / 4, `rgb(${c.join(',')})`))
      ctx.fillStyle = gradient; ctx.fillRect(120, 103, 75, 5)
      return
    }
    const xy = ([x, y]: [number, number]) => [100 - y * 5.5, 72 - x * 5.5]
    ctx.strokeStyle = '#1c2633'
    for (const r of [5, 10]) { ctx.beginPath(); ctx.arc(100, 72, r * 5.5, 0, Math.PI * 2); ctx.stroke() }
    function path(points: [number, number][], color: string, dashed = false) {
      ctx!.strokeStyle = color; ctx!.lineWidth = 2; ctx!.setLineDash(dashed ? [3, 3] : [])
      ctx!.beginPath(); points.forEach((p, i) => { const [x, y] = xy(p); if (i) ctx!.lineTo(x, y); else ctx!.moveTo(x, y) }); ctx!.stroke(); ctx!.setLineDash([])
    }
    path(h.route ?? [], '#ffffff', true)
    for (let i = 0; i < h.n; i++) {
      const [x, y] = xy([points[i * 3] / 100, points[i * 3 + 1] / 100])
      ctx.fillStyle = hazards[i] ? '#f87171' : `hsl(${Math.max(100, Math.min(210, 170 + points[i * 3 + 2] / 3))} 60% 55%)`
      ctx.fillRect(x, y, hazards[i] ? 2 : 1, hazards[i] ? 2 : 1)
    }
    ctx.fillStyle = '#ff9a3d'
    for (const p of h.shadows ?? []) { const [x, y] = xy(p); ctx.fillRect(x - 1, y - 1, 3, 3) }
    for (const candidate of h.candidates ?? []) if (candidate.best) path(candidate.xy, '#5ee6ff')
    if (h.carrot) { const [x, y] = xy(h.carrot); ctx.fillStyle = '#5ee6ff'; ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill() }
    ctx.strokeStyle = '#e8eef5'; ctx.strokeRect(100 - 2.7 * 5.5 / 2, 72 - 2.9 * 5.5 / 2, 2.7 * 5.5, 2.9 * 5.5)
  }, [frame, view, status])
  return <section className="glass overflow-hidden rounded-lg" style={{ width: 200 }} aria-label="Lidar scan">
    <div className="flex items-center justify-between px-2 py-1 text-[9px]">
      <span><i className="mr-1 inline-block h-1.5 w-1.5 rounded-full" style={{ background: status === 'live' ? COLORS[frame?.header.mode ?? 'track'] : '#8494a7' }} />Lidar {frame?.header.range ?? 10} m · {frame?.header.mode ?? status}</span>
      <div>{(['cloud', 'depth'] as const).map(v => <button key={v} aria-pressed={view === v} onClick={() => setView(v)} className={view === v ? 'px-1 text-cyan' : 'px-1 text-muted'}>{v === 'cloud' ? 'Cloud' : 'Depth'}</button>)}</div>
    </div>
    <canvas ref={canvas} style={{ width: 200, height: 110 }} />
  </section>
}
