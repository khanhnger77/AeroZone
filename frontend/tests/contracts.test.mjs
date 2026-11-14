import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function source(path) {
  const text = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}
const { parseLidar } = await source('../src/services/lidar.ts')
const sim = await source('../src/services/sim.ts')
function frame(n = 1) {
  const json = JSON.stringify({ n, w: 2, h: 1, range: 10, v_fov: [-45, 15], route: [], candidates: [], shadows: [], carrot: null, mode: 'track' })
  const header = new TextEncoder().encode(json.padEnd(Math.ceil(json.length / 4) * 4, ' '))
  const buffer = new ArrayBuffer(8 + header.length + n * 7 + 4), v = new DataView(buffer)
  new Uint8Array(buffer).set(new TextEncoder().encode('LDR1'))
  v.setUint32(4, header.length, true); new Uint8Array(buffer, 8).set(header)
  if (n) { v.setInt16(8 + header.length, -123, true); v.setUint8(8 + header.length + n * 6, 1) }
  v.setUint16(8 + header.length + n * 7, 1000, true)
  return buffer
}
test('LDR1 handles odd point counts and unaligned depth blocks', () => {
  const parsed = parseLidar(frame())
  assert.equal(parsed.points[0], -123)
  assert.deepEqual([...parsed.hazards], [1])
  assert.deepEqual([...parsed.depth], [1000, 0])
})
test('LDR1 allows no points and rejects truncated frames, magic and dimensions', () => {
  assert.equal(parseLidar(frame(0)).points.length, 0)
  assert.throws(() => parseLidar(frame().slice(0, -1)), /payload/)
  assert.throws(() => parseLidar(new ArrayBuffer(8)), /magic/)
  const bad = frame(); new DataView(bad).setUint32(4, 0xffffffff, true)
  assert.throws(() => parseLidar(bad), /header/)
})
test('C5 sends selected geometry, alternatives, endpoints and obstacle seed exactly', async t => {
  let sent
  t.mock.method(globalThis, 'fetch', async (url, init) => { sent = { url, body: JSON.parse(init.body) }; return new Response('{"id":"1234567890"}') })
  const routes = ['direct', 'balanced', 'cautious'].map(id => ({ id, label: id, color: '#fff', length_m: 10, coordinates: [[77.4, 18.4], [77.3, 18.5]], profile: [] }))
  await sim.loadSimMission({ routes }, 'balanced', { enabled: true, seed: 7 })
  assert.equal(sent.url, '/sim/api/mission')
  assert.equal(sent.body.route.id, 'balanced')
  assert.equal(sent.body.alternatives.length, 2)
  assert.equal('profile' in sent.body.route, false)
  assert.deepEqual(sent.body.start, { lon: 77.4, lat: 18.4 })
  assert.deepEqual(sent.body.end, { lon: 77.3, lat: 18.5 })
  assert.deepEqual(sent.body.obstacles, { enabled: true, seed: 7 })
})
test('C5 reports unreachable sim, HTTP detail, and command rejection', async t => {
  const mock = t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 502 }))
  await assert.rejects(sim.simConfig(), /Sim server not reachable on :8100/)
  mock.mock.mockImplementation(async () => new Response('{"detail":"Terrain missing"}', { status: 503 }))
  await assert.rejects(sim.simConfig(), /Terrain missing/)
  mock.mock.mockImplementation(async () => new Response('{"ok":false,"error":"Invalid speed"}'))
  await assert.rejects(sim.simCommand({ type: 'start' }), /Invalid speed/)
})
test('C4 planRoute posts start/end and surfaces the 422 detail string', async t => {
  const api = await source('../src/services/api.ts')
  let sent
  const mock = t.mock.method(globalThis, 'fetch', async (url, init) => {
    sent = { url, body: JSON.parse(init.body) }
    return new Response('{"detail":"start is outside the terrain grid"}', { status: 422 })
  })
  await assert.rejects(api.planRoute({ lon: 10, lat: 10 }, { lon: 77.3, lat: 18.5 }), /start is outside the terrain grid/)
  assert.equal(sent.url, '/api/route')
  assert.deepEqual(sent.body, { start: { lon: 10, lat: 10 }, end: { lon: 77.3, lat: 18.5 } })
  mock.mock.mockImplementation(async () => new Response('not json', { status: 500, statusText: 'Server Error' }))
  await assert.rejects(api.getPoint({ lon: 77.4, lat: 18.4 }), /500 Server Error/)
})
