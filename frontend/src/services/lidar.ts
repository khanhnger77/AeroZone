export interface LidarHeader {
  n: number; w: number; h: number; range: number; v_fov: [number, number]; sim_time: number
  mode: 'track' | 'avoid' | 'recover' | 'blocked'; route: [number, number][]
  candidates: { xy: [number, number][]; ok: boolean; best: boolean }[]
  carrot: [number, number] | null; shadows: [number, number][]; rocks_near: number; craters_near: number
}
export interface LidarFrame { header: LidarHeader; points: Int16Array; hazards: Uint8Array; depth: Uint16Array }
/** Blocks can start at odd offsets; copy before constructing typed arrays. */
export function parseLidar(buffer: ArrayBuffer): LidarFrame {
  const view = new DataView(buffer)
  if (buffer.byteLength < 8 || view.getUint32(0, false) !== 0x4c445231) throw new Error('Invalid LDR1 magic')
  const size = view.getUint32(4, true)
  if (size > buffer.byteLength - 8 || size % 4 !== 0) throw new Error('Invalid LDR1 header length')
  const header = JSON.parse(new TextDecoder().decode(buffer.slice(8, 8 + size))) as LidarHeader
  const { n, w, h } = header
  if (![n, w, h].every(v => Number.isSafeInteger(v) && v >= 0) || !w || !h || n > 1e6 || w * h > 1e6) throw new Error('Invalid LDR1 dimensions')
  let offset = 8 + size
  if (offset + n * 7 + w * h * 2 !== buffer.byteLength) throw new Error('Invalid LDR1 payload length')
  const pointBuffer = buffer.slice(offset, offset + n * 6); offset += n * 6
  const hazards = new Uint8Array(buffer.slice(offset, offset + n)); offset += n
  const depthBuffer = buffer.slice(offset)
  // DataView explicitly handles little-endian regardless of the host architecture.
  const points = new Int16Array(n * 3), depth = new Uint16Array(w * h)
  const pv = new DataView(pointBuffer), dv = new DataView(depthBuffer)
  for (let i = 0; i < points.length; i++) points[i] = pv.getInt16(i * 2, true)
  for (let i = 0; i < depth.length; i++) depth[i] = dv.getUint16(i * 2, true)
  return { header, points, hazards, depth }
}
